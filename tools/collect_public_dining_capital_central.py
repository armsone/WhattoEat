#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect_public_dining_capital_central.py

Official public dining expenses collector for:
  - 경기 (Gyeonggi)
  - 인천 (Incheon)
  - 대전 (Daejeon)
  - 세종 (Sejong)
  - 충북 (Chungbuk)
  - 충남 (Chungnam)

Collects official government expense meal payment records from municipal disclosures,
using a rolling 18-month window and at least one verified public-institution meal payment.
Department identity is optional metadata, never an eligibility gate.

Features:
  - Strict privacy sanitation: NEVER persists personal names, participant names, purposes, phones.
  - Full numbered road/lot addresses only: never invents addresses.
  - Preserves address hyphens and prevents chain branch collisions.
  - Excludes future dates (> 2026-09-13) and non-meal expenditures.
  - Computes and records SHA-256 for all downloaded source files.
  - Supports XLS conversion to XLSX via headless LibreOffice (soffice) with unique profile.
  - Diagnostic mode (--diagnostic) to inspect probe files and dump headers/sample records.
  - Outputs: sources.json, coverage.json, records.jsonl.gz, capital-central-window.csv.gz.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import gzip
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Dict, List, Optional, Set, Tuple

# Optional third-party packages available in codex-primary-runtime
try:
    import openpyxl
except ImportError:
    openpyxl = None

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

from pathlib import Path
import io
import calendar
from zoneinfo import ZoneInfo
_today = datetime.datetime.now(ZoneInfo("Asia/Seoul")).date()
_month_index = _today.year * 12 + _today.month - 1 - 18
_window_year, _window_month0 = divmod(_month_index, 12)
CURRENT_DATE = _today.isoformat()
WINDOW_START_DATE = datetime.date(_window_year, _window_month0 + 1, min(_today.day, calendar.monthrange(_window_year, _window_month0 + 1)[1])).isoformat()
RECENT_MEAL_START = WINDOW_START_DATE

TARGET_REGIONS = {"경기", "인천", "대전", "세종", "충북", "충남"}
CACHE_SOURCE_INDEX = {}
DEFAULT_ADDRESS_DIR = "/tmp/whattoeat-sbiz-addresses"
SBIZ_DATA_SOURCE_URL = "https://www.data.go.kr/data/15083033/fileData.do"
SBIZ_SNAPSHOT_DATE = "2026-06-30"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

DEFAULT_SOFFICE = (
    "/Users/armsone/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/soffice"
)

# ----------------------------------------------------------------------
# Region metadata & Institution definitions
# ----------------------------------------------------------------------
REGION_CONFIGS = {
    "gg": {
        "region_code": "경기",
        "institution": "경기도",
        "name": "경기도청",
        "site_url": "https://www.gg.go.kr",
        "board_url": "https://www.gg.go.kr/bbs/board.do?bsIdx=535&menuId=1778&bcIdx=536",
    },
    "dj": {
        "region_code": "대전",
        "institution": "대전광역시",
        "name": "대전광역시청",
        "site_url": "https://www.daejeon.go.kr",
        "board_url": "https://www.daejeon.go.kr/drh/open/drhDataOpen/drhDataOpenBoardView.do?boardSeq=694",
    },
    "sj": {
        "region_code": "세종",
        "institution": "세종특별자치시",
        "name": "세종특별자치시청",
        "site_url": "https://www.sejong.go.kr",
        "board_url": "https://www.sejong.go.kr/bbs/R0091/list.do",
    },
    "cn": {
        "region_code": "충남",
        "institution": "충청남도",
        "name": "충청남도청",
        "site_url": "https://www.chungnam.go.kr",
        "board_url": "https://www.chungnam.go.kr/cnportal/bbs/B0000187/list.do?menuNo=500122",
    },
    "cb": {
        "region_code": "충북",
        "institution": "충청북도",
        "name": "충청북도청",
        "site_url": "https://www.chungbuk.go.kr",
        "board_url": "https://www.chungbuk.go.kr/www/selectBbsNttList.do?key=211&bbsNo=2",
    },
    "ic": {
        "region_code": "인천",
        "institution": "인천광역시",
        "name": "인천광역시청",
        "site_url": "https://www.incheon.go.kr",
        "board_url": "https://www.incheon.go.kr/open/OPEN010305",
    },
}

CODE_TO_REGION_KEY = {cfg["region_code"]: k for k, cfg in REGION_CONFIGS.items()}

# ----------------------------------------------------------------------
# Non-restaurant and meal classification keywords
# ----------------------------------------------------------------------
NON_RESTAURANT_KEYWORDS = [
    "마트", "슈퍼", "편의점", "이마트", "홈플러스", "롯데마트", "하나로마트",
    "코스트코", "트레이더스", "백화점", "아울렛", "다이소", "쿠팡", "네이버페이",
    "인터파크", "11번가", "지마켓", "옥션", "위메프", "티몬", "문구", "알파문구",
    "모닝글로리", "사무용품", "드림디포", "오피스디포", "인쇄", "복사", "제본",
    "화방", "화원", "꽃집", "플라워", "화훼", "농원", "조경", "베이커리", "제과",
    "빵집", "파리바게뜨", "뚜레쥬르", "던킨", "크리스피", "카페", "커피", "스타벅스",
    "투썸플레이스", "이디야", "메가커피", "빽다방", "컴포즈", "엔제리너스", "할리스",
    "폴바셋", "약국", "병원", "의원", "치과", "한의원", "은행", "농협", "수협",
    "신협", "새마을금고", "우체국", "주유소", "충전소", "정비", "카센터", "주차장",
    "㈜", "주식회사", "(주)", "(유)", "사단법인", "재단법인", "협회", "학회", "공단", "공사",
]

EXCLUDE_PURPOSE_KEYWORDS = [
    "구입", "구매", "물품", "상품권", "온누리상품권", "선물", "기념품", "화환",
    "조화", "꽃", "축하", "조의", "경조", "배송", "택배", "우편", "원두",
    "케이크", "떡", "다과", "음료", "간식", "비품", "용품", "사무용품", "도서",
    "인쇄", "구독", "수수료", "주차", "주유", "차량",
]

MEAL_PURPOSE_KEYWORDS = [
    "식사", "오찬", "만찬", "석식", "중식", "조찬", "회식", "급식", "간담회",
    "회의", "접대", "초청", "면담", "격려", "노고", "위로", "토론", "평가회",
    "보고회", "워크숍", "워크샵", "세미나", "당직", "비상근무",
]

# ----------------------------------------------------------------------
# HTTP & Cache Utilities
# ----------------------------------------------------------------------
def fetch_url_curl(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 40,
) -> bytes:
    """
    Safe subprocess curl GET implementation for Chungbuk and fallback transport.
    Uses safe list arguments (no shell=True), explicit browser UA, and bounded timeout.
    """
    curl_bin = shutil.which("curl")
    if not curl_bin:
        raise RuntimeError("curl binary not found in PATH")

    cmd = [
        curl_bin,
        "-sL",
        "--max-time", str(timeout),
        "-A", USER_AGENT,
    ]
    if headers:
        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])
    cmd.append(url)

    res = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
    if res.returncode != 0 or not res.stdout:
        err_msg = res.stderr.decode("utf-8", errors="replace") if res.stderr else f"returncode {res.returncode}"
        raise RuntimeError(f"curl failed for {url}: {err_msg}")
    return res.stdout


def fetch_url(
    url: str,
    post_data: Optional[Dict[str, str]] = None,
    headers: Optional[Dict[str, str]] = None,
    retries: int = 2,
    timeout: int = 30,
) -> bytes:
    """Fetches URL with urllib; for chungbuk.go.kr or upon urllib connection closure, falls back to safe curl GET."""
    if "chungbuk.go.kr" in url and post_data is None:
        return fetch_url_curl(url, headers=headers, timeout=timeout)

    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)

    encoded_data = None
    if post_data is not None:
        encoded_data = urllib.parse.urlencode(post_data).encode("utf-8")
        if "Content-Type" not in req_headers:
            req_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"

    req = urllib.request.Request(url, data=encoded_data, headers=req_headers)

    last_err = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            last_err = e
            # Fallback to safe curl GET if urllib connection closed / failed
            if post_data is None:
                try:
                    return fetch_url_curl(url, headers=headers, timeout=timeout)
                except Exception:
                    pass
    raise RuntimeError(f"Failed to fetch {url} after {retries} retries: {last_err}")


def compute_file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def download_cached_file(
    url: str,
    cache_dir: str,
    filename_hint: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    offline: bool = False,
) -> Tuple[str, str, int]:
    """Downloads or retrieves from cache. Returns (local_path, sha256, size_bytes).
    Reuses existing snapshots matching url_hash so identical files are not re-downloaded.
    """
    os.makedirs(cache_dir, exist_ok=True)

    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]

    # Check if any file matching url_hash already exists in cache_dir
    if os.path.exists(cache_dir):
        for fname in os.listdir(cache_dir):
            if fname.startswith(f"{url_hash}_") or fname.startswith(f"{url_hash}."):
                existing_p = os.path.join(cache_dir, fname)
                if os.path.isfile(existing_p) and os.path.getsize(existing_p) > 0:
                    sha = compute_file_sha256(existing_p)
                    return existing_p, sha, os.path.getsize(existing_p)

    ext = ".bin"
    if filename_hint:
        clean_hint = re.sub(r'[\s/\\:*?"<>|]', "_", filename_hint)
        _, raw_ext = os.path.splitext(clean_hint)
        if raw_ext.lower() in [".xlsx", ".xls", ".pdf", ".csv", ".hwp", ".hwpx"]:
            ext = raw_ext.lower()
        target_name = f"{url_hash}_{clean_hint}"
    else:
        target_name = f"{url_hash}{ext}"

    local_path = os.path.join(cache_dir, target_name)

    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        sha = compute_file_sha256(local_path)
        return local_path, sha, os.path.getsize(local_path)

    if offline:
        raise FileNotFoundError(f"Offline mode: cached file not found for {url}")

    data = fetch_url(url, headers=headers)
    with open(local_path, "wb") as f:
        f.write(data)

    sha = compute_file_sha256(local_path)
    return local_path, sha, len(data)


# ----------------------------------------------------------------------
# File Conversion via LibreOffice (soffice)
# ----------------------------------------------------------------------
def convert_file_via_soffice(
    src_path: str,
    out_dir: str,
    target_ext: str = "xlsx",
    soffice_path: str = DEFAULT_SOFFICE,
    source_ext: Optional[str] = None,
) -> Optional[str]:
    """
    Converts binary document (XLS, HWP, HWPX) to target_ext ('xlsx' or 'pdf') using headless LibreOffice.
    Uses an isolated temporary profile directory to avoid lock collisions.
    If source lacks extension, creates a temporary copy with source_ext to assist LibreOffice import filter.
    """
    if not os.path.exists(src_path) or os.path.getsize(src_path) == 0:
        return None

    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(src_path))[0]
    expected_out = os.path.join(out_dir, f"{base}.{target_ext}")

    if os.path.exists(expected_out) and os.path.getsize(expected_out) > 0:
        return expected_out

    bin_path = soffice_path if os.path.exists(soffice_path) else shutil.which("soffice")
    if not bin_path:
        print(f"[WARN] soffice binary not found for converting {src_path}", file=sys.stderr)
        return None

    work_src = src_path
    temp_copy = None
    if source_ext and not src_path.lower().endswith(source_ext.lower()):
        temp_copy = os.path.join(out_dir, f"{base}_in{source_ext}")
        shutil.copyfile(src_path, temp_copy)
        work_src = temp_copy

    try:
        with tempfile.TemporaryDirectory(prefix="soffice_prof_") as tmp_prof:
            profile_uri = f"file://{tmp_prof}"
            cmd = [
                bin_path,
                f"-env:UserInstallation={profile_uri}",
                "--headless",
                "--convert-to",
                target_ext,
                "--outdir",
                out_dir,
                work_src,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if os.path.exists(expected_out) and os.path.getsize(expected_out) > 0:
                return expected_out

            # Check if LibreOffice generated output with slight variation in filename
            for fn in os.listdir(out_dir):
                if fn.endswith(f".{target_ext}") and base in fn:
                    cand_p = os.path.join(out_dir, fn)
                    if os.path.getsize(cand_p) > 0:
                        return cand_p
    except Exception as e:
        print(f"[WARN] soffice conversion exception for {src_path}: {e}", file=sys.stderr)
    finally:
        if temp_copy and os.path.exists(temp_copy):
            try:
                os.remove(temp_copy)
            except OSError:
                pass

    return None


def convert_xls_to_xlsx(
    xls_path: str,
    out_dir: str,
    soffice_path: str = DEFAULT_SOFFICE,
) -> Optional[str]:
    """Converts old .xls binary format to .xlsx using soffice in headless mode."""
    if not os.path.exists(xls_path):
        return None

    # Check if file is already a valid zip/xlsx
    if openpyxl is not None:
        try:
            wb = openpyxl.load_workbook(xls_path, read_only=True)
            wb.close()
            return xls_path
        except Exception:
            pass

    return convert_file_via_soffice(
        src_path=xls_path,
        out_dir=out_dir,
        target_ext="xlsx",
        soffice_path=soffice_path,
        source_ext=".xls",
    )


# ----------------------------------------------------------------------
# Data normalization and sanitization
# ----------------------------------------------------------------------
def parse_korean_date(val: Any) -> Optional[str]:
    """Extracts YYYY-MM-DD from various string/datetime formats including 2-digit years and newlines."""
    if val is None:
        return None

    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.strftime("%Y-%m-%d")

    s = str(val).strip()
    # Normalize unicode quotes and whitespaces
    s = s.replace("‘", "'").replace("’", "'").replace("`", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = re.sub(r"[\r\n\t]+", " ", s).strip()

    # 1. 4-digit year: 2024 ~ 2026
    m4 = re.search(r"(?:^|[^\d])(202\d)[\.\-\/년\s]+(\d{1,2})[\.\-\/월\s]+(\d{1,2})", s)
    if m4:
        try:
            year, month, day = int(m4.group(1)), int(m4.group(2)), int(m4.group(3))
            return datetime.date(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # 2. 2-digit year: e.g. '26. 8. 4. or 26. 8. 4. or 25-10-13
    m2 = re.search(r"(?:^|['\s])(2[3-6])[\.\-\/년\s]+(\d{1,2})[\.\-\/월\s]+(\d{1,2})", s)
    if m2:
        try:
            year, month, day = 2000 + int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
            return datetime.date(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # 3. Compact 8-digit YYYYMMDD: 20240913 ~ 20260913
    m_comp = re.search(r"\b(202\d)(\d{2})(\d{2})\b", s)
    if m_comp:
        try:
            year, month, day = int(m_comp.group(1)), int(m_comp.group(2)), int(m_comp.group(3))
            return datetime.date(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def parse_amount(val: Any) -> int:
    """Parses KRW expense amount into an integer."""
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).strip().replace(",", "").replace("원", "").replace("₩", "")
    try:
        return int(float(s))
    except ValueError:
        return 0


def is_multi_merchant(text: str) -> bool:
    """
    Detects if a place field contains multiple establishments
    (e.g., '가연, 서로히', '맛찬들왕소금구이, 커피노리', '진성문, 뚜레주르').
    These must be excluded to prevent inventing records or misattributions.
    """
    if not text:
        return False
    # Strip parentheses and their content if they are address or phone notes
    s = re.sub(r"\([^\)]*\)", "", text).strip()
    if re.search(r"[가-힣a-zA-Z0-9]\s*,\s*[가-힣a-zA-Z0-9]", s):
        return True
    if re.search(r"[가-힣a-zA-Z0-9]\s+및\s+[가-힣a-zA-Z0-9]", s):
        return True
    if re.search(r"[가-힣a-zA-Z0-9]\s+/\s+[가-힣a-zA-Z0-9]", s):
        return True
    return False


def classify_meal(purpose_text: str, place_text: str) -> bool:
    """Determines whether a transaction is a dining expense."""
    p_norm = (purpose_text or "").strip()
    l_norm = (place_text or "").strip()

    for kw in NON_RESTAURANT_KEYWORDS:
        if kw in l_norm:
            # allow hotel dining if explicit restaurant marker
            if kw == "호텔" and any(m in l_norm for m in ["호텔점", "식당", "뷔페", "레스토랑"]):
                continue
            return False

    for ex in EXCLUDE_PURPOSE_KEYWORDS:
        if ex in p_norm:
            return False

    if any(m in p_norm for m in MEAL_PURPOSE_KEYWORDS):
        return True

    # If purpose is vague or empty, check if place explicitly indicates dining
    if any(m in l_norm for m in [
        "식당", "가든", "한우", "갈비", "횟집", "반점", "순대", "밥상",
        "냉면", "찌개", "구이", "삼계탕", "면옥", "보쌈", "육회", "국밥", "장어", "분식"
    ]):
        return True

    # If purpose is non-empty and not excluded, and place is not non-restaurant, consider candidate meal
    if p_norm and len(p_norm) >= 2 and not any(ex in p_norm for ex in EXCLUDE_PURPOSE_KEYWORDS):
        return True

    return False


def clean_restaurant_name(raw_name: str) -> str:
    """Cleans restaurant name, keeping brand/branch names intact."""
    s = html.unescape(raw_name or "").strip()
    s = re.sub(r"^[\"\'`‘“\s]+|[\"\'`’”\s]+$", "", s)
    s = re.sub(r"\s*외\s*\d+.*$", "", s)
    s = re.sub(r"^\((?:주|유)\)\s*|^주식회사\s*", "", s)
    s = re.sub(r"\([^\)]*(?:시|구|동|로|길|\d{2,4}-\d{3,4})[^\)]*\)", "", s)
    return s.strip()


def normalize_business_name(name: str) -> str:
    """Normalizes business name for exact match indexing."""
    if not name:
        return ""
    s = html.unescape(name).strip().lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"^\((?:주|유)\)|^\(사\)|^\(재\)|^주식회사|^유한회사", "", s)
    s = re.sub(r"\((?:주|유)\)$|\(사\)$|\(재\)$", "", s)
    s = re.sub(r"^[\"\'`‘“]+|[\"\'`’”]+$", "", s)
    return s


PROVINCE_PREFIX_MAP = {
    "경기": "경기도",
    "경기도": "경기도",
    "인천": "인천광역시",
    "인천광역시": "인천광역시",
    "대전": "대전광역시",
    "대전광역시": "대전광역시",
    "세종": "세종특별자치시",
    "세종특별자치시": "세종특별자치시",
    "세종시": "세종특별자치시",
    "충북": "충청북도",
    "충청북도": "충청북도",
    "충남": "충청남도",
    "충청남도": "충청남도",
    "서울": "서울특별시",
    "서울특별시": "서울특별시",
}

PROVINCE_TO_ABBR = {
    "경기도": "경기",
    "인천광역시": "인천",
    "대전광역시": "대전",
    "세종특별자치시": "세종",
    "충청북도": "충북",
    "충청남도": "충남",
    "서울특별시": "서울",
}

# Mapping of cities, counties, and autonomous/administrative districts
CITY_DISTRICT_TO_PROVINCE: Dict[str, Any] = {
    # Gyeonggi
    "수원시": "경기", "장안구": "경기", "권선구": "경기", "팔달구": "경기", "영통구": "경기",
    "성남시": "경기", "수정구": "경기", "중원구": "경기", "분당구": "경기",
    "의정부시": "경기", "안양시": "경기", "만안구": "경기", "동안구": "경기",
    "부천시": "경기", "원미구": "경기", "소사구": "경기", "오정구": "경기",
    "광명시": "경기", "평택시": "경기", "동두천시": "경기",
    "안산시": "경기", "상록구": "경기", "단원구": "경기",
    "고양시": "경기", "덕양구": "경기", "일산동구": "경기", "일산서구": "경기",
    "과천시": "경기", "구리시": "경기", "남양주시": "경기", "오산시": "경기", "시흥시": "경기",
    "군포시": "경기", "의왕시": "경기", "하남시": "경기",
    "용인시": "경기", "처인구": "경기", "기흥구": "경기", "수지구": "경기",
    "파주시": "경기", "이천시": "경기", "안성시": "경기", "김포시": "경기", "화성시": "경기",
    "광주시": "경기", "양주시": "경기", "포천시": "경기", "여주시": "경기",
    "연천군": "경기", "가평군": "경기", "양평군": "경기",
    # Incheon
    "미추홀구": "인천", "연수구": "인천", "남동구": "인천", "부평구": "인천", "계양구": "인천",
    "강화군": "인천", "옹진군": "인천",
    # Daejeon
    "유성구": "대전", "대덕구": "대전",
    # Chungbuk
    "청주시": "충북", "상당구": "충북", "서원구": "충북", "흥덕구": "충북", "청원구": "충북",
    "충주시": "충북", "제천시": "충북", "보은군": "충북", "옥천군": "충북", "영동군": "충북",
    "증평군": "충북", "진천군": "충북", "괴산군": "충북", "음성군": "충북", "단양군": "충북",
    # Chungnam
    "천안시": "충남", "동남구": "충남", "서북구": "충남",
    "공주시": "충남", "보령시": "충남", "아산시": "충남", "서산시": "충남",
    "논산시": "충남", "계룡시": "충남", "당진시": "충남",
    "금산군": "충남", "부여군": "충남", "서천군": "충남", "청양군": "충남",
    "홍성군": "충남", "예산군": "충남", "태안군": "충남",
    # Seoul
    "종로구": "서울", "용산구": "서울", "성동구": "서울", "광진구": "서울",
    "동대문구": "서울", "중랑구": "서울", "성북구": "서울", "강북구": "서울",
    "도봉구": "서울", "노원구": "서울", "은평구": "서울", "서대문구": "서울",
    "마포구": "서울", "양천구": "서울", "강서구": "서울", "구로구": "서울",
    "금천구": "서울", "영등포구": "서울", "동작구": "서울", "관악구": "서울",
    "서초구": "서울", "강남구": "서울", "송파구": "서울", "강동구": "서울",
    # Ambiguous district names across cities
    "중구": ["인천", "대전", "서울"],
    "서구": ["인천", "대전"],
    "동구": ["대전"],
}


def normalize_address(
    addr_str: str,
    hint_region: Optional[str] = None,
    is_validated_lookup: bool = False,
) -> Optional[Tuple[str, str]]:
    """
    Validates and normalizes full numbered road or lot address.
    Never invents address. Preserves address numbers and hyphens.
    Requires region + city/district (Sejong exempt) or validated official lookup.
    Returns (region_code, full_normalized_address) or None.
    """
    if not addr_str:
        return None

    raw = html.unescape(addr_str).strip()
    raw = re.sub(r"[\r\n\t]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw)
    raw = re.sub(r"\s*외\s*\d+.*$", "", raw)

    # Normalize repeated Sejong or province prefixes in raw text
    raw = re.sub(r"^(세종특별자치시|세종시)(\s+(?:세종특별자치시|세종시))+", "세종특별자치시", raw)
    raw = re.sub(r"^(경기도)(\s+경기도)+", "경기도", raw)
    raw = re.sub(r"^(인천광역시)(\s+인천광역시)+", "인천광역시", raw)
    raw = re.sub(r"^(대전광역시)(\s+대전광역시)+", "대전광역시", raw)
    raw = re.sub(r"^(충청북도)(\s+충청북도)+", "충청북도", raw)
    raw = re.sub(r"^(충청남도)(\s+충청남도)+", "충청남도", raw)

    # Road or lot address numbered pattern with hyphen preserved
    # e.g.: ...로 12, ...길 45-1, ...동 123-4
    m_num = re.search(
        r"([가-힣\d]+(?:로|길|동|리|가))(?:\s+\d+번?길)?\s+(\d+(?:-\d+)?)",
        raw,
    )
    if not m_num:
        return None

    # Find province or city
    found_prov = None
    for prov_name, standard_name in PROVINCE_PREFIX_MAP.items():
        if re.search(rf"\b{prov_name}\b", raw):
            found_prov = standard_name
            break

    found_city = None
    for city_name in CITY_DISTRICT_TO_PROVINCE:
        if city_name in raw:
            found_city = city_name
            break

    is_sejong = "세종" in raw or found_prov == "세종특별자치시" or (hint_region == "세종" and not found_prov)
    if is_sejong:
        found_prov = "세종특별자치시"

    # If province not in text, try to resolve from unambiguous city/district
    if not found_prov and found_city:
        cand_prov = CITY_DISTRICT_TO_PROVINCE[found_city]
        if isinstance(cand_prov, list):
            # Ambiguous district name (e.g. 중구, 서구, 동구)
            if hint_region and hint_region in cand_prov:
                found_prov = PROVINCE_PREFIX_MAP[hint_region]
        else:
            found_prov = PROVINCE_PREFIX_MAP[cand_prov]

    # Validated official lookup can use hint_region if province wasn't explicit
    if not found_prov and is_validated_lookup and hint_region and hint_region in PROVINCE_PREFIX_MAP:
        found_prov = PROVINCE_PREFIX_MAP[hint_region]

    # Reject if province not found
    if not found_prov:
        return None

    # District requirement: non-Sejong addresses MUST have a city/district
    # Road-only fallback without city/district is unsafe and rejected
    if not is_sejong and not found_city and not is_validated_lookup:
        return None

    # Map back to 2-letter abbreviation
    prov_abbr = PROVINCE_TO_ABBR.get(found_prov, found_prov[:2])

    # Clean address start
    idx = raw.find(found_prov)
    if idx >= 0:
        clean_addr = raw[idx:].strip()
    elif found_city and found_city in raw:
        c_idx = raw.find(found_city)
        clean_addr = f"{found_prov} {raw[c_idx:].strip()}"
    elif is_validated_lookup:
        if raw.startswith(found_prov):
            clean_addr = raw
        elif found_prov == "세종특별자치시" and (raw.startswith("세종특별자치시") or raw.startswith("세종시")):
            clean_addr = re.sub(r"^(?:세종특별자치시|세종시)\s*", "세종특별자치시 ", raw)
        else:
            clean_addr = f"{found_prov} {raw}".strip()
    else:
        return None

    # Deduplicate repeated province prefix in clean_addr
    clean_addr = re.sub(r"^(세종특별자치시|세종시)(\s+(?:세종특별자치시|세종시))+", "세종특별자치시", clean_addr)
    clean_addr = re.sub(r"^(경기도)(\s+경기도)+", "경기도", clean_addr)
    clean_addr = re.sub(r"^(인천광역시)(\s+인천광역시)+", "인천광역시", clean_addr)
    clean_addr = re.sub(r"^(대전광역시)(\s+대전광역시)+", "대전광역시", clean_addr)
    clean_addr = re.sub(r"^(충청북도)(\s+충청북도)+", "충청북도", clean_addr)
    clean_addr = re.sub(r"^(충청남도)(\s+충청남도)+", "충청남도", clean_addr)
    clean_addr = re.sub(r"\s+", " ", clean_addr).strip()

    # Re-verify that building / lot number is preserved
    if not re.search(r"\d+(?:-\d+)?", clean_addr):
        return None

    return prov_abbr, clean_addr


def extract_place_and_address(
    place_val: Any,
    addr_val: Any = None,
    hint_region: Optional[str] = None,
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Extracts (clean_name, clean_address, province_abbr) from place/address cell values.
    Handles '식당명 (주소)' format as well as separate columns.
    """
    p_str = re.sub(r"[\r\n\t]+", " ", str(place_val or "")).strip()
    a_str = re.sub(r"[\r\n\t]+", " ", str(addr_val or "")).strip() if addr_val else ""

    # Check if place_val has (주소)
    m = re.search(r"^(.*?)\s*[\(\[](.*?(?:로|길|동|리|시|구).*?)[\)\]]", p_str)
    if m:
        candidate_name = m.group(1).strip()
        candidate_addr = m.group(2).strip()
        norm = normalize_address(candidate_addr, hint_region)
        if norm:
            return clean_restaurant_name(candidate_name), norm[1], norm[0]

    # If separate address column exists
    if a_str:
        norm = normalize_address(a_str, hint_region)
        if norm:
            return clean_restaurant_name(p_str), norm[1], norm[0]

    # Check if p_str itself is address
    norm = normalize_address(p_str, hint_region)
    if norm:
        return "", norm[1], norm[0]

    return clean_restaurant_name(p_str), None, None


def extract_district_hint(raw_place: str, raw_addr: str = "", department: str = "") -> Optional[str]:
    """Extracts candidate district (시군구) name from raw text for constraining search."""
    combined = f"{raw_place} {raw_addr} {department}"
    for district in CITY_DISTRICT_TO_PROVINCE:
        if district in combined and len(district) >= 2:
            return district
    return None


VALID_UNIT_SUFFIXES = (
    "과", "담당관", "관", "센터", "사업소", "소방서", "위원회",
    "읍", "면", "동", "실", "국", "단", "본부", "원", "소", "처", "대변인", "도서관", "부",
)

VALID_UNIT_ROLE_SUFFIXES = (
    "과장", "국장", "실장", "본부장", "단장", "처장", "부장", "팀장",
    "소장", "서장", "원장", "센터장", "위원장", "읍장", "면장", "동장",
    "시장", "도지사", "부시장", "부지사",
)

GENERIC_DEPARTMENT_KEYWORDS = [
    "서식", "시책추진업무추진비", "시책추진", "부서운영", "기관운영", "업무추진비",
    "공개서식", "시책", "부서", "기관", "행정서식", "업추비", "시책업무추진비",
    "기관운영업무추진비", "부서운영업무추진비",
    "sheet1", "sheet2", "sheet3", "sheet", "table1", "table", "공개자료",
    "내역", "집행내역", "사용내역", "집행현황", "사용현황",
    "본청", "시청", "도청",
    "경기도청", "인천광역시청", "대전광역시청", "세종특별자치시청", "충청남도청", "충청북도청",
    "경기도", "인천광역시", "대전광역시", "세종특별자치시", "충청남도", "충청북도",
    "경제중심도약기반구축", "경제관리업무추진", "소비진작및금융경제정책지원업무추진",
    "경제정책자문업무추진", "일자리경제정책업무추진", "도정시책설명", "지역상생협력",
    "청사방호업무", "자치행정업무", "총무업무", "공무원단체업무",
    "국장", "과장", "실장", "팀장", "관님", "과장님",
]


def is_generic_department(name: Optional[str]) -> bool:
    """Checks if department candidate is merely a generic sheet title, expense category, or administrative label."""
    if not name or not isinstance(name, str):
        return True
    s = re.sub(r"[\s\d_\-\(\)\[\]\.\,\'\"]+", "", name).strip().lower()
    if not s or len(s) <= 1:
        return True
    for kw in GENERIC_DEPARTMENT_KEYWORDS:
        if s == kw.lower():
            return True
    if s.startswith("sheet") or s in ("서식", "시책", "부서", "내역", "공개", "기관"):
        return True
    for exp in ("업무추진비", "시책추진", "기관운영", "부서운영", "도약기반구축", "업무추진", "상생협력", "청사방호"):
        if exp in s and not any(s.endswith(sfx) for sfx in VALID_UNIT_SUFFIXES):
            return True
    return False


def is_valid_department_name(name: Optional[str]) -> bool:
    """
    Validates whether candidate string represents a real, specific organizational unit.
    Rejects generic expense categories, purpose phrases, dates, and institution fallbacks.
    """
    if not name or not isinstance(name, str):
        return False
    s = name.strip()
    s = re.sub(r"^[□○※\-\*\s]+", "", s).strip()
    s = re.sub(r"^(?:붙임|서식|별지|게시용)[\s_\)\]\:]*", "", s).strip()
    s = re.sub(r"^\[[^\]]*\]\s*", "", s).strip()

    clean_no_space = re.sub(r"[\s\d_\-\(\)\[\]\.\,\'\"]+", "", s).lower()
    if not clean_no_space or len(clean_no_space) <= 1:
        return False

    if is_generic_department(s):
        return False

    for phrase in [
        "업무추진비", "시책추진", "기관운영", "부서운영", "공개서식", "공개자료",
        "집행내역", "사용내역", "집행현황", "사용현황", "행정서식",
        "도약기반구축", "업무추진", "시책설명", "상생협력", "청사방호",
    ]:
        if phrase in clean_no_space:
            return False

    if re.search(r"^\d+년|^\d+월|^\d+분기|^[상하]반기", s):
        return False

    # Check for unit before parenthesized role e.g. "노인복지과(노인복지과장)"
    m_paren = re.match(r"^([A-Za-z0-9가-힣\s]+?)\s*\([^\)]*\)$", s)
    cand_base = m_paren.group(1).strip() if m_paren else s

    if not re.search(r"[가-힣]", cand_base):
        return False

    ends_valid_unit = any(cand_base.endswith(sfx) for sfx in VALID_UNIT_SUFFIXES)
    ends_valid_role = any(cand_base.endswith(sfx) for sfx in VALID_UNIT_ROLE_SUFFIXES)

    if not (ends_valid_unit or ends_valid_role):
        return False

    if cand_base in ("과", "국", "실", "단", "부", "처", "소", "원", "관", "팀"):
        return False

    return True


def canonicalize_department_name(dept: Optional[str]) -> str:
    """
    Normalizes department title to canonical organization unit.
    Strips position suffixes like ~과장, ~국장, ~실장, ~본부장, ~단장, ~처장, ~부장, ~팀장, ~님
    so that e.g. '운영지원과장' maps to '운영지원과', preventing artificial department inflation.
    Preserves Latin acronyms like AI (e.g. 'AI혁신과').
    """
    if not dept:
        return ""
    s = dept.strip()

    m_full = re.match(r"^[\(\[\<](.+?)[\)\]\>]$", s)
    if m_full:
        s = m_full.group(1).strip()

    s = re.sub(r"^[□○※\-\*\s]+", "", s).strip()
    s = re.sub(r"^(?:붙임|서식|별지|게시용)[\s_\)\]\:]*", "", s).strip()
    s = re.sub(r"^\[[^\]]*\]\s*", "", s).strip()
    s = re.sub(r"^\([^)]*?(?:서식|붙임|게시용)[^)]*?\)\s*", "", s).strip()
    s = s.replace("`", "").replace("'", "").replace('"', "").strip()

    # "노인복지과(노인복지과장)" -> take base unit before parenthesis if valid
    m_paren = re.match(r"^([A-Za-z0-9가-힣\s]+?)\s*\([^\)]*\)$", s)
    if m_paren and is_valid_department_name(m_paren.group(1).strip()):
        s = m_paren.group(1).strip()

    # Position suffixes to canonical department:
    s = re.sub(r"과장(?:님)?$", "과", s)
    s = re.sub(r"국장(?:님)?$", "국", s)
    s = re.sub(r"실장(?:님)?$", "실", s)
    s = re.sub(r"본부장(?:님)?$", "본부", s)
    s = re.sub(r"단장(?:님)?$", "단", s)
    s = re.sub(r"처장(?:님)?$", "처", s)
    s = re.sub(r"부장(?:님)?$", "부", s)
    s = re.sub(r"팀장(?:님)?$", "팀", s)
    s = re.sub(r"소장(?:님)?$", "소", s)
    s = re.sub(r"서장(?:님)?$", "서", s)
    s = re.sub(r"원장(?:님)?$", "원", s)
    s = re.sub(r"센터장(?:님)?$", "센터", s)
    s = re.sub(r"위원장(?:님)?$", "위원회", s)
    s = re.sub(r"담당관(?:님)?$", "담당관", s)
    s = re.sub(r"관님$", "관", s)
    s = re.sub(r"대변인(?:님)?$", "대변인", s)
    s = re.sub(r"(?:도지사|시장|부시장|부지사)(?:실)?$", "비서실", s)
    s = re.sub(r"시장직\s*인수위(?:원회)?$", "시장직인수위원회", s)

    return s.strip()


def extract_department_from_title(title: str) -> Optional[str]:
    """
    Extracts department name from article/file titles.
    Derives department from explicit brackets or clean starting pattern.
    Preserves Latin acronyms like AI (e.g. AI혁신과).
    Never uses arbitrary prefix match.
    """
    if not title:
        return None

    # 1. Explicit brackets e.g. (성평등가족과), (AI혁신과), (소상공정책과), (하천관리사업소), [도시계획과]
    m_brackets = re.findall(r"[\(\[]([^()\[\]]+)[\)\]]", title)
    for cand in reversed(m_brackets):
        c = cand.strip()
        if is_valid_department_name(c):
            return canonicalize_department_name(c)
        m_inner = re.search(r"([A-Za-z0-9가-힣]+(?:과|과장|담당관|관|센터|사업소|소방서|위원회|읍|면|동|실|실장|국|국장|단|단장|본부|본부장|처|처장|대변인))\b", c)
        if m_inner and is_valid_department_name(m_inner.group(1)):
            return canonicalize_department_name(m_inner.group(1))

    # 2. Leading department pattern e.g. "AI혁신과 업무추진비 집행내역", "2026년 8월 탄소중립경제과 업무추진비"
    m_lead = re.search(
        r"(?:^|^\d{4}년\s*(?:\d{1,2}월\s*)?|^\d{1,2}월\s*)"
        r"([A-Za-z0-9가-힣]+(?:과|과장|담당관|관|센터|사업소|소방서|위원회|읍|면|동|실|실장|국|국장|단|단장|본부|본부장|처|처장|대변인))\s+"
        r"(?:업무추진비|시책|기관운영|집행내역)",
        title.strip()
    )
    if m_lead:
        c = m_lead.group(1).strip()
        if is_valid_department_name(c):
            return canonicalize_department_name(c)

    return None


def extract_department_from_header_rows(
    rows: List[List[Any]], max_rows: int = 15
) -> Optional[Tuple[str, str]]:
    """
    Extracts explicit department from metadata rows above the main table.
    Looks for labels such as '부서(기관명) : 노인복지과', '소속부서 : 환경보건안전과',
    '부서명 : ...', or adjacent cell in same row.
    Returns (raw_department_label, location_description) or None.
    """
    HEADER_DEPT_PREFIXES = [
        "부서(기관명)", "기관(부서)명", "소속부서", "담당부서", "작성부서", "부서명", "집행부서", "기관명", "부서"
    ]

    for r_idx, row in enumerate(rows[:max_rows]):
        if not row:
            continue
        for c_idx, cell in enumerate(row):
            if cell is None:
                continue
            txt = str(cell).strip()
            if not txt:
                continue

            # Case A: Same cell has label and value, e.g.:
            # "□ 부서(기관명) :  노인복지과(노인복지과장)"
            for pfx in HEADER_DEPT_PREFIXES:
                if pfx in txt:
                    m = re.search(rf"{re.escape(pfx)}\s*[:：\-]\s*([A-Za-z0-9가-힣\s\(\)]+)", txt)
                    if m:
                        val = m.group(1).strip()
                        val_first = val.split("\n")[0].strip()
                        val_clean = re.sub(r"\s{2,}.*$", "", val_first).strip()
                        if is_valid_department_name(val_clean):
                            return val_clean, f"header:row {r_idx}, col {c_idx}"
                        m_cand = re.search(r"([A-Za-z0-9가-힣]+(?:과|과장|담당관|관|센터|사업소|소방서|위원회|읍|면|동|실|실장|국|국장|단|단장|본부|본부장|처|처장|대변인))", val_clean)
                        if m_cand and is_valid_department_name(m_cand.group(1)):
                            return m_cand.group(1), f"header:row {r_idx}, col {c_idx}"

            # Case B: Cell is exactly the label, value in adjacent cell
            clean_txt = re.sub(r"^[□○※\-\*\s]+", "", txt).strip()
            for pfx in HEADER_DEPT_PREFIXES:
                if clean_txt in (pfx, f"{pfx}:", f"{pfx} :"):
                    for next_c in range(c_idx + 1, min(c_idx + 4, len(row))):
                        next_val = row[next_c]
                        if next_val is not None:
                            n_str = str(next_val).strip()
                            if n_str and n_str != "-":
                                if is_valid_department_name(n_str):
                                    return n_str, f"header:row {r_idx}, col {next_c}"
                                m_cand = re.search(r"([A-Za-z0-9가-힣]+(?:과|과장|담당관|관|센터|사업소|소방서|위원회|읍|면|동|실|실장|국|국장|단|단장|본부|본부장|처|처장|대변인))", n_str)
                                if m_cand and is_valid_department_name(m_cand.group(1)):
                                    return m_cand.group(1), f"header:row {r_idx}, col {next_c}"

    return None


class AddressResolver:
    """
    Resolves missing restaurant addresses using official SBiz store registry CSVs.
    Downloaded from: https://www.data.go.kr/data/15083033/fileData.do snapshot 2026-06-30.
    CSVs UTF-8-SIG columns:
      상가업소번호,상호명,지점명,상권업종중분류명,상권업종소분류명,시도명,시군구명,지번주소,도로명주소
    """
    def __init__(self, address_dir: str = DEFAULT_ADDRESS_DIR):
        self.address_dir = address_dir
        self.source_url = SBIZ_DATA_SOURCE_URL
        self.snapshot_date = SBIZ_SNAPSHOT_DATE
        self._loaded_regions: Dict[str, bool] = {}
        self._provenance: Dict[str, Dict[str, Any]] = {}
        self._by_name: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        self._by_name_branch: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        self._by_branch_exact: Dict[str, Dict[Tuple[str, str], List[Dict[str, Any]]]] = {}
        self._stats = {
            "totalQueries": 0,
            "resolved": 0,
            "exactNameAndBranch": 0,
            "uniqueExactNameWithinRegion": 0,
            "ambiguousMultipleCandidates": 0,
            "notFound": 0,
        }

    def _get_candidate_csv_paths(self, region_code: str) -> List[str]:
        candidates = [
            f"{region_code}.csv",
            f"{PROVINCE_PREFIX_MAP.get(region_code, region_code)}.csv",
        ]
        abbr_map = {"경기": "gg", "인천": "ic", "대전": "dj", "세종": "sj", "충북": "cb", "충남": "cn"}
        if region_code in abbr_map:
            candidates.append(f"{abbr_map[region_code]}.csv")
        return [os.path.join(self.address_dir, name) for name in candidates]

    def load_region(self, region_code: str) -> bool:
        if region_code in self._loaded_regions:
            return self._loaded_regions[region_code]

        self._by_name[region_code] = {}
        self._by_name_branch[region_code] = {}
        self._by_branch_exact[region_code] = {}

        if not os.path.exists(self.address_dir):
            self._loaded_regions[region_code] = False
            return False

        csv_path = None
        for p in self._get_candidate_csv_paths(region_code):
            if os.path.exists(p) and os.path.getsize(p) > 0:
                csv_path = p
                break

        if not csv_path:
            self._loaded_regions[region_code] = False
            return False

        try:
            sha = compute_file_sha256(csv_path)
            sz = os.path.getsize(csv_path)
            row_count = 0

            with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    bno = (row.get("상가업소번호") or "").strip()
                    name = (row.get("상호명") or "").strip()
                    branch = (row.get("지점명") or "").strip()
                    sido = (row.get("시도명") or "").strip()
                    sigungu = (row.get("시군구명") or "").strip()
                    doro = (row.get("도로명주소") or "").strip()
                    jibun = (row.get("지번주소") or "").strip()

                    if not name or not bno:
                        continue

                    # Validate address
                    target_addr = doro if doro else jibun
                    if not target_addr:
                        continue

                    # Deduplicate repeated Sejong prefix if present in source CSV
                    target_addr = re.sub(r"^(세종특별자치시|세종시)(\s+(?:세종특별자치시|세종시))+", "세종특별자치시", target_addr)

                    norm = normalize_address(target_addr, hint_region=region_code, is_validated_lookup=True)
                    if not norm:
                        continue
                    cand_reg, cand_addr = norm
                    if cand_reg not in TARGET_REGIONS:
                        continue

                    cand = {
                        "bno": bno,
                        "name": name,
                        "branch": branch,
                        "sido": sido,
                        "sigungu": sigungu,
                        "address": cand_addr,
                        "region": cand_reg,
                    }
                    row_count += 1

                    norm_name = normalize_business_name(name)
                    if norm_name:
                        self._by_name[region_code].setdefault(norm_name, []).append(cand)

                    if branch:
                        norm_branch = normalize_business_name(branch)
                        norm_full = normalize_business_name(f"{name}{branch}")
                        if norm_full:
                            self._by_name_branch[region_code].setdefault(norm_full, []).append(cand)
                        if norm_name and norm_branch:
                            self._by_branch_exact[region_code].setdefault((norm_name, norm_branch), []).append(cand)

            self._provenance[region_code] = {
                "type": "address_reference_csv",
                "region": region_code,
                "sourceURL": self.source_url,
                "snapshot": self.snapshot_date,
                "filePath": csv_path,
                "filename": os.path.basename(csv_path),
                "sha256": sha,
                "bytes": sz,
                "recordCount": row_count,
                "loadedRecords": row_count,
            }
            self._loaded_regions[region_code] = True
            return True
        except Exception as e:
            print(f"[WARN] Failed to load address CSV for {region_code} from {csv_path}: {e}", file=sys.stderr)
            self._loaded_regions[region_code] = False
            return False

    def get_provenance(self) -> List[Dict[str, Any]]:
        if self._provenance:
            return list(self._provenance.values())
        # If not loaded yet, report available CSVs on disk so diagnostic/coverage doesn't report 'Found 0'
        available = []
        for reg in sorted(TARGET_REGIONS):
            for p in self._get_candidate_csv_paths(reg):
                if os.path.exists(p) and os.path.getsize(p) > 0:
                    sz = os.path.getsize(p)
                    available.append({
                        "type": "address_reference_csv",
                        "region": reg,
                        "sourceURL": self.source_url,
                        "snapshot": self.snapshot_date,
                        "filePath": p,
                        "filename": os.path.basename(p),
                        "sha256": compute_file_sha256(p),
                        "bytes": sz,
                        "recordCount": "available (lazy loaded)",
                        "loadedRecords": 0,
                    })
                    break
        return available

    def get_stats(self) -> Dict[str, Any]:
        """Returns accurate runtime address resolution statistics and loaded status."""
        return {
            "totalQueries": self._stats["totalQueries"],
            "resolved": self._stats["resolved"],
            "exactNameAndBranch": self._stats["exactNameAndBranch"],
            "uniqueExactNameWithinRegion": self._stats["uniqueExactNameWithinRegion"],
            "ambiguousMultipleCandidates": self._stats["ambiguousMultipleCandidates"],
            "notFound": self._stats["notFound"],
            "loadedRegions": {
                reg: {
                    "loaded": self._loaded_regions.get(reg, False),
                    "records": self._provenance.get(reg, {}).get("loadedRecords", 0),
                }
                for reg in sorted(TARGET_REGIONS)
            },
        }

    def load_matched_addresses(self, file_path: str) -> int:
        """
        Loads pre-resolved matched addresses from matched-addresses.csv.gz.
        Allows offline replay/lookup even without raw broad SBiz CSV files.
        """
        if not file_path or not os.path.exists(file_path):
            return 0
        loaded = 0
        try:
            opener = gzip.open(file_path, "rt", encoding="utf-8") if file_path.endswith(".gz") else open(file_path, "r", encoding="utf-8")
            with opener as f:
                reader = csv.DictReader(f)
                for row in reader:
                    reg = (row.get("region") or "").strip()
                    name = (row.get("name") or "").strip()
                    addr = (row.get("address") or "").strip()
                    bno = (row.get("addressSourceID") or "").strip()
                    if not reg or not name or not addr:
                        continue
                    if reg not in self._by_name:
                        self._by_name[reg] = {}
                    if reg not in self._by_name_branch:
                        self._by_name_branch[reg] = {}
                    if reg not in self._by_branch_exact:
                        self._by_branch_exact[reg] = {}

                    cand = {
                        "bno": bno,
                        "name": name,
                        "branch": "",
                        "sido": "",
                        "sigungu": "",
                        "address": addr,
                        "region": reg,
                    }
                    norm_name = normalize_business_name(name)
                    if norm_name:
                        if norm_name not in self._by_name[reg]:
                            self._by_name[reg][norm_name] = []
                        if not any(c["address"] == addr for c in self._by_name[reg][norm_name]):
                            self._by_name[reg][norm_name].append(cand)
                            loaded += 1
            if loaded > 0:
                self._loaded_regions["matched_cache"] = True
        except Exception as e:
            print(f"[WARN] Failed to load matched addresses cache from {file_path}: {e}", file=sys.stderr)
        return loaded

    def resolve(
        self,
        name: str,
        region: str,
        raw_place: str = "",
        district_hint: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        self._stats["totalQueries"] += 1

        if not self.load_region(region):
            if region not in self._by_name or not self._by_name[region]:
                self._stats["notFound"] += 1
                return None

        if is_multi_merchant(raw_place or name):
            self._stats["ambiguousMultipleCandidates"] += 1
            return None

        q_norm = normalize_business_name(name)
        if not q_norm:
            self._stats["notFound"] += 1
            return None

        # Check if query has branch marker (e.g. 식당 둔산점, 식당(도청점))
        m_br = re.search(r"^(.*?)\s*[\(\[\-]?\s*([가-힣a-zA-Z0-9]+(?:점|호점))\s*[\)\]]?$", name)
        q_base_norm = normalize_business_name(m_br.group(1)) if m_br else None
        q_branch_norm = normalize_business_name(m_br.group(2)) if m_br else None

        # Priority 1: Exact Name + Branch Match
        candidates: List[Dict[str, Any]] = []
        is_branch_match = False
        if q_norm in self._by_name_branch[region]:
            candidates = self._by_name_branch[region][q_norm]
            is_branch_match = True
        elif q_base_norm and q_branch_norm and (q_base_norm, q_branch_norm) in self._by_branch_exact[region]:
            candidates = self._by_branch_exact[region][(q_base_norm, q_branch_norm)]
            is_branch_match = True

        if is_branch_match and candidates:
            unique_addrs = {c["address"] for c in candidates}
            if len(unique_addrs) == 1:
                best = candidates[0]
                self._stats["resolved"] += 1
                self._stats["exactNameAndBranch"] += 1
                return {
                    "address": best["address"],
                    "region": best["region"],
                    "addressMatch": "exactNameAndBranch",
                    "addressSourceURL": self.source_url,
                    "addressSourceID": best["bno"],
                }
            elif district_hint:
                dist_cands = [c for c in candidates if district_hint in c["sigungu"] or district_hint in c["address"]]
                if len({c["address"] for c in dist_cands}) == 1:
                    best = dist_cands[0]
                    self._stats["resolved"] += 1
                    self._stats["exactNameAndBranch"] += 1
                    return {
                        "address": best["address"],
                        "region": best["region"],
                        "addressMatch": "exactNameAndBranch",
                        "addressSourceURL": self.source_url,
                        "addressSourceID": best["bno"],
                    }
            self._stats["ambiguousMultipleCandidates"] += 1
            return None

        # Priority 2: Standalone Exact Name Match (without branch)
        if q_norm in self._by_name[region]:
            candidates = self._by_name[region][q_norm]
            unique_addrs = {c["address"] for c in candidates}
            if len(unique_addrs) == 1:
                best = candidates[0]
                self._stats["resolved"] += 1
                self._stats["uniqueExactNameWithinRegion"] += 1
                return {
                    "address": best["address"],
                    "region": best["region"],
                    "addressMatch": "uniqueExactNameWithinRegion",
                    "addressSourceURL": self.source_url,
                    "addressSourceID": best["bno"],
                }
            elif district_hint:
                dist_cands = [c for c in candidates if district_hint in c["sigungu"] or district_hint in c["address"]]
                if len({c["address"] for c in dist_cands}) == 1:
                    best = dist_cands[0]
                    self._stats["resolved"] += 1
                    self._stats["uniqueExactNameWithinRegion"] += 1
                    return {
                        "address": best["address"],
                        "region": best["region"],
                        "addressMatch": "uniqueExactNameWithinRegion",
                        "addressSourceURL": self.source_url,
                        "addressSourceID": best["bno"],
                    }
            self._stats["ambiguousMultipleCandidates"] += 1
            return None

        self._stats["notFound"] += 1
        return None


# ----------------------------------------------------------------------
# Generic Excel & PDF Parsing
# ----------------------------------------------------------------------
def detect_header_columns(rows: List[List[Any]]) -> Tuple[int, Dict[str, int]]:
    """
    Scans first 15 rows to identify header row and column indices:
    'date', 'purpose', 'amount', 'place', 'address', 'department'.
    """
    best_row_idx = -1
    best_mapping: Dict[str, int] = {}
    max_score = 0

    DATE_KEYWORDS = ["일자", "일시", "날짜", "집행일", "사용일", "일자(일시)", "사용일자", "집행일자", "집행일시", "사용일시"]
    PURPOSE_KEYWORDS = ["집행목적", "사용목적", "목적", "집행내역", "사용내역", "내역", "적요", "내용", "사용목적(내역)"]
    AMOUNT_KEYWORDS = ["집행금액", "사용금액", "금액", "지출액", "지출금액", "집행액", "사용액", "금액(원)", "집행액(원)", "사용금액(원)"]
    PLACE_KEYWORDS = ["집행장소", "사용장소", "장소", "상호", "가맹점", "사용처", "업소명", "거래처명", "거래처", "가맹점명", "식당명", "사용장소(가맹점명)"]
    ADDRESS_KEYWORDS = ["소재지", "주소", "가맹점주소", "사업장주소", "식당주소", "소재지(주소)"]
    DEPT_KEYWORDS = ["부서명", "담당부서", "소속부서", "부서", "집행부서", "사용부서", "기관명", "부서(기관명)", "기관(부서)명"]

    for r_idx, row in enumerate(rows[:15]):
        mapping: Dict[str, int] = {}
        score = 0
        for c_idx, cell in enumerate(row):
            if cell is None:
                continue
            txt = str(cell).strip().replace(" ", "").replace("\n", "").replace("\r", "")
            if not txt:
                continue

            if any(k in txt for k in DATE_KEYWORDS):
                if "date" not in mapping:
                    mapping["date"] = c_idx
                    score += 2
            elif any(k in txt for k in PURPOSE_KEYWORDS):
                if "purpose" not in mapping:
                    mapping["purpose"] = c_idx
                    score += 2
            elif any(k in txt for k in AMOUNT_KEYWORDS):
                if "amount" not in mapping:
                    mapping["amount"] = c_idx
                    score += 2
            elif any(k in txt for k in PLACE_KEYWORDS):
                if "place" not in mapping:
                    mapping["place"] = c_idx
                    score += 3
            elif any(k in txt for k in ADDRESS_KEYWORDS):
                if "address" not in mapping:
                    mapping["address"] = c_idx
                    score += 2
            elif any(k in txt for k in DEPT_KEYWORDS):
                if "department" not in mapping:
                    mapping["department"] = c_idx
                    score += 1

        if score > max_score and ("place" in mapping or "address" in mapping):
            max_score = score
            best_row_idx = r_idx
            best_mapping = mapping

    return best_row_idx, best_mapping


def parse_rows_into_records(
    rows: List[List[Any]],
    header_idx: int,
    col_map: Dict[str, int],
    institution: str,
    default_department: Optional[str] = None,
    source_url: str = "",
    record_prefix: str = "",
    hint_region: str = "",
    address_resolver: Optional[AddressResolver] = None,
    default_department_raw: Optional[str] = None,
    default_department_evidence: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    """
    Extracts and sanitizes records from tabular rows.
    Returns (accepted_records, pending_transactions, exclusion_stats).
    """
    accepted: List[Dict[str, Any]] = []
    pending_transactions: List[Dict[str, Any]] = []
    stats = {
        "raw_rows": 0,
        "meal_candidates": 0,
        "accepted": 0,
        "missing_date": 0,
        "date_out_of_window": 0,
        "future_date": 0,
        "missing_department": 0,
        "missing_name": 0,
        "missing_address": 0,
        "non_meal": 0,
        "multi_merchant": 0,
        "out_of_region": 0,
    }

    date_idx = col_map.get("date")
    purp_idx = col_map.get("purpose")
    amt_idx = col_map.get("amount")
    place_idx = col_map.get("place")
    addr_idx = col_map.get("address")
    dept_idx = col_map.get("department")

    for row_num, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
        if not any(c is not None and str(c).strip() for c in row):
            continue

        stats["raw_rows"] += 1

        # Date
        p_date = None
        if date_idx is not None and date_idx < len(row):
            p_date = parse_korean_date(row[date_idx])

        if not p_date:
            stats["missing_date"] += 1
            continue

        if p_date > CURRENT_DATE:
            stats["future_date"] += 1
            continue

        if p_date < WINDOW_START_DATE:
            stats["date_out_of_window"] += 1
            continue

        # Department resolution: per-row header -> sheet/metadata fallback -> reject if absent/invalid
        dept = None
        dept_raw = None
        dept_evidence = None

        if dept_idx is not None and dept_idx < len(row) and row[dept_idx]:
            d_val = str(row[dept_idx]).strip()
            if is_valid_department_name(d_val):
                dept_raw = d_val
                dept = canonicalize_department_name(d_val)
                dept_evidence = {
                    "kind": "row",
                    "location": f"row:{row_num}, col:{dept_idx}",
                    "sourceURL": source_url,
                }

        if not dept and default_department and is_valid_department_name(default_department):
            dept_raw = default_department_raw or default_department
            dept = canonicalize_department_name(default_department)
            dept_evidence = default_department_evidence or {
                "kind": "posting",
                "location": "default_metadata",
                "sourceURL": source_url,
            }

        if not dept or not is_valid_department_name(dept):
            stats["missing_department"] += 1
            dept, dept_raw, dept_evidence = "", "", None

        # Purpose & Meal Check
        purp = str(row[purp_idx]).strip() if purp_idx is not None and purp_idx < len(row) else ""
        raw_place = row[place_idx] if place_idx is not None and place_idx < len(row) else None
        raw_addr = row[addr_idx] if addr_idx is not None and addr_idx < len(row) else None
        raw_place_str = str(raw_place or "").strip()

        if not classify_meal(purp, raw_place_str):
            stats["non_meal"] += 1
            continue

        # Check multi-merchant payments like '가연,서로히' and exclude without inventing rows
        if is_multi_merchant(raw_place_str):
            stats["multi_merchant"] += 1
            continue

        # Name and Address from document
        name, addr, prov = extract_place_and_address(raw_place, raw_addr, hint_region=hint_region)
        if not name:
            stats["missing_name"] += 1
            continue

        amt = parse_amount(row[amt_idx]) if amt_idx is not None and amt_idx < len(row) else 0
        rec_id = f"{record_prefix}-r{row_num}"

        # Preserve sanitized transaction candidates for all meal rows before resolution
        pending_tx = {
            "institution": institution,
            "department": dept,
            "departmentRawLabel": dept_raw,
            "departmentEvidence": dept_evidence,
            "date": p_date,
            "rawRestaurantName": name,
            "region": hint_region,
            "addressHint": extract_district_hint(raw_place_str, str(raw_addr or "")),
            "isMeal": True,
            "addressIfPresent": addr if addr else None,
            "source": source_url,
            "recordID": rec_id,
        }
        pending_transactions.append(pending_tx)
        stats["meal_candidates"] += 1

        addr_match = None
        addr_src_url = source_url
        addr_src_id = ""

        if addr:
            addr_match = "documentAddress"
        elif address_resolver:
            district_hint = extract_district_hint(raw_place_str, str(raw_addr or ""), dept)
            resolved = address_resolver.resolve(
                name=name,
                region=hint_region,
                raw_place=raw_place_str,
                district_hint=district_hint,
            )
            if resolved:
                addr = resolved["address"]
                prov = resolved["region"]
                addr_match = resolved["addressMatch"]
                addr_src_url = resolved["addressSourceURL"]
                addr_src_id = resolved["addressSourceID"]

        if not addr:
            stats["missing_address"] += 1
            continue

        region_final = prov or hint_region
        if region_final not in TARGET_REGIONS:
            stats["out_of_region"] += 1
            continue

        record = {
            "recordID": rec_id,
            "region": region_final,
            "institution": institution,
            "department": dept,
            "departmentRawLabel": dept_raw,
            "departmentEvidence": dept_evidence,
            "name": name,
            "address": addr,
            "paymentDate": p_date,
            "amount": amt,
            "isMeal": True,
            "sourceURL": source_url,
            "addressSourceURL": addr_src_url,
            "addressMatch": addr_match or "uniqueExactNameWithinRegion",
            "addressSourceID": addr_src_id,
        }
        accepted.append(record)
        stats["accepted"] += 1

    return accepted, pending_transactions, stats


def parse_xlsx_file(
    file_path: str,
    institution: str,
    default_department: Optional[str] = None,
    source_url: str = "",
    record_prefix: str = "",
    hint_region: str = "",
    address_resolver: Optional[AddressResolver] = None,
    default_department_raw: Optional[str] = None,
    default_department_evidence: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    """Loads XLSX with openpyxl and extracts records across worksheets."""
    if openpyxl is None:
        raise RuntimeError("openpyxl is not available in runtime")

    all_accepted = []
    all_pending = []
    total_stats = {
        "raw_rows": 0,
        "meal_candidates": 0,
        "accepted": 0,
        "missing_date": 0,
        "date_out_of_window": 0,
        "future_date": 0,
        "missing_department": 0,
        "missing_name": 0,
        "missing_address": 0,
        "non_meal": 0,
        "multi_merchant": 0,
        "out_of_region": 0,
    }

    try:
        wb = openpyxl.load_workbook(io.BytesIO(Path(file_path).read_bytes()), data_only=True)
    except Exception as e:
        # Fallback to read_only mode for malformed or complex XML sheets
        try:
            wb = openpyxl.load_workbook(io.BytesIO(Path(file_path).read_bytes()), data_only=True, read_only=True)
        except Exception:
            print(f"[WARN] openpyxl failed to load {file_path}: {e}", file=sys.stderr)
            return [], [], total_stats

    for ws in wb.worksheets:
        try:
            rows = list(ws.iter_rows(values_only=True))
        except Exception as e_iter:
            print(f"[WARN] openpyxl iter_rows failed on sheet {ws.title}: {e_iter}", file=sys.stderr)
            continue

        if not rows:
            continue

        # 1. First priority: actual source explicit 부서(기관명) header row
        header_dept_info = extract_department_from_header_rows(rows)
        dept = None
        dept_raw = None
        dept_ev = None

        if header_dept_info:
            raw_cand, loc = header_dept_info
            dept_raw = raw_cand
            dept = canonicalize_department_name(raw_cand)
            dept_ev = {
                "kind": "header",
                "location": f"sheet:'{ws.title}', {loc}",
                "sourceURL": source_url,
            }
        else:
            # 2. Second priority: real worksheet name only if real unit syntax
            sheet_title_clean = ws.title.strip() if ws.title else ""
            extracted = extract_department_from_title(sheet_title_clean)
            if extracted and is_valid_department_name(extracted):
                dept_raw = sheet_title_clean
                dept = extracted
                dept_ev = {
                    "kind": "sheet",
                    "location": f"sheet:'{ws.title}'",
                    "sourceURL": source_url,
                }
            elif sheet_title_clean and is_valid_department_name(sheet_title_clean):
                dept_raw = sheet_title_clean
                dept = canonicalize_department_name(sheet_title_clean)
                dept_ev = {
                    "kind": "sheet",
                    "location": f"sheet:'{ws.title}'",
                    "sourceURL": source_url,
                }
            elif default_department and is_valid_department_name(default_department):
                # 3. Third priority: posting metadata department
                dept_raw = default_department_raw or default_department
                dept = canonicalize_department_name(default_department)
                dept_ev = default_department_evidence or {
                    "kind": "posting",
                    "location": "default_metadata",
                    "sourceURL": source_url,
                }

        h_idx, col_map = detect_header_columns(rows)
        if h_idx < 0:
            continue

        records, pending, stats = parse_rows_into_records(
            rows=rows,
            header_idx=h_idx,
            col_map=col_map,
            institution=institution,
            default_department=dept,
            source_url=source_url,
            record_prefix=f"{record_prefix}_{ws.title}",
            hint_region=hint_region,
            address_resolver=address_resolver,
            default_department_raw=dept_raw,
            default_department_evidence=dept_ev,
        )
        all_accepted.extend(records)
        all_pending.extend(pending)
        for k, v in stats.items():
            total_stats[k] = total_stats.get(k, 0) + v

    wb.close()
    return all_accepted, all_pending, total_stats


def parse_pdf_file(
    file_path: str,
    institution: str,
    default_department: Optional[str] = None,
    source_url: str = "",
    record_prefix: str = "",
    hint_region: str = "",
    address_resolver: Optional[AddressResolver] = None,
    default_department_raw: Optional[str] = None,
    default_department_evidence: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    """Loads PDF with pdfplumber and extracts tabular records."""
    if pdfplumber is None:
        raise RuntimeError("pdfplumber is not available in runtime")

    all_accepted = []
    all_pending = []
    total_stats = {
        "raw_rows": 0,
        "meal_candidates": 0,
        "accepted": 0,
        "missing_date": 0,
        "date_out_of_window": 0,
        "future_date": 0,
        "missing_department": 0,
        "missing_name": 0,
        "missing_address": 0,
        "non_meal": 0,
        "multi_merchant": 0,
        "out_of_region": 0,
    }

    try:
        with pdfplumber.open(file_path) as pdf:
            for p_idx, page in enumerate(pdf.pages):
                tables = page.extract_tables()
                if not tables:
                    continue
                for t_idx, table in enumerate(tables):
                    if not table or len(table) < 2:
                        continue
                    h_idx, col_map = detect_header_columns(table)
                    if h_idx < 0:
                        continue

                    header_dept_info = extract_department_from_header_rows(table)
                    dept = None
                    dept_raw = None
                    dept_ev = None

                    if header_dept_info:
                        raw_cand, loc = header_dept_info
                        dept_raw = raw_cand
                        dept = canonicalize_department_name(raw_cand)
                        dept_ev = {
                            "kind": "header",
                            "location": f"pdf:p{p_idx+1}_t{t_idx+1}, {loc}",
                            "sourceURL": source_url,
                        }
                    elif default_department and is_valid_department_name(default_department):
                        dept_raw = default_department_raw or default_department
                        dept = canonicalize_department_name(default_department)
                        dept_ev = default_department_evidence or {
                            "kind": "posting",
                            "location": "default_metadata",
                            "sourceURL": source_url,
                        }

                    records, pending, stats = parse_rows_into_records(
                        rows=table,
                        header_idx=h_idx,
                        col_map=col_map,
                        institution=institution,
                        default_department=dept,
                        source_url=source_url,
                        record_prefix=f"{record_prefix}_p{p_idx}_t{t_idx}",
                        hint_region=hint_region,
                        address_resolver=address_resolver,
                        default_department_raw=dept_raw,
                        default_department_evidence=dept_ev,
                    )
                    all_accepted.extend(records)
                    all_pending.extend(pending)
                    for k, v in stats.items():
                        total_stats[k] = total_stats.get(k, 0) + v
    except Exception as e:
        print(f"[WARN] pdfplumber failed on {file_path}: {e}", file=sys.stderr)

    return all_accepted, all_pending, total_stats


def parse_hwpx_file(
    file_path: str,
    institution: str,
    default_department: Optional[str] = None,
    source_url: str = "",
    record_prefix: str = "",
    hint_region: str = "",
    address_resolver: Optional[AddressResolver] = None,
    default_department_raw: Optional[str] = None,
    default_department_evidence: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    """Extracts tables from HWPX zip archive (Contents/section*.xml) and parses rows into dining records."""
    all_accepted = []
    all_pending = []
    total_stats = {
        "raw_rows": 0,
        "meal_candidates": 0,
        "accepted": 0,
        "missing_date": 0,
        "date_out_of_window": 0,
        "future_date": 0,
        "missing_department": 0,
        "missing_name": 0,
        "missing_address": 0,
        "non_meal": 0,
        "multi_merchant": 0,
        "out_of_region": 0,
    }

    if not os.path.exists(file_path):
        return [], [], total_stats

    try:
        with zipfile.ZipFile(file_path, "r") as z:
            section_names = sorted([n for n in z.namelist() if re.match(r"^Contents/section\d+\.xml$", n)])
            if not section_names:
                section_names = sorted([n for n in z.namelist() if "section" in n.lower() and n.endswith(".xml")])

            for sec_idx, sec_name in enumerate(section_names):
                xml_data = z.read(sec_name)
                root = ET.fromstring(xml_data)

                for t_idx, tbl in enumerate(root.iter()):
                    if not (tbl.tag.endswith("tbl") or tbl.tag.split("}")[-1] == "tbl"):
                        continue

                    table_rows: List[List[str]] = []
                    for tr in tbl.iter():
                        if not (tr.tag.endswith("tr") or tr.tag.split("}")[-1] == "tr"):
                            continue
                        row_cells: List[str] = []
                        for tc in tr.iter():
                            if not (tc.tag.endswith("tc") or tc.tag.split("}")[-1] == "tc"):
                                continue
                            cell_text = "".join(tc.itertext()).strip()
                            row_cells.append(cell_text)
                        if row_cells:
                            table_rows.append(row_cells)

                    if len(table_rows) < 2:
                        continue

                    h_idx, col_map = detect_header_columns(table_rows)
                    if h_idx < 0:
                        continue

                    header_dept_info = extract_department_from_header_rows(table_rows)
                    dept = None
                    dept_raw = None
                    dept_ev = None

                    if header_dept_info:
                        raw_cand, loc = header_dept_info
                        dept_raw = raw_cand
                        dept = canonicalize_department_name(raw_cand)
                        dept_ev = {
                            "kind": "header",
                            "location": f"hwpx:sec{sec_idx}_t{t_idx}, {loc}",
                            "sourceURL": source_url,
                        }
                    elif default_department and is_valid_department_name(default_department):
                        dept_raw = default_department_raw or default_department
                        dept = canonicalize_department_name(default_department)
                        dept_ev = default_department_evidence or {
                            "kind": "posting",
                            "location": "default_metadata",
                            "sourceURL": source_url,
                        }

                    records, pending, stats = parse_rows_into_records(
                        rows=table_rows,
                        header_idx=h_idx,
                        col_map=col_map,
                        institution=institution,
                        default_department=dept,
                        source_url=source_url,
                        record_prefix=f"{record_prefix}_sec{sec_idx}_t{t_idx}",
                        hint_region=hint_region,
                        address_resolver=address_resolver,
                        default_department_raw=dept_raw,
                        default_department_evidence=dept_ev,
                    )
                    all_accepted.extend(records)
                    all_pending.extend(pending)
                    for k, v in stats.items():
                        total_stats[k] = total_stats.get(k, 0) + v
    except Exception as e:
        print(f"[WARN] HWPX parsing failed for {file_path}: {e}", file=sys.stderr)

    return all_accepted, all_pending, total_stats


def detect_file_type(file_path: str) -> str:
    """
    Detects true file format using magic bytes and archive inspection.
    Returns one of: 'pdf', 'xlsx', 'hwpx', 'xls', 'hwp', 'csv', 'unknown'.
    """
    if not os.path.exists(file_path) or os.path.getsize(file_path) < 4:
        return "unknown"

    with open(file_path, "rb") as f:
        head = f.read(4096)

    # 1. PDF (%PDF)
    if head.startswith(b"%PDF"):
        return "pdf"

    # 2. ZIP Archive (PK\x03\x04 or PK\x05\x06)
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        try:
            with zipfile.ZipFile(file_path, "r") as z:
                names = z.namelist()
                if "[Content_Types].xml" in names:
                    return "xlsx"
                if any(n.startswith("Contents/section") or n == "Contents/content.hwpml" or "section" in n.lower() for n in names):
                    return "hwpx"
                if "mimetype" in names:
                    try:
                        mime = z.read("mimetype").decode("utf-8", errors="ignore").strip()
                        if "hwp" in mime:
                            return "hwpx"
                    except Exception:
                        pass
        except Exception:
            pass
        return "zip"

    # 3. OLE Compound Document (D0 CF 11 E0)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        if b"HwpSummaryInformation" in head or b"FileHeader" in head or b"HWP Document" in head:
            return "hwp"
        if b"Workbook" in head or b"Book" in head:
            return "xls"
        return "ole"

    return "unknown"


def dispatch_and_parse_file(
    file_path: str,
    institution: str,
    default_department: Optional[str] = None,
    source_url: str = "",
    record_prefix: str = "",
    hint_region: str = "",
    address_resolver: Optional[AddressResolver] = None,
    soffice_path: str = DEFAULT_SOFFICE,
    cache_dir: Optional[str] = None,
    default_department_raw: Optional[str] = None,
    default_department_evidence: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Centralized file dispatch routine that detects true file magic
    and dispatches to the appropriate parser (XLSX, HWPX, PDF, XLS/HWP via soffice).
    Returns (records, pending_transactions, stats_dict).
    Does not count failed/unsupported files as successfully parsed.
    """
    ftype = detect_file_type(file_path)
    conv_dir = os.path.join(cache_dir, "conv") if cache_dir else tempfile.gettempdir()

    stats: Dict[str, Any] = {
        "raw_rows": 0,
        "accepted": 0,
        "meal_candidates": 0,
        "format": ftype,
        "status": "failed",
        "failureReason": None,
    }

    # 1. PDF
    if ftype == "pdf" or (file_path.lower().endswith(".pdf") and ftype != "xlsx"):
        recs, pending, s = parse_pdf_file(
            file_path=file_path,
            institution=institution,
            default_department=default_department,
            source_url=source_url,
            record_prefix=record_prefix,
            hint_region=hint_region,
            address_resolver=address_resolver,
            default_department_raw=default_department_raw,
            default_department_evidence=default_department_evidence,
        )
        stats.update(s)
        stats["format"] = "pdf"
        if stats.get("raw_rows", 0) > 0:
            stats["status"] = "parsed"
        else:
            stats["failureReason"] = "pdf_no_valid_tables_or_headers"
        return recs, pending, stats

    # 2. XLSX
    if ftype == "xlsx" or (file_path.lower().endswith(".xlsx") and ftype not in ("hwpx", "pdf", "hwp", "xls", "ole")):
        recs, pending, s = parse_xlsx_file(
            file_path=file_path,
            institution=institution,
            default_department=default_department,
            source_url=source_url,
            record_prefix=record_prefix,
            hint_region=hint_region,
            address_resolver=address_resolver,
            default_department_raw=default_department_raw,
            default_department_evidence=default_department_evidence,
        )
        stats.update(s)
        stats["format"] = "xlsx"
        if stats.get("raw_rows", 0) > 0:
            stats["status"] = "parsed"
            return recs, pending, stats

        # If openpyxl failed or found 0 rows, try soffice conversion / repair
        repaired = convert_file_via_soffice(file_path, conv_dir, target_ext="xlsx", soffice_path=soffice_path)
        if repaired:
            recs, pending, s = parse_xlsx_file(
                file_path=repaired,
                institution=institution,
                default_department=default_department,
                source_url=source_url,
                record_prefix=f"{record_prefix}_rep",
                hint_region=hint_region,
                address_resolver=address_resolver,
                default_department_raw=default_department_raw,
                default_department_evidence=default_department_evidence,
            )
            stats.update(s)
            if stats.get("raw_rows", 0) > 0:
                stats["status"] = "parsed"
                stats["failureReason"] = None
                return recs, pending, stats

        stats["failureReason"] = stats.get("failureReason") or "xlsx_no_valid_headers"
        return recs, pending, stats

    # 3. HWPX
    if ftype == "hwpx":
        recs, pending, s = parse_hwpx_file(
            file_path=file_path,
            institution=institution,
            default_department=default_department,
            source_url=source_url,
            record_prefix=record_prefix,
            hint_region=hint_region,
            address_resolver=address_resolver,
            default_department_raw=default_department_raw,
            default_department_evidence=default_department_evidence,
        )
        stats.update(s)
        stats["format"] = "hwpx"
        if stats.get("raw_rows", 0) > 0:
            stats["status"] = "parsed"
            return recs, pending, stats

        # Fallback to soffice conversion to PDF
        conv_pdf = convert_file_via_soffice(
            file_path, conv_dir, target_ext="pdf", soffice_path=soffice_path, source_ext=".hwpx"
        )
        if conv_pdf:
            recs, pending, s = parse_pdf_file(
                file_path=conv_pdf,
                institution=institution,
                default_department=default_department,
                source_url=source_url,
                record_prefix=f"{record_prefix}_hwpx",
                hint_region=hint_region,
                address_resolver=address_resolver,
                default_department_raw=default_department_raw,
                default_department_evidence=default_department_evidence,
            )
            stats.update(s)
            if stats.get("raw_rows", 0) > 0:
                stats["status"] = "parsed"
                stats["failureReason"] = None
                return recs, pending, stats

        stats["failureReason"] = "hwpx_table_extraction_failed"
        return recs, pending, stats

    # 4. XLS / OLE
    if ftype in ("xls", "ole"):
        conv_xlsx = convert_file_via_soffice(
            file_path, conv_dir, target_ext="xlsx", soffice_path=soffice_path, source_ext=".xls"
        )
        if conv_xlsx:
            recs, pending, s = parse_xlsx_file(
                file_path=conv_xlsx,
                institution=institution,
                default_department=default_department,
                source_url=source_url,
                record_prefix=f"{record_prefix}_xls",
                hint_region=hint_region,
                address_resolver=address_resolver,
                default_department_raw=default_department_raw,
                default_department_evidence=default_department_evidence,
            )
            stats.update(s)
            stats["format"] = "xls"
            if stats.get("raw_rows", 0) > 0:
                stats["status"] = "parsed"
                return recs, pending, stats

        conv_pdf = convert_file_via_soffice(
            file_path, conv_dir, target_ext="pdf", soffice_path=soffice_path, source_ext=".xls"
        )
        if conv_pdf:
            recs, pending, s = parse_pdf_file(
                file_path=conv_pdf,
                institution=institution,
                default_department=default_department,
                source_url=source_url,
                record_prefix=f"{record_prefix}_pdf",
                hint_region=hint_region,
                address_resolver=address_resolver,
                default_department_raw=default_department_raw,
                default_department_evidence=default_department_evidence,
            )
            stats.update(s)
            stats["format"] = "xls"
            if stats.get("raw_rows", 0) > 0:
                stats["status"] = "parsed"
                stats["failureReason"] = None
                return recs, pending, stats

        stats["failureReason"] = "xls_conversion_or_header_failed"
        return recs, pending, stats

    # 5. HWP
    if ftype == "hwp":
        conv_pdf = convert_file_via_soffice(
            file_path, conv_dir, target_ext="pdf", soffice_path=soffice_path, source_ext=".hwp"
        )
        if conv_pdf:
            recs, pending, s = parse_pdf_file(
                file_path=conv_pdf,
                institution=institution,
                default_department=default_department,
                source_url=source_url,
                record_prefix=f"{record_prefix}_hwp",
                hint_region=hint_region,
                address_resolver=address_resolver,
                default_department_raw=default_department_raw,
                default_department_evidence=default_department_evidence,
            )
            stats.update(s)
            stats["format"] = "hwp"
            if stats.get("raw_rows", 0) > 0:
                stats["status"] = "parsed"
                return recs, pending, stats

        stats["failureReason"] = "hwp_conversion_or_parse_failed"
        return recs, pending, stats

    stats["failureReason"] = f"unsupported_format_{ftype}"
    return [], [], stats


# ----------------------------------------------------------------------
# Region-Specific Collectors
# ----------------------------------------------------------------------
class BaseRegionCollector:
    def __init__(self, key: str, config: Dict[str, str], cache_dir: str, soffice_path: str):
        self.key = key
        self.config = config
        self.cache_dir = cache_dir
        self.soffice_path = soffice_path
        self.region_code = config["region_code"]
        self.institution = config["institution"]

    def get_manifest_path(self) -> str:
        return os.path.join(self.cache_dir, "downloads", self.key, "manifest.json")

    def save_manifest(self, items: List[Dict[str, Any]]) -> None:
        path = self.get_manifest_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARN] Failed to save manifest to {path}: {e}", file=sys.stderr)

    def load_manifest(self) -> List[Dict[str, Any]]:
        path = self.get_manifest_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[WARN] Failed to load manifest from {path}: {e}", file=sys.stderr)
        return []

    def discover_cached_files(self) -> List[Dict[str, Any]]:
        """Discovers cached files directly from downloads/<key> on disk when offline or manifest missing."""
        target_dir = os.path.join(self.cache_dir, "downloads", self.key)
        if not os.path.exists(target_dir):
            return []

        discovered = []
        for fn in sorted(os.listdir(target_dir)):
            if fn.startswith(".") or fn == "manifest.json" or fn == "conv" or fn.endswith(".json"):
                continue
            full_p = os.path.join(target_dir, fn)
            if not os.path.isfile(full_p) or os.path.getsize(full_p) == 0:
                continue

            raw_dept = extract_department_from_title(fn)
            dept = canonicalize_department_name(raw_dept) if raw_dept and is_valid_department_name(raw_dept) else None
            evidence = None
            if dept:
                evidence = {
                    "kind": "post_title_brackets",
                    "location": f"filename:{fn}",
                    "sourceURL": f"file://{full_p}",
                }

            known_source = CACHE_SOURCE_INDEX.get(compute_file_sha256(full_p))
            if not known_source:
                # Unknown cached provenance is not an official source record.
                continue
            discovered.append({
                "download_url": known_source["downloadURL"],
                "view_url": known_source["sourceURL"],
                "department": dept,
                "department_raw": raw_dept if dept else None,
                "department_evidence": evidence,
                "filename": fn,
                "local_path": full_p,
            })
        return discovered

    def collect(
        self,
        max_pages: int = 20,
        max_files: int = 200,
        offline: bool = False,
        address_resolver: Optional[AddressResolver] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        raise NotImplementedError


class GyeonggiCollector(BaseRegionCollector):
    def collect(
        self,
        max_pages: int = 20,
        max_files: int = 200,
        offline: bool = False,
        address_resolver: Optional[AddressResolver] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        records = []
        pending_transactions = []
        sources = []
        coverage_stats: Dict[str, Any] = {
            "region": self.region_code,
            "institution": self.institution,
            "raw_rows": 0,
            "accepted_rows": 0,
            "pending_candidates": 0,
            "departments": set(),
            "sources": sources,
            "parsed_sources": [],
            "failed_sources": [],
            "stats": {},
        }

        discovered_files = []
        if offline:
            discovered_files = self.load_manifest()
            if not discovered_files:
                discovered_files = self.discover_cached_files()
        else:
            categories = [(535, 536), (535, 535), (537, 537)]
            ajax_url = "https://www.gg.go.kr/ajax/board/getList.do"
            headers = {"X-Requested-With": "XMLHttpRequest"}

            for bsIdx, bcIdx in categories:
                offset = 0
                limit = 15
                for _ in range(max_pages):
                    data = {
                        "bsIdx": str(bsIdx),
                        "bcIdx": str(bcIdx),
                        "menuId": "1778",
                        "isManager": "false",
                        "isCharge": "false",
                        "offset": str(offset),
                        "limit": str(limit),
                    }
                    try:
                        raw_json = fetch_url(ajax_url, post_data=data, headers=headers)
                        res = json.loads(raw_json.decode("utf-8"))
                        items = res.get("items", [])
                        if not items:
                            break
                        for it in items:
                            image_idx = it.get("IMAGE_IDX")
                            b_idx = it.get("B_IDX")
                            writer = (it.get("WRITER") or "").strip()
                            subj = it.get("SUBJECT", "")
                            img_str = it.get("IMAGE_STR", "")

                            view_url = f"https://www.gg.go.kr/bbs/boardView.do?bsIdx={bsIdx}&bIdx={b_idx}&menuId=1778&bcIdx={bcIdx}" if b_idx else self.config["board_url"]
                            raw_dept = None
                            dept = None
                            evidence = None
                            if writer and is_valid_department_name(writer):
                                raw_dept = writer
                                dept = canonicalize_department_name(raw_dept)
                                evidence = {
                                    "kind": "bulletin_board_cell",
                                    "location": "WRITER",
                                    "sourceURL": view_url,
                                }
                            else:
                                title_dept = extract_department_from_title(subj)
                                if title_dept and is_valid_department_name(title_dept):
                                    raw_dept = title_dept
                                    dept = canonicalize_department_name(raw_dept)
                                    evidence = {
                                        "kind": "post_title_brackets",
                                        "location": "SUBJECT",
                                        "sourceURL": view_url,
                                    }

                            if image_idx and b_idx:
                                dl_url = f"https://www.gg.go.kr/cmmn/download.do?idx={image_idx}"
                                discovered_files.append({
                                    "download_url": dl_url,
                                    "view_url": view_url,
                                    "department": dept,
                                    "department_raw": raw_dept,
                                    "department_evidence": evidence,
                                    "filename": img_str or f"gg_{image_idx}.xlsx",
                                    "b_idx": b_idx,
                                })
                        offset += limit
                        if len(discovered_files) >= max_files:
                            break
                    except Exception as e:
                        print(f"[WARN] Gyeonggi ajax error: {e}", file=sys.stderr)
                        break
                if len(discovered_files) >= max_files:
                    break

            if discovered_files:
                self.save_manifest(discovered_files)

        # Process downloaded / cached files
        for item in discovered_files[:max_files]:
            try:
                local_path = item.get("local_path")
                if not local_path or not os.path.exists(local_path):
                    local_path, sha, sz = download_cached_file(
                        item["download_url"],
                        cache_dir=os.path.join(self.cache_dir, "downloads", "gg"),
                        filename_hint=item.get("filename"),
                        offline=offline,
                    )
                else:
                    sha = compute_file_sha256(local_path)
                    sz = os.path.getsize(local_path)

                raw_dept = item.get("department_raw")
                evidence = item.get("department_evidence")
                dept = canonicalize_department_name(item.get("department") or raw_dept) if (item.get("department") or raw_dept) and is_valid_department_name(item.get("department") or raw_dept) else None
                rec_pfx = f"gg-{item.get('b_idx', os.path.basename(local_path)[:8])}"

                recs, pending, stats = dispatch_and_parse_file(
                    file_path=local_path,
                    institution=self.institution,
                    default_department=dept,
                    source_url=item.get("view_url", self.config["board_url"]),
                    record_prefix=rec_pfx,
                    hint_region=self.region_code,
                    address_resolver=address_resolver,
                    soffice_path=self.soffice_path,
                    cache_dir=os.path.join(self.cache_dir, "downloads", "gg"),
                    default_department_raw=raw_dept,
                    default_department_evidence=evidence,
                )

                source_meta = {
                    "region": self.region_code,
                    "institution": self.institution,
                    "department": dept,
                    "downloadURL": item.get("download_url", ""),
                    "sourceURL": item.get("view_url", self.config["board_url"]),
                    "sha256": sha,
                    "bytes": sz,
                    "fetchedAt": datetime.datetime.now().isoformat(),
                    "status": stats.get("status", "failed"),
                    "format": stats.get("format", "unknown"),
                    "rawRows": stats.get("raw_rows", 0),
                    "parseStats": dict(stats),
                    "acceptedRows": len(recs),
                    "pendingCandidates": len(pending),
                    "failureReason": stats.get("failureReason"),
                }
                sources.append(source_meta)

                if stats.get("status") == "parsed":
                    records.extend(recs)
                    pending_transactions.extend(pending)
                    coverage_stats["raw_rows"] += stats.get("raw_rows", 0)
                    coverage_stats["accepted_rows"] += stats.get("accepted", 0)
                    coverage_stats["pending_candidates"] += len(pending)
                    coverage_stats["parsed_sources"].append(source_meta)
                    if dept and is_valid_department_name(dept):
                        coverage_stats["departments"].add(dept)
                    for r in recs:
                        r_dept = r.get("department")
                        if r_dept and is_valid_department_name(r_dept):
                            coverage_stats["departments"].add(canonicalize_department_name(r_dept))
                else:
                    coverage_stats["failed_sources"].append(source_meta)
            except Exception as e:
                print(f"[WARN] Error processing Gyeonggi item {item}: {e}", file=sys.stderr)

        coverage_stats["departments"] = sorted(list(coverage_stats["departments"]))
        return records, pending_transactions, sources, coverage_stats


class DaejeonCollector(BaseRegionCollector):
    def collect(
        self,
        max_pages: int = 20,
        max_files: int = 200,
        offline: bool = False,
        address_resolver: Optional[AddressResolver] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        records = []
        pending_transactions = []
        sources = []
        coverage_stats: Dict[str, Any] = {
            "region": self.region_code,
            "institution": self.institution,
            "raw_rows": 0,
            "accepted_rows": 0,
            "pending_candidates": 0,
            "departments": set(),
            "sources": sources,
            "parsed_sources": [],
            "failed_sources": [],
            "stats": {},
        }

        discovered_articles = []
        if offline:
            discovered_articles = self.load_manifest()
            if not discovered_articles:
                discovered_articles = self.discover_cached_files()
        else:
            base_url = "https://www.daejeon.go.kr/drh/open/drhDataOpen/drhDataOpenBoardView.do"
            for p in range(1, max_pages + 1):
                post_data = {
                    "pageIndex": "1",
                    "subPageIndex": str(p),
                    "boardSeq": "694",
                    "menuSeq": "",
                    "searchCondition": "",
                    "searchKeyword": "",
                    "searchCondition2": "",
                    "searchCondition3": "",
                }
                try:
                    html_text = fetch_url(base_url, post_data=post_data).decode("utf-8", errors="replace")
                    rows = re.findall(
                        r'articleSeq=(\d+)[^"]*">.*?</a>\s*</td>\s*<td class="date">([^<]*)</td>\s*<td class="subject">([^<]*)</td>',
                        html_text,
                        re.S,
                    )
                    if not rows:
                        break
                    for art_seq, dt, dept_raw in rows:
                        dept_clean = dept_raw.strip()
                        view_url = f"https://www.daejeon.go.kr/drh/open/drhDataOpen/drhDataOpenBoardArticleView.do?boardSeq=694&articleSeq={art_seq}"
                        raw_dept = None
                        dept = None
                        evidence = None
                        if is_valid_department_name(dept_clean):
                            raw_dept = dept_clean
                            dept = canonicalize_department_name(raw_dept)
                            evidence = {
                                "kind": "bulletin_board_cell",
                                "location": "td.subject",
                                "sourceURL": view_url,
                            }
                        else:
                            title_dept = extract_department_from_title(dept_clean)
                            if title_dept and is_valid_department_name(title_dept):
                                raw_dept = title_dept
                                dept = canonicalize_department_name(raw_dept)
                                evidence = {
                                    "kind": "post_title_brackets",
                                    "location": "title",
                                    "sourceURL": view_url,
                                }
                        discovered_articles.append({
                            "articleSeq": art_seq,
                            "date": dt.strip(),
                            "department": dept,
                            "department_raw": raw_dept,
                            "department_evidence": evidence,
                            "view_url": view_url,
                        })
                    if len(discovered_articles) >= max_files:
                        break
                except Exception as e:
                    print(f"[WARN] Daejeon list page {p} error: {e}", file=sys.stderr)
                    break

            if discovered_articles:
                self.save_manifest(discovered_articles)

        for item in discovered_articles[:max_files]:
            art_seq = item.get("articleSeq", "")
            raw_dept = item.get("department_raw")
            evidence = item.get("department_evidence")
            dept = canonicalize_department_name(item.get("department") or raw_dept) if (item.get("department") or raw_dept) and is_valid_department_name(item.get("department") or raw_dept) else None
            view_url = item.get("view_url", self.config["board_url"])
            try:
                local_path = item.get("local_path")
                dl_url = item.get("download_url", "")
                if not local_path or not os.path.exists(local_path):
                    view_html = fetch_url(view_url).decode("utf-8", errors="replace")
                    m_dl = re.search(r"fileDownLoad\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)", view_html)
                    if not m_dl:
                        continue
                    rel_path, file_name = m_dl.group(1), m_dl.group(2)
                    dl_url = urllib.parse.urljoin("https://www.daejeon.go.kr/", rel_path)

                    local_path, sha, sz = download_cached_file(
                        dl_url,
                        cache_dir=os.path.join(self.cache_dir, "downloads", "dj"),
                        filename_hint=file_name,
                        offline=offline,
                    )
                else:
                    sha = compute_file_sha256(local_path)
                    sz = os.path.getsize(local_path)

                rec_pfx = f"dj-{art_seq or os.path.basename(local_path)[:8]}"

                recs, pending, stats = dispatch_and_parse_file(
                    file_path=local_path,
                    institution=self.institution,
                    default_department=dept,
                    source_url=view_url,
                    record_prefix=rec_pfx,
                    hint_region=self.region_code,
                    address_resolver=address_resolver,
                    soffice_path=self.soffice_path,
                    cache_dir=os.path.join(self.cache_dir, "downloads", "dj"),
                    default_department_raw=raw_dept,
                    default_department_evidence=evidence,
                )

                source_meta = {
                    "region": self.region_code,
                    "institution": self.institution,
                    "department": dept,
                    "downloadURL": dl_url,
                    "sourceURL": view_url,
                    "sha256": sha,
                    "bytes": sz,
                    "fetchedAt": datetime.datetime.now().isoformat(),
                    "status": stats.get("status", "failed"),
                    "format": stats.get("format", "unknown"),
                    "rawRows": stats.get("raw_rows", 0),
                    "parseStats": dict(stats),
                    "acceptedRows": len(recs),
                    "pendingCandidates": len(pending),
                    "failureReason": stats.get("failureReason"),
                }
                sources.append(source_meta)

                if stats.get("status") == "parsed":
                    records.extend(recs)
                    pending_transactions.extend(pending)
                    coverage_stats["raw_rows"] += stats.get("raw_rows", 0)
                    coverage_stats["accepted_rows"] += stats.get("accepted", 0)
                    coverage_stats["pending_candidates"] += len(pending)
                    coverage_stats["parsed_sources"].append(source_meta)
                    if dept and is_valid_department_name(dept):
                        coverage_stats["departments"].add(dept)
                    for r in recs:
                        r_dept = r.get("department")
                        if r_dept and is_valid_department_name(r_dept):
                            coverage_stats["departments"].add(canonicalize_department_name(r_dept))
                else:
                    coverage_stats["failed_sources"].append(source_meta)
            except Exception as e:
                print(f"[WARN] Error processing Daejeon article {art_seq}: {e}", file=sys.stderr)

        coverage_stats["departments"] = sorted(list(coverage_stats["departments"]))
        return records, pending_transactions, sources, coverage_stats


class SejongCollector(BaseRegionCollector):
    def collect(
        self,
        max_pages: int = 20,
        max_files: int = 200,
        offline: bool = False,
        address_resolver: Optional[AddressResolver] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        records = []
        pending_transactions = []
        sources = []
        coverage_stats: Dict[str, Any] = {
            "region": self.region_code,
            "institution": self.institution,
            "raw_rows": 0,
            "accepted_rows": 0,
            "pending_candidates": 0,
            "departments": set(),
            "sources": sources,
            "parsed_sources": [],
            "failed_sources": [],
            "stats": {},
        }

        discovered_items = []
        if offline:
            discovered_items = self.load_manifest()
            if not discovered_items:
                discovered_items = self.discover_cached_files()
        else:
            list_url = "https://www.sejong.go.kr/bbs/R0091/list.do"
            for p in range(1, max_pages + 1):
                try:
                    url = f"{list_url}?pageIndex={p}"
                    txt = fetch_url(url).decode("utf-8", errors="replace")
                    matches = re.findall(
                        r'<a href="(/bbs/R0091/view\.do\?nttId=([^"&]+)[^"]*)".*?>(.*?)</a>.*?<td data-cell-header="작성자">([^<]*)</td>',
                        txt,
                        re.S,
                    )
                    if not matches:
                        matches_fallback = re.findall(
                            r'<a href="(/bbs/R0091/view\.do\?nttId=([^"&]+)[^"]*)".*?>(.*?)</a>',
                            txt,
                            re.S,
                        )
                        matches = [(m[0], m[1], m[2], "") for m in matches_fallback]
                    if not matches:
                        break
                    for link, ntt_id, title, writer_raw in matches:
                        clean_t = re.sub(r"<[^>]+>", "", title).strip()
                        view_url = urllib.parse.urljoin("https://www.sejong.go.kr", link)
                        writer_clean = writer_raw.strip()
                        raw_dept = None
                        dept = None
                        evidence = None
                        if is_valid_department_name(writer_clean):
                            raw_dept = writer_clean
                            dept = canonicalize_department_name(raw_dept)
                            evidence = {
                                "kind": "bulletin_board_cell",
                                "location": "td[작성자]",
                                "sourceURL": view_url,
                            }
                        else:
                            title_dept = extract_department_from_title(clean_t)
                            if title_dept and is_valid_department_name(title_dept):
                                raw_dept = title_dept
                                dept = canonicalize_department_name(raw_dept)
                                evidence = {
                                    "kind": "post_title_brackets",
                                    "location": "title",
                                    "sourceURL": view_url,
                                }
                        discovered_items.append({
                            "nttId": ntt_id,
                            "title": clean_t,
                            "department": dept,
                            "department_raw": raw_dept,
                            "department_evidence": evidence,
                            "view_url": view_url,
                        })
                    if len(discovered_items) >= max_files:
                        break
                except Exception as e:
                    print(f"[WARN] Sejong list page {p} error: {e}", file=sys.stderr)
                    break

            if discovered_items:
                self.save_manifest(discovered_items)

        for it in discovered_items[:max_files]:
            ntt_id = it.get("nttId", "")
            raw_dept = it.get("department_raw")
            evidence = it.get("department_evidence")
            dept = canonicalize_department_name(it.get("department") or raw_dept) if (it.get("department") or raw_dept) and is_valid_department_name(it.get("department") or raw_dept) else None
            view_url = it.get("view_url", f"https://www.sejong.go.kr/bbs/R0091/view.do?nttId={ntt_id}&mno=sub01_0301")
            try:
                local_path = it.get("local_path")
                dl_url = it.get("download_url", "")
                if not local_path or not os.path.exists(local_path):
                    view_html = fetch_url(view_url).decode("utf-8", errors="replace")
                    m_file = re.search(r"fn_egov_downFile\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)", view_html)
                    if not m_file:
                        continue
                    atch_id, sn = m_file.group(1), m_file.group(2)
                    dl_url = f"https://www.sejong.go.kr/cmm/fms/FileDown.do?atchFileId={atch_id}&fileSn={sn}"

                    # Try to extract real filename from link text
                    m_fname = re.search(r"fn_egov_downFile\([^)]+\)[^>]*>.*?([가-힣\w\-\.]+\.(?:xlsx|xls|hwpx|hwp|pdf))", view_html, re.S)
                    fn_hint = m_fname.group(1).strip() if m_fname else f"sj_{atch_id}_{sn}.bin"

                    local_path, sha, sz = download_cached_file(
                        dl_url,
                        cache_dir=os.path.join(self.cache_dir, "downloads", "sj"),
                        filename_hint=fn_hint,
                        offline=offline,
                    )
                else:
                    sha = compute_file_sha256(local_path)
                    sz = os.path.getsize(local_path)

                rec_pfx = f"sj-{ntt_id or os.path.basename(local_path)[:8]}"

                recs, pending, stats = dispatch_and_parse_file(
                    file_path=local_path,
                    institution=self.institution,
                    default_department=dept,
                    source_url=view_url,
                    record_prefix=rec_pfx,
                    hint_region=self.region_code,
                    address_resolver=address_resolver,
                    soffice_path=self.soffice_path,
                    cache_dir=os.path.join(self.cache_dir, "downloads", "sj"),
                    default_department_raw=raw_dept,
                    default_department_evidence=evidence,
                )

                source_meta = {
                    "region": self.region_code,
                    "institution": self.institution,
                    "department": dept,
                    "downloadURL": dl_url,
                    "sourceURL": view_url,
                    "sha256": sha,
                    "bytes": sz,
                    "fetchedAt": datetime.datetime.now().isoformat(),
                    "status": stats.get("status", "failed"),
                    "format": stats.get("format", "unknown"),
                    "rawRows": stats.get("raw_rows", 0),
                    "parseStats": dict(stats),
                    "acceptedRows": len(recs),
                    "pendingCandidates": len(pending),
                    "failureReason": stats.get("failureReason"),
                }
                sources.append(source_meta)

                if stats.get("status") == "parsed":
                    records.extend(recs)
                    pending_transactions.extend(pending)
                    coverage_stats["raw_rows"] += stats.get("raw_rows", 0)
                    coverage_stats["accepted_rows"] += stats.get("accepted", 0)
                    coverage_stats["pending_candidates"] += len(pending)
                    coverage_stats["parsed_sources"].append(source_meta)
                    if dept and is_valid_department_name(dept):
                        coverage_stats["departments"].add(dept)
                    for r in recs:
                        r_dept = r.get("department")
                        if r_dept and is_valid_department_name(r_dept):
                            coverage_stats["departments"].add(canonicalize_department_name(r_dept))
                else:
                    coverage_stats["failed_sources"].append(source_meta)
            except Exception as e:
                print(f"[WARN] Error processing Sejong nttId {ntt_id}: {e}", file=sys.stderr)

        coverage_stats["departments"] = sorted(list(coverage_stats["departments"]))
        return records, pending_transactions, sources, coverage_stats


class ChungnamCollector(BaseRegionCollector):
    def collect(
        self,
        max_pages: int = 20,
        max_files: int = 200,
        offline: bool = False,
        address_resolver: Optional[AddressResolver] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        records = []
        pending_transactions = []
        sources = []
        coverage_stats: Dict[str, Any] = {
            "region": self.region_code,
            "institution": self.institution,
            "raw_rows": 0,
            "accepted_rows": 0,
            "pending_candidates": 0,
            "departments": set(),
            "sources": sources,
            "parsed_sources": [],
            "failed_sources": [],
            "stats": {},
        }

        discovered_items = []
        if offline:
            discovered_items = self.load_manifest()
            if not discovered_items:
                discovered_items = self.discover_cached_files()
        else:
            list_url = "https://www.chungnam.go.kr/cnportal/bbs/B0000187/list.do?menuNo=500122"
            for p in range(1, max_pages + 1):
                try:
                    url = f"{list_url}&pageIndex={p}"
                    txt = fetch_url(url).decode("utf-8", errors="replace")
                    matches = re.findall(
                        r'href="(/cnportal/bbs/B0000187/view\.do\?nttId=(\d+)[^"]*)".*?>(.*?)</a>.*?<td>.*?</td>\s*<td>\s*([^<]+)\s*</td>',
                        txt,
                        re.S,
                    )
                    if not matches:
                        matches_fallback = re.findall(
                            r'href="(/cnportal/bbs/B0000187/view\.do\?nttId=(\d+)[^"]*)".*?>(.*?)</a>',
                            txt,
                            re.S,
                        )
                        matches = [(m[0], m[1], m[2], "") for m in matches_fallback]
                    if not matches:
                        break
                    for link, ntt_id, title, dept_cell in matches:
                        clean_t = re.sub(r"<[^>]+>", "", title).strip()
                        view_url = urllib.parse.urljoin("https://www.chungnam.go.kr", link)
                        dept_clean = dept_cell.strip()
                        raw_dept = None
                        dept = None
                        evidence = None
                        if is_valid_department_name(dept_clean):
                            raw_dept = dept_clean
                            dept = canonicalize_department_name(raw_dept)
                            evidence = {
                                "kind": "bulletin_board_cell",
                                "location": "td.dept",
                                "sourceURL": view_url,
                            }
                        else:
                            title_dept = extract_department_from_title(clean_t)
                            if title_dept and is_valid_department_name(title_dept):
                                raw_dept = title_dept
                                dept = canonicalize_department_name(raw_dept)
                                evidence = {
                                    "kind": "post_title_brackets",
                                    "location": "title",
                                    "sourceURL": view_url,
                                }
                        discovered_items.append({
                            "nttId": ntt_id,
                            "title": clean_t,
                            "department": dept,
                            "department_raw": raw_dept,
                            "department_evidence": evidence,
                            "view_url": view_url,
                        })
                    if len(discovered_items) >= max_files:
                        break
                except Exception as e:
                    print(f"[WARN] Chungnam list page {p} error: {e}", file=sys.stderr)
                    break

            if discovered_items:
                self.save_manifest(discovered_items)

        for it in discovered_items[:max_files]:
            ntt_id = it.get("nttId", "")
            raw_dept = it.get("department_raw")
            evidence = it.get("department_evidence")
            dept = canonicalize_department_name(it.get("department") or raw_dept) if (it.get("department") or raw_dept) and is_valid_department_name(it.get("department") or raw_dept) else None
            view_url = it.get("view_url", f"https://www.chungnam.go.kr/cnportal/bbs/B0000187/view.do?nttId={ntt_id}&menuNo=500122")
            try:
                local_path = it.get("local_path")
                dl_url = it.get("download_url", "")
                if not local_path or not os.path.exists(local_path):
                    view_html = fetch_url(view_url).decode("utf-8", errors="replace")
                    m_dl = re.search(r'href="(/cnportal/cmmn/file/fileDown\.do\?[^"]*atchFileId=([^"&]+)[^"]*fileSn=(\d+)[^"]*)"', view_html)
                    if not m_dl:
                        continue
                    rel_url, atch_id, file_sn = m_dl.group(1), m_dl.group(2), m_dl.group(3)
                    dl_url = urllib.parse.urljoin("https://www.chungnam.go.kr", html.unescape(rel_url))

                    # Extract true filename hint from previewAjax or anchor
                    m_fname = re.search(r"previewAjax\([^,]+,\s*['\"]([^'\"]+)['\"]\)", view_html)
                    fn_hint = m_fname.group(1).strip() if m_fname else f"cn_{atch_id}_{file_sn}.bin"

                    local_path, sha, sz = download_cached_file(
                        dl_url,
                        cache_dir=os.path.join(self.cache_dir, "downloads", "cn"),
                        filename_hint=fn_hint,
                        offline=offline,
                    )
                else:
                    sha = compute_file_sha256(local_path)
                    sz = os.path.getsize(local_path)

                rec_pfx = f"cn-{ntt_id or os.path.basename(local_path)[:8]}"

                recs, pending, stats = dispatch_and_parse_file(
                    file_path=local_path,
                    institution=self.institution,
                    default_department=dept,
                    source_url=view_url,
                    record_prefix=rec_pfx,
                    hint_region=self.region_code,
                    address_resolver=address_resolver,
                    soffice_path=self.soffice_path,
                    cache_dir=os.path.join(self.cache_dir, "downloads", "cn"),
                    default_department_raw=raw_dept,
                    default_department_evidence=evidence,
                )

                source_meta = {
                    "region": self.region_code,
                    "institution": self.institution,
                    "department": dept,
                    "downloadURL": dl_url,
                    "sourceURL": view_url,
                    "sha256": sha,
                    "bytes": sz,
                    "fetchedAt": datetime.datetime.now().isoformat(),
                    "status": stats.get("status", "failed"),
                    "format": stats.get("format", "unknown"),
                    "rawRows": stats.get("raw_rows", 0),
                    "parseStats": dict(stats),
                    "acceptedRows": len(recs),
                    "pendingCandidates": len(pending),
                    "failureReason": stats.get("failureReason"),
                }
                sources.append(source_meta)

                if stats.get("status") == "parsed":
                    records.extend(recs)
                    pending_transactions.extend(pending)
                    coverage_stats["raw_rows"] += stats.get("raw_rows", 0)
                    coverage_stats["accepted_rows"] += stats.get("accepted", 0)
                    coverage_stats["pending_candidates"] += len(pending)
                    coverage_stats["parsed_sources"].append(source_meta)
                    if dept and is_valid_department_name(dept):
                        coverage_stats["departments"].add(dept)
                    for r in recs:
                        r_dept = r.get("department")
                        if r_dept and is_valid_department_name(r_dept):
                            coverage_stats["departments"].add(canonicalize_department_name(r_dept))
                else:
                    coverage_stats["failed_sources"].append(source_meta)
            except Exception as e:
                print(f"[WARN] Error processing Chungnam nttId {ntt_id}: {e}", file=sys.stderr)

        coverage_stats["departments"] = sorted(list(coverage_stats["departments"]))
        return records, pending_transactions, sources, coverage_stats


class ChungbukCollector(BaseRegionCollector):
    def collect(
        self,
        max_pages: int = 20,
        max_files: int = 200,
        offline: bool = False,
        address_resolver: Optional[AddressResolver] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        records = []
        pending_transactions = []
        sources = []
        coverage_stats: Dict[str, Any] = {
            "region": self.region_code,
            "institution": self.institution,
            "raw_rows": 0,
            "accepted_rows": 0,
            "pending_candidates": 0,
            "departments": set(),
            "sources": sources,
            "parsed_sources": [],
            "failed_sources": [],
            "stats": {},
        }

        discovered_items = []
        if offline:
            discovered_items = self.load_manifest()
            if not discovered_items:
                discovered_items = self.discover_cached_files()
        else:
            list_url = "https://www.chungbuk.go.kr/www/selectBbsNttList.do?key=211&bbsNo=2"
            for p in range(1, max_pages + 1):
                try:
                    url = f"{list_url}&pageIndex={p}"
                    # fetch_url will automatically use safe subprocess curl GET for chungbuk.go.kr
                    txt = fetch_url(url).decode("utf-8", errors="replace")

                    # Parse Chungbuk table rows: extract nttNo, title, dept, and direct file download url if present
                    row_matches = re.findall(
                        r'<tr>\s*<td>.*?</td>\s*<td class="p-subject">\s*<a href="[^"]*nttNo=(\d+)[^"]*".*?>(.*?)</a>.*?</td>\s*<td>\s*([^<]+)\s*</td>.*?<td class="p-file">\s*(.*?)\s*</td>\s*</tr>',
                        txt,
                        re.S,
                    )
                    if not row_matches:
                        # Fallback row matching
                        row_matches = re.findall(
                            r'href="[^"]*nttNo=(\d+)[^"]*".*?>(.*?)</a>.*?<td>\s*([가-힣\w\s]+(?:소방서|사업소|과|관|실|팀|대변인|본부))\s*</td>',
                            txt,
                            re.S,
                        )
                        row_matches = [(m[0], m[1], m[2], "") for m in row_matches]

                    if not row_matches:
                        break

                    for match in row_matches:
                        ntt_no = match[0]
                        title_raw = match[1]
                        dept_raw = match[2]
                        file_td = match[3] if len(match) > 3 else ""

                        clean_t = re.sub(r"<[^>]+>", "", title_raw).strip()
                        clean_t = clean_t.replace("&#40;", "(").replace("&#41;", ")")

                        view_url = f"https://www.chungbuk.go.kr/www/selectBbsNttView.do?key=211&bbsNo=2&nttNo={ntt_no}"
                        dept_clean = dept_raw.strip() if dept_raw else ""
                        raw_dept = None
                        dept = None
                        evidence = None
                        if is_valid_department_name(dept_clean):
                            raw_dept = dept_clean
                            dept = canonicalize_department_name(raw_dept)
                            evidence = {
                                "kind": "bulletin_board_cell",
                                "location": "td[3]",
                                "sourceURL": view_url,
                            }
                        else:
                            title_dept = extract_department_from_title(clean_t)
                            if title_dept and is_valid_department_name(title_dept):
                                raw_dept = title_dept
                                dept = canonicalize_department_name(raw_dept)
                                evidence = {
                                    "kind": "post_title_brackets",
                                    "location": "title",
                                    "sourceURL": view_url,
                                }

                        # Check for direct download link in row
                        dl_rel = None
                        m_fl = re.search(r'href="(/www/downloadBbsFile\.do\?[^"]*atchmnflNo=(\d+)[^"]*)"', file_td)
                        if m_fl:
                            dl_rel = m_fl.group(1)

                        dl_url = urllib.parse.urljoin("https://www.chungbuk.go.kr", html.unescape(dl_rel)) if dl_rel else None

                        discovered_items.append({
                            "nttNo": ntt_no,
                            "title": clean_t,
                            "department": dept,
                            "department_raw": raw_dept,
                            "department_evidence": evidence,
                            "view_url": view_url,
                            "download_url": dl_url,
                        })

                    if len(discovered_items) >= max_files:
                        break
                except Exception as e:
                    print(f"[WARN] Chungbuk list page {p} error: {e}", file=sys.stderr)
                    break

            if discovered_items:
                self.save_manifest(discovered_items)

        for it in discovered_items[:max_files]:
            ntt_no = it.get("nttNo", "")
            raw_dept = it.get("department_raw")
            evidence = it.get("department_evidence")
            dept = canonicalize_department_name(it.get("department") or raw_dept) if (it.get("department") or raw_dept) and is_valid_department_name(it.get("department") or raw_dept) else None
            view_url = it.get("view_url", self.config["board_url"])
            dl_url = it.get("download_url")
            try:
                local_path = it.get("local_path")
                if not local_path or not os.path.exists(local_path):
                    if not dl_url:
                        view_html = fetch_url(view_url).decode("utf-8", errors="replace")
                        m_dl = re.search(r'href="(/www/downloadBbsFile\.do\?[^"]*atchmnflNo=(\d+)[^"]*)"', view_html)
                        if not m_dl:
                            continue
                        rel_url, fl_no = m_dl.group(1), m_dl.group(2)
                        dl_url = urllib.parse.urljoin("https://www.chungbuk.go.kr", html.unescape(rel_url))
                    else:
                        m_fl_no = re.search(r"atchmnflNo=(\d+)", dl_url)
                        fl_no = m_fl_no.group(1) if m_fl_no else ntt_no

                    local_path, sha, sz = download_cached_file(
                        dl_url,
                        cache_dir=os.path.join(self.cache_dir, "downloads", "cb"),
                        filename_hint=f"cb_{fl_no}.xls",
                        offline=offline,
                    )
                else:
                    sha = compute_file_sha256(local_path)
                    sz = os.path.getsize(local_path)

                rec_pfx = f"cb-{ntt_no or os.path.basename(local_path)[:8]}"

                recs, pending, stats = dispatch_and_parse_file(
                    file_path=local_path,
                    institution=self.institution,
                    default_department=dept,
                    source_url=view_url,
                    record_prefix=rec_pfx,
                    hint_region=self.region_code,
                    address_resolver=address_resolver,
                    soffice_path=self.soffice_path,
                    cache_dir=os.path.join(self.cache_dir, "downloads", "cb"),
                    default_department_raw=raw_dept,
                    default_department_evidence=evidence,
                )

                source_meta = {
                    "region": self.region_code,
                    "institution": self.institution,
                    "department": dept,
                    "downloadURL": dl_url or "",
                    "sourceURL": view_url,
                    "sha256": sha,
                    "bytes": sz,
                    "fetchedAt": datetime.datetime.now().isoformat(),
                    "status": stats.get("status", "failed"),
                    "format": stats.get("format", "unknown"),
                    "rawRows": stats.get("raw_rows", 0),
                    "parseStats": dict(stats),
                    "acceptedRows": len(recs),
                    "pendingCandidates": len(pending),
                    "failureReason": stats.get("failureReason"),
                }
                sources.append(source_meta)

                if stats.get("status") == "parsed":
                    records.extend(recs)
                    pending_transactions.extend(pending)
                    coverage_stats["raw_rows"] += stats.get("raw_rows", 0)
                    coverage_stats["accepted_rows"] += stats.get("accepted", 0)
                    coverage_stats["pending_candidates"] += len(pending)
                    coverage_stats["parsed_sources"].append(source_meta)
                    if dept and is_valid_department_name(dept):
                        coverage_stats["departments"].add(dept)
                    for r in recs:
                        r_dept = r.get("department")
                        if r_dept and is_valid_department_name(r_dept):
                            coverage_stats["departments"].add(canonicalize_department_name(r_dept))
                else:
                    coverage_stats["failed_sources"].append(source_meta)
            except Exception as e:
                print(f"[WARN] Error processing Chungbuk nttNo {ntt_no}: {e}", file=sys.stderr)

        coverage_stats["departments"] = sorted(list(coverage_stats["departments"]))
        return records, pending_transactions, sources, coverage_stats


class IncheonCollector(BaseRegionCollector):
    def collect(
        self,
        max_pages: int = 20,
        max_files: int = 200,
        offline: bool = False,
        address_resolver: Optional[AddressResolver] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        records = []
        pending_transactions = []
        sources = []
        coverage_stats: Dict[str, Any] = {
            "region": self.region_code,
            "institution": self.institution,
            "raw_rows": 0,
            "accepted_rows": 0,
            "pending_candidates": 0,
            "departments": set(),
            "sources": sources,
            "parsed_sources": [],
            "failed_sources": [],
            "stats": {},
        }

        discovered_items = []
        if offline:
            discovered_items = self.load_manifest()
            if not discovered_items:
                discovered_items = self.discover_cached_files()
        else:
            list_url = "https://www.incheon.go.kr/open/OPEN010305"
            for p in range(1, max_pages + 1):
                try:
                    url = f"{list_url}?curPage={p}"
                    txt = fetch_url(url).decode("utf-8", errors="replace")
                    matches = re.findall(
                        r'<a href="/open/OPEN010305/(\d+)"[^>]*>(.*?)</a>\s*</td>\s*<td[^>]*>\s*([^<]+)\s*</td>',
                        txt,
                        re.S,
                    )
                    if not matches:
                        matches_fallback = re.findall(
                            r'<a href="/open/OPEN010305/(\d+)"[^>]*>(.*?)</a>',
                            txt,
                            re.S,
                        )
                        matches = [(m[0], m[1], "") for m in matches_fallback]
                    if not matches:
                        break
                    for upper_no, title, dept_cell in matches:
                        clean_t = re.sub(r"<[^>]+>", "", title).strip()
                        dept_clean = dept_cell.strip()
                        view_url = f"https://www.incheon.go.kr/open/OPEN010305/{upper_no}"
                        raw_dept = None
                        dept = None
                        evidence = None
                        if is_valid_department_name(dept_clean):
                            raw_dept = dept_clean
                            dept = canonicalize_department_name(raw_dept)
                            evidence = {
                                "kind": "bulletin_board_cell",
                                "location": "td.dept",
                                "sourceURL": view_url,
                            }
                        else:
                            title_dept = extract_department_from_title(clean_t)
                            if title_dept and is_valid_department_name(title_dept):
                                raw_dept = title_dept
                                dept = canonicalize_department_name(raw_dept)
                                evidence = {
                                    "kind": "post_title_brackets",
                                    "location": "title",
                                    "sourceURL": view_url,
                                }
                        discovered_items.append({
                            "upperNo": upper_no,
                            "title": clean_t,
                            "department": dept,
                            "department_raw": raw_dept,
                            "department_evidence": evidence,
                            "view_url": view_url,
                        })
                    if len(discovered_items) >= max_files:
                        break
                except Exception as e:
                    print(f"[WARN] Incheon list page {p} error: {e}", file=sys.stderr)
                    break

            if discovered_items:
                self.save_manifest(discovered_items)

        for it in discovered_items[:max_files]:
            upper_no = it.get("upperNo", "")
            raw_dept = it.get("department_raw")
            evidence = it.get("department_evidence")
            dept = canonicalize_department_name(it.get("department") or raw_dept) if (it.get("department") or raw_dept) and is_valid_department_name(it.get("department") or raw_dept) else None
            view_url = it.get("view_url", f"https://www.incheon.go.kr/open/OPEN010305/{upper_no}")
            try:
                local_path = it.get("local_path")
                dl_url = it.get("download_url", "")
                if not local_path or not os.path.exists(local_path):
                    view_html = fetch_url(view_url).decode("utf-8", errors="replace")
                    m_dl = re.search(r'href="(/comm/getFile\?[^"]*srvcId=BBSTY1[^"]*upperNo=(\d+)[^"]*fileNo=(\d+)[^"]*)"', view_html)
                    if not m_dl:
                        continue
                    rel_url, up_no, fl_no = m_dl.group(1), m_dl.group(2), m_dl.group(3)
                    dl_url = urllib.parse.urljoin("https://www.incheon.go.kr", html.unescape(rel_url))

                    m_fn = re.search(r'class="file-name">([^<]+)<', view_html)
                    fn_hint = m_fn.group(1).strip() if m_fn else f"ic_{upper_no}.pdf"

                    local_path, sha, sz = download_cached_file(
                        dl_url,
                        cache_dir=os.path.join(self.cache_dir, "downloads", "ic"),
                        filename_hint=fn_hint,
                        offline=offline,
                    )
                else:
                    sha = compute_file_sha256(local_path)
                    sz = os.path.getsize(local_path)

                rec_pfx = f"ic-{upper_no or os.path.basename(local_path)[:8]}"

                recs, pending, stats = dispatch_and_parse_file(
                    file_path=local_path,
                    institution=self.institution,
                    default_department=dept,
                    source_url=view_url,
                    record_prefix=rec_pfx,
                    hint_region=self.region_code,
                    address_resolver=address_resolver,
                    soffice_path=self.soffice_path,
                    cache_dir=os.path.join(self.cache_dir, "downloads", "ic"),
                    default_department_raw=raw_dept,
                    default_department_evidence=evidence,
                )

                source_meta = {
                    "region": self.region_code,
                    "institution": self.institution,
                    "department": dept,
                    "downloadURL": dl_url,
                    "sourceURL": view_url,
                    "sha256": sha,
                    "bytes": sz,
                    "fetchedAt": datetime.datetime.now().isoformat(),
                    "status": stats.get("status", "failed"),
                    "format": stats.get("format", "unknown"),
                    "rawRows": stats.get("raw_rows", 0),
                    "parseStats": dict(stats),
                    "acceptedRows": len(recs),
                    "pendingCandidates": len(pending),
                    "failureReason": stats.get("failureReason"),
                }
                sources.append(source_meta)

                if stats.get("status") == "parsed":
                    records.extend(recs)
                    pending_transactions.extend(pending)
                    coverage_stats["raw_rows"] += stats.get("raw_rows", 0)
                    coverage_stats["accepted_rows"] += stats.get("accepted", 0)
                    coverage_stats["pending_candidates"] += len(pending)
                    coverage_stats["parsed_sources"].append(source_meta)
                    if dept and is_valid_department_name(dept):
                        coverage_stats["departments"].add(dept)
                    for r in recs:
                        r_dept = r.get("department")
                        if r_dept and is_valid_department_name(r_dept):
                            coverage_stats["departments"].add(canonicalize_department_name(r_dept))
                else:
                    coverage_stats["failed_sources"].append(source_meta)
            except Exception as e:
                print(f"[WARN] Error processing Incheon upperNo {upper_no}: {e}", file=sys.stderr)

        coverage_stats["departments"] = sorted(list(coverage_stats["departments"]))
        return records, pending_transactions, sources, coverage_stats


COLLECTOR_CLASSES = {
    "gg": GyeonggiCollector,
    "dj": DaejeonCollector,
    "sj": SejongCollector,
    "cn": ChungnamCollector,
    "cb": ChungbukCollector,
    "ic": IncheonCollector,
}


# ----------------------------------------------------------------------
# Probe / Diagnostic Mode
# ----------------------------------------------------------------------
def run_diagnostic_inspection(
    probe_dir: str,
    soffice_path: str = DEFAULT_SOFFICE,
    address_dir: str = DEFAULT_ADDRESS_DIR,
) -> None:
    """
    Inspects probe samples and prints headers, column mappings, and sample sanitized records.
    Safe expanded diagnostic mode: inspects first 15 rows of each sheet/table and prints
    ONLY header candidates (matching keywords) without revealing personal data.
    """
    print("=" * 70)
    print("CAPITAL-CENTRAL PUBLIC DINING COLLECTOR: DIAGNOSTIC MODE")
    print(f"Current Date: {CURRENT_DATE}")
    print(f"Probe Directory: {probe_dir}")
    print(f"Address Directory: {address_dir}")
    print(f"soffice Path: {soffice_path} (exists: {os.path.exists(soffice_path)})")
    print(f"openpyxl: {'available' if openpyxl else 'MISSING'}")
    print(f"pdfplumber: {'available' if pdfplumber else 'MISSING'}")
    print("=" * 70)

    # Initialize AddressResolver
    address_resolver = AddressResolver(address_dir=address_dir)
    loaded_prov = address_resolver.get_provenance()
    print(f"Address Resolver initialized. Found {len(loaded_prov)} regional address CSV(s):")
    for prov in loaded_prov:
        print(f"  - [{prov['region']}] {os.path.basename(prov['filePath'])}: "
              f"{prov['recordCount']} records (SHA256: {prov['sha256'][:12]}...)")
    if not loaded_prov:
        print("  [NOTE] No address CSVs found in address_dir yet (normal before TM runs with downloaded CSVs).")

    if not os.path.exists(probe_dir):
        print(f"[ERROR] Probe directory {probe_dir} not found.")
        return

    probe_samples = [
        ("경기 (Gyeonggi)", "gg_sample.xlsx", "xlsx", "gg"),
        ("대전 (Daejeon)", "dj_sample.xlsx", "xlsx", "dj"),
        ("세종 (Sejong)", "sj_sample.xlsx", "xlsx", "sj"),
        ("충남 (Chungnam)", "cn_sample.xlsx", "xlsx", "cn"),
        ("충북 (Chungbuk)", "conv/cb_sample.xlsx", "xlsx", "cb"),
        ("인천 (Incheon)", "ic_sample.pdf", "pdf", "ic"),
    ]

    for label, rel_path, ftype, reg_key in probe_samples:
        full_path = os.path.join(probe_dir, rel_path)
        print(f"\n>>> Inspecting {label}: {rel_path}")
        if not os.path.exists(full_path):
            print(f"    [SKIP] Sample file {full_path} not found.")
            continue

        size = os.path.getsize(full_path)
        sha = compute_file_sha256(full_path)
        print(f"    Size: {size:,} bytes | SHA256: {sha}")

        cfg = REGION_CONFIGS[reg_key]

        # Companion metadata discovery (headers, companion view HTML, filename) without hardcoding
        comp_dept = None
        comp_raw = None
        comp_evidence = None

        # 1. Check companion .h header file
        h_cand = os.path.join(probe_dir, f"{reg_key}_sample.h")
        if os.path.exists(h_cand):
            try:
                with open(h_cand, "r", encoding="utf-8", errors="replace") as f:
                    h_txt = f.read()
                m_fn = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^";\r\n]+)', h_txt, re.I)
                if m_fn:
                    unquoted_fn = urllib.parse.unquote(m_fn.group(1).strip().strip('"\''))
                    t_dept = extract_department_from_title(unquoted_fn)
                    if t_dept and is_valid_department_name(t_dept):
                        comp_raw = t_dept
                        comp_dept = canonicalize_department_name(comp_raw)
                        comp_evidence = {
                            "kind": "companion_header_filename",
                            "location": f"{reg_key}_sample.h",
                            "sourceURL": f"file://{h_cand}",
                        }
            except Exception as e:
                pass

        # 2. Check companion _view.html file if not found
        if not comp_dept:
            v_cand = os.path.join(probe_dir, f"{reg_key}_view.html")
            if os.path.exists(v_cand):
                try:
                    with open(v_cand, "r", encoding="utf-8", errors="replace") as f:
                        v_txt = f.read()
                    m_subj = re.search(r'class="subject"[^>]*>([^<]+)<', v_txt)
                    if not m_subj:
                        m_subj = re.search(r'<title>([^<]+)</title>', v_txt)
                    if m_subj:
                        clean_subj = html.unescape(m_subj.group(1)).replace("&#40;", "(").replace("&#41;", ")").strip()
                        t_dept = extract_department_from_title(clean_subj)
                        if t_dept and is_valid_department_name(t_dept):
                            comp_raw = t_dept
                            comp_dept = canonicalize_department_name(comp_raw)
                            comp_evidence = {
                                "kind": "companion_view_html",
                                "location": f"{reg_key}_view.html",
                                "sourceURL": f"file://{v_cand}",
                            }
                except Exception as e:
                    pass

        # 3. Check filename itself
        if not comp_dept:
            t_dept = extract_department_from_title(os.path.basename(rel_path))
            if t_dept and is_valid_department_name(t_dept):
                comp_raw = t_dept
                comp_dept = canonicalize_department_name(comp_raw)
                comp_evidence = {
                    "kind": "filename_title",
                    "location": rel_path,
                    "sourceURL": f"file://{full_path}",
                }

        if comp_dept:
            print(f"    [Companion Evidence] Detected metadata: '{comp_raw}' -> '{comp_dept}' ({comp_evidence['kind']})")
        else:
            print("    [Companion Evidence] None detected; will rely strictly on internal table/sheet provenance.")

        # Safe header inspection: print ONLY detected header row and col_map (no candidate row contents logged)
        if ftype == "xlsx" and openpyxl:
            try:
                wb = openpyxl.load_workbook(full_path, data_only=True)
                for ws in wb.worksheets:
                    sheet_rows = list(ws.iter_rows(values_only=True))
                    hdr_dept, hdr_raw, hdr_ev = extract_department_from_header_rows(sheet_rows, max_rows=15)
                    if hdr_dept:
                        print(f"    [Sheet: '{ws.title}'] -> [Header Evidence] Matched label: '{hdr_raw}' (location: {hdr_ev['location']})")
                    h_idx, col_map = detect_header_columns(sheet_rows)
                    if h_idx >= 0:
                        print(f"    [Sheet: '{ws.title}'] -> Detected header at row {h_idx}: {col_map}")
                    else:
                        print(f"    [Sheet: '{ws.title}'] -> [WARN] No header detected in sheet '{ws.title}'")
                wb.close()
            except Exception as e:
                print(f"    [WARN] Error inspecting xlsx headers: {e}", file=sys.stderr)

        elif ftype == "pdf" and pdfplumber:
            try:
                with pdfplumber.open(full_path) as pdf:
                    for p_idx, page in enumerate(pdf.pages[:1]):
                        tables = page.extract_tables()
                        for t_idx, tbl in enumerate(tables):
                            hdr_dept, hdr_raw, hdr_ev = extract_department_from_header_rows(tbl, max_rows=15)
                            if hdr_dept:
                                print(f"    [PDF Page {p_idx+1}, Table {t_idx+1}] -> [Header Evidence] Matched label: '{hdr_raw}' (location: {hdr_ev['location']})")
                            h_idx, col_map = detect_header_columns(tbl)
                            if h_idx >= 0:
                                print(f"    [PDF Page {p_idx+1}, Table {t_idx+1}] -> Detected header at row {h_idx}: {col_map}")
                            else:
                                print(f"    [PDF Page {p_idx+1}, Table {t_idx+1}] -> [WARN] No header detected in table")
            except Exception as e:
                print(f"    [WARN] Error inspecting pdf headers: {e}", file=sys.stderr)

        # Parse file with dispatch_and_parse_file (handles xlsx, pdf, hwpx, xls)
        recs, pending, stats = dispatch_and_parse_file(
            file_path=full_path,
            institution=cfg["institution"],
            default_department=comp_dept,
            source_url=cfg["board_url"],
            record_prefix=f"probe-{reg_key}",
            hint_region=cfg["region_code"],
            address_resolver=address_resolver,
            soffice_path=soffice_path,
            cache_dir=os.path.join(probe_dir, "conv"),
            default_department_raw=comp_raw,
            default_department_evidence=comp_evidence,
        )

        departments_found = sorted(list({r["department"] for r in recs} | {p["department"] for p in pending}))

        print(f"    Parse Stats: raw={stats.get('raw_rows',0)}, meal_candidates={len(pending)}, "
              f"accepted={len(recs)}, missing_addr={stats.get('missing_address',0)}, "
              f"multi_merchant={stats.get('multi_merchant',0)}, non_meal={stats.get('non_meal',0)}, "
              f"out_of_window={stats.get('date_out_of_window',0)}, "
              f"distinct_departments={len(departments_found)} ({departments_found[:3]}{'...' if len(departments_found) > 3 else ''})")

        if recs:
            print("    Sample accepted records (sanitized preview):")
            for r in recs[:3]:
                print(f"      - [{r['paymentDate']}] {r['department']} -> {r['name']} | "
                      f"{r['address']} ({r['addressMatch']}) | {r['amount']:,}원")
        else:
            print("    [NOTE] No records accepted (check address resolution or date window).")

    print("\n" + "=" * 70)
    print("DIAGNOSTIC INSPECTION COMPLETE")
    print("=" * 70)


# ----------------------------------------------------------------------
# Aggregation & Quality Assessment
# ----------------------------------------------------------------------
def analyze_restaurants(
    records: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], int, int]:
    """
    Groups dining records by restaurant key = (clean_name, address).
    Calculates optional department count and the one-payment eligibility count.
    """
    restaurants: Dict[str, Dict[str, Any]] = {}
    for r in records:
        key = (r["name"], r["address"])
        if key not in restaurants:
            restaurants[key] = {
                "name": r["name"],
                "address": r["address"],
                "region": r["region"],
                "institution": r["institution"],
                "departments": set(),
                "recordCount": 0,
                "firstPaymentDate": r["paymentDate"],
                "lastPaymentDate": r["paymentDate"],
                "sourceURLs": set(),
            }
        rest = restaurants[key]
        if r.get("department"):
            rest["departments"].add(r["institution"] + ":" + r["department"])
        rest["recordCount"] += 1
        rest["sourceURLs"].add(r["sourceURL"])
        if r["paymentDate"] < rest["firstPaymentDate"]:
            rest["firstPaymentDate"] = r["paymentDate"]
        if r["paymentDate"] > rest["lastPaymentDate"]:
            rest["lastPaymentDate"] = r["paymentDate"]

    total_3plus = 0
    recent_3plus = 0
    for rest in restaurants.values():
        rest["departmentCount"] = len(rest["departments"])
        rest["departments"] = sorted(list(rest["departments"]))
        rest["sourceURLs"] = sorted(list(rest["sourceURLs"]))
        if rest["recordCount"] >= 1:
            total_3plus += 1
            if rest["lastPaymentDate"] >= WINDOW_START_DATE:
                recent_3plus += 1

    return restaurants, total_3plus, recent_3plus


# ----------------------------------------------------------------------
# Output Writing & Dataset Helpers
# ----------------------------------------------------------------------
def write_dataset_outputs(
    output_dir: str,
    all_records: List[Dict[str, Any]],
    all_pending_transactions: List[Dict[str, Any]],
    all_sources: List[Dict[str, Any]],
    coverage_by_region: Dict[str, Any],
    target_regions: List[str],
    address_resolver: AddressResolver,
) -> Dict[str, Any]:
    """
    Writes out all 6 final datasets to output_dir:
    1. records.jsonl.gz
    2. capital-central-window.csv.gz
    3. pending-transactions.jsonl.gz
    4. matched-addresses.csv.gz
    5. sources.json
    6. coverage.json
    """
    os.makedirs(output_dir, exist_ok=True)

    # Only official document URLs are retained as department evidence.
    for row in all_records + all_pending_transactions:
        if isinstance(row.get("departmentEvidence"), dict):
            row["departmentEvidence"]["sourceURL"] = row.get("sourceURL", row.get("source", ""))
    # Sort records chronologically
    all_records.sort(key=lambda r: (r.get("paymentDate", ""), r.get("region", ""), r.get("department", ""), r.get("name", "")))
    all_pending_transactions.sort(key=lambda p: (p.get("paymentDate", ""), p.get("region", ""), p.get("department", ""), p.get("name", "")))

    # Restaurant analysis
    restaurants, total_3plus, recent_3plus = analyze_restaurants(all_records)

    # 1. records.jsonl.gz
    records_gz_path = os.path.join(output_dir, "records.jsonl.gz")
    with gzip.open(records_gz_path, "wt", encoding="utf-8") as f_out:
        for r in all_records:
            f_out.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Saved: {records_gz_path} ({len(all_records)} records)")

    # 2. capital-central-window.csv.gz (sanitized reduced CSV)
    csv_gz_path = os.path.join(output_dir, "capital-central-window.csv.gz")
    fieldnames = [
        "recordID", "region", "institution", "department", "departmentRawLabel", "name",
        "address", "paymentDate", "amount", "isMeal", "sourceURL",
        "addressMatch", "addressSourceID"
    ]
    with gzip.open(csv_gz_path, "wt", encoding="utf-8", newline="") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in all_records:
            writer.writerow(r)
    print(f"Saved: {csv_gz_path}")

    # 3. pending-transactions.jsonl.gz
    pending_gz_path = os.path.join(output_dir, "pending-transactions.jsonl.gz")
    with gzip.open(pending_gz_path, "wt", encoding="utf-8") as f_out:
        for p in all_pending_transactions:
            f_out.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"Saved: {pending_gz_path} ({len(all_pending_transactions)} pending transactions)")

    # 4. matched-addresses.csv.gz (reusable compact matched address registry)
    matched_gz_path = os.path.join(output_dir, "matched-addresses.csv.gz")
    matched_dict: Dict[Tuple[str, str, str, str], Dict[str, str]] = {}
    for r in all_records:
        addr = r.get("address")
        if addr:
            key = (
                r.get("addressSourceID") or "",
                r.get("region") or "",
                r.get("name") or "",
                addr,
            )
            if key not in matched_dict:
                matched_dict[key] = {
                    "addressSourceID": r.get("addressSourceID") or "",
                    "region": r.get("region") or "",
                    "name": r.get("name") or "",
                    "address": addr,
                    "addressMatch": r.get("addressMatch") or "verified",
                }
    matched_rows = sorted(list(matched_dict.values()), key=lambda m: (m["region"], m["name"], m["address"]))
    matched_fieldnames = ["addressSourceID", "region", "name", "address", "addressMatch"]
    with gzip.open(matched_gz_path, "wt", encoding="utf-8", newline="") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=matched_fieldnames)
        writer.writeheader()
        for m in matched_rows:
            writer.writerow(m)
    print(f"Saved: {matched_gz_path} ({len(matched_rows)} unique matched addresses)")

    # 5. sources.json
    sources_json_path = os.path.join(output_dir, "sources.json")
    sources_data = {
        "generatedAt": datetime.datetime.now().isoformat(),
        "totalExpenseFiles": len(all_sources),
        "totalAddressFiles": len(address_resolver.get_provenance()),
        "expenseSources": all_sources,
        "addressSources": address_resolver.get_provenance(),
        "sources": all_sources,  # Alias for compatibility
    }
    with open(sources_json_path, "w", encoding="utf-8") as f_out:
        json.dump(sources_data, f_out, ensure_ascii=False, indent=2)
    print(f"Saved: {sources_json_path} ({len(all_sources)} expense source files)")

    accepted_ids = {r["recordID"] for r in all_records}
    unresolved_path = os.path.join(output_dir, "unresolved-address-records.jsonl.gz")
    with gzip.open(unresolved_path, "wt", encoding="utf-8") as unresolved_file:
        for row in all_pending_transactions:
            if row.get("recordID") in accepted_ids or row.get("addressIfPresent"):
                continue
            d = row.get("date", row.get("paymentDate", ""))
            if WINDOW_START_DATE <= d <= CURRENT_DATE:
                unresolved_file.write(json.dumps({
                    "institution": row.get("institution", ""),
                    "department": row.get("department") or None,
                    "name": row.get("rawRestaurantName", row.get("name", "")),
                    "region": row.get("region") or next((cfg["region_code"] for cfg in REGION_CONFIGS.values() if cfg["institution"] == row.get("institution")), ""),
                    "addressHint": row.get("addressHint") or row.get("addressIfPresent"),
                    "paymentDate": d,
                    "sourceURL": row.get("source", row.get("sourceURL", "")),
                    "recordID": row.get("recordID"), "isMeal": True,
                }, ensure_ascii=False) + "\n")
    # 6. coverage.json
    # Fill in coverage for any active region not explicitly reported
    for rk in target_regions:
        reg_code = REGION_CONFIGS[rk]["region_code"] if rk in REGION_CONFIGS else rk
        if reg_code not in coverage_by_region:
            reg_recs = [r for r in all_records if r.get("region") == reg_code]
            reg_pend = [p for p in all_pending_transactions if p.get("region") == reg_code]
            reg_srcs = [s for s in all_sources if s.get("region") == reg_code]
            reg_depts = sorted(list({r["department"] for r in reg_recs if r.get("department")}))
            coverage_by_region[reg_code] = {
                "region": reg_code,
                "institution": REGION_CONFIGS[rk]["institution"] if rk in REGION_CONFIGS else "",
                "raw_rows": sum(s.get("rawRows", 0) for s in reg_srcs),
                "accepted_rows": len(reg_recs),
                "pending_candidates": len(reg_pend),
                "departments": reg_depts,
                "sources": reg_srcs,
                "parsed_sources": [s for s in reg_srcs if s.get("status") == "parsed"],
                "failed_sources": [s for s in reg_srcs if s.get("status") == "failed"],
                "stats": {},
            }

    for region_code, cov in coverage_by_region.items():
        reg_sources = [x for x in all_sources if x.get("region") == region_code]
        reg_records = [x for x in all_records if x.get("region") == region_code]
        counts = {}
        for source in reg_sources:
            for key, value in source.get("parseStats", {}).items():
                if isinstance(value, int):
                    counts[key] = counts.get(key, 0) + value
        cov["stats"] = counts
        raw_rows = sum(x.get("rawRows", 0) for x in reg_sources)
        cov["funnel"] = {
            "downloadedDocuments": len(reg_sources),
            "parsedDocuments": sum(x.get("status") == "parsed" for x in reg_sources),
            "failedDocuments": sum(x.get("status") != "parsed" for x in reg_sources),
            "rawTableRows": raw_rows,
            "datedPaymentRows": raw_rows - counts.get("missing_date", 0),
            "mealRows": sum(x.get("pendingCandidates", 0) for x in reg_sources),
            "addressMatchedRows": len(reg_records),
            "knownDepartmentRows": sum(bool(x.get("department")) for x in reg_records),
            "eligibleRestaurants18Months": len({(x["name"], x["address"]) for x in reg_records}),
            "exclusions": {k: v for k, v in counts.items() if k in {"missing_date", "date_out_of_window", "future_date", "missing_name", "missing_address", "non_meal", "multi_merchant", "out_of_region"}},
        }
        cov["sourceURLs"] = sorted({x["sourceURL"] for x in reg_sources})
        cov["institutions"] = sorted({x["institution"] for x in reg_sources})
        dates = [x["paymentDate"] for x in reg_records]
        cov["period"] = {"from": min(dates) if dates else None, "to": max(dates) if dates else None}
    coverage_json_path = os.path.join(output_dir, "coverage.json")
    coverage_data = {
        "generatedAt": datetime.datetime.now().isoformat(),
        "currentDate": CURRENT_DATE,
        "windowStartDate": WINDOW_START_DATE,
        "recentMealStartDate": RECENT_MEAL_START,
        "targetRegions": [REGION_CONFIGS[k]["region_code"] if k in REGION_CONFIGS else k for k in target_regions],
        "summary": {
            "totalAcceptedRecords": len(all_records),
            "totalPendingCandidates": len(all_pending_transactions),
            "uniqueRestaurants": len(restaurants),
            "restaurantsWithPublicPayment": total_3plus,
            "eligibleRestaurants18Months": recent_3plus,
        },
        "addressResolverStats": address_resolver.get_stats(),
        "regions": coverage_by_region,
        "disclaimer": "본 자료는 각 지자체 공식 업무추진비 공개자료를 수집·가공한 것으로, 현재 영업 여부 및 음식의 맛을 보증하지 않습니다.",
        "limitations": [
            "도로명 또는 지번 및 건물번호가 명확히 확인된 장소만 채택하여 주소가 없는 행은 제외됨",
            "개인정보(작성자, 참석자, 전화번호, 집행목적 원문)는 엄격히 제거됨",
            "시책 및 기관운영 추진비 공개 기준에 따른 공식 부서(기관명) 표기 및 유효 실/국/과/담당관/소속기관 단위만 반영(예산항목명/일반지자체명 폴백 제외)",
            "기관장/직책 명칭은 소속 부서 단위(비서실/실/본부)로 표준화되고 부서 미확인 기록은 빈값으로 보존되며 기관의 공식 이용 증거로 판정함",
        ],
    }
    with open(coverage_json_path, "w", encoding="utf-8") as f_out:
        json.dump(coverage_data, f_out, ensure_ascii=False, indent=2)
    print(f"Saved: {coverage_json_path}")

    return {
        "restaurants": restaurants,
        "total_3plus": total_3plus,
        "recent_3plus": recent_3plus,
    }


def load_saved_outputs(
    output_dir: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Loads existing records, pending transactions, sources, and coverage from an output directory."""
    records: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    coverage: Dict[str, Any] = {}

    rec_path = os.path.join(output_dir, "records.jsonl.gz")
    if os.path.exists(rec_path):
        try:
            with gzip.open(rec_path, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except Exception as e:
            print(f"[WARN] Failed to read {rec_path}: {e}", file=sys.stderr)

    pending_path = os.path.join(output_dir, "pending-transactions.jsonl.gz")
    if os.path.exists(pending_path):
        try:
            with gzip.open(pending_path, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        pending.append(json.loads(line))
        except Exception as e:
            print(f"[WARN] Failed to read {pending_path}: {e}", file=sys.stderr)

    sources_path = os.path.join(output_dir, "sources.json")
    if os.path.exists(sources_path):
        try:
            with open(sources_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                sources = data.get("expenseSources") or data.get("sources") or []
        except Exception as e:
            print(f"[WARN] Failed to read {sources_path}: {e}", file=sys.stderr)

    cov_path = os.path.join(output_dir, "coverage.json")
    if os.path.exists(cov_path):
        try:
            with open(cov_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                coverage = data.get("regions") or {}
        except Exception as e:
            print(f"[WARN] Failed to read {cov_path}: {e}", file=sys.stderr)

    return records, pending, sources, coverage


def run_offline_regeneration(
    output_dir: str,
    address_dir: str,
    merge_dirs: Optional[List[str]] = None,
) -> int:
    """
    Regenerates final datasets from sanitized saved records/pending-transactions/sources.
    Does not require raw files or network access. Re-normalizes addresses and canonical departments.
    """
    print("=" * 70)
    print("RUNNING OFFLINE REGENERATION PIPELINE")
    print(f"Target Output Directory: {output_dir}")
    print(f"Address Directory: {address_dir}")
    if merge_dirs:
        print(f"Merge Source Directories: {merge_dirs}")
    print("=" * 70)

    input_dirs = merge_dirs if merge_dirs else [output_dir]
    all_raw_records: List[Dict[str, Any]] = []
    all_raw_pending: List[Dict[str, Any]] = []
    all_raw_sources: List[Dict[str, Any]] = []
    combined_coverage: Dict[str, Any] = {}

    for d in input_dirs:
        if not os.path.exists(d):
            print(f"[WARN] Directory does not exist: {d}", file=sys.stderr)
            continue
        recs, pends, srcs, cov = load_saved_outputs(d)
        print(f"Loaded from {d}: {len(recs)} records, {len(pends)} pending, {len(srcs)} sources, {len(cov)} region coverages")
        all_raw_records.extend(recs)
        all_raw_pending.extend(pends)
        all_raw_sources.extend(srcs)
        combined_coverage.update(cov)

    if not all_raw_records and not all_raw_pending:
        print("[ERROR] No saved records or pending transactions found to regenerate.", file=sys.stderr)
        return 1

    address_resolver = AddressResolver(address_dir=address_dir)
    # Check if matched-addresses.csv.gz exists in any input dir or output_dir
    for d in input_dirs + [output_dir]:
        matched_p = os.path.join(d, "matched-addresses.csv.gz")
        if os.path.exists(matched_p):
            cnt = address_resolver.load_matched_addresses(matched_p)
            print(f"Loaded {cnt} matched address cache entries from {matched_p}")

    # Deduplicate and re-normalize records
    seen_rec_keys = set()
    cleaned_records: List[Dict[str, Any]] = []
    stripped_invalid_dept_records = 0
    for r in all_raw_records:
        rec_id = r.get("recordID") or f"{r.get('region')}-{r.get('name')}-{r.get('paymentDate')}-{r.get('amount')}"
        if rec_id in seen_rec_keys:
            continue
        seen_rec_keys.add(rec_id)

        # Re-validate department: do not trust legacy invalid departments
        dept_raw = r.get("departmentRawLabel") or r.get("department")
        if not dept_raw or not is_valid_department_name(dept_raw):
            stripped_invalid_dept_records += 1
            dept_raw = ""

        r["department"] = canonicalize_department_name(dept_raw)
        if "departmentRawLabel" not in r:
            r["departmentRawLabel"] = dept_raw

        norm = normalize_address(r.get("address", ""), hint_region=r.get("region"), is_validated_lookup=True)
        if norm:
            r["address"] = norm[1]

        # Date window check
        pdate = r.get("paymentDate", "")
        if pdate < WINDOW_START_DATE or pdate > CURRENT_DATE:
            continue

        cleaned_records.append(r)

    # Re-evaluate pending transactions
    seen_pend_keys = set()
    cleaned_pending: List[Dict[str, Any]] = []
    resolved_from_pending = 0
    stripped_invalid_dept_pending = 0
    for p in all_raw_pending:
        p_id = p.get("recordID") or f"{p.get('region')}-{p.get('name')}-{p.get('paymentDate')}-{p.get('amount')}"
        if p_id in seen_pend_keys or p_id in seen_rec_keys:
            continue
        seen_pend_keys.add(p_id)

        p_dept_raw = p.get("departmentRawLabel") or p.get("department")
        if not p_dept_raw or not is_valid_department_name(p_dept_raw):
            stripped_invalid_dept_pending += 1
            p_dept_raw = ""

        p["department"] = canonicalize_department_name(p_dept_raw)
        if "departmentRawLabel" not in p:
            p["departmentRawLabel"] = p_dept_raw

        # Try to resolve address if not yet resolved
        reg = p.get("region") or next((cfg["region_code"] for cfg in REGION_CONFIGS.values() if cfg["institution"] == p.get("institution")), "")
        pname = p.get("rawRestaurantName", p.get("name", ""))
        r_place = p.get("rawPlace", "")
        dist_hint = extract_district_hint(r_place, department=p.get("department", ""))
        res = address_resolver.resolve(name=pname, region=reg, raw_place=r_place, district_hint=dist_hint)
        if res:
            pdate = p.get("date", p.get("paymentDate", ""))
            if WINDOW_START_DATE <= pdate <= CURRENT_DATE:
                rec = {
                    "recordID": p.get("recordID") or f"{reg}-{generate_record_id(f'{pname}-{res['address']}-{pdate}-{p.get('amount', 0)}')}",
                    "region": reg,
                    "institution": p.get("institution", ""),
                    "department": p.get("department", ""),
                    "departmentRawLabel": p.get("departmentRawLabel", ""),
                    "departmentEvidence": p.get("departmentEvidence"),
                    "name": pname,
                    "address": res["address"],
                    "paymentDate": pdate,
                    "amount": p.get("amount", 0),
                    "isMeal": True,
                    "sourceURL": p.get("source", p.get("sourceURL", "")),
                    "addressSourceURL": res.get("addressSourceURL", SBIZ_DATA_SOURCE_URL),
                    "addressMatch": res["addressMatch"],
                    "addressSourceID": res.get("addressSourceID", ""),
                }
                cleaned_records.append(rec)
                resolved_from_pending += 1
                continue

        cleaned_pending.append(p)

    if stripped_invalid_dept_records > 0 or stripped_invalid_dept_pending > 0:
        print(f"[WARN] Stripped {stripped_invalid_dept_records} legacy record(s) and {stripped_invalid_dept_pending} pending candidate(s) lacking valid department provenance.", file=sys.stderr)
        print("[WARN] Run raw collection (--offline or online collection) to re-parse original disclosure tables and extract accurate departments.", file=sys.stderr)

    if resolved_from_pending > 0:
        print(f"Resolved {resolved_from_pending} pending candidates into accepted records during regeneration!")

    # Deduplicate sources
    seen_src_keys = set()
    cleaned_sources: List[Dict[str, Any]] = []
    for s in all_raw_sources:
        s_key = s.get("downloadURL") or (s.get("region"), s.get("sourceURL"), s.get("sha256"))
        if s_key in seen_src_keys:
            continue
        seen_src_keys.add(s_key)
        cleaned_sources.append(s)

    target_regs = list(COLLECTOR_CLASSES.keys())
    res_summary = write_dataset_outputs(
        output_dir=output_dir,
        all_records=cleaned_records,
        all_pending_transactions=cleaned_pending,
        all_sources=cleaned_sources,
        coverage_by_region=combined_coverage,
        target_regions=target_regs,
        address_resolver=address_resolver,
    )

    print("\n" + "=" * 70)
    print("OFFLINE REGENERATION COMPLETE:")
    print(f"  Accepted Records: {len(cleaned_records)}")
    print(f"  Pending Candidates: {len(cleaned_pending)}")
    print(f"  Unique Verified Restaurants: {len(res_summary['restaurants'])}")
    print(f"  Restaurants with >=1 Public Payment: {res_summary['total_3plus']}")
    print(f"  Restaurants with Payment in Rolling 18 Months: {res_summary['recent_3plus']}")
    print("=" * 70)
    return 0


# ----------------------------------------------------------------------
# Main Entry Point
# ----------------------------------------------------------------------
def main() -> int:
    default_out_dir = str(Path(__file__).resolve().parents[1] / "data/public-dining/capital_central")
    if not os.path.exists(os.path.dirname(default_out_dir)):
        default_out_dir = "/tmp/whattoeat-capital-central/output"

    parser = argparse.ArgumentParser(
        description="Public Dining Collector for Capital & Central Regions (경기, 인천, 대전, 세종, 충북, 충남)"
    )
    parser.add_argument(
        "--output-dir",
        default=default_out_dir,
        help="Directory to save final datasets (sources.json, coverage.json, records.jsonl.gz, csv)",
    )
    parser.add_argument(
        "--cache-dir",
        default="/tmp/whattoeat-capital-central",
        help="Local cache directory for raw official downloads",
    )
    parser.add_argument(
        "--probe-dir",
        default="/tmp/whattoeat-capital-central/probe",
        help="Directory containing downloaded probe samples",
    )
    parser.add_argument(
        "--address-dir",
        default=DEFAULT_ADDRESS_DIR,
        help="Directory containing official Small Enterprise (소상공인시장진흥공단) address CSV files",
    )
    parser.add_argument(
        "--regions",
        default="gg,dj,sj,cn,cb,ic",
        help="Comma-separated region codes (gg,dj,sj,cn,cb,ic)",
    )
    parser.add_argument(
        "--max-pages-per-region",
        type=int,
        default=20,
        help="Max disclosure list pages to query per region (expanded budget)",
    )
    parser.add_argument(
        "--max-files-per-region",
        type=int,
        default=200,
        help="Max attachment files to download and parse per region (expanded budget)",
    )
    parser.add_argument(
        "--soffice-path",
        default=DEFAULT_SOFFICE,
        help="Path to soffice LibreOffice binary for .xls conversion",
    )
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="Run non-personal diagnostic inspection on probe samples and exit",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run in offline mode using cached files only without making network calls",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Run offline regeneration from saved records/pending transactions without raw files",
    )
    parser.add_argument(
        "--merge-dirs",
        nargs="+",
        help="One or more regional/output directories to merge records and sources from",
    )
    parser.add_argument(
        "--no-preserve-existing",
        action="store_true",
        help="Disable automatic preservation of existing non-target regions from --output-dir",
    )

    parser.add_argument("--as-of", default=CURRENT_DATE, help="YYYY-MM-DD, Korean calendar date")
    parser.add_argument("--months", type=int, default=18, choices=[18], help="Rolling window; currently 18 months")
    parser.add_argument("--source-index", help="Previous sources.json used to restore cached document provenance")
    args = parser.parse_args()
    global WINDOW_START_DATE, RECENT_MEAL_START, CACHE_SOURCE_INDEX
    as_of = datetime.date.fromisoformat(args.as_of)
    globals()["CURRENT_DATE"] = as_of.isoformat()
    mi = as_of.year * 12 + as_of.month - 1 - args.months
    yy, mm0 = divmod(mi, 12)
    WINDOW_START_DATE = datetime.date(yy, mm0 + 1, min(as_of.day, calendar.monthrange(yy, mm0 + 1)[1])).isoformat()
    RECENT_MEAL_START = WINDOW_START_DATE
    index_path = args.source_index or os.path.join(args.output_dir, "sources.json")
    if os.path.exists(index_path):
        payload = json.loads(Path(index_path).read_text())
        for src in payload.get("expenseSources", payload.get("sources", [])):
            if src.get("downloadURL", "").startswith("https://") and src.get("sourceURL", "").startswith("https://") and src.get("sha256"):
                CACHE_SOURCE_INDEX[src["sha256"]] = src

    if args.diagnostic:
        run_diagnostic_inspection(
            probe_dir=args.probe_dir,
            soffice_path=args.soffice_path,
            address_dir=args.address_dir,
        )
        return 0

    if args.regenerate or args.merge_dirs:
        return run_offline_regeneration(
            output_dir=args.output_dir,
            address_dir=args.address_dir,
            merge_dirs=args.merge_dirs,
        )

    target_regions = [r.strip().lower() for r in args.regions.split(",") if r.strip().lower() in COLLECTOR_CLASSES]
    if not target_regions:
        print("[ERROR] No valid regions specified.", file=sys.stderr)
        return 1

    os.makedirs(args.output_dir, exist_ok=True)
    os.makedirs(args.cache_dir, exist_ok=True)

    # Initialize AddressResolver
    address_resolver = AddressResolver(address_dir=args.address_dir)

    # If matched-addresses.csv.gz already exists, load it into address resolver as cache
    matched_p = os.path.join(args.output_dir, "matched-addresses.csv.gz")
    if os.path.exists(matched_p):
        address_resolver.load_matched_addresses(matched_p)

    target_region_codes = {REGION_CONFIGS[k]["region_code"] for k in target_regions}

    # Check for preservation of existing non-target regions
    preserved_records: List[Dict[str, Any]] = []
    preserved_pending: List[Dict[str, Any]] = []
    preserved_sources: List[Dict[str, Any]] = []
    preserved_coverage: Dict[str, Any] = {}

    if not args.no_preserve_existing and os.path.exists(os.path.join(args.output_dir, "records.jsonl.gz")):
        ex_records, ex_pending, ex_sources, ex_cov = load_saved_outputs(args.output_dir)
        preserved_records = [r for r in ex_records if r.get("region") not in target_region_codes]
        preserved_pending = [p for p in ex_pending if p.get("region") not in target_region_codes]
        preserved_sources = [s for s in ex_sources if s.get("region") not in target_region_codes]
        preserved_coverage = {k: v for k, v in ex_cov.items() if k not in target_region_codes}
        if preserved_records:
            pres_regs = sorted(list({r.get('region') for r in preserved_records}))
            print(f"[INFO] Preserving {len(preserved_records)} existing records and {len(preserved_sources)} sources for {len(pres_regs)} region(s) ({', '.join(pres_regs)}) not in target_regions.")

    print("=" * 70)
    print("STARTING CAPITAL-CENTRAL PUBLIC DINING COLLECTION")
    print(f"Target Regions: {target_regions}")
    print(f"Output Directory: {args.output_dir}")
    print(f"Cache Directory: {args.cache_dir}")
    print(f"Address Directory: {args.address_dir}")
    print(f"Date Window: {WINDOW_START_DATE} ~ {CURRENT_DATE}")
    print(f"Max Pages Per Region: {args.max_pages_per_region}")
    print(f"Max Files Per Region: {args.max_files_per_region}")
    print("=" * 70)

    new_records: List[Dict[str, Any]] = []
    new_pending_transactions: List[Dict[str, Any]] = []
    new_sources: List[Dict[str, Any]] = []
    new_coverage_by_region: Dict[str, Any] = {}

    for reg_key in target_regions:
        cfg = REGION_CONFIGS[reg_key]
        collector_cls = COLLECTOR_CLASSES[reg_key]
        collector = collector_cls(
            key=reg_key,
            config=cfg,
            cache_dir=args.cache_dir,
            soffice_path=args.soffice_path,
        )

        print(f"\n[COLLECT] Collecting {cfg['name']} ({cfg['region_code']})...")
        recs, pending, srcs, cov = collector.collect(
            max_pages=args.max_pages_per_region,
            max_files=args.max_files_per_region,
            offline=args.offline,
            address_resolver=address_resolver,
        )

        new_records.extend(recs)
        new_pending_transactions.extend(pending)
        new_sources.extend(srcs)
        new_coverage_by_region[cfg["region_code"]] = cov

        print(f"  -> Extracted {len(recs)} accepted records, {len(pending)} pending meal candidates from {len(srcs)} sources.")
        print(f"  -> Departments: {len(cov.get('departments', []))} distinct units.")

    # Combine preserved with new
    all_records = preserved_records + new_records
    all_pending_transactions = preserved_pending + new_pending_transactions
    all_sources = preserved_sources + new_sources
    all_coverage = {**preserved_coverage, **new_coverage_by_region}

    # Determine full active target regions
    active_regions = list(dict.fromkeys(
        target_regions + [CODE_TO_REGION_KEY.get(r, r) for r in preserved_coverage.keys() if r in CODE_TO_REGION_KEY]
    ))

    # Restaurant Analysis
    restaurants, total_3plus, recent_3plus = analyze_restaurants(all_records)
    print("\n" + "=" * 70)
    print("COLLECTION SUMMARY:")
    print(f"  Total Accepted Dining Records: {len(all_records)} (new: {len(new_records)}, preserved: {len(preserved_records)})")
    print(f"  Total Pending Meal Candidates: {len(all_pending_transactions)}")
    print(f"  Total Unique Verified Restaurants: {len(restaurants)}")
    print(f"  Restaurants with >=1 Public Payment: {total_3plus}")
    print(f"  Restaurants with Public Payment since {RECENT_MEAL_START}: {recent_3plus}")
    print("=" * 70)

    # Write all 6 output datasets
    write_dataset_outputs(
        output_dir=args.output_dir,
        all_records=all_records,
        all_pending_transactions=all_pending_transactions,
        all_sources=all_sources,
        coverage_by_region=all_coverage,
        target_regions=active_regions,
        address_resolver=address_resolver,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
