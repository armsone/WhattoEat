#!/usr/bin/env python3
"""전국 공공기관 업무추진비 식당 기록을 하나의 WhattoEat 카탈로그(schemaVersion 1)로 통합한다.

입력:
  - 서울(기존 1권역): tools/build_public_dining_catalog.py 를 import 하여
    data/public-dining/seoul-oa22156-window.csv.gz 축소 원자료로 오프라인 재생성한다.
  - 3권역 shard: data/public-dining/{capital_central,east,southwest}/records.jsonl.gz
    한 줄에 하나의 JSON 객체(원문 1행 = 결제 1건). 필수 키:
      region(내부 17개 권역 약칭), institution, department, name, address,
      paymentDate(YYYY-MM-DD), sourceURL(공식 원문 HTTPS), recordID(원문 문서 id+행 번호)
    같은 폴더의 coverage.json / sources.json 은 있으면 참고 정보로만 읽는다(구조 미고정).

출력: 기본 WhattoEat/PublicDiningCatalog.json (--output 으로 변경).
  - 최상위 필수 계약(schemaVersion, generatedAt, coverageDescription, sourceURLs, restaurants)을 유지한다.
  - 식당 항목에 region / coverageDescription / sourceURLs 를 추가해 앱이 지역별 범위와 출처를 표시한다.
  - 최상위 regions 에 내부 17개 권역의 확보 상태(수집 식당 수·기관 수·기간·미확보)를 그대로 적는다.

사용법:
  python3 tools/merge_public_dining_catalog.py                      # 기본 경로로 통합 생성
  python3 tools/merge_public_dining_catalog.py --output X.json      # 출력 경로 지정
  python3 tools/merge_public_dining_catalog.py --report R.md        # 지역별 표를 파일로(미지정 시 stdout)
  python3 tools/merge_public_dining_catalog.py --strict             # 미확보 지역이 있으면 종료 코드 3

표준 라이브러리만 사용하며 네트워크 요청을 하지 않는다. shard 의 미사용 열은 결과에 보존하지 않는다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_public_dining_catalog as seoul  # noqa: E402  (서울 단일 수집기, 수정하지 않음)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "public-dining"
OUTPUT_JSON = REPO_ROOT / "WhattoEat" / "PublicDiningCatalog.json"

# --- 지역 정의 -------------------------------------------------------------------
REGIONS_17 = ["서울", "경기", "인천", "대전", "세종", "충북", "충남",
              "부산", "대구", "울산", "경북", "경남", "강원",
              "광주", "전북", "전남", "제주"]
SHARD_REGIONS = {
    "seoul": ["서울"],
    "capital_central": ["경기", "인천", "대전", "세종", "충북", "충남"],
    "east": ["부산", "대구", "울산", "경북", "경남", "강원"],
    "southwest": ["광주", "전북", "전남", "제주"],
}
SHARD_LABELS = {"seoul": "서울", "capital_central": "수도권·중부",
                "east": "동부", "southwest": "남서부"}
REGION_TO_SHARD = {r: s for s, rs in SHARD_REGIONS.items() for r in rs}
SHARD_REGIONS["address-enrichment"] = REGIONS_17
SHARD_LABELS["address-enrichment"] = "공식 사업장 주소 보완"

# 광주 5개 자치구 (통합특별시 주소에서 광주/전남 내부 권역 분류 기준)
GWANGJU_DISTRICTS = {"동구", "서구", "남구", "북구", "광산구"}
# 전남광주 통합특별시 표기 (REGION_ALIASES 에 한 지역으로 고정 매핑하지 않음)
INTEGRATED_SPECIAL_PREFIXES = ["전남광주통합특별시", "전남광주시", "전남광주"]

# 광역 표기 변형 → 내부 17개 권역 약칭. 주소 접두와 region 값 정규화에 함께 쓴다.
# 주의: 전남광주통합특별시는 시군구에 따라 광주/전남으로 나뉘므로 고정 매핑하지 않는다.
REGION_ALIASES: dict[str, str] = {
    "서울특별시": "서울", "서울시": "서울", "서울": "서울",
    "부산광역시": "부산", "부산시": "부산", "부산": "부산",
    "대구광역시": "대구", "대구시": "대구", "대구": "대구",
    "인천광역시": "인천", "인천시": "인천", "인천": "인천",
    "광주광역시": "광주", "광주시": "광주", "광주": "광주",
    "대전광역시": "대전", "대전시": "대전", "대전": "대전",
    "울산광역시": "울산", "울산시": "울산", "울산": "울산",
    "세종특별자치시": "세종", "세종시": "세종", "세종": "세종",
    "경기도": "경기", "경기": "경기",
    "강원특별자치도": "강원", "강원도": "강원", "강원": "강원",
    "충청북도": "충북", "충북": "충북", "충청남도": "충남", "충남": "충남",
    "전북특별자치도": "전북", "전라북도": "전북", "전북": "전북",
    "전라남도": "전남", "전남": "전남",
    "경상북도": "경북", "경북": "경북", "경상남도": "경남", "경남": "경남",
    "제주특별자치도": "제주", "제주도": "제주", "제주": "제주",
}
# 긴 표기를 먼저 시도해 "경기"가 "경기도"의 앞부분만 잘라 가지 않게 한다 (통합특별시 접두사 포함).
_ALL_PREFIX_PATTERNS = sorted(list(REGION_ALIASES.keys()) + INTEGRATED_SPECIAL_PREFIXES, key=len, reverse=True)
_REGION_PREFIX_RE = re.compile(
    "^(" + "|".join(map(re.escape, _ALL_PREFIX_PATTERNS)) + r")(?![가-힣])"
)
# 공식 세종 주소의 광역 토큰 연속 중복("세종특별자치시 세종특별자치시 ...") 정규화
_SEJONG_DUP_RE = re.compile(r"^(세종특별자치시|세종시|세종)\s+(?:세종특별자치시|세종시|세종)(?![가-힣])")
# 지번: "태평로1가 31-20", "인계동 1113-1", "○○리 12", "지하 12", "12번지" 접미 허용
JIBUN_RE = re.compile(r"([가-힣]+\d*(?:동|리|가|읍|면))\s?(?:(지하)\s*)?(\d+(?:-\d+)?)(?:번지)?(?![가-힣\d])")
SIGUNGU_RE = re.compile(r"^([가-힣]{1,6}(?:시|군|구))(?![가-힣])")
SUB_GU_RE = re.compile(r"^([가-힣]{1,5}구)(?![가-힣])")
EUPMYEON_RE = re.compile(r"^([가-힣]{1,6}(?:읍|면))(?![가-힣])")
# 도로명 + 건물번호: "무교로 21", "을지로 지하 12", "을지로3길 30-14" 등 실제 부가구분(지하, 부번) 존중
ROAD_RE = re.compile(r"([가-힣A-Za-z0-9·]+?(?:로|길))\s?(?:(지하)\s*)?(\d+(?:-\d+)?)(?![가-힣\d])")
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

REQUIRED_KEYS = ["region", "institution", "name", "address",
                 "paymentDate", "sourceURL", "recordID"]
MAX_SOURCE_URLS = seoul.MAX_SOURCE_URLS
MIN_DEPARTMENTS = seoul.MIN_DEPARTMENTS


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def months_before(day: dt.date, months: int) -> dt.date:
    return seoul.subtract_months(day, months)


def clean_text(s) -> str:
    if not isinstance(s, str):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s)).strip()


def is_https(url: str) -> bool:
    return bool(re.match(r"^https://[^\s/]+", url or ""))


# --- 주소 파싱 ---------------------------------------------------------------------
def classify_integrated_city(sigungu: str) -> str | None:
    """통합특별시 주소의 시군구로 내부 17개 권역(광주 또는 전남)을 분류한다."""
    if not sigungu:
        return None
    first_gu = sigungu.split()[0]
    if first_gu in GWANGJU_DISTRICTS:
        return "광주"
    if first_gu.endswith("시") or first_gu.endswith("군"):
        return "전남"
    return None


def parse_address(addr: str, region: str) -> tuple[str, str, str] | None:
    """'광역 + 시군구 + 도로명/지번 + 번호' 주소를 (정리 주소, 병합 키, 시군구)로 나눈다.

    광역 표기가 없거나 region 과 다르면 None (지역 충돌 거부).
    전남광주통합특별시는 시군구(동구·서구·남구·북구·광산구)에 따라 광주/전남으로 분류한다.
    세종은 시군구가 없어도 허용하며, 공식 세종 주소의 연속 중복 세종 표기를 제거한다.
    지번 병합 키는 읍면 토큰을 반드시 포함하고, 도로명은 지하/부번 등 실제 부가구분을 존중한다.
    """
    addr = clean_text(addr).strip(" ,./")
    # 공식 세종 주소의 광역 토큰 연속 중복("세종특별자치시 세종특별자치시 ...") 제거
    addr = _SEJONG_DUP_RE.sub(r"\1", addr)

    m = _REGION_PREFIX_RE.match(addr)
    if not m:
        return None
    prefix = m[1]
    rest = addr[m.end():].strip(" ,")

    sigungu_parts: list[str] = []
    sm = SIGUNGU_RE.match(rest)
    if sm:
        sigungu_parts.append(sm[1])
        rest2 = rest[sm.end():].strip(" ,")
        if sm[1].endswith("시"):
            gm = SUB_GU_RE.match(rest2)
            if gm:
                sigungu_parts.append(gm[1])
                rest2 = rest2[gm.end():].strip(" ,")
        rest = rest2
    sigungu = " ".join(sigungu_parts)

    # 주소 접두사로부터 내부 권역 판별
    if prefix in INTEGRATED_SPECIAL_PREFIXES:
        detected_region = classify_integrated_city(sigungu)
        if not detected_region:
            return None
    else:
        detected_region = REGION_ALIASES.get(prefix)
        if not detected_region:
            return None

    # 세종 외 지역은 시군구 필수
    if not sigungu and detected_region != "세종":
        return None

    # region 충돌 검증
    if region in INTEGRATED_SPECIAL_PREFIXES:
        resolved_region = detected_region
    else:
        expected_region = REGION_ALIASES.get(region, region)
        if detected_region != expected_region:
            return None
        resolved_region = detected_region

    # 읍면 파싱 (시군구 바로 뒤)
    eupmyeon: str | None = None
    em = EUPMYEON_RE.match(rest)
    if em:
        eupmyeon = em[1]
        rest_after_em = rest[em.end():].strip(" ,")
    else:
        rest_after_em = rest

    # 도로명 안 띄어쓰기("세종대로 14길", "퇴계로 2가길")를 붙여 같은 도로가 다른 키로 갈리지 않게 한다.
    rest_road = re.sub(r"([가-힣A-Za-z0-9·]+(?:로|길))\s+([가-힣\d]+(?:길))", r"\1\2", rest)
    road = ROAD_RE.search(rest_road)
    if road:
        road_name = road[1]
        is_underground = bool(road[2])
        bldg_no = road[3]
        bldg_part = f"지하{bldg_no}" if is_underground else bldg_no
        locator = f"도로|{seoul.norm_text(road_name)}|{bldg_part}"
    else:
        # 지번 매칭: 읍면이 있는 경우와 없는 경우 모두 지원
        target_jibun_text = rest_after_em if eupmyeon else rest
        jibun = JIBUN_RE.search(target_jibun_text)
        if jibun:
            dong_ri = jibun[1]
            is_under = bool(jibun[2])
            num_part = f"지하{jibun[3]}" if is_under else jibun[3]
            if eupmyeon:
                # 같은 군 내 다른 읍면 동일 리 번지 충돌 방지를 위해 읍면 토큰 반드시 포함
                locator = f"지번|{seoul.norm_text(eupmyeon)}|{seoul.norm_text(dong_ri)}|{num_part}"
            else:
                locator = f"지번|{seoul.norm_text(dong_ri)}|{num_part}"
        elif eupmyeon:
            num_m = re.match(r"^(?:(지하)\s*)?(\d+(?:-\d+)?)(?:번지)?(?![가-힣\d])", rest_after_em)
            if num_m:
                num_part = f"지하{num_m[2]}" if num_m[1] else num_m[2]
                locator = f"지번|{seoul.norm_text(eupmyeon)}|{num_part}"
            else:
                return None
        else:
            return None

    cleaned = " ".join(x for x in (prefix, sigungu, rest) if x)
    return cleaned, f"{resolved_region}|{sigungu}|{locator}", sigungu


def dept_key(institution: str, department: str) -> str:
    """기관+부서 고유 키. 부서는 서울과 같이 마지막 토큰(과·담당관 단위)으로 본다."""
    return f"{institution}|{seoul.dept_key(department)}"


# --- shard 읽기 -------------------------------------------------------------------
def shard_records_path(shard_dir: Path) -> Path | None:
    for name in ("records.jsonl.gz", "records.jsonl"):
        p = shard_dir / name
        if p.is_file():
            return p
    return None


def iter_jsonl(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                yield lineno, None
                continue
            yield lineno, obj if isinstance(obj, dict) else None


def load_side_json(shard_dir: Path, name: str):
    p = shard_dir / name
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        log(f"경고: {p} 를 읽지 못했다 ({e}); 무시한다")
        return None


def collect_https_urls(obj, out: list[str]) -> None:
    """구조가 고정되지 않은 sources.json 에서 HTTPS 문자열만 순서대로 모은다."""
    if isinstance(obj, str):
        if is_https(obj.strip()) and obj.strip() not in out:
            out.append(obj.strip())
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_https_urls(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_https_urls(v, out)


def region_source_urls(sources, region: str) -> list[str]:
    """sources.json 에 지역별 항목이 있으면 그 지역 것만, 없으면 전체 URL 을 돌려준다."""
    if sources is None:
        return []
    if isinstance(sources, dict):
        for key, val in sources.items():
            if REGION_ALIASES.get(str(key).strip()) == region:
                urls: list[str] = []
                collect_https_urls(val, urls)
                return urls
        for container_key in ("regions", "byRegion", "지역"):
            sub = sources.get(container_key)
            if isinstance(sub, dict):
                for key, val in sub.items():
                    if REGION_ALIASES.get(str(key).strip()) == region:
                        urls = []
                        collect_https_urls(val, urls)
                        return urls
            if isinstance(sub, list):
                urls = []
                for item in sub:
                    if isinstance(item, dict) and REGION_ALIASES.get(str(item.get("region", "")).strip()) == region:
                        collect_https_urls(item, urls)
                if urls:
                    return urls
    urls = []
    collect_https_urls(sources, urls)
    return urls


def summarize_side(obj, limit: int = 4000):
    """coverage.json 같은 참고 파일을 원문 그대로 싣되, 지나치게 크면 최상위 키만 남긴다."""
    if obj is None:
        return None
    text = json.dumps(obj, ensure_ascii=False)
    if len(text) <= limit:
        return obj
    if isinstance(obj, dict):
        return {"truncated": True, "keys": sorted(map(str, obj.keys()))}
    return {"truncated": True, "length": len(text)}


# --- 권역 집계 --------------------------------------------------------------------
def aggregate_shard(shard: str, shard_dir: Path, generated_at: dt.datetime,
                    lookback_months: int, fresh_months: int,
                    seen_docs: set[tuple[str, str]]) -> dict:
    """shard 하나를 읽어 검증·병합한다. 파일이 없으면 status=missing."""
    today = generated_at.date()
    fresh_start = months_before(today, fresh_months)
    lookback_start = months_before(today, lookback_months)
    allowed = set(SHARD_REGIONS[shard])
    result = {
        "shard": shard, "label": SHARD_LABELS[shard], "regions": SHARD_REGIONS[shard],
        "status": "missing", "recordsFile": None, "restaurantsByRegion": {},
        "rows": 0, "validRows": 0, "sources": None, "coverage": None,
    }
    rec_path = (shard_dir / "recovered-records.jsonl.gz") if shard == "address-enrichment" else shard_records_path(shard_dir)
    if rec_path is None or not rec_path.exists():
        return result
    raw = rec_path.read_bytes()
    result["recordsFile"] = {
        "path": os.path.relpath(rec_path, REPO_ROOT), "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "modifiedAt": dt.datetime.fromtimestamp(rec_path.stat().st_mtime, dt.timezone.utc)
        .astimezone().isoformat(timespec="seconds"),
    }
    sources = load_side_json(shard_dir, "sources.json")
    coverage = load_side_json(shard_dir, "coverage.json")
    result["sources"] = summarize_side(sources)
    result["coverage"] = summarize_side(coverage)

    rejected: Counter = Counter()
    places: dict[tuple, dict] = {}
    rows = 0
    for lineno, obj in iter_jsonl(rec_path):
        rows += 1
        if obj is None:
            rejected["JSON형식오류"] += 1
            continue
        if any(k not in obj for k in REQUIRED_KEYS):
            rejected["필수키누락"] += 1
            continue
        raw_reg = clean_text(obj["region"])
        target_region = raw_reg if raw_reg in INTEGRATED_SPECIAL_PREFIXES else REGION_ALIASES.get(raw_reg)
        if target_region is None:
            rejected["지역미확인"] += 1
            continue
        if target_region not in INTEGRATED_SPECIAL_PREFIXES and target_region not in allowed:
            rejected["권역외지역"] += 1
            continue
        if target_region in INTEGRATED_SPECIAL_PREFIXES and not ("광주" in allowed or "전남" in allowed):
            rejected["권역외지역"] += 1
            continue
        institution = clean_text(obj["institution"])
        department = clean_text(obj.get("department", ""))
        name = clean_text(obj.get("canonicalName") or obj["name"]).strip(" -–,./")
        source_url = clean_text(obj["sourceURL"])
        record_id = clean_text(obj["recordID"])
        if not institution:
            rejected["기관누락"] += 1
            continue
        if len(name) < 2 or re.fullmatch(r"[\d\W_]+", name):
            rejected["상호누락"] += 1
            continue
        lname = name.lower()
        if any(k in lname for k in seoul.NON_RESTAURANT_NAME_KEYWORDS) or seoul.HOTEL_SELF_RE.search(name):
            rejected["비음식점명"] += 1
            continue
        if not is_https(source_url):
            rejected["원문URL비HTTPS"] += 1
            continue
        if not record_id:
            rejected["recordID누락"] += 1
            continue
        dm = DATE_RE.match(clean_text(obj["paymentDate"]))
        try:
            pay_date = dt.date(int(dm[1]), int(dm[2]), int(dm[3])) if dm else None
        except ValueError:
            pay_date = None
        if pay_date is None:
            rejected["날짜형식오류"] += 1
            continue
        if pay_date > today:
            rejected["미래날짜"] += 1
            continue
        if pay_date < lookback_start:
            rejected["오래된날짜"] += 1
            continue
        parsed = parse_address(obj["address"], target_region)
        if parsed is None:
            addr_text = clean_text(obj["address"])
            addr_text = _SEJONG_DUP_RE.sub(r"\1", addr_text)
            pm = _REGION_PREFIX_RE.match(addr_text)
            if not addr_text:
                rejected["주소누락"] += 1
            elif not pm:
                rejected["주소광역누락"] += 1
            else:
                pm_p = pm[1]
                if pm_p in INTEGRATED_SPECIAL_PREFIXES:
                    rest_c = addr_text[pm.end():].strip(" ,")
                    sm_c = SIGUNGU_RE.match(rest_c)
                    detected = classify_integrated_city(sm_c[1]) if sm_c else None
                else:
                    detected = REGION_ALIASES.get(pm_p)
                target_norm = target_region if target_region in INTEGRATED_SPECIAL_PREFIXES else REGION_ALIASES.get(target_region, target_region)
                if detected and target_norm not in INTEGRATED_SPECIAL_PREFIXES and detected != target_norm:
                    rejected["주소지역충돌"] += 1
                else:
                    rejected["주소시군구·번호미확인"] += 1
            continue
        address, addr_key, sigungu = parsed
        region = addr_key.split("|")[0]
        if region not in allowed:
            rejected["권역외지역"] += 1
            continue
        doc = (source_url, record_id)
        if doc in seen_docs:
            rejected["중복(원문URL+recordID)"] += 1
            continue
        seen_docs.add(doc)

        key = (region, seoul.norm_text(name), addr_key)
        p = places.setdefault(key, {
            "region": region, "names": Counter(), "addrs": Counter(), "sigungu": sigungu,
            "records": [],
        })
        p["names"][name] += 1
        if obj.get("canonicalName"):
            p["names"][clean_text(obj["name"])] += 0
        p["addrs"][address] += 1
        p["records"].append({"date": pay_date, "dept": (dept_key(institution, department) if department else ""),
                             "institution": institution, "url": source_url})

    by_region: dict[str, list[dict]] = defaultdict(list)
    valid_rows = sum(len(p["records"]) for p in places.values())
    place_rejected: Counter = Counter()
    region_stats: dict[str, dict] = {
        r: {"validRows": 0, "institutions": set(), "candidatePlaces": 0,
            "minDate": None, "maxDate": None} for r in SHARD_REGIONS[shard]
    }
    for key, p in places.items():
        st = region_stats[p["region"]]
        st["validRows"] += len(p["records"])
        st["candidatePlaces"] += 1
        for rec in p["records"]:
            st["institutions"].add(rec["institution"])
            st["minDate"] = rec["date"] if st["minDate"] is None else min(st["minDate"], rec["date"])
            st["maxDate"] = rec["date"] if st["maxDate"] is None else max(st["maxDate"], rec["date"])
        depts = {rec["dept"] for rec in p["records"] if rec["dept"]}
        recs = sorted(p["records"], key=lambda x: x["date"], reverse=True)
        if recs[0]["date"] < fresh_start:
            place_rejected["최근결제없음"] += 1
            continue
        seen_urls, urls = set(), []
        for rec in recs:
            if rec["url"] not in seen_urls:
                seen_urls.add(rec["url"])
                urls.append(rec["url"])
            if len(urls) >= MAX_SOURCE_URLS:
                break
        by_region[p["region"]].append({
            "name": p["names"].most_common(1)[0][0],
            "address": max(p["addrs"], key=lambda a: (p["addrs"][a], len(a))),
            "region": p["region"],
            "district": p["sigungu"],
            "departmentCount": len(depts),
            "lastPaymentDate": recs[0]["date"].isoformat(),
            "firstPaymentDate": recs[-1]["date"].isoformat(),
            "recordCount": len(recs),
            "institutions": sorted({rec["institution"] for rec in recs}),
            "departments": sorted(depts),
            "sourceURL": recs[0]["url"],
            "sourceDocumentURLs": urls,
            "nameVariants": sorted(p["names"]),
        })

    for region, items in by_region.items():
        items.sort(key=lambda x: (-x["departmentCount"], -x["recordCount"], x["name"]))

    result.update({
        "status": "collected" if valid_rows else "empty",
        "rows": rows, "validRows": valid_rows,
        "rejectedRows": dict(rejected), "rejectedPlaces": dict(place_rejected),
        "restaurantsByRegion": dict(by_region),
        "regionStats": {
            r: {"validRows": s["validRows"], "institutionCount": len(s["institutions"]),
                "candidatePlaces": s["candidatePlaces"],
                "windowStart": s["minDate"].isoformat() if s["minDate"] else None,
                "windowEnd": s["maxDate"].isoformat() if s["maxDate"] else None}
            for r, s in region_stats.items()
        },
        "regionSourceURLs": {r: region_source_urls(sources, r) for r in SHARD_REGIONS[shard]},
        "freshStart": fresh_start.isoformat(), "lookbackStart": lookback_start.isoformat(),
    })
    return result


def origin_urls(restaurants: list[dict]) -> list[str]:
    """지역 데이터셋 페이지가 없을 때의 대체: 원문 URL 의 공식 사이트 루트(https://host/)."""
    out: list[str] = []
    for r in restaurants:
        m = re.match(r"^(https://[^/\s]+)", r["sourceURL"])
        if m and m[1] + "/" not in out:
            out.append(m[1] + "/")
    return out


def region_coverage_text(region: str, stats: dict, restaurant_count: int, fresh_months: int,
                         lookback_months: int, extra_note: str | None) -> str:
    text = (
        f"{region} 지역 공공기관 {stats['institutionCount']}곳의 업무추진비 집행 공개자료 중 "
        f"{stats['windowStart']}부터 {stats['windowEnd']}까지 결제된 식사 기록 {stats['validRows']}건을 집계했다. "
        f"수집한 기관의 기록만 포함하므로 {region} 지역 모든 기관·모든 식당 자료가 아니다. "
        f"공공기관 식사 이용이 1건 이상 확인되며 최근 {fresh_months}개월 안에 결제 기록이 있는 장소 {restaurant_count}곳만 남겼다"
        f"(기관·부서 수는 최근 {lookback_months}개월 기록으로 센다)."
    )
    if extra_note:
        text += " " + extra_note
    return text


def coverage_note_from(coverage, region: str) -> str | None:
    """coverage.json 에 사람이 읽는 설명이 있으면 지역 것 우선으로 한 문장만 가져온다."""
    if not isinstance(coverage, dict):
        return None
    candidates = []
    for key, val in coverage.items():
        if REGION_ALIASES.get(str(key).strip()) == region and isinstance(val, dict):
            candidates.append(val)
    candidates.append(coverage)
    for c in candidates:
        for k in ("coverageDescription", "description", "note", "notes", "summary"):
            v = c.get(k)
            if isinstance(v, str) and v.strip():
                return clean_text(v)
    return None


# --- 서울 -------------------------------------------------------------------------
def build_seoul(generated_at: dt.datetime, months: int) -> dict:
    """기존 서울 단일 수집기의 축소 원자료로 서울 카탈로그를 그대로 재생성한다."""
    if not seoul.REDUCED_CSV.is_file():
        return {"shard": "seoul", "label": SHARD_LABELS["seoul"], "regions": ["서울"],
                "status": "missing", "recordsFile": None, "restaurantsByRegion": {}}
    rows = seoul.read_reduced_csv(seoul.REDUCED_CSV)
    fetch_info = None
    if seoul.EVIDENCE_JSON.exists():
        fetch_info = json.loads(seoul.EVIDENCE_JSON.read_text(encoding="utf-8")).get("fullCSV")
    catalog, stats = seoul.build_catalog(rows, months, generated_at, fetch_info)
    restaurants = []
    for r in catalog["restaurants"]:
        item = dict(r)
        item["region"] = "서울"
        item["coverageDescription"] = catalog["coverageDescription"]
        item["sourceURLs"] = list(catalog["sourceURLs"])
        restaurants.append(item)
    raw = seoul.REDUCED_CSV.read_bytes()
    institutions = {"서울특별시 본청"} if restaurants else set()
    return {
        "shard": "seoul", "label": SHARD_LABELS["seoul"], "regions": ["서울"],
        "status": "collected" if restaurants else "empty",
        "recordsFile": {"path": str(seoul.REDUCED_CSV.relative_to(REPO_ROOT)), "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(rows)},
        "rows": len(rows), "validRows": stats["windowRows"],
        "restaurantsByRegion": {"서울": restaurants},
        "regionStats": {"서울": {"validRows": stats["windowRows"], "institutionCount": len(institutions),
                                "candidatePlaces": stats["places"],
                                "windowStart": stats["windowStart"], "windowEnd": stats["windowEnd"]}},
        "regionSourceURLs": {"서울": list(catalog["sourceURLs"])},
        "regionCoverage": {"서울": catalog["coverageDescription"]},
        "sourceDataset": catalog["sourceDataset"],
        "aggregation": catalog["aggregation"],
        "rejectedPlaces": stats["rejected"],
    }


# --- 통합 -------------------------------------------------------------------------
def merge(data_dir: Path, generated_at: dt.datetime, seoul_months: int,
          lookback_months: int, fresh_months: int) -> tuple[dict, list[dict]]:
    seen_docs: set[tuple[str, str]] = set()
    shards = [build_seoul(generated_at, seoul_months)]
    for shard in ("capital_central", "east", "southwest"):
        shards.append(aggregate_shard(shard, data_dir / shard, generated_at,
                                      lookback_months, fresh_months, seen_docs))
    by_shard = {s["shard"]: s for s in shards}
    enrichment = aggregate_shard("address-enrichment", data_dir / "address-enrichment", generated_at,
                                 lookback_months, fresh_months, seen_docs)
    if enrichment["status"] != "missing":
        shards.append(enrichment)
        for region, additions in enrichment["restaurantsByRegion"].items():
            target = by_shard[REGION_TO_SHARD[region]]
            items = target.setdefault("restaurantsByRegion", {}).setdefault(region, [])
            index = {}
            for item in items:
                parsed = parse_address(item["address"], region)
                if parsed:
                    for name in item.get("nameVariants", [item["name"]]):
                        index[(seoul.norm_text(name), parsed[1])] = item
            for addition in additions:
                parsed = parse_address(addition["address"], region)
                keys = [(seoul.norm_text(n), parsed[1]) for n in addition["nameVariants"]]
                existing = next((index[k] for k in keys if k in index), None)
                if existing is None:
                    items.append(addition)
                    existing = addition
                else:
                    for field in ("nameVariants", "departments", "institutions", "sourceDocumentURLs"):
                        existing[field] = sorted(set(existing.get(field, [])) | set(addition.get(field, [])))
                    existing["departmentCount"] = len(existing["departments"])
                    existing["recordCount"] += addition["recordCount"]
                    existing["firstPaymentDate"] = min(existing["firstPaymentDate"], addition["firstPaymentDate"])
                    if addition["lastPaymentDate"] > existing["lastPaymentDate"]:
                        existing["lastPaymentDate"] = addition["lastPaymentDate"]
                        existing["sourceURL"] = addition["sourceURL"]
                for key in keys:
                    index[key] = existing
            stats = target["regionStats"][region]
            stats["validRows"] += enrichment["regionStats"][region]["validRows"]
            stats["candidatePlaces"] = max(stats["candidatePlaces"], len(items))

    regions_meta: list[dict] = []
    restaurants: list[dict] = []
    all_source_urls: list[str] = []
    for region in REGIONS_17:
        shard = by_shard[REGION_TO_SHARD[region]]
        items = shard.get("restaurantsByRegion", {}).get(region, [])
        stats = shard.get("regionStats", {}).get(region)
        entry = {"region": region, "shard": shard["shard"], "shardLabel": shard["label"]}
        if shard["status"] == "missing":
            entry.update({"status": "missing", "restaurantCount": 0, "institutionCount": 0,
                          "validRecordCount": 0, "windowStart": None, "windowEnd": None,
                          "sourceURLs": [], "coverageDescription": None,
                          "note": f"{shard['label']} 권역 shard 파일이 아직 없어 미확보 상태다."})
        elif not stats or not stats["validRows"]:
            entry.update({"status": "empty", "restaurantCount": 0, "institutionCount": 0,
                          "validRecordCount": 0, "windowStart": None, "windowEnd": None,
                          "sourceURLs": [], "coverageDescription": None,
                          "note": f"{shard['label']} 권역 shard 에 {region} 지역의 유효 기록이 없다."})
        else:
            urls = shard.get("regionSourceURLs", {}).get(region) or []
            derived = False
            if not urls:
                urls = origin_urls(items)
                derived = bool(urls)
            cov = f"{region} 공공기관의 최근 {fresh_months}개월 식사 이용 기록을 수집했습니다. 이용 부서 수 제한 없이 1건 이상인 식당을 포함하며, 주소가 빠진 일부 기록은 공식 사업장 자료로 보완했습니다."
            for item in items:
                item["coverageDescription"] = cov
                # 지역 전체 문서 수천 개를 식당마다 복제하지 않는다.
                item["sourceURLs"] = list(dict.fromkeys(item.get("sourceDocumentURLs", [item["sourceURL"]])))[:3]
                if enrichment["status"] != "missing":
                    item["sourceURLs"] = item["sourceURLs"][:2] + ["https://www.data.go.kr/data/15083033/fileData.do"]
            entry.update({"status": "collected" if items else "no-eligible-restaurant",
                          "restaurantCount": len(items), "institutionCount": stats["institutionCount"],
                          "validRecordCount": stats["validRows"], "candidatePlaces": stats["candidatePlaces"],
                          "windowStart": stats["windowStart"], "windowEnd": stats["windowEnd"],
                          "sourceURLs": urls, "sourceURLsDerivedFromOrigin": derived,
                          "coverageDescription": cov})
            if not items:
                entry["note"] = f"{region} 지역에 최근 {fresh_months}개월 식사 이용 기록과 주소가 확인된 식당이 없다."
        regions_meta.append(entry)
        restaurants.extend(items)
        for u in entry["sourceURLs"]:
            if u not in all_source_urls:
                all_source_urls.append(u)

    collected = [e["region"] for e in regions_meta if e["status"] == "collected"]
    partial = [e["region"] for e in regions_meta if e["status"] == "no-eligible-restaurant"]
    empty = [e["region"] for e in regions_meta if e["status"] == "empty"]
    missing = [e["region"] for e in regions_meta if e["status"] == "missing"]
    total_restaurants = len(restaurants)
    national_text = (
        f"전국 내부 17개 권역 중 식당 기록이 확보된 권역은 {len(collected)}곳({', '.join(collected) or '없음'})이며 "
        f"식당 {total_restaurants}곳을 담았다. "
    )
    if partial:
        national_text += f"유효 기록은 있으나 조건을 만족한 식당이 없는 권역: {', '.join(partial)}. "
    if empty:
        national_text += f"수집 파일은 있으나 유효 기록이 없는 권역: {', '.join(empty)}. "
    if missing:
        national_text += f"아직 수집 자료가 없는 권역: {', '.join(missing)}. "
    national_text += ("각 식당의 실제 수집 범위는 항목별 coverageDescription 을 따르며, "
                      "확보된 지역도 수집한 기관의 기록만 포함하므로 전국 완전 수집이 아니다.")

    catalog = {
        "schemaVersion": 1,
        "generatedAt": generated_at.isoformat(timespec="seconds"),
        "coverageDescription": national_text,
        "sourceURLs": all_source_urls,
        "nationalCoverage": {
            "regionCount": len(REGIONS_17),
            "collectedRegions": collected,
            "noEligibleRestaurantRegions": partial,
            "emptyRegions": empty,
            "missingRegions": missing,
            "allRegionsCollected": not (missing or empty),
            "restaurantCount": total_restaurants,
            "institutionCount": sum(e["institutionCount"] for e in regions_meta),
            "rules": {"minDepartments": MIN_DEPARTMENTS, "freshMonths": fresh_months,
                      "lookbackMonths": lookback_months, "seoulWindowMonths": seoul_months,
                      "maxSourceDocumentURLs": MAX_SOURCE_URLS,
                      "mergeKey": "내부 권역 + 정규화 상호 + 시군구 + 도로명(지하·부번 존중)/지번(읍면·동리·번호) + 건물번호(하이픈 보존)",
                      "departmentKey": "기관 + 부서 마지막 토큰", "dedupKey": "sourceURL + recordID"},
        },
        "regions": regions_meta,
        "shards": [
            {k: v for k, v in s.items() if k not in ("restaurantsByRegion", "regionCoverage")}
            for s in shards
        ],
        "restaurants": restaurants,
    }
    return catalog, regions_meta


def report_markdown(catalog: dict) -> str:
    lines = [
        f"# 공공기관 식당 기록 통합 현황 ({catalog['generatedAt']})", "",
        f"{catalog['coverageDescription']}", "",
        "| 지역 | 권역 | 상태 | 식당 | 기관 | 유효 기록 | 기간 | 비고 |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    status_ko = {"collected": "확보", "missing": "미확보", "empty": "유효 기록 없음",
                 "no-eligible-restaurant": "조건 충족 식당 없음"}
    for e in catalog["regions"]:
        period = f"{e['windowStart']} ~ {e['windowEnd']}" if e.get("windowStart") else "-"
        lines.append(f"| {e['region']} | {e['shardLabel']} | {status_ko[e['status']]} | "
                     f"{e['restaurantCount']} | {e['institutionCount']} | {e['validRecordCount']} | "
                     f"{period} | {e.get('note', '')} |")
    lines.append("")
    for s in catalog["shards"]:
        if s["status"] == "missing":
            continue
        rej_rows = s.get("rejectedRows") or {}
        rej_places = s.get("rejectedPlaces") or {}
        lines.append(f"- {s['label']} 권역: 행 {s.get('rows', 0)}, 유효 행 {s.get('validRows', 0)}, "
                     f"행 제외 {json.dumps(rej_rows, ensure_ascii=False)}, "
                     f"장소 제외 {json.dumps(rej_places, ensure_ascii=False)}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR, help=f"권역 shard 폴더의 상위 (기본 {DATA_DIR})")
    ap.add_argument("--output", type=Path, default=OUTPUT_JSON, help=f"카탈로그 출력 경로 (기본 {OUTPUT_JSON})")
    ap.add_argument("--report", type=Path, help="지역별 현황 Markdown 경로 (미지정 시 stdout)")
    ap.add_argument("--seoul-months", type=int, default=18, help="서울 집계 개월 수 (기본 18)")
    ap.add_argument("--lookback-months", type=int, default=18, help="권역 기록을 인정하는 개월 수 (기본 18)")
    ap.add_argument("--fresh-months", type=int, default=18, help="최근 결제가 있어야 하는 개월 수 (기본 18)")
    ap.add_argument("--strict", action="store_true", help="미확보·유효 기록 없는 지역이 있으면 종료 코드 3")
    args = ap.parse_args()

    generated_at = dt.datetime.now(dt.timezone.utc).astimezone()
    catalog, regions_meta = merge(args.data_dir, generated_at, args.seoul_months,
                                  args.lookback_months, args.fresh_months)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"카탈로그 저장: {args.output} (식당 {len(catalog['restaurants'])}곳)")

    report = report_markdown(catalog)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
        log(f"현황 보고 저장: {args.report}")
    else:
        print(report)

    nc = catalog["nationalCoverage"]
    if nc["missingRegions"] or nc["emptyRegions"]:
        log(f"주의: 미확보 {nc['missingRegions']} / 유효 기록 없음 {nc['emptyRegions']} — 전국 완료가 아니다.")
        if args.strict:
            return 3
    if not catalog["restaurants"]:
        log("경고: 조건을 만족하는 식당이 없습니다.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
