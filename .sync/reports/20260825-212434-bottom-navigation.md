# WhattoEat 추천 하단 내비게이션 동기화 보고

- 시작: 2026-08-25 21:06:10 KST
- 종료: 2026-08-25 21:24:34 KST
- 경과: 18분 24초
- 그룹: `whattoeat` — Apple `/Users/armsone/git/WhattoEat `, Android `/Users/armsone/git/WhattoEat-Android`
- 공통 후보: 제품 버전 `0.4.1`, 빌드 `202608252106`, Android 내부 코드 `341106`
- 실제 참여: TM(Codex) 요구 확정·검증·통합, S(Codex Spark) Apple 버전 메타데이터, G(Gemini) Android 구현·검증

## 실제 동기화 표

| 기능 | 계약 | Apple | Android | 판정 |
|---|---|---|---|---|
| 추천 중앙 탭 아이콘 | 앱 아이콘에서 가져온 투명 배경의 컬러 점심 가방 | 30pt 이미지 | 38.25dp 동일 원본 이미지 | 일치 |
| 미선택 상태 | 흰색→아이보리 원, 중립 그림자, 흰색 `추천` | iPhone·iPad·Mac Catalyst 확인 | SM-F968N 실기기 확인 | 일치 |
| 선택 상태 | 배경·글자는 그대로, 아래 빨간 막대만 표시 | iPhone·iPad·Mac Catalyst 확인 | SM-F968N 실기기 확인 | 일치 |
| 동작·접근성 | 탭하면 새 추천, `추천 다시 고르기`, 선택 의미 노출 | 기존 동작 유지 | 연결 계측 테스트 통과 | 일치 |

## 검증 결과

| 프로젝트 | 검증 |
|---|---|
| Apple | iPhone 17 Debug, iPad A16 Debug, Mac Catalyst Debug 빌드 성공; iPhone/iPad 결정적 캡처 확인; iPhone 17 Pro Release 빌드·데이터 유지 설치·실행 성공 |
| Android | `testDebugUnitTest`, `assembleDebug`, SM-F968N API 36 연결 계측 7/7 성공; 데이터 유지 설치·실행 및 12개 결정적 상태 캡처 성공 |
| Matchup | `.parity/ledger.json` 3행 모두 `matched`; 엄격 게이트 통과 |

## 증거

- Apple iPhone: `.sync/evidence/0.4.1/ios-iphone17/`
- Apple iPad: `.sync/evidence/0.4.1/ios-ipad-a16/`
- Android 실기기: `/Users/armsone/git/WhattoEat-Android/.parity/evidence/0.4.1/android-sm-f968n/`
- 원자 장부: `/Users/armsone/git/WhattoEat-Android/.parity/ledger.json`

## 오류와 해결

| 단계 | 오류 | 원인 | 조치 | 결과 |
|---|---|---|---|---|
| Apple 위임 편집 | 대상 경로 접근 거부 | 저장소 이름 끝 공백을 외부 편집기가 안전하게 처리하지 못함 | TM이 승인된 최소 diff를 직접 적용 | Apple 빌드·실행 성공 |
| Android 기기 확인 | 기본 `adb` 명령 미발견 | SDK 도구가 셸 PATH 밖에 있음 | SDK의 명시 경로 사용 | 실기기 테스트·설치 성공 |
| Android 계측 준비 | 테스트 서비스 `appops` 경고 | 테스트 서비스 UID를 찾지 못한 비치명 준비 경고 | 테스트 결과와 앱 동작을 별도 확인 | 7개 테스트 모두 성공 |

## 사용량 변화(남은 기준)

| 작업자 | 시작 | 종료 | 변화 |
|---|---:|---:|---:|
| Claude 주간 | 71% | 71% | 0%p |
| Claude Fable | 43% | 43% | 0%p |
| Claude 5시간 | 65% | 63% | -2%p |
| Gemini 주간 | 60.13% | 59.61% | -0.53%p |
| Gemini 5시간 | 100% | 96.84% | -3.16%p |
| Codex 주간 | 57% | 57% | 0%p |
| Codex Spark | 96% | 95% | -1%p |
| Codex 크레딧 | 26389.711161 | 26389.711161 | 0 |

종료 측정은 모두 `fresh=true`였다.
