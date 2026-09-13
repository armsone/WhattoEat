# 영남·강원 공공기관 식사 이용 기록

2026-09-13 기준, 공식 공공기관 식사 지출이 1건 이상 확인되고 주소를 대조한 식당을 포함한다. 기간은 최근 18개월(2025-03-13~2026-09-13)이며 부서 수는 조건이 아니다. 확인할 수 없는 부서는 빈 문자열이다. 영업 여부, 일반인 이용 가능 여부, 맛을 보증하는 명단은 아니다.

## 실제 확보 범위

각 기관 공개 게시판의 최근 15페이지를 수집했다. 광역기관의 부서·직속기관 등의 공개분이며 전국 모든 자치단체나 해당 18개월의 모든 문서를 전수 수집한 자료가 아니다. 사용된 지출일의 실제 범위는 지역별 coverage.json에 기록했다.

| 지역 | 원문 문서/집행일 발견 | 실제 파싱 지출행 | 식사 판정 | 주소 일치 지출행 | 식당 수 |
|---|---:|---:|---:|---:|---:|
| 부산 | 150 | 1,703 | 783 | 393 | 198 |
| 대구 | 149 | 2,529 | 1,297 | 662 | 401 |
| 울산 | 150일 | 1,805 | 1,318 | 606 | 303 |
| 경북 | 150 | 2,290 | 1,091 | 637 | 288 |
| 경남 | 150 | 2,516 | 1,194 | 644 | 318 |
| 강원 | 225 | 2,858 | 785 | 484 | 240 |

식당 수는 공백을 정리한 상호와 주소 조합 기준이다. 전체 앱 통합에서 같은 지점의 표기 변형이 합쳐지면 감소할 수 있다. 강원 주소대조 성공 485행 중 중복 원문행 1건을 제외했다. 식사 판정은 날짜 확인 뒤 수행하므로 단순한 원문 전체 비율과 다르다.

## 공식 출처

- 부산: https://www.busan.go.kr/ghopen12/list?schBizNo=45
- 대구: https://www.daegu.go.kr/index.do?menu_id=00000084
- 울산: https://www.ulsan.go.kr/u/rep/transfer/chief/list.ulsan?mId=001003002005000000
- 경북: https://council.gb.go.kr/kr/bbs?bbs_id=open — 도청 원문이 점검 화면으로 이동해 정상 공개 중인 도의회 업무추진비를 수집했다.
- 경남: https://www.gyeongnam.go.kr/board/list.gyeong?boardId=BBS_0000957&paging=ok&menuCd=DOM_000000138002012000
- 강원: https://state.gwd.go.kr/portal/administration/opendata/propulsionCost/director
- 주소: https://www.data.go.kr/data/15083033/fileData.do — 소상공인시장진흥공단 상가상권정보 2026년 6월 자료. 상가업소번호·상호·지점·주소를 직접 대조했다. 지역 안에서 다른 주소의 동명 지점이 여럿이면 제외했다.

sources.json의 url은 문서 출처, contentURL은 해시를 계산한 실제 첨부/본문 URL이다. path는 수집 당시 작업 전용 임시 원문의 위치이고 오프라인 재생성에 필요하지 않다. SHA-256은 원문 다운로드 바이트 기준이다.

## 실행

프로젝트 루트에서 실행한다. 표준 Python 외에 기존 번들 openpyxl/pdfplumber가 필요하다. 별도 패키지를 설치하지 않았다.

```sh
PY=/Users/armsone/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
# 공식 목록/첨부 수집 후 처리. 기존 다운로드는 재사용한다.
"$PY" tools/collect_public_dining_east.py --collect --pages 15 --as-of 2026-09-13 --months 18 --out data/public-dining/east --cache-dir /tmp/whattoeat-east-task --address-dir /tmp/whattoeat-sbiz-addresses
# 커밋된 최소화 자료로 오프라인 재생성: 원문 캐시와 지역전체 주소CSV 불필요.
"$PY" tools/collect_public_dining_east.py --rebuild --as-of 2026-09-13 --months 18 --out data/public-dining/east
```

`--regions 부산,대구`로 수집 범위를 지정할 수 있다. `--output-dir`은 `--out`과 같다. `--as-of` 생략 시 실행일을 사용하고 기본 기간은 18개월이다. 오프라인 재생성은 이미 보존한 지출행의 기간만 필터링한다. 새 기간의 미수집 지출은 `--collect`가 필요하다.

## 보존 자료와 검증

- records.jsonl.gz: 주소가 확인된 식사 지출 3,426행. 원문 식별자와 행 번호를 포함한다.
- raw_expenditures.jsonl: 목적·참석자·직원 이름·전화번호를 제거한 재생성 입력.
- address-evidence.jsonl: 실제 사용한 주소 증거 1,743행. 지역 전체 업체목록은 보존하지 않는다.
- sources.json / coverage.json / errors.json: 원문 URL·해시, 범위·단계별 수량, 형식 해독 등의 진단.
- verification.json: 전 3,426행의 식당명·날짜를 원문 해당 행과 대조한 결과와 주소 원본 대조 결과. 불일치 0.

원문 캐시와 전체 주소CSV가 없는 경로로 오프라인 재생성을 실행하여 3,426개 레코드의 모든 필드가 동일함을 확인했다. 빌드·테스트·설치·Git·배포는 이 권역 수집 작업에서 실행하지 않았다.

DRM 문서, 지원하지 않는 파일 형식, 해독되지 않은 표, 식사 아닌 구매, 확인 주소가 없거나 지점이 모호한 지출을 포함하지 않았다. 해독 실패 문서는 성공으로 계산하지 않으며 errors.json에 남긴다. 파싱 지출행은 해독된 표의 실제 지출행으로, 미해독 문서까지 포함하는 원자료 전체 행 수가 아니다.

주소 확인을 보류한 실제 식사행은 `unresolved-address-records.jsonl.gz`에 원문 식당명·기관·선택 부서·날짜·원문 식별자·주소단서만 보존한다. 목적 원문이나 개인정보를 보존하지 않으며 주소가 확인되기 전 앱의 유효 명단에 합치지 않는다.
