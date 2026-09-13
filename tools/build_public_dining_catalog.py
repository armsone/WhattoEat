#!/usr/bin/env python3
"""서울시 본청 업무추진비 공개자료로 WhattoEat 내장 식당 카탈로그를 생성한다.

원자료: 서울 열린데이터광장 OA-22156 "서울시 본청 업무추진비 목록" (공공누리 1유형)
  - 데이터셋 페이지: https://data.seoul.go.kr/dataList/OA-22156/S/1/datasetView.do
  - 전체 CSV: 데이터셋 페이지의 "내려받기(CSV)" 버튼이 호출하는
    https://datafile.seoul.go.kr/bigfile/iot/sheet/csv/download.do (POST, CP949)
  - 문서 원문: 각 행의 "문서url" (https://opengov.seoul.go.kr/expense/{문서고유id})

사용법:
  python3 tools/build_public_dining_catalog.py                 # 원격 CSV 다운로드 후 생성
  python3 tools/build_public_dining_catalog.py --full-csv X    # 이미 받은 전체 CSV(CP949) 사용
  python3 tools/build_public_dining_catalog.py --from-reduced  # data/public-dining 축소 원자료로 재생성

표준 라이브러리만 사용한다. 개인명(작성자)·전화번호 열은 축소 원자료와 카탈로그에 기록하지 않는다.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import re
import statistics
import sys
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "public-dining"
REDUCED_CSV = DATA_DIR / "seoul-oa22156-window.csv.gz"
EVIDENCE_JSON = DATA_DIR / "source-evidence.json"
OUTPUT_JSON = REPO_ROOT / "WhattoEat" / "PublicDiningCatalog.json"

DATASET_PAGE = "https://data.seoul.go.kr/dataList/OA-22156/S/1/datasetView.do"
DATASET_API_DOC = "https://data.seoul.go.kr/dataList/openApiView.do?infId=OA-22156&srvType=A&serviceKind=1"
DATAGOKR_PAGE = "https://www.data.go.kr/data/15124812/fileData.do"
CSV_DOWNLOAD_URL = "https://datafile.seoul.go.kr/bigfile/iot/sheet/csv/download.do"
CSV_FORM = {
    "srvType": "S",
    "infId": "OA-22156",
    "serviceKind": "1",
    "pageNo": "1",
    "gridTotalCnt": "",
    "ssUserId": "SAMPLE_VIEW",
    "strWhere": "",
    "strOrderby": "EXEC_YR DESC",
    "filterCol": "필터선택",
    "txtFilter": "",
}

# 원자료 열 이름 (CSV 헤더 기준)
COL_NID = "문서고유id"
COL_DEPT = "전체부서명"
COL_DT = "집행일시"
COL_LOC = "집행장소"
COL_PURPOSE = "집행목적"
COL_TARGET = "집행대상"
COL_PAY = "결제방법"
COL_AMOUNT = "집행금액"
COL_BIMOK = "비목"
COL_URL = "문서url"
COL_REG = "등록일"
COL_YR = "해당년도"
COL_MON = "해당월"
# 축소 원자료에 보존하는 열 (작성자·전화번호 제외)
KEEP_COLS = [COL_NID, COL_DEPT, COL_YR, COL_MON, COL_DT, COL_LOC, COL_PURPOSE,
             COL_AMOUNT, COL_URL]

# --- 판정 규칙 ---------------------------------------------------------------
# 식사 성격 집행목적 키워드 (하나 이상 포함)
MEAL_KEYWORDS = ["간담회", "식사", "오찬", "만찬", "석식", "중식", "조찬", "회식",
                 "급식", "회의", "격려", "접대", "초청"]
# 구매·물품·경조 등 비식사 집행목적 키워드 (하나라도 포함되면 식사 기록으로 보지 않음)
NON_MEAL_KEYWORDS = ["구입", "구매", "물품", "상품권", "선물", "기념품", "화환", "조화",
                     "경조", "부의", "축의", "배송", "커피", "원두", "도시락", "케이크",
                     "떡", "간식", "음료", "생수", "다과", "비품", "용품"]
# 장소명에 포함되면 비음식점으로 간주하는 키워드
NON_RESTAURANT_NAME_KEYWORDS = [
    "마트", "쿠팡", "편의점", "cu ", "gs25", "세븐일레븐", "이마트", "홈플러스", "롯데마트",
    "백화점", "온라인", "배달", "문구", "꽃", "화원", "플라워", "떡집", "제과", "베이커리",
    "과자점", "빵", "바게트", "바게뜨", "뚜레쥬르", "던킨", "크리스피크림", "와플", "디저트",
    "카페", "커피", "스타벅스", "투썸", "파스쿠찌", "이디야", "할리스", "폴바셋", "커피빈",
    "메가엠지씨", "컴포즈", "바나프레소", "블루보틀", "테라로사", "엔제리너스", "탐앤탐스",
    "빽다방", "더벤티", "매머드", "공차", "설빙", "배스킨", "팀홀튼", "티하우스", "티룸",
    "아티제", "가배", "스패뉴", "매점", "카페테리아", "구내식당", "푸드테크", "푸드서비스", "푸드시스템", "푸드빌",
    "에프앤비", "f&b", "케이터링", "다이소", "약국", "병원", "택시", "주유", "주차",
    "상품권", "페이", "주식회사", "㈜", "(주)", "유한회사", "(유)", "농협", "하나로", "슈퍼",
    "정육점", "청과", "도매", "유통", "물산", "상사", "네이버", "11번가", "지마켓", "옥션",
    "인터파크", "티몬", "위메프", "컬리", "배민", "요기요", "우체국", "은행", "서울시청",
    "서울특별시청", "센터", "협회", "재단", "학교", "대학", "티켓", "예매", "공연", "극장",
    "영화", "cgv", "메가박스",
]
# 호텔 자체(호텔 이름으로 끝나는 장소)는 제외하되 "○○호텔점" 같은 지점 표기는 허용
HOTEL_SELF_RE = re.compile(r"호텔\s*$")
# 주소 앞뒤에 붙는 사업자 대표자명("홍길동, 서울 중구 …", "… 세종대로 30/홍길동")과 "외 1" 접미
PERSON_PREFIX_RE = re.compile(r"^[가-힣]{2,4}\s*[,/]\s*")
PERSON_SUFFIX_RE = re.compile(r"\s*[,/]\s*[가-힣]{2,4}\s*$")
ETC_SUFFIX_RE = re.compile(r"\s*외\s*\d+\s*(?:곳|명|건|개소)?\s*$")
# 도로명+건물번호: "무교로 21", "을지로3길 30-14", "세종대로20길 23" 등. "태평로1가 31-20"(지번)은 제외.
ROAD_RE = re.compile(r"([가-힣A-Za-z0-9·]+?(?:로|길))\s?(\d+(?:-\d+)?)(?![가-힣\d])")
DISTRICT_RE = re.compile(r"([가-힣]{1,4}구)(?![가-힣])")
SEOUL_DISTRICTS = {
    "종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구",
    "도봉구", "노원구", "은평구", "서대문구", "마포구", "양천구", "강서구", "구로구", "금천구",
    "영등포구", "동작구", "관악구", "서초구", "강남구", "송파구", "강동구",
}
# 마지막 괄호 묶음을 주소로 본다. 장소명 안의 괄호("열빈 (광화문점)")는 허용한다.
LOC_RE = re.compile(r"^\s*(?P<name>.+?)\s*[(（](?P<addr>[^()（）]*)[)）]\s*$")

MIN_DEPARTMENTS = 0          # 부서 수는 자격 조건이 아님
MIN_MEAL_SHARE = 0.0         # 실제 식사 이용 1건이면 인정
MAX_SOURCE_URLS = 10         # 식당별 보존하는 원문 링크 수


def subtract_months(day: dt.date, months: int) -> dt.date:
    year, month0 = divmod(day.year * 12 + day.month - 1 - months, 12)
    month = month0 + 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# --- 원자료 획득 --------------------------------------------------------------
def download_full_csv(dest: Path) -> dict:
    """서울 열린데이터광장 시트 CSV 전량을 내려받아 dest에 저장하고 증거 메타를 반환한다."""
    body = urllib.parse.urlencode(CSV_FORM).encode("utf-8")
    req = urllib.request.Request(
        CSV_DOWNLOAD_URL, data=body, method="POST",
        headers={
            "User-Agent": "Mozilla/5.0 (WhattoEat public dining catalog builder)",
            "Referer": DATASET_PAGE,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    started = dt.datetime.now(dt.timezone.utc).astimezone()
    with urllib.request.urlopen(req, timeout=600) as resp:
        headers = {k: v for k, v in resp.headers.items()
                   if k.lower() in ("content-type", "content-disposition", "date", "server")}
        raw = resp.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    return {
        "downloadURL": CSV_DOWNLOAD_URL,
        "formFields": CSV_FORM,
        "fetchedAt": started.isoformat(timespec="seconds"),
        "responseHeaders": headers,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def read_full_csv(path: Path) -> list[dict]:
    raw = path.read_bytes()
    text = raw.decode("cp949", errors="replace")
    # 서버 CSV는 첫 헤더 앞에 큰따옴표가 하나 더 붙어 있다 ("" 문서고유id ...)
    if text.startswith('""'):
        text = text[1:]
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows or COL_LOC not in rows[0]:
        raise SystemExit(f"CSV 헤더가 예상과 다릅니다: {reader.fieldnames}")
    return rows


def read_reduced_csv(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_reduced_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=KEEP_COLS)
        w.writeheader()
        for r in rows:
            reduced = {c: r.get(c, "") for c in KEEP_COLS}
            parsed = parse_location(r.get(COL_LOC, ""))
            reduced[COL_LOC] = f"{parsed[0]} ({parsed[1]})" if parsed else ""
            reduced[COL_PURPOSE] = "식사" if is_meal_purpose(r.get(COL_PURPOSE, "")) else ""
            w.writerow(reduced)


def write_unresolved(rows: list[dict]) -> None:
    """주소를 읽지 못한 식사 기록도 상호 검색용으로 보존한다. 개인명·목적 원문은 제외한다."""
    path = DATA_DIR / "seoul-unresolved-address-records.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for index, row in enumerate(rows):
            location = row.get(COL_LOC, "").strip()
            if parse_location(location) or not is_meal_purpose(row.get(COL_PURPOSE, "")):
                continue
            name = re.split(r"[(（]", location, maxsplit=1)[0].strip()
            name = ETC_SUFFIX_RE.sub("", name)
            if len(name) < 2 or len(name) > 70 or any(k in name.lower() for k in NON_RESTAURANT_NAME_KEYWORDS):
                continue
            # 주소 힌트에서 대표자명 등이 유출되지 않도록 행정구역만 보존한다.
            district = next((d for d in SEOUL_DISTRICTS if d in location), "")
            day = exec_date(row)
            if not day:
                continue
            record = dict(region="서울", institution="서울특별시", department=row.get(COL_DEPT, ""),
                          name=name, addressHint="서울특별시 " + district, paymentDate=str(day),
                          sourceURL=row.get(COL_URL, ""), recordID=f"{row.get(COL_NID, '')}:{index}", isMeal=True)
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


# --- 판정 -------------------------------------------------------------------
def exec_date(row: dict) -> dt.date | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", row.get(COL_DT, ""))
    if not m:
        return None
    try:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def is_meal_purpose(purpose: str) -> bool:
    p = purpose.replace(" ", "")
    if any(k in p for k in NON_MEAL_KEYWORDS):
        return False
    return any(k in p for k in MEAL_KEYWORDS)


def dept_key(dept: str) -> str:
    """부서 고유 키. '경제실 경제정책과'와 '경제정책과'처럼 상위 조직 표기 유무만 다른 경우를 한 부서로 본다."""
    return re.sub(r"\s+", " ", dept).strip().split(" ")[-1]


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[\s\W_]+", "", s)


def parse_location(loc: str) -> tuple[str, str, str, str] | None:
    """'식당명 (주소)' 형식을 (원문명, 주소, 도로명키, 구)로 나눈다. 조건 미충족 시 None."""
    loc = unicodedata.normalize("NFKC", loc).strip()
    loc = ETC_SUFFIX_RE.sub("", loc)
    m = LOC_RE.match(loc)
    if not m:
        return None
    name = re.sub(r"\s+", " ", m["name"]).strip(" -–,./")
    name = PERSON_PREFIX_RE.sub("", name)
    addr = re.sub(r"\s+", " ", m["addr"]).strip(" ,./")
    # 주소 문자열에서 개인명(대표자) 앞뒤 토큰 제거. 지역·도로명 토큰은 2~4자 한글 뒤에 구분자가 오지 않는다.
    addr = PERSON_PREFIX_RE.sub("", addr)
    addr = PERSON_SUFFIX_RE.sub("", addr)
    if len(name) < 2 or len(addr) < 4:
        return None
    lname = name.lower()
    if any(k in lname for k in NON_RESTAURANT_NAME_KEYWORDS):
        return None
    if HOTEL_SELF_RE.search(name):
        return None
    if re.fullmatch(r"[\d\W_]+", name):
        return None
    # "세종대로 14길 22" 처럼 도로명 안의 띄어쓰기를 붙여 같은 도로가 다른 키로 갈리지 않게 한다.
    addr = re.sub(r"(로)\s+(\d+(?:번)?길)", r"\1\2", addr)
    road = ROAD_RE.search(addr)
    if not road:
        return None
    # 주소는 도로명·지역 토큰 앞의 잡음을 버리고 첫 지역/도로 토큰부터 시작한다.
    start = re.search(r"(서울특별시|서울시|서울|경기도|인천|[가-힣]{1,4}(?:시|구|군)\s|[가-힣A-Za-z0-9·]+(?:로|길)\s?\d)", addr)
    if start and start.start() > 0:
        addr = addr[start.start():].strip(" ,./")
    if not ROAD_RE.search(addr):
        return None
    district_m = DISTRICT_RE.search(addr)
    district = district_m[1] if district_m else ""
    if district not in SEOUL_DISTRICTS:
        return None
    # 현재 명단은 서울만 다룬다. 다른 광역 지역이 명시된 주소는 동명 도로라도 합치지 않는다.
    prefix = addr[:district_m.start()].strip()
    if prefix not in ("", "서울", "서울시", "서울특별시"):
        return None
    # 지역과 도로명, 건물번호를 분리하고 12-3과 123을 서로 다른 주소로 보존한다.
    road_key = f"서울|{district}|{norm_text(road[1])}|{road[2]}"
    return name, addr, road_key, district


def address_rank(addr: str) -> tuple:
    """대표 주소 선택 기준: 서울 소속 구 포함 > 시/도 표기 포함 > 긴 주소."""
    has_district = any(d in addr for d in SEOUL_DISTRICTS)
    has_city = "서울" in addr
    return (has_district, has_city, len(addr))


def build_catalog(rows: list[dict], months: int, generated_at: dt.datetime,
                  fetch_info: dict | None) -> tuple[dict, dict]:
    dated = [(exec_date(r), r) for r in rows]
    dated = [(d, r) for d, r in dated if d is not None]
    if not dated:
        raise SystemExit("집행일시를 읽을 수 있는 행이 없습니다.")
    window_end = generated_at.date()
    window_start = subtract_months(window_end, months)

    # 월별 부서 커버리지로 완결 기준월 산정: 마지막 달 이전 달들의 중앙값 대비 95% 이상이면 완결로 본다
    dept_by_month: dict[str, set] = defaultdict(set)
    for d, r in dated:
        dept_by_month[d.strftime("%Y-%m")].add(dept_key(r[COL_DEPT]))
    months_sorted = sorted(dept_by_month)
    coverage = {m: len(dept_by_month[m]) for m in months_sorted}
    # 부서는 집행 다음 달에 공개하므로, 생성일 기준 다음 달이 모두 지난 달만 완결 후보로 본다.
    today = generated_at.date()
    prev_month_first = dt.date(today.year, today.month, 1)
    complete_month = None
    prior = [coverage[m] for m in months_sorted[-7:-1]] if len(months_sorted) >= 7 else []
    base = statistics.median(prior) if prior else 0
    for m in reversed(months_sorted):
        y_, m_ = int(m[:4]), int(m[5:])
        next_first = dt.date(y_ + (m_ // 12), m_ % 12 + 1, 1)
        deadline_passed = next_first < prev_month_first  # M+1 이 완전히 지났는가
        if deadline_passed and coverage[m] >= base * 0.95:
            complete_month = m
            break
    if complete_month is None:
        complete_month = months_sorted[-1]

    window_rows = [(d, r) for d, r in dated if window_start <= d <= window_end]

    # 장소 키별 집계
    places: dict[tuple, dict] = {}
    parse_fail = 0
    for d, r in window_rows:
        parsed = parse_location(r.get(COL_LOC, ""))
        if parsed is None:
            parse_fail += 1
            continue
        name, addr, road_key, district = parsed
        key = (norm_text(name), road_key)
        p = places.setdefault(key, {
            "names": Counter(), "addrs": Counter(), "districts": Counter(),
            "records": [], "meal": 0, "total": 0,
        })
        p["names"][name] += 1
        p["addrs"][addr] += 1
        if district:
            p["districts"][district] += 1
        meal = is_meal_purpose(r.get(COL_PURPOSE, ""))
        p["total"] += 1
        if meal:
            p["meal"] += 1
            amount = re.sub(r"[^\d]", "", r.get(COL_AMOUNT, "") or "")
            p["records"].append({
                "date": d, "dept": dept_key(r[COL_DEPT]), "nid": r[COL_NID],
                "url": r.get(COL_URL, ""), "amount": int(amount) if amount else 0,
            })

    restaurants = []
    rejected = Counter()
    for key, p in places.items():
        depts = {rec["dept"] for rec in p["records"] if rec["dept"]}
        if not p["records"]:
            rejected["식사기록없음"] += 1
            continue
        best_addr = max(p["addrs"], key=lambda a: (address_rank(a), p["addrs"][a]))
        if not DISTRICT_RE.search(best_addr):
            rejected["구미확인"] += 1
            continue
        recs = sorted(p["records"], key=lambda x: x["date"], reverse=True)
        name = p["names"].most_common(1)[0][0]
        seen, urls = set(), []
        for rec in recs:
            if rec["url"] and rec["url"] not in seen:
                seen.add(rec["url"])
                urls.append(rec["url"])
            if len(urls) >= MAX_SOURCE_URLS:
                break
        amounts = [rec["amount"] for rec in recs if rec["amount"] > 0]
        restaurants.append({
            "name": name,
            "address": best_addr,
            "district": p["districts"].most_common(1)[0][0] if p["districts"] else "",
            "departmentCount": len(depts),
            "lastPaymentDate": recs[0]["date"].isoformat(),
            "firstPaymentDate": recs[-1]["date"].isoformat(),
            "recordCount": len(recs),
            "medianAmountKRW": int(statistics.median(amounts)) if amounts else None,
            "departments": sorted(depts),
            "sourceURL": recs[0]["url"] or DATASET_PAGE,
            "sourceDocumentURLs": urls,
            "nameVariants": sorted(p["names"]),
        })

    restaurants.sort(key=lambda x: (-x["departmentCount"], -x["recordCount"], x["name"]))

    complete_label = f"{complete_month[:4]}년 {int(complete_month[5:])}월"
    coverage_desc = (
        "서울특별시 본청(시장단·실·국·본부 부서)의 업무추진비 집행 공개자료 중 "
        f"{window_start.isoformat()}부터 {window_end.isoformat()}까지 집행된 기록만 집계했다. "
        f"부서별 공개 현황으로 추정한 기준월은 {complete_label}이며, 모든 부서의 공개 완료를 보증하지 않는다. "
        "서울시 본청 부서가 결제한 장소만 포함하므로 서울 자치구·타 지역 기관·전국 자료가 아니며, "
        "시청(중구·종로구) 주변 식당이 많다. 공공기관의 식사 이용 기록이 1건 이상 확인된 장소를 포함하며 "
        "부서 수 조건은 적용하지 않는다."
    )
    catalog = {
        "schemaVersion": 1,
        "generatedAt": generated_at.isoformat(timespec="seconds"),
        "coverageDescription": coverage_desc,
        "sourceURLs": [DATASET_PAGE, DATAGOKR_PAGE],
        "sourceDataset": {
            "name": "서울시 본청 업무추진비 목록 (OA-22156)",
            "publisher": "서울특별시 디지털도시국 정보시스템과 (원본시스템: 정보소통광장)",
            "license": "공공누리 제1유형 (출처표시)",
            "attribution": "서울특별시 열린데이터광장, 서울시 본청 업무추진비 목록",
            "fetchedAt": (fetch_info or {}).get("fetchedAt"),
            "sourceSHA256": (fetch_info or {}).get("sha256"),
            "sourceRowCount": (fetch_info or {}).get("sourceRowCount"),
        },
        "aggregation": {
            "windowStart": window_start.isoformat(),
            "windowEnd": window_end.isoformat(),
            "windowMonths": months,
            "estimatedCompleteThroughMonth": complete_month,
            "departmentCoverageByMonth": {m: coverage[m] for m in months_sorted[-(months + 2):]},
            "windowRecordCount": len(window_rows),
            "minDepartments": MIN_DEPARTMENTS,
            "minMealShare": MIN_MEAL_SHARE,
            "candidatePlaces": len(places),
            "rejected": dict(rejected),
            "unparsedLocations": parse_fail,
        },
        "restaurants": restaurants,
    }
    stats = {
        "windowRows": len(window_rows), "places": len(places), "restaurants": len(restaurants),
        "rejected": dict(rejected), "parseFail": parse_fail, "completeMonth": complete_month,
        "windowStart": window_start.isoformat(), "windowEnd": window_end.isoformat(),
    }
    return catalog, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full-csv", type=Path, help="이미 내려받은 전체 CSV(CP949) 경로")
    ap.add_argument("--from-reduced", action="store_true", help=f"{REDUCED_CSV} 축소 원자료로 재생성")
    ap.add_argument("--months", type=int, default=18, help="오늘 기준 집계 개월 수 (기본 18)")
    ap.add_argument("--keep-months", type=int, default=18, help="축소 원자료 보존 개월 수 (기본 18)")
    ap.add_argument("--output", type=Path, default=OUTPUT_JSON)
    args = ap.parse_args()

    generated_at = dt.datetime.now(dt.timezone.utc).astimezone()
    fetch_info: dict | None = None

    if args.from_reduced:
        rows = read_reduced_csv(REDUCED_CSV)
        if EVIDENCE_JSON.exists():
            fetch_info = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8")).get("fullCSV")
        log(f"축소 원자료 {REDUCED_CSV.name}: {len(rows)}행")
    else:
        if args.full_csv:
            full_path = args.full_csv
            raw = full_path.read_bytes()
            fetch_info = {"downloadURL": CSV_DOWNLOAD_URL, "localFile": str(full_path),
                          "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                          "fetchedAt": dt.datetime.fromtimestamp(full_path.stat().st_mtime,
                                                                 dt.timezone.utc).astimezone().isoformat(timespec="seconds")}
        else:
            full_path = Path("/tmp") / "seoul-oa22156-full.csv"
            log(f"전체 CSV 다운로드: {CSV_DOWNLOAD_URL}")
            fetch_info = download_full_csv(full_path)
            log(f"저장: {full_path} ({fetch_info['bytes']} bytes)")
        rows_all = read_full_csv(full_path)
        fetch_info["sourceRowCount"] = len(rows_all)
        log(f"전체 행: {len(rows_all)}")
        # 축소 원자료: 최근 keep_months 달력월, 개인명·전화번호 제외
        dated = [(exec_date(r), r) for r in rows_all]
        keep_from = subtract_months(generated_at.date(), max(args.keep_months, args.months))
        rows = [r for d, r in dated if d and keep_from <= d <= generated_at.date()]
        write_reduced_csv(REDUCED_CSV, rows)
        write_unresolved(rows)
        month_counts = Counter(f"{r[COL_YR]}-{int(r[COL_MON]):02d}" for r in rows_all)
        evidence = {
            "dataset": {"id": "OA-22156", "name": "서울시 본청 업무추진비 목록",
                        "page": DATASET_PAGE, "apiDoc": DATASET_API_DOC, "dataGoKr": DATAGOKR_PAGE,
                        "license": "공공누리 제1유형 (출처표시)"},
            "fullCSV": fetch_info,
            "fullCSVRowsByExecMonth": dict(sorted(month_counts.items())),
            "reducedCSV": {"path": str(REDUCED_CSV.relative_to(REPO_ROOT)), "rows": len(rows),
                           "keepFrom": keep_from.isoformat(), "columns": KEEP_COLS,
                           "droppedColumns": ["작성자", "전화번호", "제목", "부서명", "구분(시장실만 사용)",
                                              COL_TARGET, COL_PAY, COL_BIMOK, COL_REG],
                           "transformations": {COL_LOC: "검증된 상호와 주소만 보존, 파싱 제외 항목은 빈 값",
                                               COL_PURPOSE: "식사 여부만 식사/빈 값으로 보존, 원문 문구 제거"}},
        }
        EVIDENCE_JSON.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"축소 원자료 저장: {REDUCED_CSV} ({len(rows)}행), 증거: {EVIDENCE_JSON.name}")

    catalog, stats = build_catalog(rows, args.months, generated_at, fetch_info)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"카탈로그 저장: {args.output}")
    log(json.dumps(stats, ensure_ascii=False))
    if not catalog["restaurants"]:
        log("경고: 조건을 만족하는 식당이 없습니다.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
