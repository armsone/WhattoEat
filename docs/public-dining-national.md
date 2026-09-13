# 공공기관 이용 기록 데이터

최근 18개월에 공공기관의 식사 이용 기록이 1건 이상 있으면 포함한다. 부서 수 제한은 없으며, 실행일 기준으로 기간을 다시 확인한다. 서울을 포함한 앞서 수집한 모든 지역을 같은 기준으로 다시 집계했다.

원문 지출 내역과 식당의 이름·주소를 보존한다. 누락 주소는 소상공인시장진흥공단 사업장 자료의 상호·지점·주소로 검색한다. 같은 이름이 여러 곳이면 공식 기관 주소 인근으로 좁히고, 여러 후보가 남으면 미확정 기록으로 보존한다. 기관 검색 중심은 같은 주소 또는 같은 도로의 인근 사업장 좌표이며 기관의 공식 좌표로 표시하지 않는다.

## 재실행

서울 원자료 재집계:
```sh
python3 tools/build_public_dining_catalog.py --full-csv /tmp/seoul-oa22156-full.csv --months 18 --keep-months 18 --output .codex-derived/seoul.json
```

주소 보완(최초 실행 때 사업장 검색 DB를 만들고 이후 재사용):
```sh
python3 tools/enrich_public_dining_addresses.py --business-zip /tmp/whattoeat-sbiz-20260630.zip --institutions data/public-dining/institution-locations.json --input data/public-dining/seoul-unresolved-address-records.jsonl.gz --input data/public-dining/capital_central/unresolved-address-records.jsonl.gz --input data/public-dining/east/unresolved-address-records.jsonl.gz --input data/public-dining/southwest/unresolved-address-records.jsonl.gz --output-dir data/public-dining/address-enrichment --cache-dir /tmp/whattoeat-address-search --months 18
python3 tools/merge_public_dining_catalog.py --report .codex-derived/public-dining-national.md
```

원자료 다운로드·지역별 수집 방법은 각 수집 프로그램의 `--help`와 지역 문서에 있다. 주소 보완 프로그램은 외부 API 키 없이 동작한다. 기간 밖 기록과 형식이 잘못된 기록은 포함하지 않으며, 입력 파일·압축 파일 자체가 없거나 잘못되면 오류로 종료한다.

## 출처

- [서울 본청 업무추진비](https://data.seoul.go.kr/dataList/OA-22156/S/1/datasetView.do): 공공누리 제1유형.
- 각 지역 `data/public-dining/*/sources.json`과 기록의 `sourceURL`: 실제 기관 공개 문서·첨부파일.
- [소상공인시장진흥공단 상가 정보](https://www.data.go.kr/data/15083033/fileData.do): 2026-06-30 기준 공식 사업장 주소 자료. 원본 ZIP 해시는 `address-enrichment/sources.json`에 보존.
- 기관 주소와 확인한 공식 페이지: `institution-locations.json`.

## 앱 계약

`schemaVersion: 1`, `generatedAt`, `coverageDescription`, `sourceURLs`, `restaurants`를 포함한다. 식당은 `name`, `nameVariants`, `address`, `region`, `departmentCount`(0 허용), `lastPaymentDate`, `sourceURL`을 가진다. 부서 수는 참고 정보이며 순위 점수가 아니다. 카탈로그가 없거나 잘못되면 기존 추천을 사용한다. 기존 13개 후보 중 이름과 주소가 같은 식당만 앞에 거리순으로 배치한다. 주소 번호·지점·지역이 다르면 같다고 처리하지 않는다.

모든 지역에 기록이 있다는 것이 모든 기관·식당의 완전 수집을 뜻하지 않는다. 확정하지 못한 주소는 `remaining-records.jsonl.gz`에 남는다.
