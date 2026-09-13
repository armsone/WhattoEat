#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect_public_dining_east.py
=============================
Public dining expenditure collector and offline rebuild processor for 6 eastern regions:
부산 (Busan), 대구 (Daegu), 울산 (Ulsan), 경북 (Gyeongbuk), 경남 (Gyeongnam), 강원 (Gangwon).

Core Principles & Invariants:
1. Multi-Department Official Acquisition:
   Collects official expenditure disclosures from official HTTPS public administration portals.
   Traverses official disclosure lists and discovers document IDs dynamically.
   Derives actual department names from title, table row, or worksheet metadata.
   Missing department is left empty and does not reject an official meal record. No hardcoded guessed departments or documents.
2. Verified Address Resolution via Official Business Address Corpus:
   Loads official Small Business Corporation (sbiz / data.go.kr 15083033) address CSV corpus
   from --address-dir at runtime (UTF-8-SIG encoding).
   Resolves exact canonical name+branch and region-unique address matches only.
   Rejects ambiguous entries with duplicate different addresses.
   Preserves address punctuation and hyphens.
   Avoids cafes, non-alcoholic drinks, bakeries, desserts, and grocery/marts even if under 음식 code.
   Saves only actually matched/used address rows into address-evidence.jsonl with business ID and sourceURL.
   No hardcoded restaurant address database.
3. Privacy Minimization:
   Raw purpose text, staff/attendee names, phone numbers, and payment targets are never stored
   in committed output records. Only permitted fields and meal flag are output.
4. Acceptance Standard (Official Meal, Rolling 18 Months by Default):
   A qualifying restaurant must have >= 1 official institutional meal record
   and a payment within the configured rolling calendar-month window.
   Each target region must contain >= 1 such qualifying restaurant.
   Records output contains all valid meal expenditures for integration; coverage tracks
   qualified restaurant counts separately.
5. True Raw Row Tracking:
   Real original parsed raw row count is tracked independently; accepted count is never substituted for raw.
6. Offline Rebuild Reproducibility:
   In --rebuild mode, records and coverage can be reproduced strictly from committed
   privacy-minimized data (raw_expenditures.jsonl) and used address evidence (address-evidence.jsonl),
   without requiring private /tmp raw full spreadsheets.
   Source metadata and SHA-256 hashes are preserved during rebuild.
7. Bounded Acquisition Progress:
   Supports --regions subset filtering and --pages positive integer bounded traversal.
   Enforces normal HTTPS verification (no SSL disabling).
   Exits nonzero on incomplete required coverage.
"""

from __future__ import annotations

import calendar
import argparse
import csv
import dataclasses
import datetime
import gzip
import hashlib
import html
import json
import logging
import os
import re
import ssl
import sys
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# Third-party libraries bundled in CodEx environment
try:
    import openpyxl
except ImportError:
    openpyxl = None

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("collect_public_dining_east")


# ---------------------------------------------------------------------------
# Constants & Reference Rules
# ---------------------------------------------------------------------------
TODAY_STR = "2026-09-13"
TODAY_DATE = datetime.date(2026, 9, 13)
WINDOW_START = datetime.date(2025, 3, 13)

ALL_REGIONS = ("부산", "대구", "울산", "경북", "경남", "강원")

REGION_PROVINCE_MAP = {
    "부산": "부산광역시",
    "대구": "대구광역시",
    "울산": "울산광역시",
    "경북": "경상북도",
    "경남": "경상남도",
    "강원": "강원특별자치도",
}

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class VerifiedRecord:
    region: str
    institution: str
    department: str
    name: str
    address: str
    paymentDate: str
    sourceURL: str
    recordID: str
    meal: bool = True
    addressSourceURL: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "region": self.region,
            "institution": self.institution,
            "department": self.department,
            "name": self.name,
            "address": self.address,
            "paymentDate": self.paymentDate,
            "sourceURL": self.sourceURL,
            "recordID": self.recordID,
            "meal": self.meal,
        }
        if self.addressSourceURL:
            d["addressSourceURL"] = self.addressSourceURL
        return d


# ---------------------------------------------------------------------------
# Meal Classifier (Privacy Filter & Expense Exclusion)
# ---------------------------------------------------------------------------
class MealClassifier:
    """
    Identifies whether an expenditure line represents a legitimate meal.
    Excludes cafes/coffee/tea, gifts/flowers, congratulatory/condolence cash,
    groceries/marts, stationery, office supplies.
    """

    NON_MEAL_PURPOSE_PATTERNS = [
        re.compile(p) for p in [
            r"축의금", r"조의금", r"부의금", r"경조사비", r"축하금", r"격려금",
            r"화환", r"꽃다발", r"기념품", r"선물", r"축하물품", r"축의물품",
            r"다과", r"간식", r"차담", r"음료", r"커피", r"생수",
            r"사무용품", r"문구", r"비품", r"도서", r"수수료", r"임차",
        ]
    ]

    NON_MEAL_PLACE_PATTERNS = [
        re.compile(p) for p in [
            r"스타벅스", r"투썸플레이스", r"이디야", r"메가커피", r"컴포즈",
            r"카페", r"커피", r"베이커리", r"제과", r"떡집", r"호두과자",
            r"이마트", r"홈플러스", r"롯데마트", r"하나로마트", r"트레이더스",
            r"마트", r"기프트", r"화원", r"농원", r"꽃집", r"문구", r"오피스",
        ]
    ]

    MEAL_PURPOSE_PATTERNS = [
        re.compile(p) for p in [
            r"간담회", r"오찬", r"만찬", r"식사", r"협의회", r"회의", r"격려", r"노고",
        ]
    ]

    MEAL_PLACE_PATTERNS = [
        re.compile(p) for p in [
            r"식당", r"복국", r"횟집", r"곰탕", r"갈비", r"한우", r"밥상", r"쌈밥",
            r"깍두기", r"도시락", r"정식", r"요리", r"삼계탕", r"설렁탕", r"국수",
            r"돼지", r"구이", r"반점", r"식육", r"추어탕", r"해물", r"찌개",
        ]
    ]

    MULTI_LOCATION_PATTERNS = [
        re.compile(p) for p in [
            r"(?:외|등)\s*\d+\s*(?:개소|곳|점|건|명|인)",
            r"\d+\s*개소",
            r"(?:외|등)\s*[1-9]\b",
            r"\b등\s*\d+",
        ]
    ]

    @classmethod
    def is_multi_location(cls, raw: str) -> bool:
        if not raw:
            return False
        s = str(raw).strip()
        return any(pat.search(s) for pat in cls.MULTI_LOCATION_PATTERNS)

    @classmethod
    def clean_place_name(cls, raw: str) -> str:
        if not raw:
            return ""
        s = str(raw).strip()
        # Remove trailing parentheses with phone or notes e.g. (051-123-4567)
        s = re.sub(r"\s*\([0-9-]+\)$", "", s)
        return s.strip()

    @classmethod
    def is_meal(cls, place: str, purpose: str) -> bool:
        if not place or place in ("-", "집무실", "소장", "관내", "상황실", "회의실"):
            return False

        # Multi-location ambiguous place row should be EXCLUDED instead of stripping aggregate phrase
        if cls.is_multi_location(place) or cls.is_multi_location(purpose):
            return False

        clean_place = cls.clean_place_name(place)
        purpose_str = str(purpose or "")

        # Check negative place patterns
        for pat in cls.NON_MEAL_PLACE_PATTERNS:
            if pat.search(clean_place):
                # Cafe/mart exception: only if purpose explicitly states meal (e.g. 오찬/만찬)
                if not any(k in purpose_str for k in ("오찬", "만찬", "식사")):
                    return False

        # Check negative purpose patterns
        for pat in cls.NON_MEAL_PURPOSE_PATTERNS:
            if pat.search(purpose_str):
                return False

        # Check positive meal signals
        has_meal_purpose = any(pat.search(purpose_str) for pat in cls.MEAL_PURPOSE_PATTERNS)
        has_meal_place = any(pat.search(clean_place) for pat in cls.MEAL_PLACE_PATTERNS)

        if has_meal_purpose or has_meal_place:
            return True

        # Default: if place has substantial restaurant naming and purpose is meeting
        if len(clean_place) >= 2 and any(k in purpose_str for k in ("간담", "회의", "논의", "협의")):
            return True

        return False


# ---------------------------------------------------------------------------
# Official Address Corpus Loader & Verifier
# ---------------------------------------------------------------------------
class AddressVerifier:
    """
    Loads official Small Business Corporation (sbiz / data.go.kr 15083033) address corpus
    at runtime from --address-dir (files: 부산.csv, 대구.csv, 울산.csv, 경북.csv, 경남.csv, 강원.csv).
    Header UTF-8-SIG: 상가업소번호,상호명,지점명,상권업종중분류명,상권업종소분류명,시도명,시군구명,지번주소,도로명주소

    Strict Rules:
    - Avoids cafes, drinks, bakery, dessert, and grocery even though under broad food code.
    - Matches exact canonical name+branch and region unique ADDRESS only.
    - Never overwrites duplicates in index; duplicate different address rejects.
    - Strictly preserves punctuation and hyphens in addresses.
    - Saves only used matched rows to address-evidence.jsonl with business ID and sourceURL.
    - Supports offline rebuild from pre-existing address-evidence.jsonl.
    """

    OFFICIAL_CORPUS_SOURCE_URL = "https://www.data.go.kr/data/15083033/fileData.do"

    EXCLUDED_CATEGORIES = {
        "카페", "커피", "음료", "차담", "전통차", "다방", "주스",
        "제과", "베이커리", "빵", "떡", "도넛", "아이스크림", "디저트",
        "마트", "슈퍼", "식료품", "유통", "편의점",
    }

    def __init__(
        self,
        address_dir: Optional[Path] = None,
        evidence_file: Optional[Path] = None,
    ):
        self.address_dir = address_dir
        self.evidence_file = evidence_file

        # region -> canonical_key -> (verified_address, source_url, matched_evidence_dict)
        self._index: Dict[str, Dict[str, Tuple[str, str, Dict[str, Any]]]] = {}
        # region -> set of ambiguous canonical keys that have multiple different addresses
        self._conflicts: Dict[str, Set[str]] = {}
        # region -> canonical_key -> set of addresses seen
        self._seen_addresses: Dict[str, Dict[str, Set[str]]] = {}
        # Loaded regions tracker
        self._loaded_regions: Set[str] = set()
        # Matched rows used across the run: businessID -> evidence dict
        self.used_evidences: Dict[str, Dict[str, Any]] = {}

        # If an evidence file is already available, load it upfront (rebuild support)
        if self.evidence_file and self.evidence_file.exists():
            self._load_evidence_file(self.evidence_file)

    def _load_evidence_file(self, path: Path) -> None:
        logger.info(f"Loading pre-existing address evidence from {path}...")
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    ev = json.loads(line)
                    biz_id = str(ev.get("businessID") or ev.get("상가업소번호") or "").strip()
                    name = str(ev.get("name") or ev.get("상호명") or "").strip()
                    branch = str(ev.get("branch") or ev.get("지점명") or "").strip()
                    addr = str(ev.get("address") or ev.get("도로명주소") or ev.get("지번주소") or "").strip()
                    reg = str(ev.get("region") or "").strip()
                    url = str(ev.get("sourceURL") or self.OFFICIAL_CORPUS_SOURCE_URL).strip()

                    if not reg:
                        # Infer region from address
                        for r_k, prov in REGION_PROVINCE_MAP.items():
                            if prov in addr or r_k in addr:
                                reg = r_k
                                break

                    if not reg or not name or not addr:
                        continue

                    if reg not in self._index:
                        self._index[reg] = {}
                        self._conflicts[reg] = set()
                        self._seen_addresses[reg] = {}

                    evidence_dict = {
                        "businessID": biz_id,
                        "name": name,
                        "branch": branch,
                        "region": reg,
                        "address": addr,
                        "sourceURL": url,
                    }
                    evidence_dict = dict(ev, **evidence_dict)
                    # Pre-load into index, only record into used_evidences when actually resolved and used
                    keys = self._generate_keys(name, branch)
                    for k in keys:
                        self._register_key(reg, k, addr, url, evidence_dict)
        except Exception as e:
            logger.warning(f"Failed loading evidence file {path}: {e}")

    def _generate_keys(self, name: str, branch: str) -> List[str]:
        keys = []
        clean_name = name.strip()
        clean_branch = branch.strip()

        if clean_branch:
            keys.append(f"{clean_name} {clean_branch}")
            keys.append(f"{clean_name}{clean_branch}")
            # If branch ends with '점', also register without '점'
            if clean_branch.endswith("점") and len(clean_branch) > 1:
                keys.append(f"{clean_name} {clean_branch[:-1]}")
                keys.append(f"{clean_name}{clean_branch[:-1]}")
        else:
            keys.append(clean_name)
            keys.append(clean_name.replace(" ", ""))

        return keys

    def _register_key(
        self,
        region: str,
        key: str,
        address: str,
        source_url: str,
        evidence_dict: Dict[str, Any],
    ) -> None:
        if not key:
            return

        seen_set = self._seen_addresses[region].setdefault(key, set())
        seen_set.add(address)

        if len(seen_set) > 1:
            # Duplicate different address encountered -> mark conflict and reject lookups
            self._conflicts[region].add(key)
            self._index[region].pop(key, None)
        else:
            if key not in self._conflicts[region]:
                # First time seen: store unique entry (never overwrite duplicates)
                self._index[region][key] = (address, source_url, evidence_dict)

    def load_region_csv(self, region: str) -> bool:
        if region in self._loaded_regions:
            return True

        if region not in self._index:
            self._index[region] = {}
            self._conflicts[region] = set()
            self._seen_addresses[region] = {}

        if not self.address_dir:
            return False

        csv_path = self.address_dir / f"{region}.csv"
        if not csv_path.exists():
            logger.warning(
                f"[AddressVerifier] Official address corpus file not found: {csv_path}. "
                "Will rely on pre-existing address evidence if available."
            )
            return False

        logger.info(f"[AddressVerifier] Loading official address corpus for {region} from {csv_path}...")
        row_count = 0
        indexed_count = 0

        try:
            with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row_count += 1
                    mid_cat = str(row.get("상권업종중분류명") or "")
                    sub_cat = str(row.get("상권업종소분류명") or "")

                    # Avoid cafe/drinks/grocery even though broad food code includes them
                    if any(c in mid_cat or c in sub_cat for c in self.EXCLUDED_CATEGORIES):
                        continue

                    biz_id = str(row.get("상가업소번호") or "").strip()
                    name = str(row.get("상호명") or "").strip()
                    branch = str(row.get("지점명") or "").strip()
                    road_addr = str(row.get("도로명주소") or "").strip()
                    lot_addr = str(row.get("지번주소") or "").strip()

                    # Prefer road address; preserve punctuation and building-number hyphens
                    addr = road_addr if road_addr else lot_addr
                    if not name or not addr:
                        continue

                    evidence_dict = {
                        "businessID": biz_id,
                        "name": name,
                        "branch": branch,
                        "middleCategory": mid_cat,
                        "subCategory": sub_cat,
                        "province": str(row.get("시도명") or "").strip(),
                        "cityDistrict": str(row.get("시군구명") or "").strip(),
                        "lotAddress": lot_addr,
                        "roadAddress": road_addr,
                        "address": addr,
                        "region": region,
                        "sourceURL": self.OFFICIAL_CORPUS_SOURCE_URL,
                    }

                    keys = self._generate_keys(name, branch)
                    for k in keys:
                        self._register_key(
                            region, k, addr, self.OFFICIAL_CORPUS_SOURCE_URL, evidence_dict
                        )
                    indexed_count += 1

            self._loaded_regions.add(region)
            logger.info(
                f"[AddressVerifier] [{region}] Parsed {row_count} food rows; "
                f"indexed {indexed_count} valid entries ({len(self._conflicts[region])} ambiguous keys rejected)."
            )
            return True

        except Exception as e:
            logger.error(f"[AddressVerifier] Error loading CSV {csv_path}: {e}")
            return False

    def resolve(self, place_name: str, region: str) -> Optional[Tuple[str, str]]:
        """
        Resolves place_name against official business address index for region.
        Returns (address, source_url) if uniquely verified; None if ambiguous or unverified.
        """
        clean = MealClassifier.clean_place_name(place_name)
        if not clean:
            return None

        # Ensure region CSV is loaded if available
        if region not in self._loaded_regions:
            self.load_region_csv(region)

        reg_index = self._index.get(region, {})
        reg_conflicts = self._conflicts.get(region, set())

        # Build candidate lookup keys from raw place name
        candidate_keys = [clean, clean.replace(" ", "")]

        # If place name contains parentheses: 상호(지점)
        m = re.match(r"^([^(]+)\(([^)]+)\)$", clean)
        if m:
            c_name = m.group(1).strip()
            c_branch = m.group(2).strip()
            candidate_keys.extend([
                f"{c_name} {c_branch}",
                f"{c_name}{c_branch}",
            ])
            if c_branch.endswith("점") and len(c_branch) > 1:
                candidate_keys.extend([
                    f"{c_name} {c_branch[:-1]}",
                    f"{c_name}{c_branch[:-1]}",
                ])

        # Check for ambiguity rejection first
        for k in candidate_keys:
            if k in reg_conflicts:
                logger.debug(
                    f"Rejected ambiguous duplicate different address match for '{clean}' (key '{k}') in {region}"
                )
                return None

        # Exact canonical match
        for k in candidate_keys:
            if k in reg_index:
                addr, url, evidence = reg_index[k]
                biz_id = str(evidence.get("businessID") or evidence.get("상가업소번호") or "").strip()
                if biz_id:
                    self.used_evidences[biz_id] = evidence
                return (addr, url)

        return None

    def save_used_evidence(
        self, output_path: Path, used_records: Optional[List[VerifiedRecord]] = None
    ) -> None:
        """
        Saves only used matched rows into address-evidence.jsonl with business ID and sourceURL.
        Does NOT copy entire region-wide lists. Output evidence only exact matched address rows.
        """
        target_evidences = self.used_evidences
        if used_records is not None:
            used_addrs = set(r.address for r in used_records)
            target_evidences = {
                k: v for k, v in self.used_evidences.items()
                if v.get("address") in used_addrs
                or v.get("roadAddress") in used_addrs
                or v.get("lotAddress") in used_addrs
            }

        logger.info(
            f"Writing {len(target_evidences)} verified address evidence records to {output_path}..."
        )
        with open(output_path, "w", encoding="utf-8") as f:
            for ev in target_evidences.values():
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Unified Multi-Format Spreadsheet Loader & Fallback Readers
# ---------------------------------------------------------------------------
SOFFICE_EXECUTABLE = (
    "/Users/armsone/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/soffice"
)

DISALLOWED_USER_TITLES = {
    "기획관", "기획담당관", "기획조정실장", "총무팀장", "조리종사자", "도시계획국장",
    "정무기획보좌관", "정책수석보좌관", "국회협력관", "메시지기획보좌관", "금융창업정책관",
    "사무처장", "위원장", "의정정책관", "건설본부장", "사무국장", "총무과장", "창업벤처담당관",
    "소장", "실장", "국장", "과장", "팀장", "보좌관", "주무관", "사무관", "서기관",
}


def get_cell(row: Any, idx: Optional[int]) -> Any:
    """Safe cell accessor preventing IndexError across variable-length rows."""
    if idx is not None and isinstance(row, (list, tuple)) and 0 <= idx < len(row):
        return row[idx]
    return None


def is_personal_name_or_title(val: str) -> bool:
    """Detects whether a string represents an individual person's name or user job title."""
    s = str(val or "").strip()
    if not s or s in ("-", "None"):
        return True
    if s in DISALLOWED_USER_TITLES:
        return True
    # Pure personal name detection: 2-4 Hangul chars with no department suffix
    if re.fullmatch(r"[가-힣]{2,4}", s):
        if not re.search(r"(과|실|국|부|팀|관|소|원|대|서|센터|단|청)$", s):
            return True
    return False


def read_xlsx_via_zip_xml(file_path: Path) -> List[Tuple[str, List[List[Any]]]]:
    """
    Standard zip+XML cell reader fallback for .xlsx files when openpyxl fails
    due to malformed styles, missing names, or index errors.
    """
    sheets: List[Tuple[str, List[List[Any]]]] = []
    try:
        with zipfile.ZipFile(file_path, "r") as z:
            # 1. Read shared strings table
            shared_strings: List[str] = []
            if "xl/sharedStrings.xml" in z.namelist():
                ss_tree = ET.fromstring(z.read("xl/sharedStrings.xml"))
                ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                for si in ss_tree.findall(".//main:si", ns) or ss_tree.findall(".//si"):
                    t_elems = si.findall(".//main:t", ns) or si.findall(".//t")
                    shared_strings.append("".join((t.text or "") for t in t_elems))

            # 2. Worksheet files
            sheet_files = [
                n for n in z.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
            ]
            sheet_files.sort(
                key=lambda x: int(re.search(r"sheet(\d+)", x).group(1))
                if re.search(r"sheet(\d+)", x)
                else 0
            )

            for sf in sheet_files:
                sheet_data: List[List[Any]] = []
                ws_tree = ET.fromstring(z.read(sf))
                s_title = Path(sf).stem

                rows = ws_tree.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row") or ws_tree.findall(".//row")
                consecutive_empty = 0
                for r in rows:
                    if len(sheet_data) > 3000:
                        break
                    row_cells: Dict[int, Any] = {}
                    max_col = -1
                    cells = r.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c") or r.findall(".//c")
                    for c in cells:
                        ref = c.get("r") or ""
                        col_m = re.match(r"([A-Za-z]+)", ref)
                        if col_m:
                            col_letters = col_m.group(1).upper()
                            col_idx = 0
                            for char in col_letters:
                                col_idx = col_idx * 26 + (ord(char) - ord("A") + 1)
                            col_idx -= 1
                        else:
                            col_idx = max_col + 1
                        max_col = max(max_col, col_idx)

                        t_type = c.get("t")
                        v_elem = c.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
                        if v_elem is None:
                            v_elem = c.find("v")
                        val = v_elem.text if v_elem is not None else None

                        if t_type == "s" and val is not None:
                            try:
                                val = shared_strings[int(val)]
                            except (IndexError, ValueError):
                                pass
                        elif t_type == "inlineStr":
                            is_elem = c.find(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
                            if is_elem is None:
                                is_elem = c.find(".//t")
                            if is_elem is not None:
                                val = is_elem.text
                        row_cells[col_idx] = val

                    actual_row = int(r.get("r", str(len(sheet_data) + 1)))
                    while len(sheet_data) < actual_row - 1:
                        sheet_data.append([])
                    if not row_cells or all(v is None or str(v).strip() == "" for v in row_cells.values()):
                        consecutive_empty += 1
                        if consecutive_empty >= 20 and len(sheet_data) > 5:
                            break
                        sheet_data.append([])
                        continue
                    consecutive_empty = 0

                    row_list = [row_cells.get(i) for i in range(max_col + 1)]
                    sheet_data.append(row_list)

                if sheet_data:
                    sheets.append((s_title, sheet_data))
    except Exception as e:
        logger.warning(f"Error reading xlsx via zip XML fallback ({file_path.name}): {e}")
    return sheets


def read_pdf_tables(file_path: Path) -> List[Tuple[str, List[List[Any]]]]:
    """Extracts expenditure tables from PDF files using bundled pdfplumber."""
    if not pdfplumber:
        logger.warning(f"pdfplumber library unavailable; cannot parse PDF {file_path.name}")
        return []
    sheets: List[Tuple[str, List[List[Any]]]] = []
    try:
        with pdfplumber.open(str(file_path)) as pdf:
            all_rows: List[List[Any]] = []
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    for t in tables:
                        for r in t:
                            if r and any(r):
                                all_rows.append(r)
            if all_rows:
                sheets.append(("PDF", all_rows))
    except Exception as e:
        logger.warning(f"Error reading PDF {file_path.name}: {e}")
    return sheets


def read_html_spreadsheet(file_path: Path) -> List[Tuple[str, List[List[Any]]]]:
    """Parses HTML table or SpreadsheetML format files misnamed as xls/xlsx."""
    sheets: List[Tuple[str, List[List[Any]]]] = []
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
        if "<Workbook" in text and "<Worksheet" in text:
            ws_matches = re.findall(r'<Worksheet[^>]*ss:Name="([^"]+)"[^>]*>(.*?)</Worksheet>', text, re.S)
            for ws_name, ws_content in ws_matches:
                ws_rows: List[List[Any]] = []
                rows = re.findall(r"<Row[^>]*>(.*?)</Row>", ws_content, re.S)
                for r in rows:
                    cells = re.findall(r"<Data[^>]*>(.*?)</Data>", r, re.S)
                    clean_cells = [html.unescape(c.strip()) for c in cells]
                    if any(clean_cells):
                        ws_rows.append(clean_cells)
                if ws_rows:
                    sheets.append((ws_name, ws_rows))
            if sheets:
                return sheets

        tables = re.findall(r"<table.*?</table>", text, re.S | re.I)
        for t_idx, tbl in enumerate(tables):
            rows_data: List[List[Any]] = []
            tr_matches = re.findall(r"<tr.*?</tr>", tbl, re.S | re.I)
            for tr in tr_matches:
                cells = [
                    html.unescape(re.sub(r"<[^>]+>", "", c).strip())
                    for c in re.findall(r"<t[dh].*?</t[dh]>", tr, re.S | re.I)
                ]
                if any(cells):
                    rows_data.append(cells)
            if rows_data:
                sheets.append((f"Table{t_idx+1}", rows_data))
    except Exception as e:
        logger.warning(f"Error reading HTML spreadsheet {file_path.name}: {e}")
    return sheets


def read_hwpx_tables(file_path: Path) -> List[Tuple[str, List[List[Any]]]]:
    """Extracts tables from HWPX zip archives."""
    sheets: List[Tuple[str, List[List[Any]]]] = []
    try:
        with zipfile.ZipFile(file_path, "r") as z:
            section_files = [
                n for n in z.namelist() if n.startswith("Contents/section") and n.endswith(".xml")
            ]
            all_rows: List[List[Any]] = []
            for sf in section_files:
                sec_tree = ET.fromstring(z.read(sf))
                tables = sec_tree.findall(".//{http://www.hancom.co.kr/hwpml/2011/paragraph}tbl") or sec_tree.findall(".//tbl")
                for tbl in tables:
                    tr_list = tbl.findall(".//{http://www.hancom.co.kr/hwpml/2011/paragraph}tr") or tbl.findall(".//tr")
                    for tr in tr_list:
                        tc_list = tr.findall(".//{http://www.hancom.co.kr/hwpml/2011/paragraph}tc") or tr.findall(".//tc")
                        row_vals = []
                        for tc in tc_list:
                            texts = [
                                t.text or ""
                                for t in (
                                    tc.findall(".//{http://www.hancom.co.kr/hwpml/2011/paragraph}t")
                                    or tc.findall(".//t")
                                )
                            ]
                            row_vals.append(" ".join(texts).strip())
                        if any(row_vals):
                            all_rows.append(row_vals)
            if all_rows:
                sheets.append(("HWPX", all_rows))
    except Exception as e:
        logger.warning(f"Error reading HWPX {file_path.name}: {e}")
    return sheets


def convert_ole_xls_with_soffice(file_path: Path, output_dir: Path) -> Optional[Path]:
    """
    Subprocess invocation of installed soffice binary for legacy OLE XLS files.
    Executable is constrained to existing environment runtime override path only.
    """
    if not os.path.exists(SOFFICE_EXECUTABLE):
        return None
    try:
        cmd = [
            SOFFICE_EXECUTABLE,
            "--headless",
            "--convert-to",
            "xlsx",
            "--outdir",
            str(output_dir),
            str(file_path),
        ]
        subprocess.run(cmd, capture_output=True, timeout=60)
        expected_out = output_dir / (file_path.stem + ".xlsx")
        if expected_out.exists() and expected_out.stat().st_size > 0:
            return expected_out
    except Exception as e:
        logger.warning(f"soffice conversion error on {file_path.name}: {e}")
    return None


def load_workbook_sheets(file_path: Path) -> List[Tuple[str, List[List[Any]]]]:
    """
    Unified loader detecting file signature and parsing standard xlsx, zip XML fallback,
    PDF, HTML spreadsheet, HWPX, or converted OLE XLS.
    """
    if not file_path.exists() or file_path.stat().st_size == 0:
        return []

    try:
        with open(file_path, "rb") as f:
            header = f.read(2048)
    except Exception:
        return []

    if header.startswith(b"\x9b DRMONE") or b"DRMONE" in header[:64] or b"Fasoo DRM" in header[:128]:
        logger.warning(f"File {file_path.name} is Fasoo DRM encrypted; skipping.")
        return []

    # 1. ZIP format (XLSX or HWPX)
    if header.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(file_path, "r") as z:
                names = z.namelist()
                if any("Contents/section" in n for n in names) or "Contents/content.hwpml" in names:
                    return read_hwpx_tables(file_path)
        except Exception:
            pass

        if openpyxl:
            try:
                wb = openpyxl.load_workbook(str(file_path), data_only=True)
                sheets: List[Tuple[str, List[List[Any]]]] = []
                for ws in wb.worksheets:
                    ws_rows: List[List[Any]] = []
                    consecutive_empty = 0
                    for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
                        if r_idx > 3000:
                            break
                        vals = [c for c in row if c is not None]
                        if not vals or all(str(v).strip() == "" for v in vals):
                            consecutive_empty += 1
                            if consecutive_empty >= 20 and len(ws_rows) > 5:
                                break
                            ws_rows.append(list(row))
                            continue
                        consecutive_empty = 0
                        ws_rows.append(list(row))
                    if ws_rows:
                        sheets.append((ws.title, ws_rows))
                if sheets:
                    return sheets
            except Exception as e:
                logger.warning(
                    f"openpyxl failed on {file_path.name} ({e}); falling back to standard zip XML reader."
                )

        return read_xlsx_via_zip_xml(file_path)

    # 2. PDF format
    if header.startswith(b"%PDF"):
        return read_pdf_tables(file_path)

    # 3. HTML / SpreadsheetML
    clean_h = header.lstrip(b"\xef\xbb\xbf \t\r\n")
    if clean_h.startswith(b"<") or b"<table" in header.lower() or b"<workbook" in header.lower():
        return read_html_spreadsheet(file_path)

    # 4. OLE Compound Document (Legacy XLS)
    if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        conv = convert_ole_xls_with_soffice(file_path, file_path.parent)
        if conv and conv != file_path and conv.exists():
            return load_workbook_sheets(conv)

    logger.warning(f"Unsupported file format signature for {file_path.name}")
    return []


# ---------------------------------------------------------------------------
# Base Regional Collector & Parser
# ---------------------------------------------------------------------------
class BaseRegionalCollector:
    """Base class for regional public expenditure acquisition and parsing."""

    def __init__(
        self,
        region: str,
        institution: str,
        cache_dir: Path,
        verifier: AddressVerifier,
        max_pages: int = 1,
    ):
        self.region = region
        self.institution = institution
        self.cache_dir = cache_dir
        self.verifier = verifier
        self.max_pages = max(1, max_pages)
        self.downloaded_sources: List[Dict[str, Any]] = []
        self.raw_row_count: int = 0  # Real original parsed raw row count tracked independently
        self.eligible_meal_checks = 0
        self.meal_row_count = 0
        self.address_match_count = 0
        self.address_unmatched_count = 0
        self.unresolved_records = []

    def remember_unresolved(self, name, department, date, source_url, record_id):
        safe_name = re.sub(r"(?:0\d{1,2}[- )]?\d{3,4}[- ]?\d{4})", "", str(name)).strip()
        if not safe_name:
            return
        department = re.split(r"\s*>\s*", department or "")[-1].strip()
        if is_personal_name_or_title(department):
            department = ""
        institution = self.institution
        match = re.search(r"([가-힣]+소방서|[가-힣]+대학교)", department)
        if match:
            institution = match.group(1)
        hints = re.findall(r"\(([^)]*(?:특별시|광역시|특별자치도|[가-힣]+[시군구]|[가-힣]+(?:로|길|동)\s*\d)[^)]*)\)", safe_name)
        self.unresolved_records.append({"region": self.region, "institution": institution, "department": department, "name": safe_name, "addressHint": " ".join(hints), "paymentDate": date, "sourceURL": source_url, "recordID": record_id, "isMeal": True})

    def classify_meal(self, place: str, purpose: str) -> bool:
        self.eligible_meal_checks += 1
        accepted = MealClassifier.is_meal(place, purpose)
        self.meal_row_count += int(accepted)
        return accepted

    def resolve_address(self, place: str, region: str):
        result = self.verifier.resolve(place, region)
        self.address_match_count += int(result is not None)
        self.address_unmatched_count += int(result is None)
        return result

    def fetch_url(
        self,
        url: str,
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> Optional[bytes]:
        """
        Fetches public HTTPS document URL with normal SSL certificate verification.
        Disabling SSL verification is strictly removed.
        """
        req_headers = {"User-Agent": DEFAULT_UA}
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, data=data, headers=req_headers)
        ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                if resp.status == 200:
                    return resp.read()
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
        return None

    def register_cached_source(
        self,
        url: str,
        target_path: Path,
        department: str = "",
        parent_doc_uid: str = "",
        att_uid: str = "",
    ) -> None:
        """
        Ensures static existing cached files have SHA-256 metadata registered
        even when re-download is skipped or in offline rebuild mode.
        """
        if not target_path.exists() or target_path.stat().st_size == 0:
            return
        data = target_path.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        path_str = str(target_path)
        for s in self.downloaded_sources:
            if s.get("path") == path_str:
                if department and not s.get("department"):
                    s["department"] = department
                if parent_doc_uid and not s.get("parent_doc_uid"):
                    s["parent_doc_uid"] = parent_doc_uid
                if att_uid and not s.get("att_uid"):
                    s["att_uid"] = att_uid
                return
        meta = {
            "region": self.region,
            "institution": self.institution,
            "url": url,
            "path": path_str,
            "sha256": sha256,
            "bytes": len(data),
        }
        meta["contentURL"] = url
        if self.region == "부산" and parent_doc_uid:
            meta["contentURL"] = self.FILE_URL_TMPL.format(upper_no=parent_doc_uid)
        elif self.region == "경남" and parent_doc_uid and att_uid:
            meta["contentURL"] = self.DOWN_URL_TMPL.format(data_sid=parent_doc_uid, file_sid=att_uid)
        elif self.region == "강원" and parent_doc_uid:
            detail = self.cache_dir / ("gw_" + parent_doc_uid + ".html")
            if detail.exists():
                match = re.search(r'href="(/egf/bp/common/front/\d+/download)"', detail.read_text(encoding="utf-8", errors="replace"))
                if match:
                    meta["contentURL"] = "https://state.gwd.go.kr" + match.group(1)
        if department:
            meta["department"] = department
        if parent_doc_uid:
            meta["parent_doc_uid"] = parent_doc_uid
        if att_uid:
            meta["att_uid"] = att_uid
        self.downloaded_sources.append(meta)

    def download_file(
        self,
        url: str,
        target_path: Path,
        department: str = "",
        parent_doc_uid: str = "",
        att_uid: str = "",
        timeout: int = 45,
    ) -> bool:
        if target_path.exists() and target_path.stat().st_size > 0:
            self.register_cached_source(
                url, target_path, department=department, parent_doc_uid=parent_doc_uid, att_uid=att_uid
            )
            return True
        data = self.fetch_url(url, timeout=timeout)
        if data:
            target_path.write_bytes(data)
            self.register_cached_source(
                url, target_path, department=department, parent_doc_uid=parent_doc_uid, att_uid=att_uid
            )
            return True
        return False

    def _dedup_paths(self, paths: Iterable[Path]) -> List[Path]:
        seen: Set[Path] = set()
        deduped: List[Path] = []
        for p in paths:
            try:
                res = p.resolve()
            except Exception:
                res = p
            if res not in seen and p.exists():
                seen.add(res)
                deduped.append(p)
        return deduped

    def collect(self) -> None:
        """Fetch remote sources and save to cache_dir."""
        raise NotImplementedError

    def parse(self) -> List[VerifiedRecord]:
        """Parse cached files and return verified records."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 1. Busan (부산광역시청)
# ---------------------------------------------------------------------------
class BusanCollector(BaseRegionalCollector):
    SEED_LIST_URL = "https://www.busan.go.kr/ghopen12/list?schBizNo=45&curPage={page}"
    VIEW_URL_TMPL = (
        "https://www.busan.go.kr/ghopen12/view?schCommand=Expense&schIndx={upper_no}&curPage=1&schBizNo=45"
    )
    FILE_URL_TMPL = (
        "https://www.busan.go.kr/comm/getFile?srvcId=OPENGOV&upperNo={upper_no}&fileTy=ATTACH&fileNo=1"
    )

    def _discover_items_from_html(self, html_text: str) -> List[Tuple[int, str]]:
        """
        Derives upperNo and actual department from official list/view HTML metadata.
        Title format: 2026년 2분기 업무추진비 집행내역(창업벤처담당관)
        Preserves department hierarchy if present (e.g. 미래공간전략국 > 생활공간혁신과).
        """
        results: List[Tuple[int, str]] = []
        rows = re.findall(r"<tr.*?>.*?</tr>", html_text, re.S)
        for r in rows:
            m_idx = re.search(r"schIndx=(\d+)", r, re.I)
            if not m_idx:
                continue
            upper_no = int(m_idx.group(1))

            dept_name = ""
            m_hier = re.search(r"<td[^>]*nowrap[^>]*txtLeft[^>]*>(.*?)</td>", r, re.S)
            if m_hier:
                raw_hier = re.sub(r"<[^>]+>", "", m_hier.group(1)).strip()
                if raw_hier:
                    dept_name = raw_hier

            if not dept_name:
                m_title = re.search(r'href="[^"]*schIndx=\d+[^"]*"[^>]*>(.*?)</a>', r, re.S | re.I)
                if m_title:
                    link_title = re.sub(r"<[^>]+>", "", m_title.group(1)).strip()
                    dept_m = re.search(r"\(([^)]+)\)", link_title)
                    if dept_m:
                        dept_name = dept_m.group(1).strip()

            if dept_name and not any(x[0] == upper_no for x in results):
                results.append((upper_no, dept_name))

        if not results:
            pattern = re.compile(
                r'href="[^"]*schIndx=(\d+)[^"]*"[^>]*>(.*?)</a>', re.S | re.IGNORECASE
            )
            for m in pattern.finditer(html_text):
                upper_no = int(m.group(1))
                link_title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                dept_m = re.search(r"\(([^)]+)\)", link_title)
                dept_name = dept_m.group(1).strip() if dept_m else ""
                if dept_name and not any(x[0] == upper_no for x in results):
                    results.append((upper_no, dept_name))

        return results

    def collect(self) -> None:
        logger.info(f"[Busan] Fetching official department disclosure lists (pages 1..{self.max_pages})...")
        discovered_items: List[Tuple[int, str]] = []
        seen_upper_nos: Set[int] = set()

        for p in range(1, self.max_pages + 1):
            list_url = self.SEED_LIST_URL.format(page=p)
            list_html = self.fetch_url(list_url)
            if list_html:
                out_list = self.cache_dir / f"busan_list_p{p}.html"
                out_list.write_bytes(list_html)
                text = list_html.decode("utf-8", errors="replace")
                items = self._discover_items_from_html(text)
                new_in_page = [it for it in items if it[0] not in seen_upper_nos]
                if p > 1 and not new_in_page:
                    logger.warning(
                        f"[Busan] Page {p} yielded no new document IDs (repeated page). Stopping pagination."
                    )
                    break
                for it in items:
                    if it[0] not in seen_upper_nos:
                        seen_upper_nos.add(it[0])
                        discovered_items.append(it)

        logger.info(f"[Busan] Discovered {len(discovered_items)} official department disclosure documents.")
        for upper_no, dept in discovered_items:
            out_file = self.cache_dir / f"b_{upper_no}.xlsx"
            file_url = self.FILE_URL_TMPL.format(upper_no=upper_no)
            if not out_file.exists():
                logger.info(f"[Busan] Downloading attachment upperNo={upper_no} ({dept})...")
            self.download_file(file_url, out_file, department=dept, parent_doc_uid=str(upper_no))

    def parse(self) -> List[VerifiedRecord]:
        records: List[VerifiedRecord] = []
        self.raw_row_count = 0

        # 1. Derive department metadata dynamically from downloaded HTMLs
        dept_by_upper_no: Dict[str, str] = {}
        candidate_htmls = self._dedup_paths(
            list(self.cache_dir.glob("busan_*.html"))
            + list(self.cache_dir.glob("b_*.html"))
            + list(Path(".").glob("busan_*.html"))
            + list(Path(".").glob("bdip.html"))
        )
        for h in candidate_htmls:
            try:
                t = h.read_text(encoding="utf-8", errors="replace")
                for u_no, d_name in self._discover_items_from_html(t):
                    dept_by_upper_no[str(u_no)] = d_name
            except Exception:
                pass

        # 2. Find cached Excel/Spreadsheet files
        candidate_paths = self._dedup_paths(
            list(self.cache_dir.glob("b_*.xlsx"))
            + list(self.cache_dir.glob("busan_*.xlsx"))
            + list(Path(".").glob("b_*.xlsx"))
            + list(Path(".").glob("busan_*.xlsx"))
        )

        for p in candidate_paths:
            m = re.search(r"(\d+)", p.name)
            upper_no = m.group(1) if m else "unknown"
            source_url = self.VIEW_URL_TMPL.format(upper_no=upper_no)
            self.register_cached_source(
                source_url, p, department=dept_by_upper_no.get(upper_no, ""), parent_doc_uid=upper_no
            )

            sheets = load_workbook_sheets(p)
            if not sheets:
                continue

            for sheet_title, rows in sheets:
                if not rows:
                    continue

                # Department: prefer discovered source-list department
                discovered_dept = dept_by_upper_no.get(upper_no, "")
                if not discovered_dept:
                    for r in rows[:6]:
                        r_str = " ".join(str(c) for c in r if c is not None)
                        m_dept = re.search(r"\(([^)]+과|[^)]+실|[^)]+본부|[^)]+관|[^)]+서)\)", r_str)
                        if m_dept:
                            cand = m_dept.group(1).strip()
                            if not is_personal_name_or_title(cand):
                                discovered_dept = cand
                                break

                header_idx = -1
                col_map: Dict[str, int] = {}
                for idx, r in enumerate(rows[:10]):
                    row_strs = [str(c).strip() if c is not None else "" for c in r]
                    if any("장소" in c or "사용처" in c for c in row_strs) and any(
                        k in "".join(row_strs) for k in ("일자", "일시")
                    ):
                        header_idx = idx
                        for c_i, val in enumerate(row_strs):
                            if "일자" in val or "일시" in val:
                                col_map["date"] = c_i
                            elif "장소" in val or "사용처" in val:
                                col_map["place"] = c_i
                            elif "목적" in val or "내역" in val:
                                col_map["purpose"] = c_i
                            elif "부서" in val:
                                # Explicit 부서 only; NEVER map 사용자/직책/성명
                                if "사용자" not in val and "직책" not in val and "성명" not in val and "개인" not in val:
                                    col_map["dept"] = c_i
                        break

                if header_idx == -1 or "date" not in col_map or "place" not in col_map:
                    continue

                for r_i, r in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
                    if not r or all(c is None or str(c).strip() == "" for c in r):
                        continue

                    raw_place = get_cell(r, col_map["place"])
                    raw_date = get_cell(r, col_map["date"])
                    raw_purpose = get_cell(r, col_map.get("purpose"))
                    raw_dept = get_cell(r, col_map.get("dept"))

                    place_val = str(raw_place or "").strip()
                    purpose_val = str(raw_purpose or "").strip()

                    # Raw expenditure count: actual expenditure rows only
                    if any(k in place_val or k in purpose_val for k in ("합계", "총계", "소계", "총 합계")):
                        continue
                    if any(k == place_val for k in ("장소", "사용처", "집행장소")):
                        continue
                    if not raw_place and not raw_date and not raw_purpose:
                        continue

                    self.raw_row_count += 1

                    # Department selection: prefer discovered source-list department
                    dept_str = ""
                    if discovered_dept:
                        dept_str = discovered_dept.strip()
                    elif raw_dept:
                        cand_dept = str(raw_dept).strip().replace("\n", " ")
                        if not is_personal_name_or_title(cand_dept):
                            dept_str = cand_dept

                    # Clean personal names; personal names must not leak
                    dept_str = re.sub(r"\s*\([가-힣]{2,4}\)$", "", dept_str).strip()
                    if not dept_str or is_personal_name_or_title(dept_str):
                        dept_str = ""

                    if not raw_place or not raw_date:
                        continue

                    date_str = normalize_date(raw_date)
                    if not date_str:
                        continue

                    try:
                        p_date = datetime.date.fromisoformat(date_str)
                    except (ValueError, TypeError):
                        continue

                    if p_date > TODAY_DATE or p_date < WINDOW_START:
                        continue

                    if not self.classify_meal(place_val, purpose_val):
                        continue

                    addr_info = self.resolve_address(place_val, "부산")
                    if not addr_info:
                        self.remember_unresolved(place_val, dept_str, date_str, source_url, f"busan_{upper_no}_s{sheet_title}_r{r_i}")
                        continue

                    addr, addr_url = addr_info
                    rec_id = f"busan_{upper_no}_s{sheet_title}_r{r_i}"

                    inst_name = "부산광역시"
                    if "소방서" in dept_str:
                        m_fire = re.search(r"([가-힣]+소방서)", dept_str)
                        inst_name = m_fire.group(1) if m_fire else "부산광역시"

                    records.append(
                        VerifiedRecord(
                            region="부산",
                            institution=inst_name,
                            department=dept_str,
                            name=MealClassifier.clean_place_name(place_val),
                            address=addr,
                            paymentDate=date_str,
                            sourceURL=source_url,
                            recordID=rec_id,
                            meal=True,
                            addressSourceURL=addr_url,
                        )
                    )

        return records


# ---------------------------------------------------------------------------
# 2. Daegu (대구광역시청)
# ---------------------------------------------------------------------------
class DaeguCollector(BaseRegionalCollector):
    SEED_LIST_URL = (
        "https://www.daegu.go.kr/index.do?menu_id=00000084&pageIndex={page}&listPageIndex={page}"
    )
    FILE_URL_TMPL = (
        "https://www.daegu.go.kr/icms/cmm/fms/FileDown.do?atchFileId={atch_file_id}&fileSn={file_sn}"
    )
    VIEW_BASE_URL = "https://www.daegu.go.kr/index.do?menu_id=00000084"

    def _discover_items_from_html(self, html_text: str) -> List[Tuple[str, str, str]]:
        """
        Derives attachment file ID, SN, and actual department from table rows in Daegu HTML.
        Rows contain: <td data-table-type="hide_t">{department}</td>
        and fn_egov_downFile('{fileId}','{fileSn}')
        """
        results: List[Tuple[str, str, str]] = []
        rows = re.findall(r"<tr.*?>.*?</tr>", html_text, re.S)
        for r in rows:
            m_file = re.search(r"fn_egov_downFile\('([^']+)',\s*'([^']+)'\)", r)
            if not m_file:
                continue
            file_id, file_sn = m_file.group(1), m_file.group(2)

            dept_name = ""
            m_dept = re.search(r'<td[^>]*data-table-type="hide_t"[^>]*>(.*?)</td>', r, re.S)
            if m_dept:
                cand = re.sub(r"<[^>]+>", "", m_dept.group(1)).strip()
                if not cand.isdigit() and len(cand) >= 2:
                    dept_name = cand

            if not dept_name:
                m_title = re.search(r"\(([^)]+)\)", r)
                if m_title:
                    dept_name = m_title.group(1).strip()

            if file_id and dept_name and not any(x[0] == file_id for x in results):
                results.append((file_id, file_sn, dept_name))
        return results

    def collect(self) -> None:
        logger.info(f"[Daegu] Fetching official department disclosures (pages 1..{self.max_pages})...")
        discovered: List[Tuple[str, str, str]] = []
        seen_file_ids: Set[str] = set()

        for p in range(1, self.max_pages + 1):
            list_url = self.SEED_LIST_URL.format(page=p)
            list_html = self.fetch_url(list_url)
            if list_html:
                out_list = self.cache_dir / f"daegu_list_p{p}.html"
                out_list.write_bytes(list_html)
                text = list_html.decode("utf-8", errors="replace")
                items = self._discover_items_from_html(text)
                new_in_page = [it for it in items if it[0] not in seen_file_ids]
                if p > 1 and not new_in_page:
                    logger.warning(
                        f"[Daegu] Page {p} yielded no new document IDs (repeated page). Stopping pagination."
                    )
                    break
                for it in items:
                    if it[0] not in seen_file_ids:
                        seen_file_ids.add(it[0])
                        discovered.append(it)

        logger.info(f"[Daegu] Discovered {len(discovered)} official attachments across departments.")
        for file_id, file_sn, dept in discovered:
            out_file = self.cache_dir / f"daegu_{file_id}_{file_sn}.xlsx"
            file_url = self.FILE_URL_TMPL.format(atch_file_id=file_id, file_sn=file_sn)
            if not out_file.exists():
                logger.info(f"[Daegu] Downloading {dept} disclosure ({file_id})...")
            self.download_file(file_url, out_file, department=dept, parent_doc_uid=file_id, att_uid=file_sn)

    def parse(self) -> List[VerifiedRecord]:
        records: List[VerifiedRecord] = []
        self.raw_row_count = 0

        # 1. Derive department metadata dynamically from HTMLs
        dept_by_file_id: Dict[str, str] = {}
        candidate_htmls = self._dedup_paths(
            list(self.cache_dir.glob("daegu_*.html"))
            + list(Path(".").glob("daegu_*.html"))
        )
        for h in candidate_htmls:
            try:
                t = h.read_text(encoding="utf-8", errors="replace")
                for fid, fsn, dname in self._discover_items_from_html(t):
                    dept_by_file_id[fid] = dname
            except Exception:
                pass

        candidate_paths = self._dedup_paths(
            list(self.cache_dir.glob("daegu_*.xlsx"))
            + list(Path(".").glob("daegu_*.xlsx"))
        )

        for p in candidate_paths:
            m = re.search(r"daegu_([A-Za-z0-9_]+)", p.name)
            doc_id = m.group(1) if m else "doc"

            discovered_dept = ""
            for fid, dname in dept_by_file_id.items():
                if fid in doc_id or fid in p.name:
                    discovered_dept = dname
                    break

            source_url = self.VIEW_BASE_URL
            m_down = re.search(r"(FILE_\d+)_(\d+)", p.name)
            if m_down:
                source_url = self.FILE_URL_TMPL.format(atch_file_id=m_down.group(1), file_sn=m_down.group(2))

            self.register_cached_source(
                source_url, p, department=discovered_dept, parent_doc_uid=doc_id
            )

            sheets = load_workbook_sheets(p)
            if not sheets:
                continue

            for sheet_title, rows in sheets:
                if not rows:
                    continue

                col_map: Dict[str, int] = {}
                header_idx = -1

                for r_idx, row in enumerate(rows[:15], start=1):
                    row_strs = [str(c).strip() if c is not None else "" for c in row]
                    if any("사용장소" in s or "장소" in s or "사용처" in s for s in row_strs) and any(
                        "일자" in s or "일시" in s for s in row_strs
                    ):
                        header_idx = r_idx
                        for c_i, val in enumerate(row):
                            val_str = str(val or "").strip()
                            if "일자" in val_str or "일시" in val_str:
                                col_map["date"] = c_i
                            elif "사용장소" in val_str or "장소" in val_str or "사용처" in val_str:
                                col_map["place"] = c_i
                            elif "내역" in val_str or "목적" in val_str:
                                col_map["purpose"] = c_i
                            elif "부서" in val_str:
                                if "사용자" not in val_str and "직책" not in val_str and "성명" not in val_str:
                                    col_map["dept"] = c_i
                        break

                if header_idx == -1 or "date" not in col_map or "place" not in col_map:
                    continue

                for r_i, row in enumerate(rows[header_idx:], start=header_idx + 1):
                    if not row or all(c is None or str(c).strip() == "" for c in row):
                        continue

                    raw_place = get_cell(row, col_map["place"])
                    raw_date = get_cell(row, col_map["date"])
                    raw_purpose = get_cell(row, col_map.get("purpose"))
                    raw_dept = get_cell(row, col_map.get("dept"))

                    place_str = str(raw_place or "").strip()
                    purpose_str = str(raw_purpose or "").strip()

                    if any(k in place_str or k in purpose_str for k in ("합계", "총계", "소계", "총 합계")):
                        continue
                    if any(k == place_str for k in ("장소", "사용처", "사용장소", "집행장소")):
                        continue
                    if not raw_place and not raw_date and not raw_purpose:
                        continue

                    self.raw_row_count += 1

                    dept_str = discovered_dept.strip()
                    if not dept_str and raw_dept:
                        cand_dept = str(raw_dept).strip()
                        if not is_personal_name_or_title(cand_dept):
                            dept_str = cand_dept

                    dept_str = re.sub(r"\s*\([가-힣]{2,4}\)$", "", dept_str).strip()
                    if not dept_str or is_personal_name_or_title(dept_str):
                        dept_str = ""

                    if not raw_place or not raw_date:
                        continue

                    date_str = normalize_date(raw_date)
                    if not date_str:
                        continue

                    try:
                        p_date = datetime.date.fromisoformat(date_str)
                    except (ValueError, TypeError):
                        continue

                    if p_date > TODAY_DATE or p_date < WINDOW_START:
                        continue

                    if not self.classify_meal(place_str, purpose_str):
                        continue

                    addr_info = self.resolve_address(place_str, "대구")
                    if not addr_info:
                        self.remember_unresolved(place_str, dept_str, date_str, source_url, f"daegu_{doc_id}_s{sheet_title}_r{r_i}")
                        continue

                    addr, addr_url = addr_info
                    rec_id = f"daegu_{doc_id}_s{sheet_title}_r{r_i}"

                    records.append(
                        VerifiedRecord(
                            region="대구",
                            institution="대구광역시",
                            department=dept_str,
                            name=MealClassifier.clean_place_name(place_str),
                            address=addr,
                            paymentDate=date_str,
                            sourceURL=source_url,
                            recordID=rec_id,
                            meal=True,
                            addressSourceURL=addr_url,
                        )
                    )

        return records


# ---------------------------------------------------------------------------
# 3. Gyeongnam (경상남도청)
# ---------------------------------------------------------------------------
class GyeongnamCollector(BaseRegionalCollector):
    SEED_LIST_URL = (
        "https://www.gyeongnam.go.kr/board/list.gyeong?boardId=BBS_0000957"
        "&paging=ok&menuCd=DOM_000000138002012000&pageNo={page}&startPage=1"
    )
    VIEW_URL_TMPL = (
        "https://www.gyeongnam.go.kr/board/view.gyeong?boardId=BBS_0000957"
        "&menuCd=DOM_000000138002012000&paging=ok&startPage=1&dataSid={data_sid}"
    )
    DOWN_URL_TMPL = (
        "https://www.gyeongnam.go.kr/board/download.gyeong?boardId=BBS_0000957"
        "&menuCd=DOM_000000138002012000&dataSid={data_sid}&command=update&fileSid={file_sid}"
    )

    def _discover_items_from_html(self, html_text: str) -> List[Tuple[int, int, str]]:
        """
        Derives dataSid, fileSid, and actual department from table rows in Gyeongnam board HTML.
        Row contains: <td>{dept}</td> and download link with dataSid and fileSid.
        """
        results: List[Tuple[int, int, str]] = []
        rows = re.findall(r"<tr.*?>.*?</tr>", html_text, re.S)
        for r in rows:
            m = re.search(r"dataSid=(\d+)[^\"']*fileSid=(\d+)", r)
            if not m:
                continue
            data_sid, file_sid = int(m.group(1)), int(m.group(2))

            cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td.*?>.*?</td>", r, re.S)]
            dept_name = ""
            if len(cells) >= 4:
                dept_name = cells[3]

            if not dept_name:
                m_title = re.search(r"\(([^)]+)\)", r)
                if m_title:
                    dept_name = m_title.group(1).strip()

            if data_sid and file_sid and dept_name and not any(x[1] == file_sid for x in results):
                results.append((data_sid, file_sid, dept_name))
        return results

    def collect(self) -> None:
        logger.info(f"[Gyeongnam] Fetching official department board (pages 1..{self.max_pages})...")
        discovered: List[Tuple[int, int, str]] = []
        seen_file_sids: Set[int] = set()

        for p in range(1, self.max_pages + 1):
            list_url = self.SEED_LIST_URL.format(page=p)
            list_html = self.fetch_url(list_url)
            if list_html:
                out_list = self.cache_dir / f"gn_list_p{p}.html"
                out_list.write_bytes(list_html)
                text = list_html.decode("utf-8", errors="replace")
                items = self._discover_items_from_html(text)
                new_in_page = [it for it in items if it[1] not in seen_file_sids]
                if p > 1 and not new_in_page:
                    logger.warning(
                        f"[Gyeongnam] Page {p} yielded no new document IDs (repeated page). Stopping pagination."
                    )
                    break
                for it in items:
                    if it[1] not in seen_file_sids:
                        seen_file_sids.add(it[1])
                        discovered.append(it)

        logger.info(f"[Gyeongnam] Discovered {len(discovered)} department attachments.")
        for data_sid, file_sid, dept in discovered:
            out_file = self.cache_dir / f"gn_{file_sid}.xlsx"
            file_url = self.DOWN_URL_TMPL.format(data_sid=data_sid, file_sid=file_sid)
            if not out_file.exists():
                logger.info(f"[Gyeongnam] Downloading {dept} disclosure (fileSid={file_sid})...")
            self.download_file(file_url, out_file, department=dept, parent_doc_uid=str(data_sid), att_uid=str(file_sid))

    def parse(self) -> List[VerifiedRecord]:
        records: List[VerifiedRecord] = []
        self.raw_row_count = 0

        meta_by_file_sid: Dict[int, Tuple[str, int]] = {}
        candidate_htmls = self._dedup_paths(
            list(self.cache_dir.glob("gn_*.html"))
            + list(Path(".").glob("gn_*.html"))
        )
        for h in candidate_htmls:
            try:
                t = h.read_text(encoding="utf-8", errors="replace")
                for ds, fs, dname in self._discover_items_from_html(t):
                    meta_by_file_sid[fs] = (dname, ds)
            except Exception:
                pass

        candidate_paths = self._dedup_paths(
            list(self.cache_dir.glob("gn_*.xlsx"))
            + list(Path(".").glob("gn_*.xlsx"))
        )

        for p in candidate_paths:
            m = re.search(r"gn_(\d+)", p.name)
            file_sid = int(m.group(1)) if m else 0

            dept_name, data_sid = meta_by_file_sid.get(file_sid, ("", 0))
            source_url = (
                self.VIEW_URL_TMPL.format(data_sid=data_sid)
                if data_sid
                else f"https://www.gyeongnam.go.kr/board/list.gyeong?boardId=BBS_0000957&fileSid={file_sid}"
            )

            self.register_cached_source(
                source_url, p, department=dept_name, parent_doc_uid=str(data_sid), att_uid=str(file_sid)
            )

            sheets = load_workbook_sheets(p)
            if not sheets:
                continue

            for sheet_title, rows in sheets:
                if not rows:
                    continue

                if not dept_name:
                    for r in rows[:6]:
                        r_str = " ".join(str(c) for c in r if c is not None)
                        m_dept = re.search(r"\(([^)]+과|[^)]+실|[^)]+관|[^)]+소)\)", r_str)
                        if m_dept:
                            cand = m_dept.group(1).strip()
                            if not is_personal_name_or_title(cand):
                                dept_name = cand
                                break

                if not dept_name or is_personal_name_or_title(dept_name):
                    dept_name = ""

                header_idx = -1
                col_map: Dict[str, int] = {}
                for idx, r in enumerate(rows[:10]):
                    row_strs = [str(c).strip() if c is not None else "" for c in r]
                    if any("사용처" in c or "장소" in c for c in row_strs) and any(
                        "일자" in c or "일시" in c for c in row_strs
                    ):
                        header_idx = idx
                        for c_i, val in enumerate(row_strs):
                            if "일자" in val or "일시" in val:
                                col_map["date"] = c_i
                            elif "사용처" in val or "장소" in val:
                                col_map["place"] = c_i
                            elif "내역" in val or "목적" in val:
                                col_map["purpose"] = c_i
                        break

                if header_idx == -1 or "date" not in col_map or "place" not in col_map:
                    continue

                for r_i, r in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
                    if not r or all(c is None or str(c).strip() == "" for c in r):
                        continue

                    raw_place = get_cell(r, col_map["place"])
                    raw_date = get_cell(r, col_map["date"])
                    raw_purpose = get_cell(r, col_map.get("purpose"))

                    place_str = str(raw_place or "").strip()
                    purpose_str = str(raw_purpose or "").strip()

                    if any(k in place_str or k in purpose_str for k in ("합계", "총계", "소계", "총 합계")):
                        continue
                    if any(k == place_str for k in ("사용처", "장소", "집행장소")):
                        continue
                    if not raw_place and not raw_date and not raw_purpose:
                        continue

                    self.raw_row_count += 1

                    if not raw_place or not raw_date:
                        continue

                    date_str = normalize_date(raw_date)
                    if not date_str:
                        continue

                    try:
                        p_date = datetime.date.fromisoformat(date_str)
                    except (ValueError, TypeError):
                        continue

                    if p_date > TODAY_DATE or p_date < WINDOW_START:
                        continue

                    if not self.classify_meal(place_str, purpose_str):
                        continue

                    addr_info = self.resolve_address(place_str, "경남")
                    if not addr_info:
                        self.remember_unresolved(place_str, dept_name, date_str, source_url, f"gn_{file_sid}_s{sheet_title}_r{r_i}")
                        continue

                    addr, addr_url = addr_info
                    rec_id = f"gn_{file_sid}_s{sheet_title}_r{r_i}"

                    records.append(
                        VerifiedRecord(
                            region="경남",
                            institution="경상남도",
                            department=dept_name,
                            name=MealClassifier.clean_place_name(place_str),
                            address=addr,
                            paymentDate=date_str,
                            sourceURL=source_url,
                            recordID=rec_id,
                            meal=True,
                            addressSourceURL=addr_url,
                        )
                    )

        return records


# ---------------------------------------------------------------------------
# 4. Gangwon (강원특별자치도청)
# ---------------------------------------------------------------------------
class GangwonCollector(BaseRegionalCollector):
    SEED_LIST_URL = "https://state.gwd.go.kr/portal/administration/opendata/propulsionCost/director?pageIndex={page}"
    VIEW_URL_TMPL = "https://state.gwd.go.kr/portal/administration/opendata/propulsionCost/director?articleSeq={seq}&mode=view"
    BASE_URL = "https://state.gwd.go.kr"

    def _discover_items_from_html(self, html_text: str) -> List[Tuple[int, str]]:
        """
        Derives articleSeq and actual department from table rows in Gangwon portal HTML.
        Row contains: goPage({seq}) and <td class="skinTb-name">{department}</td>
        """
        results: List[Tuple[int, str]] = []
        rows = re.findall(r"<tr.*?>.*?</tr>", html_text, re.S)
        for r in rows:
            m_seq = re.search(r"goPage\((\d+)\)", r)
            if not m_seq:
                continue
            seq = int(m_seq.group(1))

            dept_m = re.search(r'<td[^>]*class="skinTb-name[^"]*"[^>]*>(.*?)</td>', r, re.S)
            dept_name = ""
            if dept_m:
                dept_name = re.sub(r"<[^>]+>", "", dept_m.group(1)).strip()

            if not dept_name:
                m_title = re.search(r"\(([^)]+)\)", r)
                if m_title:
                    dept_name = m_title.group(1).strip()

            if seq and dept_name and not any(x[0] == seq for x in results):
                results.append((seq, dept_name))
        return results

    def collect(self) -> None:
        logger.info(f"[Gangwon] Fetching director expenditure disclosures (pages 1..{self.max_pages})...")
        discovered: List[Tuple[int, str]] = []
        seen_seqs: Set[int] = set()

        for p in range(1, self.max_pages + 1):
            list_url = self.SEED_LIST_URL.format(page=p)
            list_html = self.fetch_url(list_url)
            if list_html:
                out_list = self.cache_dir / f"gw_list_p{p}.html"
                out_list.write_bytes(list_html)
                text = list_html.decode("utf-8", errors="replace")
                items = self._discover_items_from_html(text)
                new_in_page = [it for it in items if it[0] not in seen_seqs]
                if p > 1 and not new_in_page:
                    logger.warning(
                        f"[Gangwon] Page {p} yielded no new document IDs (repeated page). Stopping pagination."
                    )
                    break
                for it in items:
                    if it[0] not in seen_seqs:
                        seen_seqs.add(it[0])
                        discovered.append(it)

        logger.info(f"[Gangwon] Discovered {len(discovered)} department director disclosures.")
        for seq, dept in discovered:
            view_url = self.VIEW_URL_TMPL.format(seq=seq)
            detail_html = self.fetch_url(view_url)
            if detail_html:
                out_view = self.cache_dir / f"gw_{seq}.html"
                out_view.write_bytes(detail_html)
                text = detail_html.decode("utf-8", errors="replace")
                m = re.search(r'href="(/egf/bp/common/front/\d+/download)"', text)
                if m:
                    full_down_url = f"{self.BASE_URL}{m.group(1)}"
                    # Detect extension from filename in link
                    ext = ".xlsx"
                    if ".pdf" in text.lower():
                        ext = ".pdf"
                    out_file = self.cache_dir / f"gw_{seq}{ext}"
                    if not out_file.exists():
                        logger.info(f"[Gangwon] Downloading {dept} disclosure (seq={seq})...")
                    self.download_file(full_down_url, out_file, department=dept, parent_doc_uid=str(seq))

    def parse(self) -> List[VerifiedRecord]:
        records: List[VerifiedRecord] = []
        self.raw_row_count = 0

        dept_by_seq: Dict[int, str] = {}
        candidate_htmls = self._dedup_paths(
            list(self.cache_dir.glob("gw_*.html"))
            + list(Path(".").glob("gw_*.html"))
        )
        for h in candidate_htmls:
            try:
                t = h.read_text(encoding="utf-8", errors="replace")
                for s, dname in self._discover_items_from_html(t):
                    dept_by_seq[s] = dname
            except Exception:
                pass

        candidate_paths = self._dedup_paths(
            list(self.cache_dir.glob("gw_*.xlsx"))
            + list(self.cache_dir.glob("gw_*.pdf"))
            + list(Path(".").glob("gw_*.xlsx"))
            + list(Path(".").glob("gw_*.pdf"))
        )

        for p in candidate_paths:
            m = re.search(r"gw_(\d+)", p.name)
            seq = int(m.group(1)) if m else 0
            dept_name = dept_by_seq.get(seq, "")
            source_url = self.VIEW_URL_TMPL.format(seq=seq)

            self.register_cached_source(
                source_url, p, department=dept_name, parent_doc_uid=str(seq)
            )

            sheets = load_workbook_sheets(p)
            if not sheets:
                continue

            for sheet_title, rows in sheets:
                if not rows:
                    continue

                if not dept_name:
                    for r in rows[:6]:
                        r_str = " ".join(str(c) for c in r if c is not None)
                        m_dept = re.search(r"\(([^)]+국장|[^)]+실장|[^)]+본부장|[^)]+국|[^)]+실|[^)]+소방서|[^)]+대학교)\)", r_str)
                        if m_dept:
                            cand = m_dept.group(1).strip()
                            if not is_personal_name_or_title(cand):
                                dept_name = cand
                                break

                if not dept_name or is_personal_name_or_title(dept_name):
                    dept_name = ""

                header_idx = -1
                col_map: Dict[str, int] = {}
                for idx, r in enumerate(rows[:10]):
                    row_strs = [str(c).strip() if c is not None else "" for c in r]
                    if any("장소" in c or "사용처" in c for c in row_strs) and any(
                        "일시" in c or "일자" in c for c in row_strs
                    ):
                        header_idx = idx
                        for c_i, val in enumerate(row_strs):
                            if "일시" in val or "일자" in val:
                                col_map["date"] = c_i
                            elif "장소" in val or "사용처" in val:
                                col_map["place"] = c_i
                            elif "목적" in val or "내역" in val:
                                col_map["purpose"] = c_i
                        break

                if header_idx == -1 or "date" not in col_map or "place" not in col_map:
                    continue

                for r_i, r in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
                    if not r or all(c is None or str(c).strip() == "" for c in r):
                        continue

                    raw_place = get_cell(r, col_map["place"])
                    raw_date = get_cell(r, col_map["date"])
                    raw_purpose = get_cell(r, col_map.get("purpose"))

                    place_str = str(raw_place or "").strip()
                    purpose_str = str(raw_purpose or "").strip()

                    if any(k in place_str or k in purpose_str for k in ("합계", "총계", "소계", "총 합계")):
                        continue
                    if any(k == place_str for k in ("장소", "사용처", "집행장소")):
                        continue
                    if not raw_place and not raw_date and not raw_purpose:
                        continue

                    self.raw_row_count += 1

                    if not raw_place or not raw_date:
                        continue

                    date_str = normalize_date(raw_date)
                    if not date_str:
                        continue

                    try:
                        p_date = datetime.date.fromisoformat(date_str)
                    except (ValueError, TypeError):
                        continue

                    if p_date > TODAY_DATE or p_date < WINDOW_START:
                        continue

                    if not self.classify_meal(place_str, purpose_str):
                        continue

                    addr_info = self.resolve_address(place_str, "강원")
                    if not addr_info:
                        self.remember_unresolved(place_str, dept_name, date_str, source_url, f"gw_{seq}_s{sheet_title}_r{r_i}")
                        continue

                    addr, addr_url = addr_info
                    rec_id = f"gw_{seq}_s{sheet_title}_r{r_i}"

                    inst_name = "강원특별자치도"
                    if "대학교" in dept_name:
                        inst_name = "강원도립대학교"
                    elif "소방서" in dept_name:
                        m_fb = re.search(r"([가-힣]+소방서)", dept_name)
                        inst_name = m_fb.group(1) if m_fb else "강원특별자치도"

                    records.append(
                        VerifiedRecord(
                            region="강원",
                            institution=inst_name,
                            department=dept_name,
                            name=MealClassifier.clean_place_name(place_str),
                            address=addr,
                            paymentDate=date_str,
                            sourceURL=source_url,
                            recordID=rec_id,
                            meal=True,
                            addressSourceURL=addr_url,
                        )
                    )

        return records


# ---------------------------------------------------------------------------
# 5. Gyeongbuk (경상북도의회)
# ---------------------------------------------------------------------------
class GyeongbukCollector(BaseRegionalCollector):
    COUNCIL_SEED_URL = "https://council.gb.go.kr/kr/bbs?bbs_id=open&page={page}"
    VIEW_URL_TMPL = "https://council.gb.go.kr/kr/bbs?reform=view&uid={uid}&bbs_id=open"
    DOWNLOAD_URL_TMPL = "https://council.gb.go.kr/kr/bbs/download?bbs_id=open&uid={att_uid}"

    def _discover_items_from_html(self, html_text: str) -> List[Tuple[str, str]]:
        """
        Derives official uid and actual department from official council board HTML.
        Link: <a href='...uid={uid}...' title='2026년 8월 업무추진비 사용내역(건설소방전문위원실)...'>
        """
        results: List[Tuple[str, str]] = []
        pattern = re.compile(
            r"href='[^']*uid=([0-9A-Fa-f]+)[^']*'[^>]*title='([^']*)'", re.IGNORECASE
        )
        for m in pattern.finditer(html_text):
            uid = m.group(1).strip()
            title = m.group(2).strip()
            m_dept = re.search(r"\(([^)]+)\)", title)
            dept_name = m_dept.group(1).strip() if m_dept else ""
            if uid and dept_name and not any(x[0] == uid for x in results):
                results.append((uid, dept_name))
        return results

    def _discover_attachments_from_view(
        self, html_text: str
    ) -> List[Tuple[str, str, str]]:
        """
        Derives attachment UID, full download link, and attachment filename/title from view HTML.
        Matches: <li class='file'><a href='/kr/bbs/download?bbs_id=open&uid=...'>
        Returns: List[Tuple[att_uid, download_url, att_title]]
        """
        results: List[Tuple[str, str, str]] = []
        pattern = re.compile(
            r"<a[^>]+href=['\"]([^'\"]*download\?[^'\"]*uid=([0-9A-Fa-f]+)[^'\"]*)['\"][^>]*>",
            re.IGNORECASE,
        )
        for m in pattern.finditer(html_text):
            href = m.group(1).replace("&amp;", "&")
            att_uid = m.group(2).strip()
            full_tag = m.group(0)
            m_title = re.search(r"title=['\"]([^'\"]*)['\"]", full_tag, re.IGNORECASE)
            title = m_title.group(1) if m_title else ""
            down_url = urllib.parse.urljoin("https://council.gb.go.kr", href)
            if att_uid and not any(x[0] == att_uid for x in results):
                results.append((att_uid, down_url, title))
        return results

    def collect(self) -> None:
        logger.info(
            f"[Gyeongbuk] Inspecting Provincial Council disclosure board (pages 1..{self.max_pages})..."
        )
        discovered: List[Tuple[str, str]] = []
        seen_uids: Set[str] = set()

        for p in range(1, self.max_pages + 1):
            list_url = self.COUNCIL_SEED_URL.format(page=p)
            list_html = self.fetch_url(list_url)
            if list_html:
                out_list = self.cache_dir / f"gbc_open_p{p}.html"
                out_list.write_bytes(list_html)
                text = list_html.decode("utf-8", errors="replace")
                items = self._discover_items_from_html(text)
                new_in_page = [it for it in items if it[0] not in seen_uids]
                if p > 1 and not new_in_page:
                    logger.warning(
                        f"[Gyeongbuk] Page {p} yielded no new document UIDs (repeated page). Stopping pagination."
                    )
                    break
                for it in items:
                    if it[0] not in seen_uids:
                        seen_uids.add(it[0])
                        discovered.append(it)

        logger.info(
            f"[Gyeongbuk] Discovered {len(discovered)} department disclosures with real UIDs."
        )
        for uid, dept in discovered:
            out_view = self.cache_dir / f"gbc_view_{uid}.html"
            view_text = ""
            if not out_view.exists():
                view_url = self.VIEW_URL_TMPL.format(uid=uid)
                logger.info(f"[Gyeongbuk] Fetching {dept} view (uid={uid})...")
                view_bytes = self.fetch_url(view_url)
                if view_bytes:
                    out_view.write_bytes(view_bytes)
                    view_text = view_bytes.decode("utf-8", errors="replace")
            else:
                try:
                    view_text = out_view.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass

            if view_text:
                attachments = self._discover_attachments_from_view(view_text)
                for att_uid, down_url, att_title in attachments:
                    ext = ".xlsx"
                    if ".pdf" in att_title.lower() or ".pdf" in down_url.lower():
                        ext = ".pdf"
                    out_att = self.cache_dir / f"gbc_{uid}_{att_uid}{ext}"
                    if not out_att.exists():
                        logger.info(
                            f"[Gyeongbuk] Downloading attachment for {dept} (parent={uid}, att={att_uid})..."
                        )
                    self.download_file(
                        down_url,
                        out_att,
                        department=dept,
                        parent_doc_uid=uid,
                        att_uid=att_uid,
                    )

    def parse(self) -> List[VerifiedRecord]:
        records: List[VerifiedRecord] = []
        self.raw_row_count = 0

        dept_by_uid: Dict[str, str] = {}
        att_to_meta: Dict[str, Dict[str, str]] = {}

        candidate_lists = self._dedup_paths(
            list(self.cache_dir.glob("gbc_open_*.html"))
            + list(self.cache_dir.glob("gbc_*.html"))
            + list(Path(".").glob("gbc_*.html"))
        )
        for h in candidate_lists:
            try:
                t = h.read_text(encoding="utf-8", errors="replace")
                for u, dname in self._discover_items_from_html(t):
                    dept_by_uid[u] = dname
            except Exception:
                pass

        candidate_views = self._dedup_paths(
            list(self.cache_dir.glob("gbc_view_*.html"))
            + list(Path(".").glob("gbc_view_*.html"))
        )
        for v in candidate_views:
            m_v = re.search(r"gbc_view_([0-9A-Fa-f]+)", v.name)
            doc_uid = m_v.group(1) if m_v else ""
            try:
                content = v.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            dept_name = dept_by_uid.get(doc_uid, "")
            if not dept_name:
                m_title = re.search(r"<title>(.*?)</title>", content, re.I)
                if m_title:
                    m_d = re.search(r"\(([^)]+)\)", m_title.group(1))
                    if m_d:
                        dept_name = m_d.group(1).strip()
            if dept_name and doc_uid:
                dept_by_uid[doc_uid] = dept_name

            attachments = self._discover_attachments_from_view(content)
            for att_uid, down_url, att_title in attachments:
                att_to_meta[att_uid] = {
                    "parent_uid": doc_uid,
                    "dept": dept_name,
                    "download_url": down_url,
                    "title": att_title,
                }

        # Find cached Excel/Spreadsheet files
        candidate_paths = self._dedup_paths(
            list(self.cache_dir.glob("gbc_*.xlsx"))
            + list(self.cache_dir.glob("gbc_*.xls"))
            + list(self.cache_dir.glob("gbc_*.pdf"))
            + list(Path(".").glob("gbc_*.xlsx"))
            + list(Path(".").glob("gbc_*.xls"))
            + list(Path(".").glob("gbc_*.pdf"))
        )

        for p in candidate_paths:
            # Parse parent_uid and att_uid from filename: gbc_{parent_uid}_{att_uid}.xlsx
            m = re.search(r"gbc_([0-9A-Fa-f]+)_([0-9A-Fa-f]+)", p.name)
            if m:
                parent_uid = m.group(1)
                att_uid = m.group(2)
            else:
                m_single = re.search(r"gbc_([0-9A-Fa-f]+)", p.name)
                parent_uid = m_single.group(1) if m_single else "doc"
                att_uid = "att"

            meta = att_to_meta.get(att_uid, {})
            dept_name = meta.get("dept") or dept_by_uid.get(parent_uid, "")
            source_url = meta.get("download_url") or self.VIEW_URL_TMPL.format(uid=parent_uid)

            self.register_cached_source(
                source_url,
                p,
                department=dept_name,
                parent_doc_uid=parent_uid,
                att_uid=att_uid,
            )

            sheets = load_workbook_sheets(p)
            if not sheets:
                continue

            for sheet_title, rows in sheets:
                if not rows:
                    continue

                # Header detection
                header_idx = -1
                col_map: Dict[str, int] = {}
                for idx, r in enumerate(rows[:15]):
                    row_strs = [str(c).strip() if c is not None else "" for c in r]
                    if any("사용처" in c or "장소" in c or "가맹점" in c or "상호" in c for c in row_strs) and any(
                        "일자" in c or "일시" in c or "사용일" in c for c in row_strs
                    ):
                        header_idx = idx
                        for c_i, val in enumerate(row_strs):
                            if "일자" in val or "일시" in val or "사용일" in val:
                                col_map["date"] = c_i
                            elif "사용처" in val or "장소" in val or "가맹점" in val or "상호" in val:
                                col_map["place"] = c_i
                            elif "내역" in val or "목적" in val:
                                col_map["purpose"] = c_i
                            elif "부서" in val:
                                if "사용자" not in val and "직책" not in val and "성명" not in val and "개인" not in val:
                                    col_map["dept"] = c_i
                        break

                if header_idx == -1 or "date" not in col_map or "place" not in col_map:
                    continue

                for r_i, r in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
                    if not r or all(c is None or str(c).strip() == "" for c in r):
                        continue

                    raw_place = get_cell(r, col_map["place"])
                    raw_date = get_cell(r, col_map["date"])
                    raw_purpose = get_cell(r, col_map.get("purpose"))
                    raw_dept = get_cell(r, col_map.get("dept"))

                    place_val = str(raw_place or "").strip()
                    purpose_val = str(raw_purpose or "").strip()

                    # Raw row count: real parsed expenditure rows
                    if any(k in place_val or k in purpose_val for k in ("합계", "총계", "소계", "총 합계")):
                        continue
                    if any(k == place_val for k in ("사용처", "장소", "집행장소", "상호")):
                        continue
                    if not raw_place and not raw_date and not raw_purpose:
                        continue

                    self.raw_row_count += 1

                    # Department selection: prefer discovered department
                    dept_str = dept_name.strip()
                    if not dept_str and raw_dept:
                        cand_dept = str(raw_dept).strip().replace("\n", " ")
                        if not is_personal_name_or_title(cand_dept):
                            dept_str = cand_dept

                    dept_str = re.sub(r"\s*\([가-힣]{2,4}\)$", "", dept_str).strip()
                    if not dept_str or is_personal_name_or_title(dept_str):
                        dept_str = ""

                    if not raw_place or not raw_date:
                        continue

                    date_str = normalize_date(raw_date)
                    if not date_str:
                        continue

                    try:
                        p_date = datetime.date.fromisoformat(date_str)
                    except (ValueError, TypeError):
                        continue

                    if p_date > TODAY_DATE or p_date < WINDOW_START:
                        continue

                    if not self.classify_meal(place_val, purpose_val):
                        continue

                    addr_info = self.resolve_address(place_val, "경북")
                    if not addr_info:
                        self.remember_unresolved(place_val, dept_str, date_str, source_url, f"gbc_{parent_uid}_{att_uid}_s{sheet_title}_r{r_i}")
                        continue

                    addr, addr_url = addr_info
                    rec_id = f"gbc_{parent_uid}_{att_uid}_s{sheet_title}_r{r_i}"

                    records.append(
                        VerifiedRecord(
                            region="경북",
                            institution=self.institution,
                            department=dept_str,
                            name=MealClassifier.clean_place_name(place_val),
                            address=addr,
                            paymentDate=date_str,
                            sourceURL=source_url,
                            recordID=rec_id,
                            meal=True,
                            addressSourceURL=addr_url,
                        )
                    )

        return records


# ---------------------------------------------------------------------------
# 6. Ulsan (울산광역시)
# ---------------------------------------------------------------------------
class UlsanCollector(BaseRegionalCollector):
    SEED_LIST_URL = (
        "https://www.ulsan.go.kr/u/rep/transfer/chief/list.ulsan?mId=001003002005000000&curPage={page}"
    )
    DETAIL_URL_TMPL = (
        "https://www.ulsan.go.kr/u/rep/transfer/chief/list.ulsan?mId=001003002005000000&useDe={use_de}"
    )
    POST_URL = "https://www.ulsan.go.kr/u/rep/transfer/chief/list.ulsan"

    def collect(self) -> None:
        logger.info(
            f"[Ulsan] Fetching official department chief disclosures (pages 1..{self.max_pages})..."
        )
        dates: List[str] = []
        seen_dates: Set[str] = set()

        for p in range(1, self.max_pages + 1):
            list_url = self.SEED_LIST_URL.format(page=p)
            list_html = self.fetch_url(list_url)
            text = list_html.decode("utf-8", errors="replace") if list_html else ""
            d_matches = (
                re.findall(r"f_detail\('(\d{4}-\d{2}-\d{2})'\)", text) if text else []
            )

            # If GET didn't return dates, try form POST
            if not d_matches:
                post_body = urllib.parse.urlencode(
                    {"curPage": str(p), "mId": "001003002005000000"}
                ).encode("utf-8")
                headers = {"Content-Type": "application/x-www-form-urlencoded"}
                list_html = self.fetch_url(
                    self.POST_URL, data=post_body, headers=headers
                )
                text = list_html.decode("utf-8", errors="replace") if list_html else ""
                d_matches = (
                    re.findall(r"f_detail\('(\d{4}-\d{2}-\d{2})'\)", text)
                    if text
                    else []
                )

            if list_html:
                out_list = self.cache_dir / f"ulsan_chief_p{p}.html"
                out_list.write_bytes(list_html)

            new_in_page = [d for d in d_matches if d not in seen_dates]
            if p > 1 and not new_in_page:
                logger.warning(
                    f"[Ulsan] Page {p} yielded no new dates (repeated page). Stopping pagination."
                )
                break

            for d in d_matches:
                if d not in seen_dates:
                    seen_dates.add(d)
                    dates.append(d)

        logger.info(f"[Ulsan] Discovered {len(dates)} disclosure dates.")
        for d in dates:
            out_file = self.cache_dir / f"ulsan_{d}.html"
            detail_url = self.DETAIL_URL_TMPL.format(use_de=d)
            if not out_file.exists():
                logger.info(f"[Ulsan] Downloading disclosure for date {d}...")
            self.download_file(
                detail_url, out_file, department="울산광역시", parent_doc_uid=d
            )

    def parse(self) -> List[VerifiedRecord]:
        records: List[VerifiedRecord] = []
        self.raw_row_count = 0

        candidate_paths = self._dedup_paths(
            list(self.cache_dir.glob("ulsan_*.html"))
            + list(Path(".").glob("ulsan_*.html"))
        )

        for p in candidate_paths:
            # Skip list summary pages
            if "chief" in p.name or "dir" in p.name:
                continue

            m = re.search(r"ulsan_(\d{4}-\d{2}-\d{2})", p.name)
            date_from_file = m.group(1) if m else ""

            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            payment_date = date_from_file
            if not payment_date:
                date_cand = re.search(r"\d{4}-\d{2}-\d{2}", text)
                payment_date = date_cand.group(0) if date_cand else ""

            source_url = (
                self.DETAIL_URL_TMPL.format(use_de=payment_date)
                if payment_date
                else self.SEED_LIST_URL.format(page=1)
            )

            self.register_cached_source(
                source_url,
                p,
                department="울산광역시",
                parent_doc_uid=payment_date or p.stem,
            )

            # Columns: [연번, 집행목적, 결제방법, 인원, 금액, 참석대상, 장소]
            tables = re.findall(r"<table.*?</table>", text, re.S)
            for tbl in tables:
                if "참석대상" not in tbl and "장소" not in tbl:
                    continue

                tbody_match = re.search(r"<tbody>(.*?)</tbody>", tbl, re.S)
                if not tbody_match:
                    continue

                rows = re.findall(r"<tr.*?</tr>", tbody_match.group(1), re.S)
                for r_idx, r in enumerate(rows, start=1):
                    cells = [
                        re.sub(r"<[^>]+>", "", c).strip()
                        for c in re.findall(r"<t[dh].*?</t[dh]>", r, re.S)
                    ]
                    if len(cells) < 7:
                        continue

                    raw_purpose = cells[1]
                    raw_place = cells[6]

                    if any(k in raw_place or k in raw_purpose for k in ("합계", "총계", "소계", "총 합계")):
                        continue
                    if any(k == raw_place for k in ("장소", "사용처", "사용장소", "집행장소", "상호")):
                        continue
                    if not raw_place and not raw_purpose:
                        continue

                    self.raw_row_count += 1

                    # Extract actual department from purpose parentheses
                    dept_match = re.search(
                        r"\(([^)]+과|[^)]+실|[^)]+소방서|[^)]+사업소|[^)]+본부|[^)]+원)\)",
                        raw_purpose,
                    )
                    dept_name = dept_match.group(1).strip() if dept_match else ""

                    # Fallback check at start of purpose
                    if not dept_name:
                        dept_start = re.match(
                            r"^([가-힣]+(?:과|실|소방서|사업소|본부|원))\b", raw_purpose
                        )
                        if dept_start:
                            dept_name = dept_start.group(1).strip()

                    dept_name = re.sub(r"\s*\([가-힣]{2,4}\)$", "", dept_name).strip()
                    if not dept_name or is_personal_name_or_title(dept_name):
                        dept_name = ""

                    if not payment_date:
                        continue

                    date_str = normalize_date(payment_date)
                    if not date_str:
                        continue

                    try:
                        p_date = datetime.date.fromisoformat(date_str)
                    except (ValueError, TypeError):
                        continue

                    if p_date > TODAY_DATE or p_date < WINDOW_START:
                        continue

                    if not self.classify_meal(raw_place, raw_purpose):
                        continue

                    addr_info = self.resolve_address(raw_place, "울산")
                    if not addr_info:
                        self.remember_unresolved(raw_place, dept_name, date_str, source_url, f"ulsan_{payment_date}_r{r_idx}")
                        continue

                    addr, addr_url = addr_info
                    rec_id = f"ulsan_{payment_date}_r{r_idx}"

                    inst_name = "울산광역시"
                    if "소방서" in dept_name:
                        m_fb = re.search(r"([가-힣]+소방서)", dept_name)
                        inst_name = m_fb.group(1) if m_fb else "울산광역시"

                    records.append(
                        VerifiedRecord(
                            region="울산",
                            institution=inst_name,
                            department=dept_name,
                            name=MealClassifier.clean_place_name(raw_place),
                            address=addr,
                            paymentDate=date_str,
                            sourceURL=source_url,
                            recordID=rec_id,
                            meal=True,
                            addressSourceURL=addr_url,
                        )
                    )

        return records


# ---------------------------------------------------------------------------
# Helper Utilities
# ---------------------------------------------------------------------------
def normalize_date(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, (datetime.datetime, datetime.date)):
        return val.strftime("%Y-%m-%d")

    s = str(val).strip()
    # Match YYYY-MM-DD or YYYY.MM.DD or YYYY/MM/DD
    m = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", s)
    if m:
        y, month, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            valid_dt = datetime.date(y, month, d)
            return valid_dt.strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            return None

    # Match YYYYMMDD
    m2 = re.search(r"\b(20\d{2})(\d{2})(\d{2})\b", s)
    if m2:
        y, month, d = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        try:
            valid_dt = datetime.date(y, month, d)
            return valid_dt.strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            return None

    return None


# ---------------------------------------------------------------------------
# Master Collector Orchestrator
# ---------------------------------------------------------------------------
class PublicDiningEastCollector:
    """
    Main orchestrator for collecting, validating, and generating 6-region public dining data.
    """

    def __init__(
        self,
        output_dir: Path,
        cache_dir: Path,
        address_dir: Path,
        target_regions: Iterable[str] = ALL_REGIONS,
        max_pages: int = 1,
        collect_mode: bool = False,
        rebuild_mode: bool = True,
    ):
        self.output_dir = output_dir
        self.cache_dir = cache_dir
        self.address_dir = address_dir
        self.target_regions = [r for r in target_regions if r in ALL_REGIONS] or list(ALL_REGIONS)
        self.max_pages = max(1, max_pages)
        self.collect_mode = collect_mode
        self.rebuild_mode = rebuild_mode

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        evidence_path = self.output_dir / "address-evidence.jsonl"
        self.verifier = AddressVerifier(
            address_dir=self.address_dir,
            evidence_file=evidence_path if evidence_path.exists() else None,
        )

        self.collectors: Dict[str, BaseRegionalCollector] = {
            "부산": BusanCollector("부산", "부산광역시", self.cache_dir, self.verifier, self.max_pages),
            "대구": DaeguCollector("대구", "대구광역시", self.cache_dir, self.verifier, self.max_pages),
            "울산": UlsanCollector("울산", "울산광역시", self.cache_dir, self.verifier, self.max_pages),
            "경북": GyeongbukCollector("경북", "경상북도의회", self.cache_dir, self.verifier, self.max_pages),
            "경남": GyeongnamCollector("경남", "경상남도", self.cache_dir, self.verifier, self.max_pages),
            "강원": GangwonCollector("강원", "강원특별자치도", self.cache_dir, self.verifier, self.max_pages),
        }

    def run(self) -> int:
        logger.info(
            f"Starting Public Dining East Collector (regions={self.target_regions}, "
            f"pages={self.max_pages}, collect={self.collect_mode}, rebuild={self.rebuild_mode})"
        )

        # 1. Network Acquisition Phase
        if self.collect_mode:
            logger.info("Executing network acquisition for target regions...")
            for region_name in self.target_regions:
                coll = self.collectors[region_name]
                try:
                    logger.info(f"--- [Acquisition] {region_name} ---")
                    coll.collect()
                except Exception as e:
                    logger.error(f"Acquisition error for {region_name}: {e}")

        # 2. Parsing & Verification Phase
        logger.info("Executing parsing and offline verification...")
        all_records: List[VerifiedRecord] = []
        region_stats: Dict[str, Dict[str, Any]] = {}
        missing_deliverables: List[str] = []

        # Check if committed derivative source table exists for pure offline rebuild
        derivative_table = self.output_dir / "raw_expenditures.jsonl"
        can_rebuild_from_derivative = (
            self.rebuild_mode
            and not self.collect_mode
            and derivative_table.exists()
            and bool(self.verifier._index)
        )

        if can_rebuild_from_derivative:
            self.verifier._loaded_regions.update(self.verifier._index)
            logger.info("Rebuilding dataset from committed privacy-minimized source table...")
            all_records = self._rebuild_from_committed_derivative(derivative_table)

        for region_name in self.target_regions:
            coll = self.collectors[region_name]
            if can_rebuild_from_derivative:
                recs = [r for r in all_records if r.region == region_name]
                previous_coverage = self.output_dir / "coverage.json"
                raw_count = 0
                if previous_coverage.exists():
                    raw_count = json.loads(previous_coverage.read_text(encoding="utf-8")).get(region_name, {}).get("raw_row_count", 0)
            else:
                try:
                    recs = coll.parse()
                except Exception as e:
                    logger.error(f"Parsing error for {region_name}: {e}")
                    recs = []
                raw_count = coll.raw_row_count
                all_records.extend(recs)

            # Collapse presentational hierarchy and 담당관/담당관실 spelling variants conservatively.
            # Original department labels remain in source metadata.
            for record in recs:
                dept = re.split(r"\s*>\s*", record.department)[-1].strip()
                dept = re.sub(r"\s+", " ", dept)
                if dept.endswith("담당관실"):
                    dept = dept[:-1]
                record.department = dept
            # Deduplicate records by recordID
            seen_rec_ids: Set[str] = set()
            deduped_recs: List[VerifiedRecord] = []
            for r in recs:
                if r.recordID not in seen_rec_ids:
                    seen_rec_ids.add(r.recordID)
                    deduped_recs.append(r)
            recs = deduped_recs

            # Analyze Multi-Department Dining Standard per Restaurant
            # A restaurant qualifies if used by >= 1 official institutional meal record
            # and has at least one payment date >= 2025-03-13.
            restaurant_groups: Dict[str, List[VerifiedRecord]] = {}
            for r in recs:
                restaurant_groups.setdefault((re.sub(r"\s+", "", r.name), r.address), []).append(r)

            qualifying_restaurants: List[Dict[str, Any]] = []
            for (_, r_addr), r_list in restaurant_groups.items():
                r_name = r_list[0].name
                dept_keys = set((r.institution, r.department) for r in r_list)
                dates = [r.paymentDate for r in r_list]
                has_recent = any(WINDOW_START.isoformat() <= d <= TODAY_STR for d in dates)
                if r_list and has_recent:
                    qualifying_restaurants.append({
                        "name": r_name,
                        "address": r_addr,
                        "departments": sorted(list(set(r.department for r in r_list))),
                        "latestPayment": max(dates),
                    })

            dates = [r.paymentDate for r in recs]
            depts = sorted(list(set(r.department for r in recs)))
            institutions = sorted(list(set(r.institution for r in recs))) or [coll.institution]
            source_urls = sorted(list(set(r.sourceURL for r in recs)))

            region_stats[region_name] = {
                "sourceURLs": source_urls,
                "institutions": institutions,
                "criteria": "official institution meal >=1; confirmed address; rolling 18 months; department optional",
                "window": {"start": WINDOW_START.isoformat(), "end": TODAY_STR},
                "period": {
                    "start": min(dates) if dates else "",
                    "end": max(dates) if dates else "",
                },
                "raw_row_count": raw_count,  # Real original raw count tracked independently
                "accepted_count": len(recs),  # Accepted count never substituted for raw
                "departments": depts,
                "funnel": {
                    "downloaded_files": len({x.get("path") for x in coll.downloaded_sources}),
                    "parsed_expenditure_rows": raw_count,
                    "rows_reaching_meal_check_after_date_filters": coll.eligible_meal_checks,
                    "meal_rows": coll.meal_row_count,
                    "address_matched_rows": coll.address_match_count,
                    "address_unmatched_rows": coll.address_unmatched_count,
                    "accepted_official_meal_rows": len(recs),
                    "known_department_rows": sum(bool(r.department) for r in recs),
                    "unique_restaurants": len(restaurant_groups),
                    "qualified_restaurants": len(qualifying_restaurants),
                    "order_note": "날짜 확인 후 식사판정, 부서는 선택항목, 그 다음 주소대조. 원문 형식/헤더 해독 실패는 실제 파싱행에 포함되지 않음.",
                },
                "qualifying_restaurant_count": len(qualifying_restaurants),
                "qualifying_restaurants": qualifying_restaurants,
                "limitations": "공식 게시판에서 확보한 문서 범위. 전국 모든 기관 또는 기간 전체의 전수 수집 아님. 주소미확인과 비식사 제외, 문서형식 해독 실패는 errors.json에 보존. 영업/공개이용/맛 보증 아님.",
            }

            # Acceptance Check: PER RESTAURANT >=1 official institutional meal record
            # and >=1 payment since 2025-03-13. Each region must contain >=1 such qualifying restaurant.
            if len(qualifying_restaurants) < 1:
                msg = (
                    f"{region_name}: Missing qualifying restaurant with >= 1 official institutional meal record "
                    f"with payment >= 2025-03-13. Found {len(qualifying_restaurants)} qualifying restaurants "
                    f"(total regional depts: {len(depts)})."
                )
                missing_deliverables.append(msg)
                region_stats[region_name]["limitations"] = msg

        # 3. Output Generation Phase
        self.generate_outputs(all_records, region_stats)

        # 4. Diagnostics & Reporting
        logger.info("============================================================")
        logger.info("                  ACQUISITION MILESTONE SUMMARY              ")
        logger.info("============================================================")
        for r_name in self.target_regions:
            st = region_stats[r_name]
            qual_count = st["qualifying_restaurant_count"]
            acc_count = st["accepted_count"]
            raw_c = st["raw_row_count"]
            period_str = f"{st['period']['start']} ~ {st['period']['end']}" if acc_count > 0 else "N/A"
            status = "PASS" if qual_count >= 1 else "INCOMPLETE"
            logger.info(
                f"[{r_name}] Status: {status} | Qual Restaurants: {qual_count} | "
                f"Accepted: {acc_count} | Raw Rows: {raw_c} | Period: {period_str}"
            )
            if st["qualifying_restaurants"]:
                for qr in st["qualifying_restaurants"]:
                    logger.info(
                        f"       -> Qualifying: {qr['name']} ({qr['address']}) | "
                        f"Depts ({len(qr['departments'])}): {', '.join(qr['departments'])} | Latest: {qr['latestPayment']}"
                    )
            if st["limitations"]:
                logger.warning(f"       Limitations: {st['limitations']}")

        logger.info("============================================================")
        if missing_deliverables:
            logger.error("Required deliverables missing for one or more target regions:")
            for m in missing_deliverables:
                logger.error(f"  - {m}")
            logger.error(
                "Milestone completed with incomplete required coverage (exit 1). "
                "Execute with --collect and --address-dir to download full official department disclosures."
            )
            return 1

        logger.info("Saved supported official meal records for target regions; consult coverage/errors for acquisition limits.")
        return 0

    def _rebuild_from_committed_derivative(self, path: Path) -> List[VerifiedRecord]:
        recs: List[VerifiedRecord] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    if not (WINDOW_START.isoformat() <= row.get("paymentDate", "") <= TODAY_STR):
                        continue
                    place_name = row.get("restaurant") or row.get("name") or ""
                    reg = row.get("region") or ""
                    addr_info = self.verifier.resolve(place_name, reg)
                    addr = addr_info[0] if addr_info else row.get("address", "")
                    addr_url = addr_info[1] if addr_info else None

                    recs.append(
                        VerifiedRecord(
                            region=reg,
                            institution=row.get("institution", ""),
                            department=row.get("department", ""),
                            name=place_name,
                            address=addr,
                            paymentDate=row.get("paymentDate", ""),
                            sourceURL=row.get("sourceURL", ""),
                            recordID=row.get("recordID", ""),
                            meal=True,
                            addressSourceURL=addr_url,
                        )
                    )
        except Exception as e:
            logger.warning(f"Failed rebuilding from derivative table {path}: {e}")
        return recs

    def generate_outputs(
        self, records: List[VerifiedRecord], region_stats: Dict[str, Dict[str, Any]]
    ) -> None:
        records = list({(r.region, r.recordID): r for r in records}.values())
        unresolved = list({(r["region"], r["recordID"]): r for coll in self.collectors.values() for r in coll.unresolved_records}.values())
        unresolved_path = self.output_dir / "unresolved-address-records.jsonl.gz"
        if unresolved or not unresolved_path.exists():
            with gzip.open(unresolved_path, "wt", encoding="utf-8") as f:
                for row in unresolved:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        # A. records.jsonl.gz (Only permitted fields and meal flag)
        records_gz_path = self.output_dir / "records.jsonl.gz"
        logger.info(f"Writing {len(records)} verified records to {records_gz_path}...")
        with gzip.open(records_gz_path, "wt", encoding="utf-8") as gz_f:
            for r in records:
                gz_f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")

        # B. address-evidence.jsonl (Only used matched rows with business ID and sourceURL)
        evidence_path = self.output_dir / "address-evidence.jsonl"
        self.verifier.save_used_evidence(evidence_path, used_records=records)

        # C. sources.json (Preserve hashes and official URLs)
        sources_path = self.output_dir / "sources.json"
        existing_sources: List[Dict[str, Any]] = []
        if sources_path.exists():
            try:
                existing_sources = json.loads(sources_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        merged_sources_map: Dict[str, Dict[str, Any]] = {
            (s.get("url", "") + "|" + s.get("path", "")): s
            for s in existing_sources
            if s.get("url") or s.get("sha256")
        }
        for coll in self.collectors.values():
            for ds in coll.downloaded_sources:
                key = ds.get("url", "") + "|" + ds.get("path", "")
                if key:
                    merged_sources_map[key] = ds

        sources_list = list(merged_sources_map.values())
        if not sources_list:
            for r_name in self.target_regions:
                coll = self.collectors[r_name]
                sources_list.append({
                    "region": r_name,
                    "institution": coll.institution,
                    "sourceURLs": region_stats[r_name]["sourceURLs"],
                    "format": "xlsx/html",
                })

        logger.info(f"Writing sources metadata to {sources_path}...")
        sources_path.write_text(
            json.dumps(sources_list, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # D. coverage.json (Separates raw row count from accepted count and qualified count)
        coverage_path = self.output_dir / "coverage.json"
        if self.rebuild_mode and coverage_path.exists():
            old_coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
            for region, stats in region_stats.items():
                if stats["funnel"]["rows_reaching_meal_check_after_date_filters"] == 0 and region in old_coverage:
                    previous = old_coverage[region].get("funnel", {})
                    previous.update({"accepted_official_meal_rows": stats["accepted_count"], "qualified_restaurants": stats["qualifying_restaurant_count"]})
                    stats["funnel"] = previous
        logger.info(f"Writing coverage statistics to {coverage_path}...")
        coverage_path.write_text(
            json.dumps(region_stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # E. Privacy-Minimized Derivative Source Table
        derivative_path = self.output_dir / "raw_expenditures.jsonl"
        logger.info(f"Writing privacy-minimized intermediate table to {derivative_path}...")
        with open(derivative_path, "w", encoding="utf-8") as f:
            for r in records:
                min_row = {
                    "recordID": r.recordID,
                    "region": r.region,
                    "institution": r.institution,
                    "department": r.department,
                    "restaurant": r.name,
                    "paymentDate": r.paymentDate,
                    "meal": True,
                    "sourceURL": r.sourceURL,
                }
                f.write(json.dumps(min_row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Public Dining Expenditure Collector for 6 Eastern Regions (부산/대구/울산/경북/경남/강원)"
    )
    parser.add_argument(
        "--output-dir", "--out",
        type=Path,
        default=Path("data/public-dining/east"),
        help="Target output directory for deliverables",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("/tmp/whattoeat-east-task"),
        help="Task cache directory for downloaded official documents",
    )
    parser.add_argument(
        "--address-dir",
        type=Path,
        default=Path("/tmp/whattoeat-sbiz-addresses"),
        help="Directory containing official sbiz address CSV files (부산.csv, 대구.csv, etc.)",
    )
    parser.add_argument(
        "--regions",
        type=str,
        default=",".join(ALL_REGIONS),
        help="Comma-separated subset of regions to iterate (e.g. 부산,대구)",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="Positive integer page limit per region to bound acquisition progress",
    )
    parser.add_argument(
        "--collect",
        action="store_true",
        help="Perform network acquisition of public documents and attachments",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Perform offline parsing and regeneration of records from cached/committed sources",
    )
    parser.add_argument("--months", type=int, default=18, help="Rolling calendar months, default 18")
    parser.add_argument("--as-of", default=datetime.date.today().isoformat(), help="Reference date YYYY-MM-DD; rolling previous 18 months")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    global TODAY_STR, TODAY_DATE, WINDOW_START
    TODAY_DATE = datetime.date.fromisoformat(args.as_of)
    TODAY_STR = TODAY_DATE.isoformat()
    if args.months < 1:
        raise SystemExit("--months must be positive")
    month_number = TODAY_DATE.year * 12 + TODAY_DATE.month - 1 - args.months
    start_year, start_month_zero = divmod(month_number, 12)
    start_month = start_month_zero + 1
    WINDOW_START = datetime.date(start_year, start_month, min(TODAY_DATE.day, calendar.monthrange(start_year, start_month)[1]))

    collect_mode = args.collect
    rebuild_mode = args.rebuild
    if not collect_mode and not rebuild_mode:
        rebuild_mode = True

    regions_list = [r.strip() for r in args.regions.split(",") if r.strip()]

    collector = PublicDiningEastCollector(
        output_dir=args.output_dir,
        cache_dir=args.cache_dir,
        address_dir=args.address_dir,
        target_regions=regions_list,
        max_pages=args.pages,
        collect_mode=collect_mode,
        rebuild_mode=rebuild_mode,
    )

    diagnostics = []
    class DiagnosticHandler(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                diagnostics.append({"level": record.levelname, "message": record.getMessage()})
    logger.addHandler(DiagnosticHandler())
    exit_code = collector.run()
    diagnostic_path = args.output_dir / "errors.json"
    if not diagnostics and args.rebuild and diagnostic_path.exists():
        diagnostics = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    diagnostic_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
