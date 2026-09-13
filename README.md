# WhattoEat (v0.4.2)

"오늘 뭐 먹지?"를 도와주는 iOS/iPadOS MVP. 현재 위치 주변 음식점을 백엔드(카카오 로컬 카테고리 검색 FD6 프록시)에서 받아, **근거가 있는 대표 메뉴만** 보여주고 하나를 고르거나 무작위로 골라 준 뒤 Apple 지도 길 안내로 연결합니다. 확정한 선택은 이 기기에만 기록되어 "이 기기에서 많이 고른 메뉴" 순위로 표시됩니다.

## 아키텍처

```
[iOS 앱 (SwiftUI, 의존성 없음)]
   │  GET https://<APIBaseURL>/api/restaurants?latitude=..&longitude=..
   ▼
[Node 20 참조 서버 (표준 라이브러리만)]
   │  GET https://dapi.kakao.com/v2/local/search/category.json
   │  Authorization: KakaoAK <KAKAO_REST_API_KEY>  ← 서버 환경 변수 전용
   ▼
[Kakao Local API — 카테고리 FD6, x=경도, y=위도, sort=distance, size=15]
```

- **앱**: SwiftUI, iOS/iPadOS 17.0+, iPhone·iPad·Mac Catalyst 지원, 서드파티 의존성 없음. Kakao 키는 앱 어디에도 없습니다.
- **업데이트**: iPhone·iPad TestFlight는 시스템 관리 경로를 유지하고, Mac 직접 배포판만 공식 GitHub Releases의 DMG를 자동 또는 수동으로 확인·다운로드해 SHA-256 검증 후 엽니다.
- **서버**: `server/server.js`와 `server/photos.js`. 1~4페이지를 `is_end` 또는 고유 place id 13개까지 수집·중복 제거 후 안정된 JSON 계약으로 반환. 앱은 이 13곳 전체를 보여 주되, 공공기관 이용 기록이 확인된 곳을 거리순으로 앞에 두고 나머지는 무작위 순서로 표시한다. 업스트림 타임아웃 5초, `/health` 제공, `PORT`에 바인딩.
- **메뉴 정책** (`WhattoEat/MenuPolicy.swift`): 투명한 정확 토큰 화이트리스트(김밥, 냉면, 돈가스/돈까스, 초밥, 국밥, 설렁탕, 칼국수, 햄버거, 피자, 치킨, 떡볶이, 샤브샤브 등)가 가게 이름 또는 최종 카테고리 텍스트에 있을 때, 또는 서버의 운영자 확인 데이터(`curated-menus.json`)가 있을 때만 '대표 메뉴'로 표시합니다. '한식' 같은 넓은 분류를 특정 요리로 바꾸지 않으며, 근거 없는 음식점은 "대표 메뉴 정보 없음"으로 정직하게 표시합니다.

- **공공기관 이용 기록 우선 표시** (`WhattoEat/PublicDiningPriority.swift`): 앱 번들의 `WhattoEat/PublicDiningCatalog.json`에 있는 식당 중 정규화한 식당명과 주소가 카카오 검색 결과와 정확히 일치하는 곳을 추천 목록 앞쪽에 거리순으로 둡니다. 카드에는 작은 `공공기관 이용 기록` 표시가 붙고, 결정 화면에서 공식 원문·최근 결제일·이용 부서 수·지역별 수집 범위를 확인할 수 있습니다.

## 공공기관 이용 기록 우선 표시

- 기존 Kakao 검색 서버는 그대로 필요하지만, 이 기능을 위한 추가 서버나 새 API는 없습니다. 카탈로그는 앱 번들에 포함된 JSON 파일이며 원자료를 어디에도 업로드하지 않습니다.
- 카탈로그는 공공기관이 공개한 결제 내역 원자료를 이 저장소에서 직접 집계한 것입니다. 다른 서비스(예: ‘그냥여기’)의 명단을 가져온 것이 아닙니다.
- 서울을 포함한 17개 지역 원자료를 최근 18개월 기준으로 재집계하며, 누락 주소는 기관 주변의 공식 사업장 자료로 검색해 보완합니다.
- 실제 17개 지역의 확보 상태(`collected`, `empty`, `no-eligible-restaurant`, `missing`)와 수집 기간·식당 수는 카탈로그 최상위 메타데이터(`nationalCoverage`, `regions`) 및 식당별 `coverageDescription`을 기준으로 동적으로 반영됩니다. 앱은 이 값을 그대로 보여 주고 별도의 숫자를 추정하지 않으며, 미확보(`missing`) 권역이 있는 경우 전국 완료로 보지 않고 확보된 지역의 기록만 선별 반영합니다.
- 전국 수집 및 통합 명령:
  ```bash
  python3 tools/merge_public_dining_catalog.py                      # 기본 카탈로그 통합 생성 (WhattoEat/PublicDiningCatalog.json)
  python3 tools/merge_public_dining_catalog.py --report R.md        # 지역별 17개 지역 현황 표 파일 출력
  ```
- 우선 표시 조건: 정규화한 식당명이 같고, 도로명 또는 지번 주소가 같은 형식끼리 토큰 단위로 일치해야 합니다(건물 번호 일부만 같은 경우나 도로명·지번 혼동은 불일치하며, 광역 지명이 다르면 절대 일치하지 않아 타 지역 동명·하이픈 번호 충돌을 방지합니다). 카탈로그 후보가 둘 이상 겹치면 미확정으로 두고 우선하지 않습니다.
- 자격 조건: 공공기관의 식사 이용 기록 1건 이상, 마지막 결제일이 앱 실행일 기준 최근 18개월 이내. 부서 수에 비례한 점수나 등급은 없습니다. 원자료의 집계 기간은 카탈로그에 명시하며, 앱은 실행일 기준으로 오래된 기록을 추가로 제외합니다.
- 파일이 없거나, `schemaVersion`이 1이 아니거나, 항목이 비어 있거나 형식이 잘못되면 기존 추천 순서로 그대로 동작합니다. 일치하는 식당이 없는 지역도 기존과 같습니다.
- 이 표시는 맛·안전·현재 영업 여부를 뜻하지 않습니다. 서울 단일 수집 계약은 [docs/public-dining-data.md](docs/public-dining-data.md), 전국 통합 정책과 데이터 계약은 [docs/public-dining-national.md](docs/public-dining-national.md)를 참고하세요.

## 사용자 변경
- 하단 추천 버튼은 가방 아이콘 표식으로 표시되며, 선택 시 밝은 원형(흰색 텍스트)과 아래 빨간 막대로만 강조됩니다.

## 정확한 한계 (중요)

- 카카오 로컬 API의 이 엔드포인트는 **메뉴, 가격, 판매 인기, 평점, 현재 영업 여부를 제공하지 않습니다.** 앱의 '대표 메뉴'는 위 정책에 따른 추정 근거가 있는 항목일 뿐 실제 판매를 보장하지 않습니다.
- **폐업·휴업 필터링은 구현되어 있지 않습니다.** 실제 방문 전 지도 앱에서 영업 여부를 확인하도록 앱 내에 안내합니다.
- 순위는 "이 기기에서 많이 고른 메뉴"일 뿐 실제 인기와 무관합니다.
- '공공기관 이용 기록'은 공개 결제 내역에 이름·주소가 같은 식당이 있다는 뜻일 뿐, 맛·안전·현재 영업 여부를 보장하지 않습니다.
- 이 저장소는 실제 Kakao API 키 없이 작성되었으므로 **실 API 호출 검증은 수행되지 않았습니다.** 계약은 공식 문서 기준입니다.

## 개인정보 / 데이터 흐름

- 위치는 When In Use 권한으로 1회 조회하여 검색 요청 쿼리에만 사용합니다.
- 서버는 사용자 좌표를 캐시하거나 로그로 남기지 않습니다.
- 선택 기록(메뉴+음식점 이름+시각)은 기기의 UserDefaults에만 저장되며 어디에도 전송되지 않습니다.
- Kakao API 키는 서버 환경 변수(`KAKAO_REST_API_KEY`)로만 주입되며 iOS 소스·응답·로그에 노출되지 않습니다.

## 서버 실행 (Node 20+)

```bash
cd server
# 선택: 운영자 확인 메뉴 사용 시
cp curated-menus.example.json curated-menus.json   # 내용을 실제 확인한 데이터로 교체

KAKAO_REST_API_KEY=발급받은키 PORT=8080 node server.js
# 확인
curl "http://localhost:8080/health"
curl "http://localhost:8080/api/restaurants?latitude=37.5665&longitude=126.9780"
```

환경 변수: `KAKAO_REST_API_KEY`(필수), `PORT`(기본 8080), `SEARCH_RADIUS_METERS`(기본 1000, 100~20000), `TOUR_API_SERVICE_KEY`(선택).

### 사진 공급 기준

1. `TOUR_API_SERVICE_KEY`가 있으면 한국관광공사 TourAPI에서 정규화한 식당명이 정확히 같고 좌표가 75m 이내인 단일 후보의 `Type1` 사진만 `restaurantVerified`로 사용합니다.
2. 실제 식당 사진이 없으면 계정·키가 필요 없는 Openverse에서 상업 이용 가능한 `CC0`, `PDM`, `CC BY` 음식 사진만 `categoryExample`로 사용합니다. 성인 콘텐츠, 세로 사진, 900×600 미만 사진은 제외하며 한 API 응답의 13곳에는 같은 작품 ID나 URL을 중복 배정하지 않습니다.
3. 앱은 대체 사진에 `메뉴 예시`를 표시하고, 사진 정보에서 저작자·원문·라이선스를 확인할 수 있게 합니다. 설정의 `사진 출처와 이용 조건`에도 전체 기준을 안내합니다.

사진 공급자가 실패해도 식당 검색 결과는 유지되며 앱의 내장 음식 예시 이미지로 복구합니다. 기존 Foursquare 실험 파일은 참고용으로 남아 있지만 실행 서버에서는 연결하지 않습니다.

## 앱 설정 및 빌드

1. `WhattoEat.xcodeproj`를 Xcode(26.x)로 엽니다. Signing Team만 지정하면 빌드됩니다.
2. `WhattoEat/Info.plist`의 `APIBaseURL`을 배포한 **HTTPS** 서버 주소로 바꿉니다. 플레이스홀더(`REPLACE-ME`) 상태에서는 앱이 명확한 설정 오류 안내를 표시합니다.
3. 주소를 소스 밖에서 관리하려면 `Config/API.xcconfig.example` 참고(복사 후 `$(API_BASE_URL)` 방식으로 연결).

로컬 서버는 HTTP라서 기본적으로 앱이 거부합니다(설정 오류 안내). 개발 중에만 임시로 HTTPS 터널(예: 자체 리버스 프록시)을 쓰거나 코드를 수정해 테스트하세요.

## API 계약

`GET /api/restaurants?latitude=<위도>&longitude=<경도>` → 200

```json
{
  "restaurants": [
    {
      "id": "카카오 place id",
      "name": "가게명",
      "category": "음식점 > 한식 > 국밥",
      "latitude": 37.56,
      "longitude": 126.97,
      "distanceMeters": 120,
      "address": "지번 주소 또는 null",
      "roadAddress": "도로명 주소 또는 null",
      "phone": "전화 또는 null",
      "placeURL": "카카오 상세 URL 또는 null",
      "curatedMenus": ["국밥"]
    }
  ],
  "source": "kakao-local-category-FD6",
  "disclaimer": "…"
}
```

오류: 400(좌표 검증 실패), 503(서버에 키 미설정), 502/504(업스트림 오류/지연), 모두 비밀 정보 없는 JSON 메시지.

## 파일 구성

- `WhattoEat.xcodeproj/` — Xcode 프로젝트 (iOS 17.0+, 버전 0.4.2, 빌드 `202608271840`, 번들 ID `com.nasfinder.WhattoEat`)
- `WhattoEat/` — SwiftUI 소스, Info.plist, 에셋
- `WhattoEat/PublicDiningPriority.swift` — 공공기관 이용 기록 카탈로그 로드·검증·전국 주소 일치 판정·우선 정렬
- `WhattoEat/PublicDiningCatalog.json` — 앱 번들에 포함되는 공공기관 이용 기록 카탈로그
- `tools/build_public_dining_catalog.py` — 서울 단일 공공기관 식당 카탈로그 수집기
- `tools/merge_public_dining_catalog.py` — 전국 17개 지역 공공기관 식당 기록 통합 수집기
- `docs/public-dining-data.md` — 서울 카탈로그 데이터 계약과 수집 절차
- `docs/public-dining-national.md` — 전국 공공기관 식당 기록 통합 정책 및 인터페이스 규격
- `Config/API.xcconfig.example` — 백엔드 주소 xcconfig 예시(선택)
- `server/server.js` — Node 20 표준 라이브러리 참조 서버
- `server/curated-menus.example.json` — 운영자 확인 메뉴 예시
