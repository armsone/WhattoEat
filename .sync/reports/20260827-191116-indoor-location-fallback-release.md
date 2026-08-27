# Indoor location fallback · 0.4.2 release

- Window: 2026-08-27 18:40–19:11 KST
- Sync group: `whattoeat-mobile`
- Apple source/release: `681b9a5`, version 0.4.2, build 202608271840
- Android source/release: `d666314`, version 0.4.2, versionCode 343840
- Site source/deployment: `defed60`, Sites version 232

## Product contract

Both phone implementations use the native OS location stack, which may fuse GPS, Wi-Fi, and cellular signals. A fresh request has a 12-second ceiling. On failure or timeout, an OS-cached location is accepted only when its timestamp is no more than five minutes old and its accuracy is valid. When neither path succeeds, manual region selection is the primary recovery action; retry remains available, and permission failures additionally offer app settings.

No private Wi-Fi scanning, new location SDK, analytics dependency, or app-owned persistent coordinate cache was added.

## Platform evidence

| Platform | Evidence | Result |
| --- | --- | --- |
| iPhone/iPad | Release simulator build | Passed |
| iPhone | Signed device archive and codesign validation | Passed |
| iPhone 17 Pro | Replace-install, launch, installed version query | 0.4.2 (202608271840) |
| Android | 53 debug unit tests | Passed |
| Android | Release assemble, zipalign, APK signature v2/v3 verification | Passed |
| Android SM-F968N | Replace-install debug build, cold launch, installed version query | 0.4.2 (343840) |

## Distribution

- iOS build uploaded and processed in App Store Connect.
- Existing internal group preserved.
- Build added to external `Public Beta`, tester notification enabled, truthful What to Test saved, and Beta App Review submitted.
- Stable public link remains `https://testflight.apple.com/join/A444RsAc`; 0.4.2 is waiting for Apple external beta review, so the link continues serving the previously approved build until approval.
- Android signed APK published at `https://github.com/armsone/WhattoEat-Android/releases/tag/v0.4.2`.
- NasFinder.com updated with the verified Android asset, 0.4.2 versions/builds, indoor recovery description, and the accurate TestFlight review state; production deployment succeeded and the live page was checked.

## Matchup / remaining evidence

Functional parity is implemented in source and Android policy tests. The affected recovery state has no new paired iOS/Android runtime screenshots, so visual parity is intentionally not declared. The Android ledger row remains `implemented_source_only`. Tablet, Google TV remote, TalkBack/large text, and a forced no-cache timeout trace remain follow-up runtime evidence rather than release claims.

## Error and resolution log

- Claude could not safely edit the iOS repository path containing a trailing space; work was produced in a temporary snapshot and integrated into the real checkout.
- Initial Android tests exposed an approximate-permission provider bug and stale 24-hour test assumptions; GPS is now excluded without fine permission and both platforms use the common five-minute window.
- Site tests contained stale WhattoEat and S.tand release assertions; current published values were updated and all 52 tests passed.
- Site lint had two pre-existing same-page anchor errors; the intended native-anchor exceptions were completed. Lint now has warnings only.

## Completion boundary

Source, builds, phone installation, Git backup, Android public release, iOS TestFlight submission, and website publication are complete. Apple review approval is asynchronous and is not represented as complete.
