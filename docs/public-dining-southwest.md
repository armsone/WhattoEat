# 호남·제주 공공기관 식사 기록

광주·전북·전남·제주의 공식 업무추진비 문서에서 식사 이용 사실을 추출한다. 현재 수집 대상은 제주특별자치도 공개 게시판, 전북특별자치도 업무추진비 게시판, 전라남도 실과소 게시판, 광산구청·서구청 및 전남광주통합특별시 공개 문서다. 모든 자치단체·기관·식당의 전수 명단은 아니다.

2026-09-13 기준 **2025-03-13 이후 최근 18개월 내 공공기관 식사 기록 1건**과 확인된 주소가 있으면 포함한다. 부서 개수나 부서명 유무는 포함 조건이 아니다. 부서명은 원문에서 확인할 수 있을 때만 기록한다. 방문·영업 지속·맛을 보증하지 않는다.

## 실행

번들 Python은 openpyxl·pdfplumber를 사용한다. 이전 XLS 파일은 번들 soffice로 변환한다. HWP5는 작업 전용 pyhwp로 실제 표의 행·열·병합 정보를 추출하며, HWPX는 XML 표를 직접 읽는다. 원문 바이트와 SHA256은 변환 전 기준으로 보존한다. 깨진 HWP 바이너리를 PDF로 출력하는 변환은 사용하지 않는다.

```bash
PY=/Users/armsone/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

# 공개 원문 새 수집. as-of를 생략하면 실행 당일이다.
"$PY" tools/collect_public_dining_southwest.py \
  --regions 광주,전북,전남,제주 --max-pages 10 \
  --as-of 2026-09-13 --lookback-months 18 --fresh-months 18 \
  --cache-dir /tmp/whattoeat-southwest-20260913 \
  --address-dir /tmp/whattoeat-sbiz-addresses

# SHA256이 검증된 기존 다운로드 문서를 재파싱한다. 옵션은 반복 가능하다.
"$PY" tools/collect_public_dining_southwest.py \
  --source-manifest data/public-dining/southwest/sources.json \
  --as-of 2026-09-13 --lookback-months 18 --fresh-months 18 \
  --cache-dir /tmp/whattoeat-southwest-20260913 \
  --address-dir /tmp/whattoeat-sbiz-addresses

# 저장소의 개인정보 제거 기록만으로 오프라인 재생성한다.
"$PY" tools/collect_public_dining_southwest.py --from-reduced \
  --as-of 2026-09-13 --lookback-months 18 --fresh-months 18
```

`--output-dir`로 산출 디렉터리를 지정할 수 있다. HWP 패키지는 공용 Python이나 앱에 설치하지 않고 작업 전용 폴더에 둔다. 현재 위치는 `/tmp/whattoeat-southwest-20260913/hwp-deps`이며, 다른 위치는 `WHATTOEAT_HWP_DEPS`로 지정한다. HWP 캐시 XML이 없으면 해당 폴더의 `bin/hwp5proc`를 실행한다. 새 환경에서는 별도 승인된 작업 전용 폴더에 `olefile==0.47`, `pyhwp==0.1b15`를 설치해야 한다.

## 실제 자료와 재현 근거

- `sources.json`: 공식 문서 URL·다운로드 URL·원문 SHA256·바이트 수·파일명. 실제 수집 범위와 문서별 근거다.
- `records.jsonl.gz`: 최근 18개월의 확인된 식사 기록. 지역·기관·확인 가능한 부서·식당명·주소·일자·원문 URL·원문 위치 ID·식사 여부를 보존한다.
- `reduced-records.jsonl.gz`: 오프라인 재생성용 최소 기록.
- `unresolved-address-records.jsonl.gz`: 식사 기록은 있으나 주소를 확정하지 못한 행. 외부 주소 대조용이며 내장 식당 명단에 바로 포함하지 않는다.
- `coverage.json`: 실제 대상 기관·기간·제외 사유·읽기 실패. 실패가 남으면 `partial`로 기록한다.

작성자·연락처·집행대상·목적 원문·금액은 최종 기록에 보관하지 않는다. HWP XML과 원본 첨부는 작업 전용 `/tmp`에만 보관한다. `recordID`는 원문 문서와 표/시트·행을 식별한다. HWP는 실제 표를 임시 워크북에 옮기며 첫 행이 부서 제목이고, `HWP_table_N` 시트의 행 번호에서 2를 빼면 원본의 0기준 표 행이다.

## 주소 대조

[공공데이터포털 상가(상권)정보](https://www.data.go.kr/data/15083033/fileData.do)의 실제 2026년 6월 자료를 대조한다. UTF-8-SIG 파일 `전남광주.csv`, `전북.csv`, `제주.csv`는 상가업소번호·상호명·지점명·업종·시군구·지번/도로명 주소만 포함한다. 출처 권역 안에서 정규화한 상호와 명시된 지점이 단 하나의 공식 항목과 일치할 때만 주소를 보완한다. 복수 지점이나 불일치는 추정하지 않고 미해결 행으로 남긴다. 보완된 행에는 `addressSourceURL`, `addressSourceRecordID`를 기록한다.

`전남광주통합특별시` 원본 주소를 그대로 보존한다. 동구·서구·남구·북구·광산구는 내부 광주 권역, 그 밖의 해당 시군은 전남 권역으로 분류한다. 건물 번호의 하이픈을 보존한다.

광산구 공식 목록 API는 POST가 403을 반환하는 경우에도 동일 공개 조회의 GET이 정상 작동하는 것을 확인했다. 수집기는 실제 응답의 `fileUrl`을 사용하며 임의 첨부 경로를 만들지 않는다.
