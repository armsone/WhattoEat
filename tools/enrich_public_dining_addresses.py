#!/usr/bin/env python3
"""공식 사업장 자료에서 기관 인근 식당을 검색한다. 외부 검색 API·키는 사용하지 않는다."""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import html
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import unicodedata
import zipfile

import merge_public_dining_catalog as catalog

SOURCE = 'https://www.data.go.kr/data/15083033/fileData.do'


def norm(value):
    value = unicodedata.normalize('NFKC', html.unescape(value or '')).lower()
    value = re.sub(r'주식회사|유한회사|\(주\)|㈜', '', value)
    return re.sub(r'[^가-힣a-z0-9]', '', value)


def region_for(address, fallback):
    tokens = (address or '').split()
    if not tokens:
        return fallback
    if tokens[0] in catalog.INTEGRATED_SPECIAL_PREFIXES:
        return catalog.classify_integrated_city(tokens[1]) if len(tokens) > 1 else fallback
    return catalog.REGION_ALIASES.get(tokens[0], fallback)


def address_key(address, region):
    parsed = catalog.parse_address(address, region)
    return parsed[1] if parsed else ''


def build_index(zip_path, db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    stamp = f'{zip_path.stat().st_size}:{zip_path.stat().st_mtime_ns}'
    db.execute('CREATE TABLE IF NOT EXISTS meta (stamp TEXT)')
    old = db.execute('SELECT stamp FROM meta').fetchone()
    if old and old[0] == stamp:
        return db
    db.execute('DROP TABLE IF EXISTS businesses')
    db.execute('CREATE TABLE businesses (id TEXT, name TEXT, branch TEXT, n TEXT, fulln TEXT, prefix TEXT, region TEXT, address TEXT, addresskey TEXT, roadkey TEXT, number INTEGER, lat REAL, lon REAL)')
    batch = []
    with zipfile.ZipFile(zip_path) as archive:
        for filename in archive.namelist():
            if not filename.endswith('.csv'):
                continue
            fallback = filename.rsplit('_', 2)[1]
            for row in csv.DictReader(io.TextIOWrapper(archive.open(filename), encoding='utf-8-sig')):
                if row.get('상권업종대분류명') != '음식':
                    continue
                address = row.get('도로명주소') or row.get('지번주소') or ''
                region = region_for(address, fallback)
                name, branch = row.get('상호명', ''), row.get('지점명', '')
                key = address_key(address, region)
                try:
                    lat, lon = float(row['위도']), float(row['경도'])
                    if not (32 < lat < 39.5 and 124 < lon < 132):
                        continue
                    number = int(key.rsplit('|', 1)[1].split('-')[0]) if key else 0
                except (ValueError, KeyError):
                    continue
                full = name if not branch or norm(name).endswith(norm(branch)) else name + branch
                batch.append((row['상가업소번호'], name, branch, norm(name), norm(full), norm(name)[:3], region, address, key, key.rsplit('|', 1)[0], number, lat, lon))
                if len(batch) >= 2000:
                    db.executemany('INSERT INTO businesses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', batch)
                    batch.clear()
    db.executemany('INSERT INTO businesses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', batch)
    for col in ('n', 'fulln', 'prefix', 'addresskey', 'roadkey'):
        db.execute(f'CREATE INDEX idx_{col} ON businesses ({col})')
    db.execute('DELETE FROM meta')
    db.execute('INSERT INTO meta VALUES (?)', (stamp,))
    db.commit()
    return db


def distance(a, b):
    lat = math.radians((a[0] + b[0]) / 2)
    return 6371000 * math.hypot(math.radians(a[0] - b[0]), math.radians(a[1] - b[1]) * math.cos(lat))


def institution_centers(db, institutions):
    centers = {}
    for office in institutions:
        region = office['region']
        key = address_key(office['address'], region)
        if not key:
            continue
        rows = db.execute('SELECT * FROM businesses WHERE addresskey=?', (key,)).fetchall()
        method = 'same-official-road-address'
        if not rows:
            # 같은 도로의 인근 사업장 좌표를 검색 중심으로만 사용한다. 기관의 공식 좌표가 아니다.
            try:
                number = int(key.rsplit('|', 1)[1].split('-')[0])
            except ValueError:
                continue
            rows = db.execute('SELECT * FROM businesses WHERE roadkey=? AND ABS(number-?)<=10', (key.rsplit('|', 1)[0], number)).fetchall()
            method = 'nearby-road-address-search-center'
        if not rows:
            continue
        lat = sum(r['lat'] for r in rows) / len(rows)
        lon = sum(r['lon'] for r in rows) / len(rows)
        if max(distance((lat, lon), (r['lat'], r['lon'])) for r in rows) > 500:
            continue
        centers.setdefault((office['institution'], region), []).append({**office, 'latitude': lat, 'longitude': lon, 'coordinateMethod': method, 'coordinateEvidenceIDs': [r['id'] for r in rows]})
    return centers


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt', encoding='utf-8') as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--business-zip', type=Path, required=True)
    parser.add_argument('--institutions', type=Path, required=True)
    parser.add_argument('--input', type=Path, action='append', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--as-of', type=dt.date.fromisoformat, default=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date())
    parser.add_argument('--months', type=int, default=18)
    parser.add_argument('--radius', type=int, default=3000)
    args = parser.parse_args()
    db = build_index(args.business_zip, args.cache_dir / 'businesses.sqlite')
    centers = institution_centers(db, json.loads(args.institutions.read_text()))
    recovered, pending, seen = [], [], set()
    oldest = catalog.months_before(args.as_of, args.months)
    for path in args.input:
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                record = json.loads(line)
                identity = (record.get('sourceURL'), record.get('recordID'))
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    if not oldest <= dt.date.fromisoformat(record['paymentDate']) <= args.as_of or record.get('isMeal') is not True:
                        continue
                except (ValueError, KeyError):
                    continue
                query = norm(record['name'])
                region = region_for(record.get('addressHint', ''), record['region'])
                offices = centers.get((record['institution'], record['region']), [])
                exact = db.execute('SELECT * FROM businesses WHERE region=? AND (n=? OR fulln=?)', (region, query, query)).fetchall()
                candidates = exact
                method = 'normalized-name-unique-in-region'
                if not candidates and len(query) >= 4 and offices:
                    candidates = db.execute('SELECT * FROM businesses WHERE region=? AND prefix=?', (region, query[:3])).fetchall()
                    candidates = [r for r in candidates if (query.startswith(r['fulln']) or r['fulln'].startswith(query)) and min(len(query), len(r['fulln'])) >= 4 and abs(len(query)-len(r['fulln'])) <= 5]
                    method = 'abbreviated-name-near-institution'
                unique = {(r['fulln'], r['addresskey']): dict(r) for r in candidates if r['addresskey']}
                if (len(unique) > 1 or method.startswith('abbreviated')) and offices:
                    near = {}
                    for key, candidate in unique.items():
                        office = min(offices, key=lambda o: distance((o['latitude'], o['longitude']), (candidate['lat'], candidate['lon'])))
                        meters = distance((office['latitude'], office['longitude']), (candidate['lat'], candidate['lon']))
                        if meters <= args.radius:
                            candidate['institutionEvidence'] = office
                            candidate['institutionDistanceMeters'] = round(meters)
                            near[key] = candidate
                    unique = near
                    method = 'exact-name-near-institution' if exact else method
                if len(unique) != 1:
                    pending.append({**record, 'searchCandidates': [{k: r[k] for k in ('id', 'name', 'branch', 'address')} for r in list(unique.values())[:20]]})
                    continue
                result = next(iter(unique.values()))
                output = {**record, 'region': result['region'], 'address': result['address'], 'addressSourceURL': SOURCE, 'addressSourceRecordID': result['id'], 'addressMatchMethod': method, 'canonicalName': result['name'] if not result['branch'] or norm(result['name']).endswith(norm(result['branch'])) else result['name'] + ' ' + result['branch']}
                for key in ('institutionEvidence', 'institutionDistanceMeters'):
                    if key in result:
                        output[key] = result[key]
                recovered.append(output)
    write_rows(args.output_dir / 'recovered-records.jsonl.gz', recovered)
    write_rows(args.output_dir / 'remaining-records.jsonl.gz', pending)
    (args.output_dir / 'sources.json').write_text(json.dumps({'businessSourceURL': SOURCE, 'businessZIP_SHA256': hashlib.file_digest(args.business_zip.open('rb'), 'sha256').hexdigest(), 'institutions': [o for rows in centers.values() for o in rows], 'asOf': str(args.as_of), 'months': args.months}, ensure_ascii=False, indent=2))
    print(f'주소 보완 기록 {len(recovered)}건 저장: {args.output_dir}')


if __name__ == '__main__':
    main()
