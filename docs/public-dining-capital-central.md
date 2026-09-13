# 수도권·충청 공공기관 식당 자료

기준일 2026-09-13. 최근18개월(2025-03-13~2026-09-13) 안에 공식 공공기관 식사 결제1건과 주소 동일성이 확인된 식당을 포함한다. 부서수 하한은 없으며, 확인되지 않은 부서는 빈값으로 둔다.

기존3부서 결과를 변환하지 않고6지역의 원자료 캐시를 새 기준으로 다시 파싱했다. 실제 확보 범위는 경기도·인천광역시·대전광역시·세종특별자치시·충청북도·충청남도의 공식 업무추진비 공개 게시판이다. 모든 시군구·공공기관을 전수 수집한 자료는 아니다.

| 지역 | 결제 기록 | 식당 |
|---|---:|---:|
| 경기 | 403 | 214 |
| 인천 | 81 | 55 |
| 대전 | 130 | 106 |
| 세종 | 3515 | 916 |
| 충북 | 160 | 106 |
| 충남 | 116 | 71 |

주소 미확인 식사 후보 2922건은 `unresolved-address-records.jsonl.gz`에 별도로 보존했다. 원문 기관·상호·지역·날짜·문서URL·식사 여부만 포함하며 집행목적·참석자·작성자·전화번호는 저장하지 않는다.

주소는 원문에 기재된 번호 있는 도로명/지번 주소를 우선한다. 누락 주소는 공공데이터포털 상가업소정보2026-06-30 자료에서 같은 지역의 상호·지점명이 정확히 일치하고 후보 주소가 유일한 경우만 보충한다. 모호한 동명업소는 포함하지 않는다. 기관주변 검색을 이용한 추가 주소보완은 공통 처리기에서 별도로 수행한다.

주소 원본: https://www.data.go.kr/data/15083033/fileData.do . 다운로드 파일해시·문서주소·파싱실패 원인은 `sources.json` 및 `coverage.json`에 기록한다. 원문 XLS/HWP/PDF 등은 저장소에 넣지 않고 작업용 캐시에만 둔다. 수집시기와 공개서식 차이, 주소 미매칭으로 누락이 남으며 영업상태나 맛을 보증하지 않는다.

## 실행

번들 Python에 openpyxl/pdfplumber가 필요하다. 실제 다운로드는 공식HTTPS만 사용하고 기존 캐시를 재사용한다. 네트워크 실패는 제한된 횟수로 재시도하며 충북은 확인된 curl 전송 경로를 사용한다.

```sh
PYTHON=/Users/armsone/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
"$PYTHON" tools/collect_public_dining_capital_central.py --as-of 2026-09-13 --months 18 --regions gg,dj,sj,cn,cb,ic --cache-dir /tmp/whattoeat-capital-central --address-dir /tmp/whattoeat-sbiz-addresses --output-dir "data/public-dining/capital_central" --max-pages-per-region 4 --max-files-per-region 25 --no-preserve-existing
```

`--offline`을 붙이면 원문 캐시를 다시 파싱한다. 캐시 복원에 필요한 문서 메타데이터는 기본적으로 출력폴더의 `sources.json`에서 해시로 확인하며, 별도 원문목록은 `--source-index PATH`로 지정한다. 메타데이터 없는 캐시를 근거 없는 목록페이지 출처로 채우지 않는다.

네트워크와 원문 파일 없이 저장된 축소자료를 다시 생성하려면 같은 출력폴더를 대상으로 `--regenerate --as-of 2026-09-13 --months 18`을 사용한다. `matched-addresses.csv.gz`는 실제 연결된 주소만 담은 보조자료다. 신규기간 자료 확장은 실제 공개자료를 다시 수집해야 한다.

빌드·설치·Git·배포는 이 수집기에 포함하지 않는다.
