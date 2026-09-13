#!/usr/bin/env python3
"""남서부 권역(광주·전북·전남·제주) 공공기관 업무추진비 식당 결제 기록 수집기.

공식 포털·게시판에서 실제 업무추진비 공개 문서(XLSX·PDF)를 내려받아 파싱하고,
개인식별정보(작성자, 전화번호, 참석자, 목적 원문 등)를 배제한 최소 안전 규격으로 정제한다.

주소 보완 정책:
  원문에 온전한 실제 주소가 기재된 경우 이를 우선 검증하여 수용한다.
  원문에 주소가 누락된 경우, 공공데이터포털(data.go.kr) 공식 소상공인진흥공단 상가정보
  CSV(15083033, 2026년 6월 기준)에서 출처 권역 내 유일하게 해소(Unique resolution)되는 식당에
  한해서만 공식 도로명/지번 주소와 상가업소번호(addressSourceRecordID)를 결합한다.
  모호하거나(다수 지점), 불일치하는 경우 엄격히 거부한다.

대상 지역:
  - 제주특별자치도 (도 본청 및 직속기관 업무추진비 XLSX)
  - 전북특별자치도 (도 본청 및 직속기관 업무추진비 PDF/XLSX)
  - 전라남도 (실과소/부서 시책·기관 업무추진비 XLSX)
  - 광주광역시 (광산구청 사전정보공표, 서구청 업무추진비, 전남-광주 공동혁신도시 PDF/XLSX)

산출물:
  - data/public-dining/southwest/records.jsonl.gz (정제된 결제 기록)
  - data/public-dining/southwest/sources.json (공식 출처 URL 및 원본 메타/해시)
  - data/public-dining/southwest/coverage.json (지역별 통계, 기간, 수집 기관, 제약 사항)

사용법:
  # 1) 공식 포털 원격 수집 및 산출물 생성 (기본 실행)
  python3 tools/collect_public_dining_southwest.py

  # 2) 오프라인 출처 매니페스트 재생(Replay): 검증된 출처 매니페스트 및 캐시 파일 기반 재파싱
  python3 tools/collect_public_dining_southwest.py --source-manifest /tmp/whattoeat-southwest-20260913/jeju-fixed/sources.json

  # 3) 주소 디렉토리 지정 및 특정 지역 수집
  python3 tools/collect_public_dining_southwest.py --address-dir /tmp/whattoeat-sbiz-addresses --regions 제주,전남

  # 4) 오프라인 모드: 기존 실제 온라인 수집 산출물로부터 재집계
  python3 tools/collect_public_dining_southwest.py --from-reduced

  # 5) 진단 모드: 캐시된 원문 파일 및 헤더 구조 분석
  python3 tools/collect_public_dining_southwest.py --diagnostics
"""

from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import gzip
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

# Optional third-party packages available in runtime
try:
    import openpyxl
except ImportError:
    openpyxl = None

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import pypdf
except ImportError:
    pypdf = None


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "public-dining" / "southwest"
DEFAULT_CACHE_DIR = Path("/tmp/whattoeat-southwest-20260913")
DEFAULT_ADDRESS_DIR = Path("/tmp/whattoeat-sbiz-addresses")
ADDRESS_SOURCE_URL = "https://www.data.go.kr/data/15083033/fileData.do"

# Document format signatures & headless converter path
SOFFICE_BIN_OVERRIDE = "/Users/armsone/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/soffice"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
PDF_MAGIC = b"%PDF"
ZIP_MAGIC = b"PK\x03\x04"
HWP_OLE_TAG = b"F\x00i\x00l\x00e\x00H\x00e\x00a\x00d\x00e\x00r\x00"
HWP_MAGIC_TAG = b"HWP Document File"
XLS_OLE_WORKBOOK = b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k\x00"
XLS_OLE_BOOK = b"B\x00o\x00o\x00k\x00"

REGIONS = ["광주", "전북", "전남", "제주"]
GWANGJU_DISTRICTS = {"동구", "서구", "남구", "북구", "광산구"}
INTEGRATED_SPECIAL_PREFIXES = ["전남광주통합특별시", "전남광주시", "전남광주"]

REGION_ALIASES: dict[str, str] = {
    "광주광역시": "광주", "광주시": "광주", "광주": "광주",
    "전북특별자치도": "전북", "전라북도": "전북", "전북": "전북",
    "전라남도": "전남", "전남": "전남",
    "제주특별자치도": "제주", "제주도": "제주", "제주": "제주",
}

REGION_CANONICAL_PREFIX: dict[str, str] = {
    "광주": "광주광역시",
    "전북": "전북특별자치도",
    "전남": "전라남도",
    "제주": "제주특별자치도",
}

SIGUNGU_TO_REGION: dict[str, str] = {
    # 광주
    "동구": "광주", "서구": "광주", "남구": "광주", "북구": "광주", "광산구": "광주",
    # 전북
    "전주시": "전북", "군산시": "전북", "익산시": "전북", "정읍시": "전북",
    "남원시": "전북", "김제시": "전북", "완주군": "전북", "진안군": "전북",
    "무주군": "전북", "장수군": "전북", "임실군": "전북", "순창군": "전북",
    "고창군": "전북", "부안군": "전북",
    # 전남
    "목포시": "전남", "여수시": "전남", "순천시": "전남", "나주시": "전남", "광양시": "전남",
    "담양군": "전남", "곡성군": "전남", "구례군": "전남", "고흥군": "전남", "보성군": "전남",
    "화순군": "전남", "장흥군": "전남", "강진군": "전남", "해남군": "전남", "영암군": "전남",
    "무안군": "전남", "함평군": "전남", "영광군": "전남", "장성군": "전남", "완도군": "전남",
    "진도군": "전남", "신안군": "전남",
    # 제주
    "제주시": "제주", "서귀포시": "제주",
}

_ALL_PREFIX_PATTERNS = sorted(list(REGION_ALIASES.keys()) + INTEGRATED_SPECIAL_PREFIXES, key=len, reverse=True)
_REGION_PREFIX_RE = re.compile(
    "^(" + "|".join(map(re.escape, _ALL_PREFIX_PATTERNS)) + r")(?![가-힣])"
)

SIGUNGU_RE = re.compile(r"^([가-힣]{1,6}(?:시|군|구))(?![가-힣])")
SUB_GU_RE = re.compile(r"^([가-힣]{1,5}구)(?![가-힣])")
EUPMYEON_RE = re.compile(r"^([가-힣]{1,6}(?:읍|면))(?![가-힣])")
ROAD_RE = re.compile(r"([가-힣A-Za-z0-9·]+?(?:로|길))\s?(?:(지하)\s*)?(\d+(?:-\d+)?)(?![가-힣\d])")
JIBUN_RE = re.compile(r"([가-힣]+\d*(?:동|리|가|읍|면))\s?(?:(지하)\s*)?(\d+(?:-\d+)?)(?:번지)?(?![가-힣\d])")
DATE_RE = re.compile(r"(?:^|[^\d])(\d{2,4})[-./년\s]+(\d{1,2})[-./월\s]+(\d{1,2})")

PERSON_PREFIX_RE = re.compile(r"^[가-힣]{2,4}\s*[,/]\s*")
PERSON_SUFFIX_RE = re.compile(r"\s*[,/]\s*[가-힣]{2,4}\s*$")
ETC_SUFFIX_RE = re.compile(r"\s*외\s*\d+\s*(?:곳|명|건|개소)?\s*$")
LOC_RE = re.compile(r"^\s*(?P<name>.+)\s*[(（](?P<addr>[^()（）]+)[)）]\s*$")
HOTEL_SELF_RE = re.compile(r"호텔\s*$")

# 식사 성격 목적 키워드 (하나 이상 포함되어야 인정)
MEAL_KEYWORDS = [
    "간담회", "식사", "오찬", "만찬", "석식", "중식", "조찬", "회식",
    "급식", "회의", "격려", "접대", "초청", "협의", "업무협의", "간담",
]

# 비식사성 지출 키워드 (하나라도 포함되면 제외)
NON_MEAL_KEYWORDS = [
    "구입", "구매", "물품", "상품권", "선물", "기념품", "화환", "조화",
    "경조", "부의", "축의", "배송", "커피", "원두", "도시락", "케이크",
    "떡", "간식", "음료", "생수", "다과", "비품", "용품", "수수료", "유류",
    "주유", "차량", "수리", "인쇄", "복사", "도서", "우편", "택배",
]

# 비음식점 상호 키워드
NON_RESTAURANT_NAME_KEYWORDS = [
    "마트", "쿠팡", "편의점", "cu ", "gs25", "세븐일레븐", "이마트", "홈플러스", "롯데마트",
    "백화점", "온라인", "배달", "문구", "꽃", "화원", "플라워", "떡집", "제과", "베이커리",
    "과자점", "빵", "바게트", "바게뜨", "뚜레쥬르", "던킨", "크리스피크림", "와플", "디저트",
    "카페", "커피", "스타벅스", "투썸", "파스쿠찌", "이디야", "할리스", "폴바셋", "커피빈",
    "메가엠지씨", "컴포즈", "바나프레소", "블루보틀", "테라로사", "엔제리너스", "탐앤탐스",
    "빽다방", "더벤티", "매머드", "공차", "설빙", "배스킨", "팀홀튼", "티하우스", "티룸",
    "아티제", "가배", "스패뉴", "매점", "카페테리아", "구내식당", "푸드테크", "푸드서비스",
    "푸드시스템", "푸드빌", "에프앤비", "f&b", "케이터링", "다이소", "약국", "병원",
    "택시", "주유", "주차", "상품권", "페이", "주식회사", "㈜", "(주)", "유한회사",
    "(유)", "농협", "하나로", "슈퍼", "정육점", "청과", "도매", "유통", "물산",
    "상사", "네이버", "11번가", "지마켓", "옥션", "인터파크", "티몬", "위메프", "컬리",
    "배민", "요기요", "우체국", "은행", "센터", "협회", "재단", "학교", "대학",
    "티켓", "예매", "공연", "극장", "영화", "cgv", "메가박스",
]

REQUIRED_RECORD_KEYS = [
    "region", "institution", "department", "name", "address",
    "paymentDate", "sourceURL", "recordID"
]

MAX_FUTURE_DATE = dt.date.today()
FUNNEL = defaultdict(Counter)
UNRESOLVED = []


def log(msg: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr)


def clean_text(s) -> str:
    if not isinstance(s, str):
        if s is None:
            return ""
        s = str(s)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s)).strip()


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[\s\W_]+", "", s)


def dept_last_token(dept: str) -> str:
    """부서 고유 키: 마지막 토큰(과·담당관·팀·소 단위)으로 정규화."""
    cleaned = clean_text(dept)
    if not cleaned:
        return ""
    tokens = cleaned.split(" ")
    return tokens[-1]


def dept_key(institution: str, department: str) -> str:
    return f"{clean_text(institution)}|{dept_last_token(department)}"


def is_meal_purpose(purpose: str) -> bool:
    p = clean_text(purpose).replace(" ", "")
    if not p:
        return True
    if any(k in p for k in NON_MEAL_KEYWORDS):
        return False
    return any(k in p for k in MEAL_KEYWORDS)


def parse_date(val) -> dt.date | None:
    if val is None:
        return None
    if isinstance(val, dt.datetime):
        return val.date()
    if isinstance(val, dt.date):
        return val
    s = clean_text(val)
    m = DATE_RE.search(s)
    if m:
        try:
            year, month, day = int(m[1]), int(m[2]), int(m[3])
            if year < 100:
                year += 2000
            return dt.date(year, month, day)
        except ValueError:
            return None
    if isinstance(val, (int, float)) and 40000 <= val <= 60000:
        try:
            base = dt.date(1899, 12, 30)
            return base + dt.timedelta(days=int(val))
        except Exception:
            pass
    return None


def clean_amount(val) -> int:
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    s = re.sub(r"[^\d]", "", clean_text(val))
    return int(s) if s else 0


def classify_integrated_city(sigungu: str) -> str | None:
    """전남광주통합특별시 주소의 시군구로 내부 17개 권역(광주 또는 전남)을 분류한다."""
    if not sigungu:
        return None
    first_gu = sigungu.split()[0]
    if first_gu in GWANGJU_DISTRICTS:
        return "광주"
    return "전남"


def parse_address(addr: str, region: str) -> tuple[str, str, str] | None:
    """주소를 검증하고 (정리 주소, 병합 키, 시군구)를 반환한다.

    merge_public_dining_catalog.py 의 검증 규격과 100% 호환된다.
    전남광주통합특별시 공식 접두사는 임의 치환 없이 원문 그대로 보존되며,
    시군구(동구·서구·남구·북구·광산구)에 따라 광주/전남으로 분류된다.
    """
    addr = clean_text(addr).strip(" ,./")
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

    if prefix in INTEGRATED_SPECIAL_PREFIXES:
        detected_region = classify_integrated_city(sigungu)
        if not detected_region:
            return None
    else:
        detected_region = REGION_ALIASES.get(prefix)
        if not detected_region:
            return None

    if not sigungu:
        return None

    expected_region = REGION_ALIASES.get(region, region)
    if detected_region != expected_region:
        return None
    resolved_region = detected_region

    eupmyeon: str | None = None
    em = EUPMYEON_RE.match(rest)
    if em:
        eupmyeon = em[1]
        rest_after_em = rest[em.end():].strip(" ,")
    else:
        rest_after_em = rest

    rest_road = re.sub(r"([가-힣A-Za-z0-9·]+(?:로|길))\s+([가-힣\d]+(?:길))", r"\1\2", rest)
    road = ROAD_RE.search(rest_road)
    if road:
        road_name = road[1]
        is_under = bool(road[2])
        bldg_no = road[3]
        bldg_part = f"지하{bldg_no}" if is_under else bldg_no
        locator = f"도로|{norm_text(road_name)}|{bldg_part}"
    else:
        target_jibun = rest_after_em if eupmyeon else rest
        jibun = JIBUN_RE.search(target_jibun)
        if jibun:
            dong_ri = jibun[1]
            is_under = bool(jibun[2])
            num_part = f"지하{jibun[3]}" if is_under else jibun[3]
            if eupmyeon:
                locator = f"지번|{norm_text(eupmyeon)}|{norm_text(dong_ri)}|{num_part}"
            else:
                locator = f"지번|{norm_text(dong_ri)}|{num_part}"
        elif eupmyeon:
            num_m = re.match(r"^(?:(지하)\s*)?(\d+(?:-\d+)?)(?:번지)?(?![가-힣\d])", rest_after_em)
            if num_m:
                num_part = f"지하{num_m[2]}" if num_m[1] else num_m[2]
                locator = f"지번|{norm_text(eupmyeon)}|{num_part}"
            else:
                return None
        else:
            return None

    cleaned = " ".join(x for x in (prefix, sigungu, rest) if x)
    return cleaned, f"{resolved_region}|{sigungu}|{locator}", sigungu


def normalize_address(raw_addr: str, default_region: str) -> tuple[str, str] | None:
    """원문 주소에서 개인명/잡음을 제거하고 필요시 광역 접두를 보완하여 (정리주소, 판정지역)을 돌려준다."""
    addr = clean_text(raw_addr).strip(" ,./")
    if not addr or len(addr) < 3:
        return None

    addr = PERSON_PREFIX_RE.sub("", addr)
    addr = PERSON_SUFFIX_RE.sub("", addr)
    addr = ETC_SUFFIX_RE.sub("", addr)

    pm = _REGION_PREFIX_RE.match(addr)
    if pm:
        prefix = pm[1]
        if prefix in INTEGRATED_SPECIAL_PREFIXES:
            rest = addr[pm.end():].strip(" ,")
            sm = SIGUNGU_RE.match(rest)
            if sm:
                detected = classify_integrated_city(sm[1])
                if detected in REGIONS:
                    parsed = parse_address(addr, detected)
                    if parsed:
                        return parsed[0], detected
            return None
        else:
            region = REGION_ALIASES.get(prefix)
            if region in REGIONS:
                canonical = REGION_CANONICAL_PREFIX[region]
                rest = addr[pm.end():].strip(" ,")
                candidate = f"{canonical} {rest}".strip()
                parsed = parse_address(candidate, region)
                if parsed:
                    return parsed[0], region
            return None

    sm = SIGUNGU_RE.match(addr)
    if sm:
        sigungu = sm[1]
        region = SIGUNGU_TO_REGION.get(sigungu)
        if region is None and default_region in REGIONS:
            region = default_region
        if region in REGIONS:
            canonical = REGION_CANONICAL_PREFIX[region]
            candidate = f"{canonical} {addr}".strip()
            parsed = parse_address(candidate, region)
            if parsed:
                return parsed[0], region

    start_m = re.search(r"(전남광주통합특별시|광주광역시|전북특별자치도|전라북도|전라남도|제주특별자치도|제주도|[가-힣]{1,5}(?:시|군|구)\s|[가-힣A-Za-z0-9·]+(?:로|길)\s?\d)", addr)
    if start_m and start_m.start() > 0:
        sub = addr[start_m.start():].strip(" ,./")
        return normalize_address(sub, default_region)

    return None


class AddressIndex:
    """공공데이터포털 소상공인진흥공단 상가정보(15083033) 2026년 6월 CSV 기반 주소 인덱스.

    오직 지정된 address_dir 내의 전남광주.csv, 전북.csv, 제주.csv 실물 파일로부터만 로드한다.
    모든 가공·추정·퍼지 매칭을 금지하며, 출처 권역 내에서 유일하게 식별되는 식당에 한해서만
    원문 상호와 공식 주소, 상가업소번호(addressSourceRecordID)를 반환한다.
    """

    def __init__(self, address_dir: Path):
        self.address_dir = Path(address_dir)
        self.by_name: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self.by_full: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self.loaded = False
        self.counts: dict[str, int] = Counter()

    def load(self) -> None:
        if self.loaded:
            return
        if not self.address_dir.exists():
            log(f"공식 주소 디렉토리가 없습니다: {self.address_dir}")
            self.loaded = True
            return

        file_mappings = [
            ("전남광주.csv", "전남광주"),
            ("전북.csv", "전북"),
            ("제주.csv", "제주"),
        ]

        for fname, file_target in file_mappings:
            fpath = self.address_dir / fname
            if not fpath.exists():
                log(f"공식 주소 CSV 없음: {fpath}")
                continue

            try:
                with open(fpath, mode="r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        rec_id = clean_text(row.get("상가업소번호", ""))
                        name = clean_text(row.get("상호명", ""))
                        branch = clean_text(row.get("지점명", ""))
                        sigungu = clean_text(row.get("시군구명", ""))
                        road_addr = clean_text(row.get("도로명주소", ""))
                        jibun_addr = clean_text(row.get("지번주소", ""))
                        addr = road_addr or jibun_addr

                        if not rec_id or not name or not addr:
                            continue

                        if file_target == "전남광주":
                            first_gu = sigungu.split()[0] if sigungu else ""
                            region = "광주" if first_gu in GWANGJU_DISTRICTS else "전남"
                        else:
                            region = file_target

                        entry = {
                            "recordID": rec_id,
                            "name": name,
                            "branch": branch,
                            "sigungu": sigungu,
                            "address": addr,
                            "region": region,
                        }

                        n_name = norm_text(name)
                        self.by_name[(region, n_name)].append(entry)
                        if branch:
                            n_full = norm_text(name + branch)
                            self.by_full[(region, n_full)].append(entry)
                        self.counts[region] += 1
            except Exception as e:
                log(f"주소 CSV 읽기 실패 ({fpath}): {e}")

        self.loaded = True
        log(f"공식 주소 인덱스 로드 완료: {dict(self.counts)}")

    def resolve(self, raw_name: str, region: str, explicit_district: str | None = None) -> dict | None:
        """상호명을 바탕으로 알려진 출처 권역 내에서 유일한 실제 식당을 엄격히 해소한다."""
        if not self.loaded:
            self.load()

        n_raw = norm_text(raw_name)
        if not n_raw:
            return None

        # 1. 지점 포함 전체 명칭으로 먼저 검색
        candidates = list(self.by_full.get((region, n_raw), []))
        if not candidates:
            # 2. 기본 상호로 검색
            candidates = list(self.by_name.get((region, n_raw), []))

        if not candidates:
            FUNNEL[region]["addressNameNotFound"] += 1
            return None

        # 3. 원문 장소/출처에 명시된 시군구가 있으면 시군구 필터링
        if explicit_district:
            filtered = [c for c in candidates if c["sigungu"] == explicit_district or explicit_district in c["sigungu"]]
            candidates = filtered

        # 4. 단일 식당 유일성 검증 (2곳 이상이면 모호성 거부, 추정 금지)
        if len(candidates) == 1:
            return candidates[0]
        FUNNEL[region]["addressAmbiguousOrDistrictMismatch"] += 1

        return None


GENERIC_DEPT_NAMES = {"부서", "실과", "실과소", "행정지원부서", "부서별공개", "사용자", "소속", "담당", "비고", "구분"}


def clean_dept_name(raw_dept: str) -> str:
    """부서명에서 직책 접미('과장', '실장', '팀장' 등)를 정제하여 부서 단위로 맞춘다. 일반 명칭은 배제한다."""
    d = clean_text(raw_dept)
    if not d:
        return ""
    d = re.sub(r"[(（][^()（）]+[)）]", "", d).strip()
    d = re.sub(r"\s+(?=(?:과장|국장|실장|팀장|담당관)\b)", "", d)
    m = re.search(r"([가-힣0-9·]+?(?:과|관|팀|단|소|원|소방서|본부|실|국|센터))\s*(?:장|담당관)?\b", d)
    if m:
        res = m[1]
        return "" if res in GENERIC_DEPT_NAMES else res
    if d in GENERIC_DEPT_NAMES:
        return ""
    return ""


def extract_place_and_address(
    place_text: str,
    address_text: str,
    default_region: str,
    address_index: AddressIndex | None = None,
) -> tuple[str, str, str, str | None, str | None] | None:
    """장소명과 주소를 추출·정규화하여 (식당명, 검증주소, 지역, addressSourceURL|None, addressSourceRecordID|None)을 반환한다."""
    p_text = clean_text(place_text)
    a_text = clean_text(address_text)

    if any(k in p_text.lower() for k in NON_RESTAURANT_NAME_KEYWORDS) or HOTEL_SELF_RE.search(p_text):
        return None

    explicit_district = None
    dm = re.search(r"[(（]([가-힣]{1,6}(?:시|군|구))[)）]", p_text)
    if dm:
        explicit_district = dm[1]

    # 1. 주소가 별도 열에 있는 경우
    if a_text and len(a_text) >= 4:
        norm = normalize_address(a_text, default_region)
        if norm:
            name = PERSON_PREFIX_RE.sub("", p_text)
            name = PERSON_SUFFIX_RE.sub("", name).strip(" -–,./")
            if len(name) >= 2 and not any(k in name.lower() for k in NON_RESTAURANT_NAME_KEYWORDS) and not HOTEL_SELF_RE.search(name):
                return name, norm[0], norm[1], None, None

    # 2. 장소명에 괄호로 주소가 들어있는 경우: "식당명 (주소)" 또는 "주소 (식당명)"
    m = LOC_RE.match(p_text)
    if m:
        part1 = clean_text(m["name"]).strip(" -–,./")
        part2 = clean_text(m["addr"]).strip(" -–,./")
        norm2 = normalize_address(part2, default_region)
        if norm2:
            name = PERSON_PREFIX_RE.sub("", part1)
            name = PERSON_SUFFIX_RE.sub("", name).strip()
            if len(name) >= 2 and not any(k in name.lower() for k in NON_RESTAURANT_NAME_KEYWORDS) and not HOTEL_SELF_RE.search(name):
                return name, norm2[0], norm2[1], None, None
        norm1 = normalize_address(part1, default_region)
        if norm1:
            name = PERSON_PREFIX_RE.sub("", part2)
            name = PERSON_SUFFIX_RE.sub("", name).strip()
            if len(name) >= 2 and not any(k in name.lower() for k in NON_RESTAURANT_NAME_KEYWORDS) and not HOTEL_SELF_RE.search(name):
                return name, norm1[0], norm1[1], None, None

    # 3. 쉼표 구분: "식당명, 주소"
    if "," in p_text:
        parts = p_text.split(",", 1)
        name = clean_text(parts[0]).strip(" -–,./")
        norm = normalize_address(parts[1], default_region)
        if norm:
            if len(name) >= 2 and not any(k in name.lower() for k in NON_RESTAURANT_NAME_KEYWORDS) and not HOTEL_SELF_RE.search(name):
                return name, norm[0], norm[1], None, None

    # 4. 원문에 주소가 누락된 경우: 오직 실제 SBiz CSV 주소 인덱스(data.go.kr 15083033)를 통해서만 해소
    if address_index:
        pure_name = PERSON_PREFIX_RE.sub("", p_text)
        pure_name = PERSON_SUFFIX_RE.sub("", pure_name)
        pure_name = re.sub(r"[(（][^()（）]+[)）]", "", pure_name).strip(" -–,./")
        if len(pure_name) >= 2 and not any(k in pure_name.lower() for k in NON_RESTAURANT_NAME_KEYWORDS) and not HOTEL_SELF_RE.search(pure_name):
            resolved = address_index.resolve(pure_name, default_region, explicit_district=explicit_district)
            if resolved:
                parsed = parse_address(resolved["address"], resolved["region"])
                if parsed:
                    return pure_name, parsed[0], resolved["region"], ADDRESS_SOURCE_URL, resolved["recordID"]

    return None


# --- HTTP 다운로더 -------------------------------------------------------------
def fetch_url(url: str, post_data: dict | None = None, headers: dict | None = None,
              timeout: int = 30, retries: int = 2) -> tuple[bytes, dict, int]:
    """공식 HTTPS URL 요청을 수행한다. (bytes, headers, http_code) 반환."""
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if headers:
        req_headers.update(headers)

    data_bytes = None
    if post_data is not None:
        if isinstance(post_data, dict):
            data_bytes = urllib.parse.urlencode(post_data).encode("utf-8")
        elif isinstance(post_data, (bytes, bytearray)):
            data_bytes = bytes(post_data)

    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data_bytes, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                content = resp.read()
                return content, resp_headers, resp.status
        except urllib.error.HTTPError as e:
            if attempt == retries:
                log(f"HTTP 오류 ({e.code}) URL={url}")
                return b"", {}, e.code
            time.sleep(1.0)
        except Exception as e:
            if attempt == retries:
                log(f"네트워크 오류 ({e}) URL={url}")
                return b"", {}, 0
            time.sleep(1.0)
    return b"", {}, 0


def is_html_content(raw: bytes) -> bool:
    """내용이 HTML 문서 또는 웹 오류 페이지인지 판별한다."""
    prefix = raw[:512].strip().lower()
    return (
        prefix.startswith(b"<!doctype html")
        or prefix.startswith(b"<html")
        or b"<html" in prefix
        or b"<head" in prefix
        or b"<script" in prefix
    )


def get_soffice_binary() -> str | None:
    """사용 가능한 LibreOffice/soffice 실행 파일 경로를 반환한다."""
    if os.path.isfile(SOFFICE_BIN_OVERRIDE) and os.access(SOFFICE_BIN_OVERRIDE, os.X_OK):
        return SOFFICE_BIN_OVERRIDE
    return shutil.which("soffice") or shutil.which("libreoffice")


def convert_with_soffice(input_path: Path, target_format: str, task_cache_dir: Path) -> Path | None:
    """soffice --headless 를 사용하여 레거시 XLS 또는 HWP 문서를 변환한다. (TM 런타임 실행용)"""
    soffice = get_soffice_binary()
    if not soffice:
        log(f"soffice 실행 파일을 찾을 수 없습니다: {SOFFICE_BIN_OVERRIDE}")
        return None

    user_install_dir = f"file:///tmp/soffice_user_{os.getpid()}_{int(time.time() * 1000)}"
    out_dir = task_cache_dir / "converted"
    out_dir.mkdir(parents=True, exist_ok=True)
    expected = out_dir / f"{input_path.stem}.{target_format}"
    if expected.exists() and expected.stat().st_size > 100:
        return expected

    cmd = [
        soffice,
        "--headless",
        f"-env:UserInstallation={user_install_dir}",
        "--convert-to", target_format,
        "--outdir", str(out_dir),
        str(input_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=60)
        expected_out = out_dir / f"{input_path.stem}.{target_format}"
        if res.returncode == 0 and expected_out.exists() and expected_out.stat().st_size > 0:
            return expected_out
        log(f"soffice 변환 실패 (반환코드 {res.returncode}): {input_path.name} -> {target_format}")
    except Exception as e:
        log(f"soffice 실행 예외 ({e}): {input_path.name}")
    return None


def download_to_cache(url: str, cache_dir: Path, filename_prefix: str,
                      post_data: dict | None = None, headers: dict | None = None) -> tuple[Path | None, str, int]:
    """URL에서 파일을 내려받아 캐시에 저장하고 (저장경로, sha256, 바이트수)를 반환한다.
    HTML 오류 페이지 등 비정상 내용은 거부한다.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw, hdrs, code = fetch_url(url, post_data=post_data, headers=headers)
    if code != 200 or not raw or len(raw) < 50:
        return None, "", 0
    if is_html_content(raw):
        log(f"다운로드된 내용이 HTML 오류 페이지임 ({len(raw)}B, 거부): URL={url}")
        return None, "", 0

    sha = hashlib.sha256(raw).hexdigest()
    # 매직 바이트 우선으로 확장자 판정
    ext = ".bin"
    if raw.startswith(PDF_MAGIC):
        ext = ".pdf"
    elif raw.startswith(ZIP_MAGIC):
        ext = ".xlsx"
    elif raw.startswith(OLE_MAGIC):
        if HWP_OLE_TAG in raw or HWP_MAGIC_TAG in raw:
            ext = ".hwp"
        elif XLS_OLE_WORKBOOK in raw or XLS_OLE_BOOK in raw:
            ext = ".xls"
        else:
            ext = ".bin"
    else:
        disp = hdrs.get("content-disposition", "")
        if ".xlsx" in disp.lower() or "spreadsheet" in hdrs.get("content-type", ""):
            ext = ".xlsx"
        elif ".pdf" in disp.lower() or "pdf" in hdrs.get("content-type", ""):
            ext = ".pdf"
        elif ".csv" in disp.lower() or "csv" in hdrs.get("content-type", ""):
            ext = ".csv"

    dest = cache_dir / f"{filename_prefix}_{sha[:16]}{ext}"
    dest.write_bytes(raw)
    return dest, sha, len(raw)


# --- 파일 파서 (XLSX, PDF) -----------------------------------------------------
def parse_xlsx_rows(file_path_or_bytes, default_inst: str, default_dept: str,
                    default_region: str, source_url: str, doc_key: str,
                    address_index: AddressIndex | None = None) -> tuple[list[dict], list[str]]:
    """XLSX 파일에서 업무추진비 결제 행을 파싱한다.

    openpyxl read_only=False 모드로 실제 병합 셀(merged_cells)을 분석하여
    병합된 셀 범위에 한해서만 일자/부서/목적을 전파하며, 비병합 빈 행에 임의 복제하지 않는다.
    """
    if openpyxl is None:
        err = "경고: openpyxl 이 설치되지 않아 XLSX 를 파싱할 수 없습니다."
        log(err)
        return [], [err]

    records: list[dict] = []
    errors: list[str] = []
    try:
        if isinstance(file_path_or_bytes, (str, Path)):
            raw_bytes = Path(file_path_or_bytes).read_bytes()
        else:
            raw_bytes = bytes(file_path_or_bytes)
        try:
            wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=False, data_only=True)
        except TypeError as metadata_error:
            # Some official exports contain a nameless custom property. Discard only
            # that optional property in the in-memory copy; retain original bytes/hash.
            if "StringProperty" not in str(metadata_error):
                raise
            import zipfile
            import xml.etree.ElementTree as ET
            repaired = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as original, zipfile.ZipFile(repaired, "w") as output:
                for item in original.infolist():
                    payload = original.read(item.filename)
                    if item.filename == "docProps/custom.xml":
                        root = ET.fromstring(payload)
                        for prop in list(root):
                            if not prop.get("name"):
                                root.remove(prop)
                        payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                    output.writestr(item, payload)
            wb = openpyxl.load_workbook(io.BytesIO(repaired.getvalue()), read_only=False, data_only=True)
    except Exception as e:
        err = f"XLSX 열기 실패 ({e}): doc={doc_key}"
        log(err)
        return [], [err]

    DATE_KEYS = ("일자", "일시", "집행일시", "결제일", "사용일", "집행일자")
    PLACE_KEYS = ("장소", "사용처", "가맹점", "상호", "거래처", "사용장소", "집행장소")
    PURPOSE_KEYS = ("목적", "내역", "집행목적", "사용목적", "집행내용", "사용내용", "내용")
    AMOUNT_KEYS = ("금액", "집행금액", "사용금액")
    DEPT_KEYS = ("부서", "부서명", "소속", "실과", "사용자", "담당부서")

    for sheet_idx, sheet in enumerate(wb.worksheets, start=1):
        max_r = sheet.max_row or 0
        max_c = sheet.max_column or 0
        if max_r < 2 or max_c < 2:
            continue

        merged_map: dict[tuple[int, int], any] = {}
        try:
            for rng in sheet.merged_cells.ranges:
                top_left_val = sheet.cell(rng.min_row, rng.min_col).value
                for r in range(rng.min_row, rng.max_row + 1):
                    for c in range(rng.min_col, rng.max_col + 1):
                        if (r, c) != (rng.min_row, rng.min_col):
                            merged_map[(r, c)] = top_left_val
        except Exception as me:
            log(f"병합 셀 분석 예외 ({me}): doc={doc_key}, sheet={sheet.title}")

        header_row = -1
        col_map: dict[str, int] = {}
        for r in range(1, min(max_r, 15) + 1):
            r_strs = [clean_text(sheet.cell(r, c).value) for c in range(1, max_c + 1)]
            if any(any(k in s for k in DATE_KEYS) for s in r_strs) and \
               any(any(k in s for k in PLACE_KEYS) for s in r_strs):
                header_row = r
                for c in range(1, max_c + 1):
                    txt = clean_text(sheet.cell(r, c).value)
                    if any(k in txt for k in DATE_KEYS):
                        col_map.setdefault("date", c)
                    elif any(k in txt for k in PLACE_KEYS):
                        col_map.setdefault("place", c)
                    elif any(k in txt for k in ("주소", "소재지", "식당주소")):
                        col_map.setdefault("address", c)
                    elif any(k in txt for k in PURPOSE_KEYS):
                        col_map.setdefault("purpose", c)
                    elif any(k in txt for k in AMOUNT_KEYS):
                        col_map.setdefault("amount", c)
                    elif any(k in txt for k in DEPT_KEYS):
                        col_map.setdefault("dept", c)
                break

        if header_row == -1 or "date" not in col_map or "place" not in col_map:
            continue

        sheet_dept = default_dept
        for r in range(1, header_row + 1):
            for c in range(1, max_c + 1):
                txt = clean_text(sheet.cell(r, c).value)
                m = re.search(r"<([가-힣0-9·]+?(?:과|관|팀|단|소|원|소방서|본부|실|국|센터))>", txt)
                if m:
                    sheet_dept = m[1]
                    break
                m2 = re.search(r"([가-힣0-9·]+?(?:과|관|팀|단|소|원|소방서|본부|실|국|센터))\s*(?:장|담당관)?\s*업무추진비", txt)
                if m2:
                    sheet_dept = m2[1]
                    break
            if sheet_dept != default_dept:
                break

        sheet_key = re.sub(r"[^\w가-힣\-]", "_", sheet.title).strip("_") or f"sheet{sheet_idx}"

        def get_val(r: int, c: int) -> any:
            val = sheet.cell(r, c).value
            if (val is None or clean_text(val) == "") and (r, c) in merged_map:
                val = merged_map[(r, c)]
            return val

        empty_streak = 0
        for r in range(header_row + 1, max_r + 1):
            row_vals = [sheet.cell(r, c).value for c in range(1, max_c + 1)]
            if all(v is None or clean_text(v) == "" for v in row_vals):
                empty_streak += 1
                if empty_streak >= 20:
                    break
                continue
            empty_streak = 0

            date_val = get_val(r, col_map["date"])
            place_val = get_val(r, col_map["place"])
            addr_val = get_val(r, col_map["address"]) if "address" in col_map else None
            purp_val = get_val(r, col_map["purpose"]) if "purpose" in col_map else ""
            amt_val = get_val(r, col_map["amount"]) if "amount" in col_map else 0
            dept_val = get_val(r, col_map["dept"]) if "dept" in col_map else None

            pay_date = parse_date(date_val)
            if pay_date is None or pay_date > MAX_FUTURE_DATE:
                continue

            FUNNEL[default_region]["datedPaymentRows"] += 1
            if not is_meal_purpose(purp_val):
                continue

            FUNNEL[default_region]["mealRows"] += 1
            parsed_place = extract_place_and_address(place_val, addr_val, default_region, address_index=address_index)
            if not parsed_place:
                if pay_date >= subtract_months(MAX_FUTURE_DATE, 18) and clean_text(place_val):
                    UNRESOLVED.append({"region": default_region, "institution": default_inst,
                        "department": clean_dept_name(dept_val), "name": clean_text(place_val),
                        "addressHint": clean_text(addr_val), "paymentDate": pay_date.isoformat(),
                        "sourceURL": source_url, "recordID": f"{doc_key}#{sheet_key}#L{r}", "isMeal": True})
                continue
            name, address, region, addr_src_url, addr_src_rec_id = parsed_place
            FUNNEL[default_region]["addressMatchedRows"] += 1

            row_dept = clean_dept_name(dept_val) if dept_val else ""
            final_dept = row_dept or clean_dept_name(sheet_dept) or clean_dept_name(default_dept)
            if final_dept in GENERIC_DEPT_NAMES:
                final_dept = ""

            rec = {
                "region": region,
                "institution": "제주특별자치도" if region == "제주" else default_inst,
                "department": final_dept,
                "name": name,
                "address": address,
                "paymentDate": pay_date.isoformat(),
                "sourceURL": source_url,
                "recordID": f"{doc_key}#{sheet_key}#L{r}",
                "isMeal": True,
            }
            if addr_src_url:
                rec["addressSourceURL"] = addr_src_url
            if addr_src_rec_id:
                rec["addressSourceRecordID"] = addr_src_rec_id
            records.append(rec)

    wb.close()
    return records, errors


def parse_pdf_rows(file_path_or_bytes, default_inst: str, default_dept: str,
                   default_region: str, source_url: str, doc_key: str,
                   address_index: AddressIndex | None = None) -> tuple[list[dict], list[str]]:
    """PDF 파일에서 업무추진비 결제 표/행을 파싱한다."""
    if pdfplumber is None:
        err = "경고: pdfplumber 가 설치되지 않아 PDF 를 파싱할 수 없습니다."
        log(err)
        return [], [err]

    records: list[dict] = []
    errors: list[str] = []
    DATE_KEYS = ("일자", "일시", "집행일시", "결제일", "사용일", "집행일자")
    PLACE_KEYS = ("장소", "사용처", "가맹점", "상호", "거래처", "사용장소", "집행장소")
    PURPOSE_KEYS = ("목적", "내역", "집행목적", "사용목적", "집행내용", "사용내용", "내용")
    AMOUNT_KEYS = ("금액", "집행금액", "사용금액")
    DEPT_KEYS = ("부서", "부서명", "소속", "실과", "사용자", "담당부서")

    try:
        stream = file_path_or_bytes if isinstance(file_path_or_bytes, (str, Path)) else io.BytesIO(file_path_or_bytes)
        with pdfplumber.open(stream) as pdf:
            row_counter = 0
            first_text = pdf.pages[0].extract_text() if pdf.pages else ""
            doc_dept = default_dept
            dm = re.search(r"([가-힣0-9·]+?(?:과|관|팀|단|소|원|소방서|본부|실|국|센터))\s*(?:장|담당관)?\s*업무추진비", first_text)
            if dm:
                doc_dept = dm[1]

            for page_idx, page in enumerate(pdf.pages):
                tables = page.extract_tables()
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    header_idx = -1
                    col_map = {}
                    for r_idx, row in enumerate(table[:5]):
                        r_strs = [clean_text(c) for c in row if c is not None]
                        if any(any(k in s for k in DATE_KEYS) for s in r_strs) and \
                           any(any(k in s for k in PLACE_KEYS) for s in r_strs):
                            header_idx = r_idx
                            for c_idx, cell in enumerate(row):
                                txt = clean_text(cell)
                                if any(k in txt for k in DATE_KEYS):
                                    col_map.setdefault("date", c_idx)
                                elif any(k in txt for k in PLACE_KEYS):
                                    col_map.setdefault("place", c_idx)
                                elif any(k in txt for k in ("주소", "소재지", "식당주소")):
                                    col_map.setdefault("address", c_idx)
                                elif any(k in txt for k in PURPOSE_KEYS):
                                    col_map.setdefault("purpose", c_idx)
                                elif any(k in txt for k in AMOUNT_KEYS):
                                    col_map.setdefault("amount", c_idx)
                                elif any(k in txt for k in DEPT_KEYS):
                                    col_map.setdefault("dept", c_idx)
                            break

                    if header_idx == -1 or "date" not in col_map or "place" not in col_map:
                        continue

                    for row in table[header_idx + 1:]:
                        row_counter += 1
                        if not row or all(not c for c in row):
                            continue
                        date_val = row[col_map["date"]] if col_map.get("date") < len(row) else None
                        place_val = row[col_map["place"]] if col_map.get("place") < len(row) else None
                        addr_val = row[col_map["address"]] if col_map.get("address") is not None and col_map["address"] < len(row) else None
                        purp_val = row[col_map["purpose"]] if col_map.get("purpose") is not None and col_map["purpose"] < len(row) else ""
                        amt_val = row[col_map["amount"]] if col_map.get("amount") is not None and col_map["amount"] < len(row) else 0
                        dept_val = row[col_map["dept"]] if col_map.get("dept") is not None and col_map["dept"] < len(row) else None

                        pay_date = parse_date(date_val)
                        if pay_date is None or pay_date > MAX_FUTURE_DATE:
                            continue

                        FUNNEL[default_region]["datedPaymentRows"] += 1
                        if not is_meal_purpose(purp_val):
                            continue

                        FUNNEL[default_region]["mealRows"] += 1
                        parsed_place = extract_place_and_address(place_val, addr_val, default_region, address_index=address_index)
                        if not parsed_place:
                            if pay_date >= subtract_months(MAX_FUTURE_DATE, 18) and clean_text(place_val):
                                UNRESOLVED.append({"region": default_region, "institution": default_inst,
                                    "department": clean_dept_name(dept_val) if dept_val else clean_dept_name(doc_dept),
                                    "name": clean_text(place_val), "addressHint": clean_text(addr_val),
                                    "paymentDate": pay_date.isoformat(), "sourceURL": source_url,
                                    "recordID": f"{doc_key}#P{page_idx+1}#L{row_counter}", "isMeal": True})
                            continue
                        name, address, region, addr_src_url, addr_src_rec_id = parsed_place
                        FUNNEL[default_region]["addressMatchedRows"] += 1

                        row_dept = clean_dept_name(dept_val) if dept_val else ""
                        dept_name = row_dept or clean_dept_name(doc_dept) or clean_dept_name(default_dept)
                        if dept_name in GENERIC_DEPT_NAMES:
                            dept_name = ""

                        rec = {
                            "region": region,
                            "institution": "제주특별자치도" if region == "제주" else default_inst,
                            "department": dept_name,
                            "name": name,
                            "address": address,
                            "paymentDate": pay_date.isoformat(),
                            "sourceURL": source_url,
                            "recordID": f"{doc_key}#P{page_idx+1}#L{row_counter}",
                            "isMeal": True,
                        }
                        if addr_src_url:
                            rec["addressSourceURL"] = addr_src_url
                        if addr_src_rec_id:
                            rec["addressSourceRecordID"] = addr_src_rec_id
                        records.append(rec)
    except Exception as e:
        err = f"PDF 파싱 실패 ({e}): doc={doc_key}"
        log(err)
        errors.append(err)

    return records, errors


def parse_hwp_table_document(fpath, default_inst, default_dept, default_region,
                             source_url, doc_key, address_index, cache_dir):
    """Read real HWP cell coordinates through pyhwp, without lossy PDF conversion."""
    import xml.etree.ElementTree as ET
    import zipfile
    raw = fpath.read_bytes()
    if raw.startswith(ZIP_MAGIC):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            roots = [ET.fromstring(archive.read(n)) for n in sorted(archive.namelist())
                     if re.fullmatch(r"Contents/section\d+\.xml", n)]
        cells_tag, table_tag, text_tag = "tc", "tbl", "t"
    else:
        xml_path = cache_dir / "hwp-xml" / (fpath.stem + ".xml")
        if not xml_path.exists():
            dep_dir = Path(os.environ.get("WHATTOEAT_HWP_DEPS", str(DEFAULT_CACHE_DIR / "hwp-deps")))
            executable = dep_dir / "bin" / "hwp5proc"
            if not executable.exists():
                return [], [f"HWP dependency missing: {executable}; doc={doc_key}"]
            env = os.environ.copy()
            env["PYTHONPATH"] = str(dep_dir)
            result = subprocess.run([sys.executable, str(executable), "xml", str(fpath)],
                                    capture_output=True, timeout=60, env=env)
            if result.returncode:
                return [], [f"HWP XML conversion failed: doc={doc_key}"]
            xml_path.parent.mkdir(parents=True, exist_ok=True)
            xml_path.write_bytes(result.stdout)
        roots = [ET.parse(xml_path).getroot()]
        cells_tag, table_tag, text_tag = "TableCell", "TableControl", "Text"
    local = lambda tag: tag.rsplit("}", 1)[-1]
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    index = 0
    for root in roots:
        for table in (e for e in root.iter() if local(e.tag) == table_tag):
            index += 1
            sheet = workbook.create_sheet(f"HWP_table_{index}")
            sheet.cell(1, 1, f"{default_dept} 업무추진비")
            for cell in (e for e in table.iter() if local(e.tag) == cells_tag):
                attrs = dict(cell.attrib)
                if cells_tag == "tc":
                    for e in cell:
                        if local(e.tag) in ("cellAddr", "cellSpan"):
                            attrs.update(e.attrib)
                row = int(attrs.get("row", attrs.get("rowAddr", 0))) + 2
                col = int(attrs.get("col", attrs.get("colAddr", 0))) + 1
                text = " ".join("".join(e.itertext()) for e in cell.iter() if local(e.tag) == text_tag)
                sheet.cell(row, col, text)
                rs = int(attrs.get("rowspan", attrs.get("rowSpan", 1)))
                cs = int(attrs.get("colspan", attrs.get("colSpan", 1)))
                if rs > 1 or cs > 1:
                    sheet.merge_cells(start_row=row, start_column=col, end_row=row+rs-1, end_column=col+cs-1)
    if not workbook.worksheets:
        return [], [f"HWP has no extractable table: doc={doc_key}"]
    stream = io.BytesIO()
    workbook.save(stream)
    return parse_xlsx_rows(stream.getvalue(), default_inst, default_dept, default_region,
                           source_url, doc_key, address_index)


def parse_document(file_path_or_bytes, default_inst: str, default_dept: str,
                   default_region: str, source_url: str, doc_key: str,
                   address_index: AddressIndex | None = None,
                   cache_dir: Path | None = None) -> tuple[list[dict], list[str]]:
    """매직 바이트 기반 통합 문서 파서 (.bin, .xlsx, .pdf, .xls, .hwp 자동 판별)."""
    if isinstance(file_path_or_bytes, (str, Path)):
        fpath = Path(file_path_or_bytes)
        if not fpath.exists() or fpath.stat().st_size == 0:
            return [], [f"파일 없음 또는 빈 파일: {fpath}"]
        raw = fpath.read_bytes()
    else:
        raw = bytes(file_path_or_bytes)
        fpath = None

    if len(raw) < 16:
        return [], [f"파일 크기 부족 ({len(raw)}B): doc={doc_key}"]

    # 1. HTML 오류 페이지 감지 및 거부
    if is_html_content(raw):
        err = f"HTML 오류 페이지 감지 (다운로드 실패 또는 웹 오류 응답, {len(raw)}B): doc={doc_key}"
        log(err)
        return [], [err]

    # 2. PDF 매직 바이트 (%PDF)
    if raw.startswith(PDF_MAGIC):
        return parse_pdf_rows(raw, default_inst, default_dept, default_region, source_url, doc_key, address_index=address_index)

    # HWP5/HWPX preserve the original table coordinates and source document hash.
    import zipfile
    is_hwpx = False
    if raw.startswith(ZIP_MAGIC):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            is_hwpx = "Contents/section0.xml" in archive.namelist()
    if is_hwpx or (raw.startswith(OLE_MAGIC) and (HWP_OLE_TAG in raw or HWP_MAGIC_TAG in raw)):
        try:
            if fpath is None or cache_dir is None:
                return [], [f"HWP requires verified cached original: doc={doc_key}"]
            return parse_hwp_table_document(fpath, default_inst, default_dept, default_region,
                                             source_url, doc_key, address_index, cache_dir)
        except Exception as exc:
            return [], [f"HWP table parsing failed ({type(exc).__name__}): doc={doc_key}"]

    # 3. XLSX (ZIP 매직 PK\x03\x04)
    if raw.startswith(ZIP_MAGIC):
        rows, errors = parse_xlsx_rows(raw, default_inst, default_dept, default_region, source_url, doc_key, address_index=address_index)
        if errors and fpath and cache_dir and any("list index out of range" in error for error in errors):
            converted = convert_with_soffice(fpath, "xlsx", cache_dir)
            if converted:
                return parse_xlsx_rows(converted, default_inst, default_dept, default_region, source_url, doc_key, address_index=address_index)
        return rows, errors

    # 4. OLE Compound Document (D0 CF 11 E0 A1 B1 1A E1)
    if raw.startswith(OLE_MAGIC):
        # 4-1. HWP 여부 확인 (FileHeader 스트림 또는 HWP Document File 서명)
        if HWP_OLE_TAG in raw or HWP_MAGIC_TAG in raw:
            log(f"HWP OLE 문서 감지: doc={doc_key}")
            if fpath and cache_dir:
                converted = convert_with_soffice(fpath, "pdf", cache_dir)
                if converted:
                    return parse_pdf_rows(converted, default_inst, default_dept, default_region, source_url, doc_key, address_index=address_index)
            err = f"HWP 문서 변환 미지원 또는 실패: doc={doc_key}"
            log(err)
            return [], [err]

        # 4-2. Legacy XLS 여부 확인 (Workbook 또는 Book 스트림)
        if XLS_OLE_WORKBOOK in raw or XLS_OLE_BOOK in raw:
            log(f"Legacy XLS OLE 문서 감지: doc={doc_key}")
            if fpath and cache_dir:
                converted = convert_with_soffice(fpath, "xlsx", cache_dir)
                if converted:
                    return parse_xlsx_rows(converted, default_inst, default_dept, default_region, source_url, doc_key, address_index=address_index)
            err = f"Legacy XLS 변환 실패 (soffice 변환 불가): doc={doc_key}"
            log(err)
            return [], [err]

        err = f"알 수 없는 OLE 복합 문서 포맷: doc={doc_key}"
        log(err)
        return [], [err]

    err = f"지원되지 않는 문서 매직 시그니처 ({raw[:8].hex()}): doc={doc_key}"
    log(err)
    return [], [err]


# --- 지역별 수집 구현 ----------------------------------------------------------

def collect_jeju(cache_dir: Path, max_pages: int, lookback_months: int,
                 address_index: AddressIndex | None = None) -> tuple[list[dict], list[dict], list[str]]:
    """제주특별자치도 본청 및 직속기관 업무추진비 수집."""
    log(f"제주도 업무추진비 수집 시작 (최대 {max_pages}페이지)...")
    records: list[dict] = []
    source_entries: list[dict] = []
    errors: list[str] = []
    base_url = "https://www.jeju.go.kr"

    boards = [
        ("도 본청", 1409, "work2.htm", "제주특별자치도"),
        ("직속기관·사업소", 1002, "work3.htm", "제주특별자치도"),
    ]

    for b_label, category, script_name, inst_name in boards:
        for page in range(1, max_pages + 1):
            list_url = f"{base_url}/open/open/work/{script_name}?category={category}&page={page}"
            content, _, code = fetch_url(list_url, timeout=20)
            if code != 200 or not content:
                if code != 200:
                    errors.append(f"제주 목록 요청 실패 ({code}): {list_url}")
                break

            html_text = content.decode("utf-8", errors="ignore")
            view_matches = re.findall(r'act=view&(?:amp;)?seq=(\d+)"\s*title="([^"]+)"', html_text)
            if not view_matches:
                break

            for seq, title in view_matches:
                title_clean = clean_text(html.unescape(title))
                dept_m = re.search(r"(?:20\d\d년\s*\d{1,2}월\s*)?([가-힣0-9·]+?(?:과|관|팀|단|소|원|소방서|본부|실))\b", title_clean)
                title_dept = dept_m[1] if dept_m else ""

                view_url = f"{base_url}/open/open/work/{script_name}?category={category}&act=view&seq={seq}"
                download_url = f"{base_url}/open/open/work/{script_name}?category={category}&act=download&seq={seq}&no=1"

                dest, sha, byte_cnt = download_to_cache(download_url, cache_dir, f"jeju_{category}_{seq}")
                if not dest:
                    errors.append(f"제주 첨부 다운로드 실패: {download_url}")
                    continue

                source_entries.append({
                    "region": "제주",
                    "institution": inst_name,
                    "department": title_dept or "부서별공개",
                    "title": title_clean,
                    "viewURL": view_url,
                    "downloadURL": download_url,
                    "sha256": sha,
                    "bytes": byte_cnt,
                    "filename": dest.name,
                })

                file_recs, file_errs = parse_document(dest, inst_name, title_dept, "제주", view_url, f"jeju_{category}_{seq}", address_index=address_index, cache_dir=cache_dir)
                records.extend(file_recs)
                errors.extend(file_errs)

            time.sleep(0.3)

    log(f"제주도 수집 완료: 소스 {len(source_entries)}건, 추출 레코드 {len(records)}행, 오류 {len(errors)}건")
    return records, source_entries, errors


def collect_jeonnam(cache_dir: Path, max_pages: int, lookback_months: int,
                    address_index: AddressIndex | None = None) -> tuple[list[dict], list[dict], list[str]]:
    """전라남도 실과소 부서 업무추진비 수집."""
    log(f"전라남도 업무추진비 수집 시작 (최대 {max_pages}페이지)...")
    records: list[dict] = []
    source_entries: list[dict] = []
    errors: list[str] = []
    base_url = "https://www.jeonnam.go.kr"
    board_id = "M486735"

    for page in range(1, max_pages + 1):
        list_url = f"{base_url}/M486735/boardList.do?menuId=jeonnam0302050300&boardId={board_id}&pageIndex={page}"
        content, _, code = fetch_url(list_url, timeout=20)
        if code != 200 or not content:
            if code != 200:
                errors.append(f"전남 목록 요청 실패 ({code}): {list_url}")
            break

        html_text = content.decode("utf-8", errors="ignore")
        matches = re.findall(r'boardView\.do\?seq=(\d+)[^"]*"\s*title="([^"]+)"', html_text)
        if not matches:
            break

        for seq, title in matches:
            title_clean = clean_text(html.unescape(title))
            dept_m = re.search(r"([가-힣0-9·]+?(?:과|관|팀|단|소|원|소방서|본부|실))\b", title_clean)
            title_dept = dept_m[1] if dept_m else ""

            view_url = f"{base_url}/M486735/boardView.do?seq={seq}&menuId=jeonnam0302050300&boardId={board_id}"
            download_url = f"{base_url}/boardDown.do?boardId={board_id}&seq={seq}&fileLinkTp=F&fileLinkSeq=1"

            dest, sha, byte_cnt = download_to_cache(download_url, cache_dir, f"jn_{board_id}_{seq}")
            if not dest:
                errors.append(f"전남 첨부 다운로드 실패: {download_url}")
                continue

            source_entries.append({
                "region": "전남",
                "institution": "전라남도",
                "department": title_dept or "실과소공개",
                "title": title_clean,
                "viewURL": view_url,
                "downloadURL": download_url,
                "sha256": sha,
                "bytes": byte_cnt,
                "filename": dest.name,
            })

            file_recs, file_errs = parse_document(dest, "전라남도", title_dept, "전남", view_url, f"jn_{seq}", address_index=address_index, cache_dir=cache_dir)
            records.extend(file_recs)
            errors.extend(file_errs)

        time.sleep(0.3)

    log(f"전라남도 수집 완료: 소스 {len(source_entries)}건, 추출 레코드 {len(records)}행, 오류 {len(errors)}건")
    return records, source_entries, errors


def collect_jeonbuk(cache_dir: Path, max_pages: int, lookback_months: int,
                    address_index: AddressIndex | None = None) -> tuple[list[dict], list[dict], list[str]]:
    """전북특별자치도 업무추진비 수집."""
    log(f"전북특별자치도 업무추진비 수집 시작 (최대 {max_pages}페이지)...")
    records: list[dict] = []
    source_entries: list[dict] = []
    errors: list[str] = []
    base_url = "https://www.jeonbuk.go.kr"
    board_id = "BBS_0000029"

    for page in range(1, max_pages + 1):
        list_url = f"{base_url}/board/list.jeonbuk?boardId={board_id}&listRow=50&listCel=1&menuCd=DOM_000000103005000000&paging=ok&startPage={page}"
        content, _, code = fetch_url(list_url, timeout=25)
        if code != 200 or not content:
            if code != 200:
                errors.append(f"전북 목록 요청 실패 ({code}): {list_url}")
            break

        html_text = content.decode("utf-8", errors="ignore")
        matches = re.findall(r'view\.jeonbuk\?[^"]*dataSid=(\d+)[^"]*"\s*title="([^"]+)"', html_text)
        if not matches:
            break

        for data_sid, title in matches:
            title_clean = clean_text(html.unescape(title))
            dept_m = re.search(r"([가-힣0-9·]+?(?:과|관|팀|단|소|원|소방서|본부|실|국))\b", title_clean)
            title_dept = dept_m[1] if dept_m else ""

            view_url = f"{base_url}/board/view.jeonbuk?boardId={board_id}&menuCd=DOM_000000103005000000&dataSid={data_sid}"

            view_content, _, vcode = fetch_url(view_url, timeout=15)
            if vcode != 200 or not view_content:
                errors.append(f"전북 뷰 페이지 요청 실패 ({vcode}): {view_url}")
                continue
            view_html = view_content.decode("utf-8", errors="ignore")
            dl_m = re.search(r'href="(/board/download\.jeonbuk\?[^"]+fileSid=\d+[^"]*)"', view_html)
            if not dl_m:
                errors.append(f"전북 다운로드 링크 미발견: dataSid={data_sid}")
                continue

            # 필수: HTML &amp; 엔티티를 unescape하여 실제 파일 다운로드 URL 파라미터 복구
            clean_href = html.unescape(dl_m[1])
            download_url = urllib.parse.urljoin(base_url, clean_href)

            dest, sha, byte_cnt = download_to_cache(download_url, cache_dir, f"jb_{data_sid}")
            if not dest:
                errors.append(f"전북 첨부 다운로드 실패 (HTML 오류 페이지 등 거부): {download_url}")
                continue

            source_entries.append({
                "region": "전북",
                "institution": "전북특별자치도",
                "department": title_dept or "도청공개",
                "title": title_clean,
                "viewURL": view_url,
                "downloadURL": download_url,
                "sha256": sha,
                "bytes": byte_cnt,
                "filename": dest.name,
            })

            file_recs, file_errs = parse_document(dest, "전북특별자치도", title_dept, "전북", view_url, f"jb_{data_sid}", address_index=address_index, cache_dir=cache_dir)
            records.extend(file_recs)
            errors.extend(file_errs)

        time.sleep(0.3)

    log(f"전북특별자치도 수집 완료: 소스 {len(source_entries)}건, 추출 레코드 {len(records)}행, 오류 {len(errors)}건")
    return records, source_entries, errors


def _is_gwangsan_item_before_cutoff(detail_nm: str, era: str, reg_dt: str, cutoff_date: dt.date) -> bool:
    """광산구청 게시물이 2024년 9월(또는 cutoff_date) 이전 내역인지 판별한다."""
    # 1. 제목에서 연도 및 분기 확인: e.g. "2024년 2분기", "2023년 4분기"
    m_yq = re.search(r"(20\d\d)년?\s*(?:도\s*)?([1-4])분기", detail_nm)
    if m_yq:
        y, q = int(m_yq[1]), int(m_yq[2])
        if y < cutoff_date.year:
            return True
        cutoff_quarter = (cutoff_date.month - 1) // 3 + 1
        if y == cutoff_date.year and q < cutoff_quarter:
            return True
        return False

    # 2. 제목에서 연도 및 월 확인: e.g. "2024.6월", "2024년 8월"
    m_ym = re.search(r"(20\d\d)[.\-년\s]+(\d{1,2})월", detail_nm)
    if m_ym:
        y, m = int(m_ym[1]), int(m_ym[2])
        if y < cutoff_date.year:
            return True
        if y == cutoff_date.year and m < cutoff_date.month:
            return True
        return False

    # 3. 제목에서 연도만 표기된 경우: e.g. "2023년도 업무추진비"
    m_y = re.search(r"\b(20\d\d)년", detail_nm)
    if m_y:
        y = int(m_y[1])
        if y < cutoff_date.year:
            return True

    # 4. 등록일자/공표일자가 cutoff_date 이전이고 제목에 3분기/9월 이상 표기가 없는 경우
    for date_str in (era, reg_dt):
        d = parse_date(date_str)
        if d:
            if d < cutoff_date:
                return True
            break

    return False


def collect_gwangju(cache_dir: Path, max_pages: int, lookback_months: int,
                    address_index: AddressIndex | None = None) -> tuple[list[dict], list[dict], list[str]]:
    """광주광역시 자치구(광산구, 서구) 및 전남-광주 공동혁신도시 업무추진비 수집."""
    log(f"광주광역시 자치구 및 공동기관 수집 시작...")
    records: list[dict] = []
    source_entries: list[dict] = []
    errors: list[str] = []

    # 1. 광주 광산구청 (공식 정보공개 GET API)
    log(f"광주 광산구청 업무추진비 수집 (최대 {max_pages}페이지)...")
    gwangsan_base = "https://www.gwangsan.go.kr"
    cutoff_date = dt.date(2024, 9, 1)

    for page in range(1, max_pages + 1):
        list_url = (
            f"{gwangsan_base}/getInfoOpenList.do"
            f"?infoOpenType=S&infoOpenCtgryUpper=O130000&infoOpenCtgry=&infoOpenSn="
            f"&recordCnt=50&movePage={page}&infoOpen=D&infoOpenCtgryTy=S"
        )
        content, _, code = fetch_url(list_url, timeout=20)
        if code != 200 or not content:
            if code != 200:
                errors.append(f"광산구 목록 요청 실패 ({code}): {list_url}")
            break

        try:
            list_data = json.loads(content.decode("utf-8", errors="ignore"))
        except Exception as e:
            errors.append(f"광산구 목록 JSON 파싱 실패 ({e}): {list_url}")
            break

        if list_data.get("error") != "N":
            errors.append(f"광산구 목록 API 오류 응답: error={list_data.get('error')}")
            break

        data_map = list_data.get("dataMap")
        if not isinstance(data_map, dict):
            break

        items = data_map.get("list")
        if not items or not isinstance(items, list):
            break

        page_has_within_cutoff = False

        for item in items:
            if not isinstance(item, dict):
                continue

            detail_nm = clean_text(item.get("detailNm") or "")
            # 업무추진비 detailNm 필터 (외 다른 내역 제외)
            if "업무추진비" not in detail_nm:
                continue

            era = clean_text(item.get("publictEra") or "")
            reg_dt = clean_text(item.get("regDt") or "")

            if _is_gwangsan_item_before_cutoff(detail_nm, era, reg_dt, cutoff_date):
                continue

            page_has_within_cutoff = True

            sn_val = item.get("sn")
            detail_sn_val = item.get("detailSn")
            if sn_val is None or detail_sn_val is None:
                continue
            try:
                sn_str = str(int(float(sn_val)))
                detail_sn_str = str(int(float(detail_sn_val)))
            except (ValueError, TypeError):
                continue

            detail_url = f"{gwangsan_base}/getInfoOpenData.do?sn={sn_str}&detailSn={detail_sn_str}&infoOpen=D"
            d_content, _, d_code = fetch_url(detail_url, timeout=20)
            if d_code != 200 or not d_content:
                errors.append(f"광산구청 상세 요청 실패 ({d_code}): {detail_url}")
                continue

            try:
                detail_resp = json.loads(d_content.decode("utf-8", errors="ignore"))
            except Exception as e:
                errors.append(f"광산구청 상세 JSON 파싱 실패 ({e}): {detail_url}")
                continue

            detail_map = detail_resp.get("dataMap")
            if not isinstance(detail_map, dict):
                errors.append(f"광산구청 상세 응답 dataMap 부재: {detail_url}")
                continue

            item_dept = clean_text(item.get("deptNm") or item.get("publictDept") or "")
            raw_dept = (
                clean_text(detail_map.get("deptNm") or "")
                or clean_text(detail_map.get("publictDept") or "")
                or item_dept
            )
            clean_dept = clean_dept_name(raw_dept)
            if not clean_dept:
                dm = re.search(r"[(（]([가-힣0-9·]+)[)）]", detail_nm)
                if dm:
                    clean_dept = clean_dept_name(dm[1])

            # 부서명 누락 시 엄격 배제 (안전 규격 준수)
            if not clean_dept:
                errors.append(f"광산구청 부서명 누락/미식별 제외: sn={sn_str}, detailSn={detail_sn_str}")
                continue

            file_list = detail_map.get("fileList") or []
            if not isinstance(file_list, list) or not file_list:
                continue

            for f_idx, f_entry in enumerate(file_list, start=1):
                if not isinstance(f_entry, dict):
                    continue
                file_url = f_entry.get("fileUrl")
                if not file_url or not isinstance(file_url, str):
                    continue

                download_url = urllib.parse.urljoin(gwangsan_base, file_url)
                file_key = f"gj_gs_{sn_str}_{detail_sn_str}_{f_idx}"
                dest, sha, byte_cnt = download_to_cache(
                    download_url, cache_dir, file_key
                )
                if not dest:
                    errors.append(f"광산구 첨부 다운로드 실패: {download_url}")
                    continue

                # 개인정보를 배제하고 안전 메타/해시만 출처 카탈로그에 기록
                source_entries.append({
                    "region": "광주",
                    "institution": "광산구청",
                    "department": clean_dept,
                    "title": detail_nm,
                    "viewURL": detail_url,
                    "downloadURL": download_url,
                    "sha256": sha,
                    "bytes": byte_cnt,
                    "filename": dest.name,
                })

                file_recs, file_errs = parse_document(
                    dest, "광산구청", clean_dept, "광주", detail_url,
                    file_key,
                    address_index=address_index, cache_dir=cache_dir
                )
                records.extend(file_recs)
                errors.extend(file_errs)

            time.sleep(0.2)

        if not page_has_within_cutoff:
            log(f"광산구청: 2024년 9월 이전 내역에 도달하여 수집 종료 (page={page})")
            break

        time.sleep(0.3)

    # 2. 광주 서구청
    log("광주 서구청 업무추진비 수집...")
    seogu_base = "https://www.seogu.gwangju.kr"
    seogu_consecutive_failures = 0
    for page in range(1, min(max_pages, 5) + 1):
        if seogu_consecutive_failures >= 2:
            log("서구청 첨부 연속 다운로드 실패로 조기 중단")
            break
        list_url = f"{seogu_base}/menu.es?mid=a10518030100&nPage={page}"
        content, _, code = fetch_url(list_url, timeout=10, retries=1)
        if code != 200 or not content:
            if code != 200:
                errors.append(f"서구 목록 요청 실패 ({code})")
            break
        html_text = content.decode("utf-8", errors="ignore")
        dl_matches = re.findall(r'href="(/openInfoDataFileDownload\.es\?[^"]+)"', html_text)
        if not dl_matches:
            break

        for dl_path in dl_matches:
            if seogu_consecutive_failures >= 2:
                break
            clean_dl = html.unescape(dl_path)
            download_url = urllib.parse.urljoin(seogu_base, clean_dl)
            dest, sha, byte_cnt = download_to_cache(download_url, cache_dir, f"gj_seogu_{len(source_entries)}")
            if not dest:
                errors.append(f"서구 첨부 다운로드 실패: {download_url}")
                seogu_consecutive_failures += 1
                continue
            seogu_consecutive_failures = 0

            source_entries.append({
                "region": "광주",
                "institution": "광주광역시 서구",
                "department": "서구청공개",
                "title": "서구청 업무추진비",
                "viewURL": list_url,
                "downloadURL": download_url,
                "sha256": sha,
                "bytes": byte_cnt,
                "filename": dest.name,
            })

            file_recs, file_errs = parse_document(dest, "광주광역시 서구", "", "광주", list_url, f"gj_seogu_{dest.stem}", address_index=address_index, cache_dir=cache_dir)
            records.extend(file_recs)
            errors.extend(file_errs)

    # 3. 전남-광주 공동혁신도시 (실제 HTML 링크 파싱)
    log("전남-광주 공동혁신도시 업무추진비 수집...")
    jngj_base = "https://www.jeonnam-gwangju.go.kr"
    jngj_consecutive_failures = 0
    for page in range(1, min(max_pages, 6) + 1):
        if jngj_consecutive_failures >= 2:
            log("공동혁신도시 연속 실패로 조기 중단")
            break
        list_url = f"{jngj_base}/boardList.do?boardId=JG_0000000018&pageId=jngj50&movePage={page}&recordCnt=10"
        content, _, code = fetch_url(list_url, timeout=10, retries=1)
        if code != 200 or not content:
            if code != 200:
                errors.append(f"공동혁신도시 목록 요청 실패 ({code})")
            break
        html_text = content.decode("utf-8", errors="ignore")
        matches = re.findall(r'href="(/boardView\.do\?[^"]*seq=\d+[^"]*)"', html_text)
        if not matches:
            break
        for vm in matches:
            if jngj_consecutive_failures >= 2:
                break
            view_url = urllib.parse.urljoin(jngj_base, html.unescape(vm))
            v_content, _, v_code = fetch_url(view_url, timeout=10, retries=1)
            if v_code != 200 or not v_content:
                jngj_consecutive_failures += 1
                continue
            v_html = v_content.decode("utf-8", errors="ignore")
            dl_m = re.search(r'href="(/fileDownload\.do\?[^"]+)"', v_html)
            if not dl_m:
                jngj_consecutive_failures += 1
                continue
            download_url = urllib.parse.urljoin(jngj_base, html.unescape(dl_m[1]))

            dest, sha, byte_cnt = download_to_cache(download_url, cache_dir, f"jngj_{len(source_entries)}")
            if not dest:
                errors.append(f"공동혁신도시 첨부 다운로드 실패: {download_url}")
                jngj_consecutive_failures += 1
                continue
            jngj_consecutive_failures = 0

            source_entries.append({
                "region": "광주",
                "institution": "전남광주통합특별시",
                "department": "혁신도시공동기관",
                "title": f"공동혁신도시 업무추진비",
                "viewURL": view_url,
                "downloadURL": download_url,
                "sha256": sha,
                "bytes": byte_cnt,
                "filename": dest.name,
            })

            file_recs, file_errs = parse_document(dest, "전남광주통합특별시", "", "광주", view_url, f"jngj_{dest.stem}", address_index=address_index, cache_dir=cache_dir)
            records.extend(file_recs)
            errors.extend(file_errs)

    log(f"광주 권역 수집 완료: 소스 {len(source_entries)}건, 추출 레코드 {len(records)}행, 오류 {len(errors)}건")
    return records, source_entries, errors


# --- 일자 유틸리티 -------------------------------------------------------------
def subtract_months(d: dt.date, months: int) -> dt.date:
    """정확한 달력 기준 개월 차감 계산 (예: 2026-09-13 에서 6개월 차감 -> 2026-03-13)."""
    y = d.year
    m = d.month - months
    while m < 1:
        m += 12
        y -= 1
    max_day = calendar.monthrange(y, m)[1]
    return dt.date(y, m, min(d.day, max_day))


# --- 집계 및 검증 ---------------------------------------------------------------
def aggregate_and_validate(records: list[dict],
                           raw_counts_by_region: dict[str, int | None] | None = None,
                           extracted_counts_by_region: dict[str, int] | None = None,
                           target_regions: list[str] | None = None,
                           lookback_months: int = 24,
                           fresh_months: int = 6) -> tuple[list[dict], dict, dict]:
    """레코드 집계 및 3개 부서·최근 6개월(달력 기준 2026-03-13) 조건 검증."""
    today = MAX_FUTURE_DATE
    fresh_start = subtract_months(today, fresh_months)
    lookback_start = subtract_months(today, lookback_months)
    active_regions = [r for r in (target_regions or REGIONS) if r in REGIONS]
    extracted_counts = extracted_counts_by_region or Counter(r.get("region") for r in records)

    seen_docs = set()
    places: dict[tuple, dict] = {}
    rejected = Counter()

    valid_records: list[dict] = []

    for r in records:
        r.pop("amountKRW", None)
        if r.get("region") == "제주":
            r["institution"] = "제주특별자치도"

        if any(k not in r for k in REQUIRED_RECORD_KEYS):
            rejected["필수키누락"] += 1
            continue

        region = REGION_ALIASES.get(r["region"])
        if region not in active_regions:
            rejected["권역외지역"] += 1
            continue

        doc_key = (r["sourceURL"], r["recordID"])
        if doc_key in seen_docs:
            rejected["중복(원문URL+recordID)"] += 1
            continue
        seen_docs.add(doc_key)

        pay_date = dt.date.fromisoformat(r["paymentDate"])
        if pay_date > today:
            rejected["미래날짜"] += 1
            continue
        if pay_date < lookback_start:
            rejected["오래된날짜"] += 1
            continue

        parsed = parse_address(r["address"], region)
        if not parsed:
            rejected["주소미확인"] += 1
            continue
        clean_addr, addr_key, sigungu = parsed

        dept_k = dept_key(r["institution"], r["department"])
        key = (region, norm_text(r["name"]), addr_key)
        p = places.setdefault(key, {
            "region": region, "name": r["name"], "address": clean_addr,
            "sigungu": sigungu, "records": [], "depts": set(),
        })
        p["records"].append(r)
        p["depts"].add(dept_k)
        valid_records.append(r)

    eligible_places = {}
    for key, p in places.items():
        recs = sorted(p["records"], key=lambda x: x["paymentDate"], reverse=True)
        latest_date = dt.date.fromisoformat(recs[0]["paymentDate"])
        dept_count = len(p["depts"])

        is_eligible = latest_date >= fresh_start
        p["departmentCount"] = dept_count
        p["recordCount"] = len(recs)
        p["lastPaymentDate"] = recs[0]["paymentDate"]
        p["firstPaymentDate"] = recs[-1]["paymentDate"]
        p["isEligible"] = is_eligible
        if is_eligible:
            eligible_places[key] = p

    stats_by_region = {}
    for reg in active_regions:
        reg_recs = [r for r in valid_records if r["region"] == reg]
        reg_places = [p for p in places.values() if p["region"] == reg]
        reg_eligible = [p for p in eligible_places.values() if p["region"] == reg]
        insts = sorted({r["institution"] for r in reg_recs})
        dates = [r["paymentDate"] for r in reg_recs]
        raw_cnt = raw_counts_by_region.get(reg) if raw_counts_by_region else None
        ext_cnt = extracted_counts.get(reg, len(reg_recs))
        stats_by_region[reg] = {
            "rawRowCount": raw_cnt,
            "extractedMealAddressRows": ext_cnt,
            "validRecordCount": len(reg_recs),
            "candidatePlaceCount": len(reg_places),
            "eligiblePlaceCount": len(reg_eligible),
            "institutionCount": len(insts),
            "institutions": insts,
            "windowStart": min(dates) if dates else None,
            "windowEnd": max(dates) if dates else None,
        }

    return valid_records, stats_by_region, dict(rejected)


# --- 진단 모드 ------------------------------------------------------------------
DIAGNOSTIC_ROOTS = [
    ("광산구청_사전정보공표", "https://www.gwangsan.go.kr/contentsView.do?pageId=www159"),
    ("서구청_업무추진비", "https://www.seogu.gwangju.kr/menu.es?mid=a10518030100"),
    ("공동혁신도시_게시판", "https://www.jeonnam-gwangju.go.kr/boardList.do?boardId=JG_0000000018&pageId=jngj50"),
    ("전북_업무추진비", "https://www.jeonbuk.go.kr/board/list.jeonbuk?boardId=BBS_0000029&menuCd=DOM_000000103005000000"),
    ("전남_실과소", "https://www.jeonnam.go.kr/M486735/boardList.do?menuId=jeonnam0302050300&boardId=M486735"),
    ("제주_본청", "https://www.jeju.go.kr/open/open/work/work2.htm?category=1409"),
]


def run_diagnostic_html(task_cache_dir: Path) -> None:
    """공식 포털 루트 HTML을 내려받아 /tmp 작업 캐시에만 저장하고 엔드포인트 및 링크 구조를 진단한다."""
    diag_dir = Path("/tmp/whattoeat-southwest-diagnostic")
    diag_dir.mkdir(parents=True, exist_ok=True)
    print(f"=== 남서부 권역 HTML 엔드포인트 진단 (/tmp 작업 캐시: {diag_dir}) ===")

    for label, url in DIAGNOSTIC_ROOTS:
        print(f"\n[진단 수집: {label}] {url}")
        raw, hdrs, code = fetch_url(url, timeout=20)
        dest = diag_dir / f"{label}.html"
        if code == 200 and raw:
            dest.write_bytes(raw)
            html_text = raw.decode("utf-8", errors="ignore")
            forms = re.findall(r'<form\s+[^>]*action="([^"]+)"', html_text, re.I)
            iframes = re.findall(r'<iframe\s+[^>]*src="([^"]+)"', html_text, re.I)
            hrefs = re.findall(r'href="([^"]+)"', html_text, re.I)
            cand_links = [h for h in hrefs if any(k in h.lower() for k in ("download", "view", "board", ".do", ".es", ".htm"))]
            print(f"  -> 저장: {dest} ({len(raw)} bytes)")
            print(f"  -> form={len(forms)}, iframe={len(iframes)}, 후보 링크={len(cand_links)}")
            for lk in cand_links[:5]:
                print(f"     샘플 링크: {html.unescape(lk)}")
        else:
            print(f"  -> 요청 실패 (HTTP {code}): {url}")


def run_diagnostics(cache_dir: Path) -> None:
    """캐시 디렉토리의 파일들을 스캔하여 포맷과 파싱 상태를 진단한다."""
    print(f"=== 남서부 권역 데이터 수집기 진단 (캐시 경로: {cache_dir}) ===")
    if not cache_dir.exists():
        print("캐시 디렉토리가 존재하지 않습니다.")
        return

    files = sorted(cache_dir.glob("*"))
    print(f"캐시 내 총 파일 수: {len(files)}개")

    xlsx_files = [f for f in files if f.suffix == ".xlsx"]
    pdf_files = [f for f in files if f.suffix == ".pdf"]
    bin_files = [f for f in files if f.suffix == ".bin"]
    html_files = [f for f in files if f.suffix == ".html"]

    print(f"- XLSX: {len(xlsx_files)}개")
    print(f"- PDF: {len(pdf_files)}개")
    print(f"- BIN: {len(bin_files)}개")
    print(f"- HTML: {len(html_files)}개")

    for bf in bin_files[:5]:
        raw = bf.read_bytes()
        sig = raw[:8].hex()
        is_html = is_html_content(raw)
        print(f"\n[BIN 파일 진단: {bf.name}] ({len(raw)}B, 시그니처: {sig}, is_html={is_html})")
        recs, errs = parse_document(bf, "진단기관", "", "제주", "https://example.com", bf.stem, cache_dir=cache_dir)
        print(f"  추출 레코드: {len(recs)}행, 오류: {errs}")

    for xf in xlsx_files[:3]:
        print(f"\n[XLSX 진단: {xf.name}]")
        recs, errs = parse_document(xf, "진단기관", "", "제주", "https://example.com", xf.stem, cache_dir=cache_dir)
        print(f"  추출 레코드: {len(recs)}행, 오류: {errs}")

    for pf in pdf_files[:3]:
        print(f"\n[PDF 진단: {pf.name}]")
        recs, errs = parse_document(pf, "진단기관", "", "전북", "https://example.com", pf.stem, cache_dir=cache_dir)
        print(f"  추출 레코드: {len(recs)}행, 오류: {errs}")


# --- 메인 실행 함수 -------------------------------------------------------------
def main() -> int:
    global MAX_FUTURE_DATE
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DATA_DIR,
                        help=f"산출물 저장 디렉토리 (기본: {DEFAULT_DATA_DIR})")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR,
                        help=f"원문 다운로드 캐시 디렉토리 (기본: {DEFAULT_CACHE_DIR})")
    parser.add_argument("--address-dir", type=Path, default=DEFAULT_ADDRESS_DIR,
                        help=f"공식 data.go.kr 15083033 상가정보 CSV 디렉토리 (기본: {DEFAULT_ADDRESS_DIR})")
    parser.add_argument("--max-pages", type=int, default=30,
                        help="각 공식 게시판별 최대 수집 페이지 수 (기본: 30)")
    parser.add_argument("--from-reduced", action="store_true",
                        help="기존에 생성된 실제 온라인 수집 레코드로부터 오프라인 재집계")
    parser.add_argument("--source-manifest", action="append", dest="source_manifests", default=[],
                        type=Path,
                        help="오프라인 재파싱을 위한 검증 출처 매니페스트(JSON) 파일 경로 (반복 지정 가능)")
    parser.add_argument("--regions", type=str, default="광주,전북,전남,제주",
                        help="수집할 지역 목록 (쉼표 구분)")
    parser.add_argument("--lookback-months", type=int, default=18,
                        help="집계 기간 달력월 수 (기본: 24)")
    parser.add_argument("--fresh-months", type=int, default=18,
                        help="최근 결제 인정 달력월 수 (기본: 6)")
    parser.add_argument("--diagnostics", action="store_true",
                        help="캐시된 파일들의 헤더 및 파싱 상태 진단")
    parser.add_argument("--diagnostic-html", action="store_true",
                        help="공식 포털 HTML을 /tmp 작업 캐시에만 저장하고 엔드포인트 구조 진단")
    parser.add_argument("--as-of", type=dt.date.fromisoformat, default=dt.date.today())
    args = parser.parse_args()
    MAX_FUTURE_DATE = args.as_of

    if args.source_manifests and args.from_reduced:
        log("오류: --source-manifest 와 --from-reduced 는 상호 배타적입니다. 동시에 지정할 수 없습니다.")
        return 1

    if args.diagnostic_html:
        run_diagnostic_html(args.cache_dir)
        return 0

    if args.diagnostics:
        run_diagnostics(args.cache_dir)
        return 0

    target_regions = [r.strip() for r in args.regions.split(",") if r.strip() in REGIONS]
    if not target_regions:
        target_regions = list(REGIONS)
    log(f"수집 대상 지역: {target_regions}")

    all_records: list[dict] = []
    sources_catalog: dict[str, list[dict]] = defaultdict(list)
    downloaded_meta: list[dict] = []
    collection_errors: list[str] = []
    raw_counts: dict[str, int | None] = {r: None for r in target_regions}
    extracted_counts: dict[str, int] = Counter()

    records_file = args.output_dir / "records.jsonl.gz"
    reduced_file = args.output_dir / "reduced-records.jsonl.gz"

    if args.source_manifests:
        log(f"오프라인 매니페스트 재생 모드: {len(args.source_manifests)}개 매니페스트에서 캐시 문서를 재파싱합니다.")
        address_index = AddressIndex(args.address_dir)
        address_index.load()

        seen_manifest_sources: set[tuple[str, str]] = set()
        for manifest_path in args.source_manifests:
            if not manifest_path.exists():
                err = f"매니페스트 파일 없음: {manifest_path}"
                log(err)
                collection_errors.append(err)
                continue
            try:
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as e:
                err = f"매니페스트 JSON 파싱 실패 ({manifest_path}): {e}"
                log(err)
                collection_errors.append(err)
                continue

            regions_data = manifest_data.get("regions") if isinstance(manifest_data.get("regions"), dict) else manifest_data
            if not isinstance(regions_data, dict):
                err = f"매니페스트 regions 형식 부적합: {manifest_path}"
                log(err)
                collection_errors.append(err)
                continue

            for reg_key, entries in regions_data.items():
                norm_reg = REGION_ALIASES.get(reg_key, reg_key)
                if norm_reg not in target_regions or not isinstance(entries, list):
                    continue

                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    download_url = clean_text(entry.get("downloadURL") or "")
                    expected_sha = clean_text(entry.get("sha256") or "").lower()
                    filename = clean_text(entry.get("filename") or "")
                    doc_url = clean_text(entry.get("url") or entry.get("viewURL") or download_url)
                    dept = clean_text(entry.get("department") or "")
                    title = clean_text(entry.get("title") or "")

                    # downloadURL + sha256 기준 중복 제거
                    dedupe_key = (download_url or doc_url or filename, expected_sha)
                    if dedupe_key in seen_manifest_sources:
                        continue
                    seen_manifest_sources.add(dedupe_key)

                    if not filename:
                        collection_errors.append(f"매니페스트 항목 filename 누락: region={norm_reg}, title={title}")
                        continue

                    file_path = args.cache_dir / filename
                    if not file_path.exists():
                        collection_errors.append(f"캐시 파일 누락 ({norm_reg}): {file_path}")
                        continue

                    try:
                        file_bytes = file_path.read_bytes()
                    except Exception as e:
                        collection_errors.append(f"캐시 파일 읽기 실패 ({filename}): {e}")
                        continue

                    actual_sha = hashlib.sha256(file_bytes).hexdigest().lower()
                    if expected_sha and actual_sha != expected_sha:
                        collection_errors.append(f"SHA256 해시 불일치 ({filename}): 매니페스트={expected_sha}, 실제={actual_sha}")
                        continue

                    # documentID derive filename stem removing final _<16 hex digest> (e.g jeju_1409_2033845)
                    stem = file_path.stem
                    m = re.match(r"^(.*?)(?:_[0-9a-fA-F]{16})$", stem)
                    doc_id = m[1] if m else stem

                    # 제주 권역은 총괄 기관 '제주특별자치도'로 일관되게 적용
                    if norm_reg == "제주":
                        inst_name = "제주특별자치도"
                    else:
                        inst_name = clean_text(entry.get("institution") or "").replace("전남광주통합특별시 공동기관", "전남광주통합특별시")

                    source_entry = {
                        "institution": inst_name,
                        "department": dept,
                        "url": doc_url,
                        "downloadURL": download_url,
                        "title": title,
                        "sha256": actual_sha,
                        "bytes": len(file_bytes),
                        "filename": filename,
                    }
                    sources_catalog[norm_reg].append(source_entry)
                    downloaded_meta.append(source_entry)

                    file_recs, file_errs = parse_document(
                        file_path,
                        default_inst=inst_name,
                        default_dept=dept,
                        default_region=norm_reg,
                        source_url=doc_url,
                        doc_key=doc_id,
                        address_index=address_index,
                        cache_dir=args.cache_dir,
                    )

                    for rec in file_recs:
                        rec.pop("amountKRW", None)
                        if norm_reg == "제주":
                            rec["institution"] = "제주특별자치도"

                    all_records.extend(file_recs)
                    collection_errors.extend(file_errs)
                    extracted_counts[norm_reg] += len(file_recs)

        for reg in target_regions:
            raw_counts[reg] = None

    elif args.from_reduced:
        source_path = None
        for p in (reduced_file, records_file, args.output_dir / "records.jsonl", args.output_dir / "reduced-records.jsonl"):
            if p.exists() and p.stat().st_size > 0:
                source_path = p
                break
        if source_path is None:
            log("오류: --from-reduced 를 사용할 수 없습니다. 실제 온라인 수집으로 생성된 파일이 없습니다.")
            return 1
        log(f"오프라인 모드: {source_path} 에서 레코드를 불러옵니다.")
        opener = gzip.open if source_path.suffix == ".gz" else open
        with opener(source_path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    rec.pop("amountKRW", None)
                    reg = rec.get("region")
                    if reg:
                        if reg == "제주":
                            rec["institution"] = "제주특별자치도"
                        if reg in target_regions:
                            all_records.append(rec)
                            extracted_counts[reg] += 1

        for reg in target_regions:
            raw_counts[reg] = None

        # 기존 sources.json 이 있으면 출처 메타데이터 보존
        sources_file = args.output_dir / "sources.json"
        if sources_file.exists():
            try:
                prev_sources = json.loads(sources_file.read_text(encoding="utf-8"))
                for reg, s_list in prev_sources.get("regions", {}).items():
                    if reg in target_regions:
                        if reg == "제주":
                            for s in s_list:
                                s["institution"] = "제주특별자치도"
                        sources_catalog[reg].extend(s_list)
                        downloaded_meta.extend(s_list)
            except Exception as e:
                log(f"기존 sources.json 메타 로드 실패 ({e})")
    else:
        address_index = AddressIndex(args.address_dir)
        address_index.load()

        if "제주" in target_regions:
            jeju_recs, jeju_sources, jeju_errs = collect_jeju(args.cache_dir, args.max_pages, args.lookback_months, address_index)
            for r in jeju_recs:
                r.pop("amountKRW", None)
                r["institution"] = "제주특별자치도"
            all_records.extend(jeju_recs)
            extracted_counts["제주"] = len(jeju_recs)
            raw_counts["제주"] = None
            sources_catalog["제주"].extend(jeju_sources)
            downloaded_meta.extend(jeju_sources)
            collection_errors.extend(jeju_errs)

        if "전남" in target_regions:
            jn_recs, jn_sources, jn_errs = collect_jeonnam(args.cache_dir, args.max_pages, args.lookback_months, address_index)
            for r in jn_recs:
                r.pop("amountKRW", None)
            all_records.extend(jn_recs)
            extracted_counts["전남"] = len(jn_recs)
            raw_counts["전남"] = None
            sources_catalog["전남"].extend(jn_sources)
            downloaded_meta.extend(jn_sources)
            collection_errors.extend(jn_errs)

        if "전북" in target_regions:
            jb_recs, jb_sources, jb_errs = collect_jeonbuk(args.cache_dir, args.max_pages, args.lookback_months, address_index)
            for r in jb_recs:
                r.pop("amountKRW", None)
            all_records.extend(jb_recs)
            extracted_counts["전북"] = len(jb_recs)
            raw_counts["전북"] = None
            sources_catalog["전북"].extend(jb_sources)
            downloaded_meta.extend(jb_sources)
            collection_errors.extend(jb_errs)

        if "광주" in target_regions:
            gj_recs, gj_sources, gj_errs = collect_gwangju(args.cache_dir, args.max_pages, args.lookback_months, address_index)
            for r in gj_recs:
                r.pop("amountKRW", None)
            all_records.extend(gj_recs)
            extracted_counts["광주"] = len(gj_recs)
            raw_counts["광주"] = None
            sources_catalog["광주"].extend(gj_sources)
            downloaded_meta.extend(gj_sources)
            collection_errors.extend(gj_errs)

    # 집계 및 검증
    valid_records, region_stats, rejected_stats = aggregate_and_validate(
        all_records,
        raw_counts_by_region=raw_counts,
        extracted_counts_by_region=extracted_counts,
        target_regions=target_regions,
        lookback_months=args.lookback_months,
        fresh_months=args.fresh_months,
    )

    log(f"총 추출 행: {len(all_records)}행 -> 유효 검증 행: {len(valid_records)}행")
    for reg, st in region_stats.items():
        raw_label = f"원시 {st['rawRowCount']}행, " if st['rawRowCount'] is not None else ""
        log(f"  [{reg}] {raw_label}추출 {st['extractedMealAddressRows']}행, 유효행 {st['validRecordCount']}건, 후보식당 {st['candidatePlaceCount']}곳, 조건충족 식당 {st['eligiblePlaceCount']}곳 (기관 {st['institutionCount']}개소)")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    if not args.from_reduced:
        unresolved_seen = set()
        with gzip.open(args.output_dir / "unresolved-address-records.jsonl.gz", "wt", encoding="utf-8") as output:
            for row in UNRESOLVED:
                key = (row["sourceURL"], row["recordID"])
                if key in unresolved_seen:
                    continue
                unresolved_seen.add(key)
                row["name"] = re.sub(r"(?:0\d{1,2})[- )]?[0-9]{3,4}[- ]?[0-9]{4}", "", row["name"]).strip()
                row["name"] = PERSON_SUFFIX_RE.sub("", PERSON_PREFIX_RE.sub("", row["name"]))
                output.write(json.dumps(row, ensure_ascii=False) + "\n")

    with gzip.open(records_file, "wt", encoding="utf-8") as f:
        for r in valid_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    log(f"결제 기록 저장 완료: {records_file} ({len(valid_records)}행)")

    with gzip.open(reduced_file, "wt", encoding="utf-8") as f:
        for r in valid_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 전체 상태 판정: 대상 지역 모두 유효 행이 있고 수집 에러가 없을 때만 completed
    has_valid_in_all = all(region_stats[r]["validRecordCount"] > 0 for r in target_regions)
    if has_valid_in_all and not collection_errors:
        shard_status = "completed"
    elif any(region_stats[r]["validRecordCount"] > 0 for r in target_regions) or collection_errors:
        shard_status = "partial"
    else:
        shard_status = "notRun"

    official_portals_all = {
        "광주": [
            "https://www.gwangsan.go.kr/contentsView.do?pageId=www159",
            "https://www.seogu.gwangju.kr/menu.es?mid=a10518030100",
            "https://www.jeonnam-gwangju.go.kr/boardList.do?boardId=JG_0000000018&pageId=jngj50"
        ],
        "전북": [
            "https://www.jeonbuk.go.kr/board/list.jeonbuk?boardId=BBS_0000029&menuCd=DOM_000000103005000000"
        ],
        "전남": [
            "https://www.jeonnam.go.kr/M486735/boardList.do?menuId=jeonnam0302050300&boardId=M486735"
        ],
        "제주": [
            "https://www.jeju.go.kr/open/open/work/work2.htm?category=1409",
            "https://www.jeju.go.kr/open/open/work/work3.htm?category=1002"
        ]
    }

    # sources.json
    sources_payload = {
        "shard": "southwest",
        "status": shard_status,
        "generatedAt": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds") if valid_records else None,
        "downloadedFileCount": len(downloaded_meta),
        "regions": {
            reg: [
                {
                    "institution": s.get("institution", ""),
                    "department": s.get("department", ""),
                    "url": s.get("viewURL", "") or s.get("url", ""),
                    "downloadURL": s.get("downloadURL", ""),
                    "title": s.get("title", ""),
                    "sha256": s.get("sha256", ""),
                    "bytes": s.get("bytes", 0),
                    "filename": s.get("filename", ""),
                }
                for s in sources_catalog.get(reg, [])
            ]
            for reg in target_regions
        },
        "officialPortals": {
            reg: official_portals_all.get(reg, [])
            for reg in target_regions if reg in official_portals_all
        },
        "addressSource": {
            "name": "소상공인시장진흥공단_상가(상권)정보",
            "url": ADDRESS_SOURCE_URL,
            "addressDir": str(args.address_dir),
        }
    }
    sources_file = args.output_dir / "sources.json"
    sources_file.write_text(json.dumps(sources_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"출처 증거 저장 완료: {sources_file}")

    # coverage.json
    coverage_payload = {
        "shard": "southwest",
        "status": shard_status,
        "generatedAt": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds") if valid_records else None,
        "rules": {
            "minPublicMealRecords": 1,
            "departmentRequired": False,
            "freshMonths": args.fresh_months,
            "lookbackMonths": args.lookback_months,
            "freshCutoffDate": subtract_months(MAX_FUTURE_DATE, args.fresh_months).isoformat(),
            "lookbackCutoffDate": subtract_months(MAX_FUTURE_DATE, args.lookback_months).isoformat(),
            "maxPages": args.max_pages,
        },
        "byRegion": {
            reg: {
                "region": reg,
                "status": (
                    "partial" if (region_stats[reg]["validRecordCount"] > 0 and (
                        any(reg in e for e in collection_errors) or
                        any(any(inst in e for inst in region_stats[reg]["institutions"]) for e in collection_errors)
                    ))
                    else ("completed" if region_stats[reg]["validRecordCount"] > 0
                          else ("failed" if any(reg in e for e in collection_errors) else "notRun"))
                ),
                "institutions": region_stats[reg]["institutions"],
                "period": {
                    "start": region_stats[reg]["windowStart"],
                    "end": region_stats[reg]["windowEnd"],
                },
                "rawRowCount": FUNNEL[reg].get("datedPaymentRows", 0),
                "funnel": dict(FUNNEL[reg]),
                "extractedMealAddressRows": region_stats[reg]["extractedMealAddressRows"],
                "acceptedRowCount": region_stats[reg]["validRecordCount"],
                "candidatePlaceCount": region_stats[reg]["candidatePlaceCount"],
                "eligiblePlaceCount": region_stats[reg]["eligiblePlaceCount"],
                "sourceURLs": sources_payload["officialPortals"].get(reg, []),
                "coverageDescription": (
                    f"{reg} 지역 공공기관 {region_stats[reg]['institutionCount']}곳의 공식 업무추진비 공개자료에서 "
                    f"추출된 결제 내역 {region_stats[reg]['extractedMealAddressRows']}건 중 검증된 기록 {region_stats[reg]['validRecordCount']}건을 수용했다."
                    if region_stats[reg]["validRecordCount"] > 0 else (
                        f"{reg} 지역 수집 시도 결과 결함/오류 발생 (추출 {region_stats[reg]['extractedMealAddressRows']}건, 수용 0건)."
                        if region_stats[reg]["extractedMealAddressRows"] > 0 or any(reg in e for e in collection_errors)
                        else "수집 실행 대기 상태."
                    )
                ),
                "limitations": [
                    "지번/도로명 주소 및 건물번호가 온전히 기재되거나 공식 상가정보(15083033)에서 유일하게 해소된 지출 기록만 수용",
                    "카페·음료·간식·상품권 등 비식사성 지출 항목 및 비음식점 상호 엄격 제외",
                    "부서명이 불분명하거나 행정지원부서 등 일반 명칭인 행은 제외",
                ]
            }
            for reg in target_regions
        },
        "rejectedStats": rejected_stats,
        "errors": collection_errors,
    }
    coverage_file = args.output_dir / "coverage.json"
    coverage_file.write_text(json.dumps(coverage_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"커버리지 정보 저장 완료: {coverage_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
