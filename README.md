# WhattoEat (v0.4.0)

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
- **서버**: `server/server.js`와 `server/photos.js`. 1~4페이지를 `is_end` 또는 고유 place id 13개까지 수집·중복 제거 후 안정된 JSON 계약으로 반환. 앱은 이 13곳 중 4곳을 무작위로 추천한다. 업스트림 타임아웃 5초, `/health` 제공, `PORT`에 바인딩.
- **메뉴 정책** (`WhattoEat/MenuPolicy.swift`): 투명한 정확 토큰 화이트리스트(김밥, 냉면, 돈가스/돈까스, 초밥, 국밥, 설렁탕, 칼국수, 햄버거, 피자, 치킨, 떡볶이, 샤브샤브 등)가 가게 이름 또는 최종 카테고리 텍스트에 있을 때, 또는 서버의 운영자 확인 데이터(`curated-menus.json`)가 있을 때만 '대표 메뉴'로 표시합니다. '한식' 같은 넓은 분류를 특정 요리로 바꾸지 않으며, 근거 없는 음식점은 "대표 메뉴 정보 없음"으로 정직하게 표시합니다.

## 정확한 한계 (중요)

- 카카오 로컬 API의 이 엔드포인트는 **메뉴, 가격, 판매 인기, 평점, 현재 영업 여부를 제공하지 않습니다.** 앱의 '대표 메뉴'는 위 정책에 따른 추정 근거가 있는 항목일 뿐 실제 판매를 보장하지 않습니다.
- **폐업·휴업 필터링은 구현되어 있지 않습니다.** 실제 방문 전 지도 앱에서 영업 여부를 확인하도록 앱 내에 안내합니다.
- 순위는 "이 기기에서 많이 고른 메뉴"일 뿐 실제 인기와 무관합니다.
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

- `WhattoEat.xcodeproj/` — Xcode 프로젝트 (iOS 17.0+, 버전 0.4.0, 빌드 `202608251921`, 번들 ID `com.nasfinder.WhattoEat`)
- `WhattoEat/` — SwiftUI 소스, Info.plist, 에셋
- `Config/API.xcconfig.example` — 백엔드 주소 xcconfig 예시(선택)
- `server/server.js` — Node 20 표준 라이브러리 참조 서버
- `server/curated-menus.example.json` — 운영자 확인 메뉴 예시
