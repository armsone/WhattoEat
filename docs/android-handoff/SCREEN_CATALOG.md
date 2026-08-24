# 최신 iPhone 화면 카탈로그

기준 설치본: `0.2.1` (`202608232316`)  
캡처 환경: Apple Simulator, iPhone 17 (`iPhone18,3`), iOS 26.5, portrait, light, 3×  
원본: 무손실 PNG 1206×2622px = 402×874pt @3×. 픽셀 origin은 좌상단이다.

모든 파일은 `/Users/armsone/git/WhattoEat /docs/android-handoff/screenshots/`에 있다. 상태바, Dynamic Island, home indicator와 system keyboard는 OS 소유다. 나머지 앱 surface는 exact-parity 대상이다. 세부 SHA-256/bytes는 `parity-manifest.json`이 정본이다.

## 캡처 목록

| 파일 | route/state | 진입 절차 | 확인 사항 |
|---|---|---|---|
| `home-current.png` | home/default-auto | 최신 앱 launch | 최신 문구 `메뉴 추천 받기`, 하단 순서 홈·지역·추천·최근·찜 |
| `region-current.png` | region/auto-resolved | 하단 `지역` | 현 위치 상태, 내 주변 3, 자주 찾는 3, 검색 field |
| `region-search-keyboard-current.png` | region/search-focused | region 검색 field 탭 | system Korean keyboard, 하단바 bottom 고정 |
| `recommendation-loading-current.png` | result/loading<10s | 하단 `추천` 즉시 | 진행 상태. 캡처 시 원격 photo/background state가 남을 수 있으므로 spinner/text는 source와 함께 판정 |
| `recommendation-results-current.png` | result/populated | 추천 완료 대기 | main+horizontal cards, 현재 데이터/사진 |
| `decision-current.png` | decision/before-record | main card 탭 | hero, map 244pt, 초기 CTA |
| `map-app-missing-alert-current.png` | decision/map-provider-missing | Naver 미설치 simulator에서 `오늘은 여기로` | 설치/다른 지도 선택 안내 dialog |
| `decision-completed-current.png` | decision/recorded | 초기 CTA 탭 후 설치 안내 취소 | `최근 한 끼에 담았어요` |
| `recent-empty-current.png` | history/empty | 저장 전 하단 `최근` | empty image/copy |
| `recent-populated-current.png` | history/populated | 결정 저장 후 하단 `최근` | 지역 group, 한 record, delete |
| `favorites-empty-current.png` | favorites/empty | 찜이 없는 상태에서 하단 `찜` | empty image/copy |
| `favorites-populated-current.png` | favorites/populated | 추천 main heart→하단 `찜` | 지역 group, 한 record, heart remove |
| `settings-current.png` | settings/naver+reminder-on | 찜 화면 gear | 지도 4종, Naver selected, reminder on |

## 최신성 판단

기존 `/docs/android-port/screenshots/` 9장은 2026-08-23 17:58~18:00 KST에 만들어졌고 현재 source는 그 뒤 수정됐다. `home.png`의 `점심 추천 받기`는 현재 `메뉴 추천 받기`와 다르고, 기존 `recommendation-loading.png`는 실제로 결과 카드 상태다. 따라서 기존 9장은 현재 baseline manifest에서 제외했다.

이번 13장은 현재 source를 별도 DerivedData에서 빌드해 설치된 `0.2.1 (202608232316)`, API base `https://nasfinder.com`을 직접 확인한 뒤 2026-08-24 00:27~00:31 KST에 재캡처했다. 이전 `0.1.4` 설치본으로 만든 중간 캡처는 같은 파일명으로 모두 교체했다.

## 아직 필요한 상태 캡처

아래는 source로 계약을 확인했으나 최신 PNG가 없어 `미검증`이다. Android 시각 완료 판정 전에 iOS reference capture를 추가해야 한다.

- region manual selected, 내 주변 empty, 자주 찾기 empty, search failure.
- recommendation loading ≥10s + `다시 고르기`, failed, denied, empty.
- recommendation heart selected, carousel을 좌로 민 뒤 마지막 카드 상태.
- photo fallback/loading/loaded/failed, restaurant-verified와 category-example, 사진 정보 sheet.
- decision 영업정보 alert, 다른 지도 선택, Apple failure.
- recent/favorites swipe delete reveal.
- settings permission 3상태, reminder off, 각 지도 selected, copyright expanded, notification denied alert.
- iPad portrait/landscape/split, Mac default/narrow/wide, large Dynamic Type/VoiceOver.

## 캡처 재현 규칙

1. source와 설치본 version/build가 동일한지 먼저 확인한다.
2. iPhone 17 / iOS 26.5 / portrait / light / 3×를 사용한다.
3. system clock과 현재 위치/식당 data는 동적 값으로 manifest에 상태를 남긴다. app-owned geometry 비교에서 텍스트 내용만 무조건 mask하지 않는다.
4. 캡처는 `xcrun simctl io <UDID> screenshot <absolute PNG>`로 무손실 저장한다.
5. 캡처 직후 width/height/bytes/SHA-256과 정확한 진입 절차를 manifest에 기록한다.
6. Android 비교 시 app-owned 영역은 pixel diff, OS-owned region만 명시 mask한다.
