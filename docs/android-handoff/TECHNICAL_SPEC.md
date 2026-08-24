# 오늘 뭐 먹지?? — Android 정밀 이식 기술서

기준일: 2026-08-24 (Asia/Seoul)  
기준 제품: iOS/iPadOS/Mac Catalyst `0.2.1` (`202608232316`)  
Android 정책: **앱 소유 영역은 iPhone 구현을 그대로 복제한다. Android식 재해석은 허용하지 않는다.**

## 0. 근거 등급과 절대 패리티 정책

- **확인됨**: 현재 소스, Info.plist, asset, 최신 설치본 접근성 트리 또는 이 문서와 함께 캡처한 PNG로 확인했다.
- **실행 확인됨**: iPhone 17 Simulator / iOS 26.5의 설치된 최신 빌드에서 직접 진입해 확인했다.
- **미검증**: 소스 의도는 있으나 현재 실기기/해당 상태의 캡처 또는 동작 검증이 없다.
- **알려진 불일치**: 소스 의도와 대표님이 본 실제 동작이 다르다.
- **가정**: Android 구현 전에 제품 결정 또는 추가 측정이 필요하다. 가정을 확인된 사실처럼 구현하지 않는다.

Android에서 임의로 바꾸면 안 되는 앱 소유 항목은 문구, 색, 폰트 계열과 metrics, 크기, 행간, 여백, 정렬, radius, stroke, shadow, 아이콘 형태, bitmap 자산, 화면 순서, 상태 전이, 버튼 범위, 제스처 threshold, 애니메이션 duration/easing, 접근성 이름과 결과이다. Material 기본 외형으로 치환하지 않는다.

예외는 OS가 강제로 소유하는 상태바·홈 인디케이터, 시스템 키보드/IME, 위치·알림 권한창, 외부 지도 앱·스토어 화면뿐이다. 지도 tile 내부는 제공자 소유 예외가 될 수 있으나 지도 카드의 frame/radius/stroke, 빨간 marker, overlay 문구와 탭 동작은 앱 소유다. Android alert/sheet/toggle/picker가 플랫폼 기본이라는 이유만으로 예외가 되지는 않는다.

근거: `WhattoEat/ContentView.swift`, `Theme.swift`, `WhattoEatApp.swift`, `Info.plist`, `Assets.xcassets`; 캡처 manifest는 `parity-manifest.json`.

## 1. 제품과 지원 범위

현재 제품 목적은 현재 위치 또는 사용자가 지정한 지역 근처에서 영업 가능성이 있는 식당을 최대 13곳 추리고 무작위 순서로 제안하여, 한 끼를 빠르게 결정하고 지도 앱으로 길 찾기를 여는 것이다. 최근 결정과 찜, 자주 사용한 지역은 이 기기에 보관한다.

확인된 Apple 범위:

- iOS 최소 17.0, iPhone/iPad family (`WhattoEat.xcodeproj/project.pbxproj:183,208,222-267`).
- iPhone portrait only. iPad는 portrait/upside-down/양 landscape (`Info.plist:46-55`).
- Mac Catalyst. 기본 창 750×1200이며 앱 논리 UI를 1.5배 확대한다 (`WhattoEatApp.swift:6-16`).
- 화면은 light 고정, dark mode 없음 (`ContentView.swift:232-245`, `Theme.swift:10-12`).
- 앱 콘텐츠 최대 논리폭 600pt, 중앙 정렬. 앱 소유 하단바는 모든 화면에 고정한다.
- 접근성은 SwiftUI의 semantic font/기본 role과 일부 명시 label을 사용한다. 큰 글자, VoiceOver 전체 순서, Switch Control, Reduce Motion, iPad split view는 미검증이다.

Android 1차 수용 범위는 휴대전화 portrait이다. 태블릿은 iPad처럼 600dp cap 중앙 rail을 유지해야 하며 임의 2-column 재배치를 금지한다. Android desktop/foldable의 별도 UX 재해석도 금지한다.

## 2. 전역 화면 구조와 내비게이션

`AppPage`: `home`, `region`, `result`, `decision`, `history`, `favorites`, `profile` (`ContentView.swift:104`).

하단 탭 순서와 아이콘은 정확히 다음과 같다 (`ContentView.swift:1175-1231`).

| 순서 | 문구 | 아이콘 | 동작 |
|---:|---|---|---|
| 1 | 홈 | `house.fill` | 홈 |
| 2 | 지역 | `map.fill` | 지역 선택 |
| 3 | 추천 | `die.face.5.fill` | 결과가 있더라도 새 추천 시작; 접근성명 `추천 다시 고르기` |
| 4 | 최근 | `clock.arrow.circlepath` | 최근 한 끼 |
| 5 | 찜 | `heart` | 찜한 맛집 |

선택 탭은 `#E41E25`, 나머지는 `#2E3338`. 하단바는 HStack 5등분, horizontal 8, top 14, bottom 10, ivory 배경, continuous radius 22, `canvasLine` alpha .7 1pt stroke, 외부 horizontal 28, maxWidth 600, bottom 4. 콘텐츠는 bottom 72를 비운다. 키보드 bottom safe-area를 무시해 하단바가 키보드와 함께 올라오지 않는 것이 의도다 (`ContentView.swift:234-245`).

설정은 홈·지역·추천·최근·찜의 우측 상단 gear로 연다. `pageBeforeSettings`를 저장하고 닫으면 정확히 이전 화면으로 돌아간다. 결정 화면은 gear 없이 `닫기`만 있고 결과로 돌아간다. 별도 `NavigationStack`/back stack과 화면 전환 애니메이션은 없다. Android system Back은 decision→result, settings→이전 페이지로 맞추고, root tab에서 새 임의 stack을 만들지 말아야 한다. root 탭에서 Back 종료 정책은 미검증 제품 결정이다.

## 3. 화면과 사용자 흐름

### 3.1 홈

요소 순서와 정확 문구 (`ContentView.swift:587-724,1136-1172`):

1. `Wordmark` bitmap(접근성 `오늘 뭐 먹지`)과 우측 설정.
2. `PinWell` 58×58 + `어디서 드실까요?`.
3. `현재 위치를 기반으로 맛있는 점심을 추천해드려요!`.
4. 카드 `자동 위치` / `현재 위치 사용`.
5. 카드 `지역 지정` / `직접 지역 선택`.
6. `LunchHero` 위 live text 버튼 `메뉴 추천 받기` + chevron.

`자동 위치`: auto mode→결과 화면→`현 위치 확인 중…`→권한/현재 위치/식당 검색. `지역 지정`: manual mode→지역 화면. `메뉴 추천 받기`: manual 좌표가 없으면 지역 화면, 아니면 결과 검색. 홈의 auto 카드는 source상 항상 selectionMint로 강조되고 전달된 `mode`는 시각에 사용하지 않는다.

레이아웃: leading VStack gap 34, horizontal 28, top 14, bottom 18. 카드 그룹 gap 14. 위치 카드 높이 76, HStack gap 16, inner horizontal 16, icon 50, radius 18, 1pt `canvasLine` alpha .8, shadow `caramelDeep` alpha .06 radius4 y2. `LunchHero` 400:300 비율, full rail. live 버튼은 표시 이미지 폭 46%, 높이 15%, x 8.25%, y 40.75%, radius는 이미지 폭의 7.5%, 글자 크기는 4% medium.

### 3.2 지역 선택

상단: red map well + `지역 선택` + 설정. 본문 순서 (`ContentView.swift:1234-1371`):

1. 2분할 `지역 지정` / `현 위치`.
2. 상태문구.
3. 검색 field `지역명으로 검색 (예: 강남, 판교)` + 검색 버튼.
4. `내 주변`.
5. `자주 찾는 지역`.

정확 상태문구:

- 초기 `현 위치를 다시 확인할 수 있어요`
- 진행 `현 위치 확인 중…`
- 성공 `현 위치: {지역명}` 또는 이름 없음 `현 위치를 확인했어요`
- 거부 `위치 권한이 꺼져 있어요`
- 주변 empty `현 위치를 확인하면 주변 지역을 보여드려요.`
- 자주 찾기 empty `아직 자주 찾는 지역이 없어요.`

`현 위치`는 지역 화면을 유지하며 위치를 다시 잡고 주변 3곳만 갱신한다. 자동으로 추천 화면으로 이동하지 않는다. 현재 지역 + 거리순 인접 2곳을 보여준다. 행정복지센터/주민센터/읍·면사무소 검색 및 4/8/12km 방사형 reverse-geocode 보완을 사용한다 (`ContentView.swift:381-495`). 자주 찾는 지역은 실제 추천에 성공한 지역의 사용횟수 내림차순, 동률 최근순, 최대 3개다 (`ChoiceStore.swift:99-120`).

검색은 공백 trim 후 빈 값 무반응. MapKit natural language search라 행정명뿐 아니라 라페스타 같은 POI도 허용한다. 성공하면 manual 좌표 저장→result. 실패 문구는 `‘{query}’ 지역을 찾지 못했어요. ‘서울 강남’처럼 시·구 단위로 입력해 보세요.` 또는 `지역 검색에 실패했어요. 네트워크 상태를 확인한 뒤 다시 시도해 주세요.`. 목록 행 전체가 버튼이다.

키보드 Search, 돋보기, 모드, 지역 행, 설정, 하단 탭, 빈 본문 탭은 focus를 해제한다. scroll은 interactive keyboard dismiss. 최신 캡처 `region-search-keyboard-current.png`에서 하단바가 화면 bottom에 고정됨을 확인했다.

레이아웃: body gap22, horizontal28, top8, bottom30. 모드 card radius18; 각 half vertical16. 상태 caption horizontal4, top -12. 검색 row horizontal18/vertical16/radius22. section gap12, list radius18, row padding15, divider leading14.

### 3.3 오늘의 한 끼 / 추천

상단은 red dice well + `오늘의 한 끼` + 설정. 그 아래 지역명 한 줄과 `chevron.down`, 전체 bar가 `지역 다시 선택` 버튼이다. 지역명을 누르면 region으로 간다 (`ContentView.swift:1373-1432`).

상태:

- 결과: 카드들.
- 실패: `문제가 생겼어요` + 실제 오류 + `다시 시도`.
- 위치 거부: `위치를 찾을 수 없어요` + `지역을 직접 지정해 주세요.` + `지역 지정`.
- empty: `주변 음식점을 찾지 못했어요` + `지역을 바꾸거나 다시 골라 주세요.`.
- loading 계열: `{지역명} 주변 오늘의 한 끼를 고르는 중…`.

loading은 spinner와 문구를 보이고 정확히 10초 후 `다시 고르기`가 opacity+bottom move, easeOut 0.2s로 나타난다. 자동 취소/timeout은 없고 새 요청 token이 이전 응답의 UI 반영만 막는다 (`ContentView.swift:1657-1693`).

결과 필터/정렬: `isOpenNow == false`만 제거한다. 거리순으로 최대 13곳 pool을 만든 뒤 모두 shuffle한다. 첫 곳은 main, 2번째부터 최대 12곳은 가로 carousel이다 (`ContentView.swift:541-564`). `isOpenNow == nil`은 유지하므로 영업 여부 미확인 식당이 들어갈 수 있다.

메인 카드: 높이230/radius20/caramel alpha .55 stroke1/shadow radius4 y2. 사진 158×230. 오른쪽 gap9, horizontal14/top16/bottom14: 메뉴 caption pill, 식당명 title2 bold 최대2줄, category subheadline 1줄, red location + `약 {N}m`, `가까워서 더 반가운 한 끼`, spacer min12, full-width `이곳 보기` minH36. 하트 circle40 top/right14. 전체 카드 탭은 decision.

`함께 보면 좋은 맛집`: headline, top padding20. horizontal ScrollView, gap10, indicator 없음, viewAligned. 카드 폭 `max(88, (availableWidth - 86) × 0.3)`, 이미지 높이105, radius16. name11 bold/category10/distance10 각1줄, 하트40 outer5. 13곳이면 최대 12개를 끝까지 drag할 수 있어야 한다.

**알려진 불일치:** 소스에는 horizontal ScrollView가 있으나 `.scrollTargetLayout()`이 없고, 대표님은 최신 설치본에서 좌측 drag가 작동하지 않는다고 보고했다 (`ContentView.swift:1450-1463`). Android 수용 기준은 실제 손가락 drag가 작동하고 마지막 카드까지 도달하는 것이다. iOS defect를 패리티로 복제하지 않는다. iOS 수정 후 새 기준 캡처가 필요하다.

찜은 restaurant id 단일 toggle. `heart` charcoal↔`heart.fill` red. 접근성 `찜하기`/`찜 해제`.

### 3.4 오늘의 결정

결과 카드 전체 탭으로 진입. 상단 `오늘의 결정` + check seal + `닫기`. decision 데이터가 없으면 result로 복귀 (`ContentView.swift:1802-2037`). ScrollView가 아니다.

요소 순서:

1. hero: 사진132×136 + 선택적 category pill + menu24 bold + restaurant16 semibold + 선택적 `여기서 약 {N}m`. 전체 높이136/radius20. 추천 결과의 대표 카드 하단은 전화번호가 있으면 왼쪽 `전화걸기`, 오른쪽 `이곳 보기`를 한 줄로 표시하고, 번호가 없으면 전화 버튼을 표시하지 않는다. `함께 보면 좋은 맛집` 카드도 전화번호가 있는 항목만 오른쪽에 전화 아이콘 버튼을 표시한다. `오늘의 결정` 화면은 전화번호가 있으면 번호와 `전화걸기`를 함께 표시한다. 전화 버튼은 숫자와 선행 `+`만 보존한 시스템 전화 URL을 열며 카드 선택과 독립적으로 동작해야 한다.
2. Apple MapKit 미리보기 높이244/radius20. top-left `{지도 shortName}로 길 찾기`, 주소가 있으면 bottom-left address. 지도 자체 hit testing off, outer button이 탭을 소유.
3. 선택 전 `오늘은 여기로`, 선택 후 `최근 한 끼에 담았어요`.
4. `영업 정보는 지도에서 확인`.

`오늘은 여기로`는 먼저 이 기기의 최근 기록에 추가하고, 즉시 현재 설정된 지도 열기를 시도한다. 저장 후 CTA는 선택 상태 정보행으로 변하고 지도 카드 탭으로 다시 열 수 있다. CTA 높이56, radius16; 초기 white→ivory/caramel .55 stroke/shadow, 완료 selectionMint. info row 높이40.

외부 지도 기본은 Naver(iOS), Mac Catalyst는 Apple. URL 계약:

- Apple: `MKMapItem.openInMaps()`.
- Naver: `nmap://place?lat={lat}&lng={lng}&name={urlEncodedName}&appname={bundleID}`.
- Kakao: id 있으면 `kakaomap://place?id={id}`, 없으면 `kakaomap://look?p={lat},{lng}`.
- Google: `comgooglemaps://?q={lat},{lng}&center={lat},{lng}`.

설치/열기 실패 dialog 순서: `{지도}가 필요해요` / `설치하거나, 이미 있는 다른 지도로 이 식당을 열 수 있어요.` / `설치하기` / `다른 지도 선택` / `취소`. 설치된 다른 지도를 선택하면 기본값을 즉시 변경하고 같은 식당을 연다. Store IDs: Naver 311867728, Kakao 304608425, Google 585027354. Apple 실패는 `Apple 지도를 열 수 없어요` / `확인`. 최신 `0.2.1 (202608232316)` simulator 재빌드·설치본에서 이 문구를 확인했다.

영업정보 alert: `영업 정보`; `식당 정보는 지도 제공처의 최신 상태와 다를 수 있어요. 방문하기 전에 영업 여부와 실제 메뉴를 지도에서 한 번 확인해 주세요.`; `확인`.

### 3.5 최근 한 끼

상단 clock + `최근 한 끼` + 설정. empty 중앙: `EmptyRecent`, `첫 한 끼를 기다리고 있어요`, `오늘 메뉴를 고르면 맛있는 기억이\n여기에 차곡차곡 쌓여요.`. 데이터는 지역별, 그룹의 최근순. 구버전 nil 지역은 `이전 기록`.

row: 사진82×76, menu headline, restaurant subheadline 1줄, 날짜+시간 caption, 우측 red trash frame36. 좌 swipe 또는 trash로 즉시 삭제하며 확인/undo가 없다. swipe max -76, minimumDistance12, 좌 30 초과 open/우 30 초과 close, easeOut .18s. footer `최근에 결정한 메뉴와 음식점은 이 기기에만 보관돼요.` (`ContentView.swift:2466-2552`).

### 3.6 찜한 맛집

상단 filled heart + `찜한 맛집` + 설정. empty: `EmptyFavorites`, `첫 하트를 기다리고 있어요`, `추천에서 마음에 드는 곳을\n콕 눌러 주세요.`. 지역별/최신 그룹순.

row는 최근과 같은 구조: 사진82×76, 식당명, category, 날짜(시간 없음), 우측 red filled heart frame44. 하트 또는 좌 swipe로 즉시 제거, 확인/undo 없음. 접근성 value `선택됨` (`ContentView.swift:2557-2647`).

### 3.7 설정

상단 gear + `설정` + `닫기`. 그룹은 gap20, horizontal28, vertical12. 공통 card gap14/padding16/ivory/radius18/canvasLine .8 1pt/shadow .07 r4 y2 (`ContentView.swift:2140-2462`).

1. `위치 권한`: 상태 `사용 중` / `꺼짐` / `아직 선택하지 않음` / `확인 필요`; 버튼 notDetermined `허용하기`, 나머지 `설정 열기`; `현재 위치를 다시 잡고 주변 음식점을 찾을 때만 사용해요.`.
2. `길 찾기 지도`: Apple/네이버/카카오/Google 순, Mac은 Apple만. icon32×32/radius8, equal cells, minH64/radius12. 선택 selectionMint/red, 비선택 clear/canvasLine. `음식점 지도를 누르면 여기서 고른 지도로 열어요.`.
3. `점심 알림`: `알림 받기`. ON이면 한 줄 `점심시간` + system time picker + `정각`/`5분 전`/`10분 전`/`15분 전`/`30분 전`. `점심시간 전에 오늘의 추천을 알려드려요.\n알림은 이 기기에서만 울려요.`.
4. `사진 출처와 이용 조건`: `사진의 출처와 사용 기준을 한곳에서 확인해요.`; `카피라이트 안내` 접힘/펼침. 펼침은 `메뉴 예시 사진`, `실제 식당 사진`, `사진별 상세 정보` 세 행과 source의 정확 설명을 표시한다. chevron 180° 및 content opacity+top move, easeInOut .22s.

알림을 켤 때만 OS 권한 요청. 거절이면 toggle false 및 `알림이 꺼져 있어요` / `iPhone 설정의 알림에서 ‘오늘 뭐 먹지’를 허용해 주세요.` / `확인`. Android 문구에서 제품명만 바꾸지 말고 동일 표기를 유지하되 `iPhone 설정`은 Android 시스템 현실과 충돌하므로 반드시 제품 승인 후 변경하거나 패리티 예외로 명시한다.

로컬 알림 identifier `whattoeat.lunch.daily`, 매일 반복. 시각은 점심−lead, 24h modulo. 제목 `곧 점심시간이에요`; 최근 메뉴가 있으면 `{지역} 근처에서 마지막으로 본 추천 후보는 ‘{메뉴}’였어요. 앱을 열면 주변 후보를 다시 찾아 드려요.`, 없으면 `{지역} 근처 점심 후보를 앱에서 찾아보세요.`. 서버 push/deep link 없음.

### 3.8 사진 정보

HTTPS 원격 사진이 실제 로드되고 metadata가 있을 때만 `사진 정보`. categoryExample이면 `메뉴 예시`. sheet 제목은 `메뉴 예시 사진` 또는 `사진 정보`; 설명은 `음식 종류를 보여 주는 예시이며, 이 식당에서 촬영한 사진은 아니에요.` 또는 `사진 제공처와 원문을 확인할 수 있어요.`. 조건부 행 `제목`, `사진`, `이용 조건`, `제공`, `출처`; 유효 HTTPS 링크 `원문 보기`, `작가 보기`, `이용 조건`; `닫기`. iOS medium detent (`ContentView.swift:999-1134`). 현재 사진별 상세는 dropdown이 아니라 sheet이고, 설정의 총괄 카피라이트만 dropdown이다.

## 4. 시각 토큰과 자산

### 4.1 색

| 토큰 | HEX / alpha |
|---|---|
| `mintBase` | `#FBF8F2FF` |
| `ivory` | `#FFFDF7FF` |
| `caramel` | `#9E5724FF` |
| `caramelDeep` | `#7A4F2BFF` |
| `chrome` | `#C7BFADFF` |
| `charcoalText` | `#2E3338FF` |
| `charcoalSoft` | `#2E3338B3` |
| `accentRed` | `#E41E25FF` |
| `selectionMint` | `#DBF2E6FF` |
| `mintInk` | `#1F5742FF` |
| `leatherLight` | `#B36B30FF` |
| `canvasLine` | `#D6CFBDFF` |

근거 `Theme.swift:13-33`. 순수 black은 앱 토큰으로 쓰지 않는다.

### 4.2 타이포그래피

`screenTitle` system 20 bold, `sectionTitle` 17 semibold, `rowTitle` 15 medium, `supporting` 13 regular (`Theme.swift:3-8`). 추가 고정값: bottom icon20 semibold(주사위21), label10 medium; small-card name11 bold/category·distance10; decision menu24 bold minScale .76, restaurant16 semibold; image badge9; provider label11; copyright title14/body13. 나머지는 SwiftUI semantic `.headline/.subheadline/.body/.caption/.caption2/.footnote/.title2/.title3`의 현재 Apple metrics다.

SF font 파일은 저장소에 없고 Android 재배포 라이선스/metrics가 미해결이다. Roboto 치환은 exact parity 실패다. Android 착수 전에 합법적인 동일 metrics font 계약 또는 문자별 screenshot 비교 허용오차를 확정해야 한다.

### 4.3 chrome/enamel 공통

- `ReferenceIconWell`: 보통48, white→ivory circle, chrome .75 1pt, shadow charcoal .14 r2 y2. glyph 40% semibold. mappin은 red+mintInk palette 46%.
- `ExactWell`: PNG scaledToFill, 지정 diameter, shadow charcoal .14 r2 y2.
- `referenceCard`: ivory, continuous radius18, canvasLine .8 1pt, shadow caramelDeep .08 r5 y2.
- Primary: headline ivory, vertical14, caramel, radius14, chrome .9 1pt.
- Secondary: headline charcoal, vertical14, ivory, radius14, chrome 1pt.
- Compact header: inner gap10/h8/height64; well34; title20 bold; trailing gear34 또는 close capsule min52×44; outer h28/top14/bottom8.

### 4.4 bitmap 원본

Android는 아래 PNG byte를 원본으로 보존하고 동일 crop/contentMode를 적용한다. 모든 SHA-256은 `parity-manifest.json`에 기록한다.

- `Wordmark.png` 190×48, 홈 162×41 scaledToFit.
- `LunchHero.png` 400×300, full rail scaledToFit; 문구/버튼은 bitmap에 굽지 않고 live UI.
- `PinWell.png` 64×64, render58×58.
- `EmptyRecent.png` 768×768, `EmptyFavorites.png` 1254×1254; 210×150 scaledToFill/radius24.
- `FoodMain.png` 190×295, `FoodSide1/2/3.png` 125×110; consumer frame scaledToFill+clip.
- `MapApple/MapNaver/MapKakao/MapGoogle.png` 512²; settings 32² scaledToFit/high interpolation/radius8.
- 나머지 보존 자산: AppIcon, AutoWell, BackWell, ChartWell, FoodBibimbap, LeatherTexture, LocationChoices, MainHeartWell, SearchWell.

SF Symbols는 Android에서 사용할 수 있는 vector가 저장소에 없다. Material icon 대체 금지. 동일 silhouette의 합법적 custom vector를 만들고 paired screenshot으로 검증해야 한다. 이는 OS 강제 예외가 아니라 미해결 앱 자산이다.

## 5. 데이터와 지속성

모델 (`Models.swift:3-104`):

- `RestaurantsResponse`: `restaurants[]`, optional `source`, `disclaimer`.
- `Restaurant`: id/name/category/lat/lng, optional distance/address/roadAddress/phone/placeURL/isOpenNow/curatedMenus/photo fields/`PhotoMatchEvidence`.
- `Decision.id = menu + "|" + restaurant.id`, memory only.
- `ChoiceRecord`: menu/name/date/optional region,image,category,photo metadata.
- `FavoriteRecord`: restaurant id/name/category/region/image/photo metadata/date.
- `RegionUsage`: name/count/lastUsed/lat/lng.

기기 JSON 저장 key (`ChoiceStore.swift:3-200`): `choiceRecords.v1`, `favoriteRecords.v1`, `regionUsage.v1`. AppStorage: `locationMode`, `manualRegionText`, `manualResolvedName`, `manualLatitude`, `manualLongitude`, `lastTopMenu`, `lunchNotifyEnabled`, `lunchHour`, `lunchMinute`, `lunchLeadMinutes`, `mapProvider`.

기본값: page home, phase idle, decision nil; launch마다 `locationMode=auto`. manual text/좌표는 유지된다. 기록은 무제한 append. 찜은 restaurant id unique toggle. decode 실패는 빈 배열, migration 없음. 저장 전 Foursquare URL과 photo metadata는 제거되므로 재실행 후 fallback이 나올 수 있다. region usage는 `현재 위치`, `지정 지역`, 공백을 저장하지 않는다. 서버 동기화/계정 없음.

## 6. 네트워크 계약

Base URL은 `Info.plist` `APIBaseURL`, 현재 `https://nasfinder.com`. 구성은 build-time plist/env로 주입하고 secret을 앱에 넣지 않는다. 비어 있거나 `REPLACE-ME`, non-HTTPS면 차단 (`APIClient.swift:5-63`).

```http
GET /api/restaurants?latitude={Double}&longitude={Double}
Accept: URLSession default
Body: none
Auth header: none
Client timeout: 15s
Automatic retry: none
Offline result cache: none
```

응답은 `RestaurantsResponse`. unknown fields는 무시. 오류:

- 설정 누락 `백엔드 주소가 설정되지 않았습니다. Info.plist의 APIBaseURL 값을 배포한 HTTPS 서버 주소로 바꿔 주세요.`
- non-2xx `서버 응답 오류({code})가 발생했습니다. 잠시 후 다시 시도해 주세요.`
- decode `서버 응답을 해석하지 못했습니다. 앱과 서버 버전이 맞는지 확인해 주세요.`
- transport/timeout `서버에 연결하지 못했습니다. 네트워크 상태를 확인해 주세요.`

저장소 `server/server.js`는 참고 서버이며 Kakao `FD6`, 거리순, page size15×최대4, unique13, upstream timeout5s, `{error,message}` 400/502/503/504/500, `Cache-Control:no-store`. 운영 NasFinder의 RedTable DB/동기화/cache는 이 앱 저장소만으로 검증되지 않았다.

사진은 HTTPS only, timeout15s. fallback이 먼저 보이며 spinner가 없다. 2xx+image decode 성공 시 overlay. Foursquare는 ephemeral/no cache, 기타는 process `NSCache`+`URLCache.shared`. fallback 매칭: 파스타/피자/양식→Side1; 국수/칼국수/국밥/탕/죽→Side2; 초밥/돈가스/돈까스/일식→Side3; 한식/비빔밥/고기/육류→Main; 나머지는 stable identity hash로 네 자산 중 하나 (`ContentView.swift:873-1056`).

## 7. 메뉴 정책

`MenuPolicy.swift:3-31`: 서버 `curatedMenus` 우선, 없으면 식당명/최종 category의 whitelist variant. canonical은 김밥, 냉면, 돈가스(돈까스), 초밥, 국밥, 설렁탕, 칼국수, 햄버거, 피자, 치킨, 떡볶이, 샤브샤브, 갈비탕, 짜장면(자장면), 쌀국수, 마라탕, 파스타, 곱창, 삼계탕, 보쌈. 카드 표시는 `첫 근거메뉴 → 최종 category → 오늘의 메뉴`.

## 8. 권한·개인정보·보안·외부 앱

- CoreLocation accuracy100m, When-In-Use, one-shot. 목적문구: `주변 음식점을 찾기 위해 현재 위치가 필요합니다. 위치는 음식점 검색 요청에만 사용되며 서버에 저장되지 않습니다.` (`LocationManager.swift`, `Info.plist:25-28`). 좌표는 GET query로 NasFinder에 전송되므로 Android 개인정보 문구도 이를 숨기면 안 된다.
- 위치 실패 `현재 위치를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.`.
- notification은 local only. 계정, login, analytics, ads SDK 없음.
- 최근/찜/region usage/settings는 기기 UserDefaults. Android는 SharedPreferences/DataStore 중 하나를 쓰되 key/수명/실패 시 empty 동작을 동일하게 한다.
- inbound scheme/universal link 없음. outbound는 지도 schemes와 사진 attribution HTTPS.
- 앱에 API secret 없음. `ITSAppUsesNonExemptEncryption=false`; Mac sandbox/outgoing network/location entitlement.
- Android 위치/알림 권한 거부 후 `설정 열기`는 이 앱의 OS permission settings로 이동한다.

## 9. 의존성과 Android 플랫폼 계약

Apple 구현은 SwiftUI, Foundation, CoreLocation, MapKit, UserNotifications, UIKit만 사용하고 외부 Swift package가 없다. Android도 새 의존성보다 플랫폼/기존 도구를 우선하되, 외형을 Material로 바꾸는 근거로 삼지 않는다.

Android 지도 intent:

- Naver/Kakao/Google 설치 여부를 package manager로 확인한다.
- 설치됨: 해당 native URI/explicit package로 연다. 브라우저 fallback 금지.
- 없음: iOS와 같은 앱 소유 dialog에서 설치 또는 설치된 다른 지도 선택. 설치는 Play Store native app, Play Store가 없을 때만 OS store URI 처리. 웹 검색 결과 화면으로 열지 않는다.
- Apple 지도는 Android에서 제공할 수 없으므로 설정 목록의 처리 정책은 제품 결정 필요. 임의 숨김은 exact parity 위반이지만 실행 불가능 항목을 활성화해서도 안 된다.

## 10. 접근성·큰 화면·큰 글자

명시 label/value/action:

- wordmark `오늘 뭐 먹지`; gear `설정`; 추천 탭 `추천 다시 고르기`; region bar `지역 다시 선택`.
- 결과 카드 role button + hint `식당 상세 보기`; heart `찜하기`/`찜 해제`.
- 사진: `{카테고리} 음식 예시 이미지`, `해당 식당 사진`, `메뉴 예시 사진, 해당 식당 사진이 아님`, `참고 이미지`; 사진 정보 `사진 정보 보기`.
- recent trash `최근 한 끼 삭제`; favorite value `선택됨`.
- map provider selected trait; copyright value `접힘`/`펼쳐짐`; reminder switch hint는 권한과 반복 알림 결과를 설명한다.
- swipe 삭제는 visible `삭제` 버튼도 제공한다.

알려진 접근성 gap: `ReferenceIconWell` 일부가 숨김 처리되지 않음, onTapGesture 카드 consolidated label 미흡, swipe custom accessibilityAction 없음, 34/40pt control 일부가 44pt 미만, explicit focus order/rotor/Reduce Motion 없음. Android가 임의 개선하면 화면/동작 패리티가 달라질 수 있으므로 공통 제품 변경으로 먼저 승인받는다.

Phone 외 width는 600 cap 중앙. iPad/Mac 실제 캡처가 아직 없으므로 tablet/desktop exact parity는 미검증이다. fixed line/height와 large text overflow도 미검증.

## 11. 애니메이션·햅틱·키보드

- custom haptic 없음. Android에서 임의 haptic 추가 금지.
- swipe open/close easeOut .18s.
- loading retry 10초 후 easeOut .2s, opacity+bottom move.
- copyright expand easeInOut .22s, chevron 180°, opacity+top move.
- sheet/dialog/control/map 시스템 애니메이션 duration은 미지정.
- keyboard Search submit, 빈 배경 tap/scroll/탭 전환 focus dismiss. 하단bar bottom fixed.
- long press 없음.

## 12. 알려진 제한·미완료·미검증

1. 추천 carousel drag는 대표님 실사용 보고상 실패. Android는 작동해야 하며 iOS도 별도 수정/재캡처 필요.
2. 최신 simulator는 source와 같은 missing-map 문구를 보였다. 지도 앱별 실제 설치·open 성공은 실기기에서 미검증이다.
3. `isOpenNow=nil`은 추천에 남는다. 휴무/영업시간 완전 제외가 아니다.
4. 네트워크 자동 retry/offline 결과 cache 없음. loading은 자동 종료되지 않는다.
5. 실제 운영 NasFinder/RedTable schema·timeout·retry·cache는 별도 서버 저장소 근거 필요.
6. 실제 지도 scheme/store fallback은 실기기에서 미검증.
7. iPad portrait/landscape/split, Mac narrow/default/wide, large text/VoiceOver/TalkBack 미검증.
8. photo info sheet, 설정 copyright expanded, 권한 denied, failed/empty, loading≥10초, swipe-open의 최신 PNG가 아직 없다.
9. 앱 display name source는 `WhattoEat`; App Store name `오늘 뭐 먹지??`와 다르다.
10. 소스 폴더는 Git repository가 아니어서 HEAD/worktree를 기록할 수 없다.

## 13. Android 수용 기준과 테스트

각 테스트는 iPhone reference PNG/상태와 Android PNG를 같은 content viewport로 정규화하고 app-owned 영역을 pixel compare한다. OS-owned mask만 제외한다. 단순 육안 ‘비슷함’ 판정은 금지한다.

1. **초기 설치**: 데이터 없음→home, auto card 강조, 하단 순서 홈/지역/추천/최근/찜, 홈 red. 재실행해도 home/auto 시작.
2. **위치 권한**: notDetermined→OS prompt; allow→현재 위치 검색; deny→denied copy와 지역 지정. 권한 설정 이동 후 복구.
3. **현 위치 재탐색**: region의 현 위치 탭은 같은 화면에서 status/주변 3개 갱신, 추천 자동 이동 금지.
4. **manual search**: `강남`, `라페스타` 검색; 빈 검색 무반응; 실패 copy; 다른 영역 탭 시 keyboard dismiss, bottom nav 고정.
5. **usage persistence**: 성공 지역을 여러 번 추천→횟수 desc/동률 recency, 3개. app kill/relaunch 후 유지.
6. **추천**: 최대13 중 first main+remaining horizontal. `false` 영업 식당 제외. loading<10, ≥10 retry, failed/denied/empty. retry stale response가 덮지 않음.
7. **carousel**: 세 card와 일부 다음 card가 보이며 drag로 최대 12개 모두 도달. tap과 heart가 scroll을 막지 않음.
8. **photo**: verified/category/fallback labels와 badges; remote timeout에서도 fallback 유지; 동일 screen에서 data가 주는 서로 다른 URL은 서로 다른 이미지를 보존.
9. **favorite**: result heart toggle→찜 list 지역 group. 찜 heart와 swipe로 제거. kill/relaunch persistence.
10. **decision**: card tap→decision. map card tap→native map only. 미설치→설치/다른 지도/취소; 다른 선택은 default 저장+즉시 open. `오늘은 여기로`→record first→map; failure여도 record 유지.
11. **recent**: 지역 group, newest order, photo/문구/date. trash/swipe immediate delete. kill/relaunch persistence.
12. **settings**: 4 provider ordering/selected; reminder off/on/time/lead; denied alert; copyright collapsed/expanded; setting persists.
13. **back/cancel**: decision close/back→result; settings close/back→origin; root tab back policy를 승인된 계약대로; dialog cancel no mutation.
14. **layout**: 402×874pt-equivalent phone에서 rail28, max600, bottom reserved72, no vertical clipping. tablet widths keep centered600, no Android-specific reflow.
15. **accessibility**: exact labels/roles/selected/value/actions; TalkBack can reach visible delete/heart/map; OS-owned permission screens excluded.

## 14. 구현 근거 색인과 저장소 상태

- 라우팅/화면/gesture/accessibility: `WhattoEat/ContentView.swift`.
- 색/typography/card/button: `WhattoEat/Theme.swift`.
- data/storage: `WhattoEat/Models.swift`, `ChoiceStore.swift`.
- location: `WhattoEat/LocationManager.swift`.
- API: `WhattoEat/APIClient.swift`.
- menu: `WhattoEat/MenuPolicy.swift`.
- app adaptation: `WhattoEat/WhattoEatApp.swift`.
- permissions/schemes/orientation/base URL: `WhattoEat/Info.plist`, `WhattoEat/WhattoEat.entitlements`.
- assets: `WhattoEat/Assets.xcassets/`.
- project version/platform: `WhattoEat.xcodeproj/project.pbxproj`.
- reference server only: `server/server.js`.

Git: `git rev-parse`와 `git status`가 모두 실패했다. `HEAD=null`, worktree status `unavailable`, 이유 `source directory is not a Git repository`. Git을 초기화하거나 source를 변경하지 않았다.
