# WhattoEat situation filter and map search synchronization run

## Run metadata

- Start: 2026-08-26 15:35:04 KST
- Monotonic start: `164828203272416`
- End: 2026-08-26 15:54:40 KST
- Monotonic end: `166004113919958`
- Elapsed: 19 minutes 35.911 seconds
- Resolved registry group: `whattoeat`
- Canonical contract: `/Users/armsone/git/WhattoEat /.sync/product-contract.yaml`
- Apple member: `/Users/armsone/git/WhattoEat ` (`237d3a5`, dirty; verified baseline retained)
- Android member: `/Users/armsone/git/WhattoEat-Android` (`617518f`, dirty; verified baseline retained)
- Scope: seven-choice situation filtering, truthful empty-match fallback, and zero-paid-API configured-map search
- Publication boundary: no install, commit, push, deployment, release, or NasFinder work was performed.

Both members are dirty with uncommitted in-scope work, so the registry's `last_verified_head` values were not advanced. The prior `last_sync_scope` was also retained because the new visual and runtime parity rows remain open.

## Actual synchronization table

| Capability | Canonical/source member | Apple implementation | Android implementation | Technical/runtime evidence | Matchup evidence | Final verdict |
|---|---|---|---|---|---|---|
| Seven optional situation choices | Apple canonical contract | Implemented in `MealSituation` and recommendation-results picker | Implemented on Home and Result with the same seven labels | Apple iPhone and Mac builds passed; Android focused source tests and build passed | No new paired captures; Android Home shortcut has no Apple Home counterpart | source-only |
| Local persistence | Shared product outcome | `@AppStorage("mealSituation")` | `SharedPreferences("situationFilter")` | Android `ChoiceStoreTest` round trip passed; Apple source-backed only | Not a visual row | source-only |
| Deterministic verified-menu filtering | Apple `MenuPolicy` truthfulness rule | Server-curated menus plus exact recognized menu name/final category | Reconciled to the same verified-menu-only rule; broad categories no longer count as situation evidence | Android situation/menu-policy tests passed; Apple has no test target | No runtime behavior trace or shared raw fixture | source-only |
| Empty-match fallback | Canonical contract | Displays full eligible list and disclosure | Displays full eligible pool and disclosure | Source paths present on both; Android unit test covers empty filtered pool | No positive/negative runtime fallback fixtures and no paired capture | source-only |
| Query composition | Canonical contract | Percent-encoded `menu + region` | Reconciled to percent-encoded `menu + region`; placeholder region labels can be omitted | Android provider URI/query tests passed; Apple source-backed only | External-app deep links not executed | source-only |
| Configured map-provider launch | Apple map action contract | Apple/Naver/Kakao/Google platform schemes | Naver/Kakao/Google Android intents | Android URI tests passed; Apple and Android builds passed | No behavior trace across provider apps | source-only |
| Missing provider and Apple-on-Android handling | Platform adaptation | Existing missing-provider picker; Apple Maps is available | Existing missing-map dialog/provider picker; Apple Maps failure dialog | Source path inspected | No runtime missing-provider or Apple fallback trace | intentional difference (capability source-only) |
| Map-search CTA presentation | Apple `DecisionView` | CTA appears on the decision route | CTA appears on recommendation results | Both compile | No paired capture; route placement differs | source-only |

The shared algorithms, choices, persistence outcome, fallback truthfulness, and query order are aligned at source level. The group is not declared synchronized because Matchup and runtime evidence gates remain open.

## Contract reconciliation

The canonical Apple contract now contains `situation_category_filter` and `no_paid_api_map_search` while preserving `direct_updates`, `bottom_navigation`, and their history. Android's local contract summary was corrected to agree with the canonical outcomes:

- Situation matching uses curated or exactly recognized menu evidence; a broad category alone cannot force a situation.
- Search query order is `menu + region` on both members.
- The same seven labels, local persistence, truthful fallback, no-paid-API boundary, and no-review/scraping boundary are specified.
- Native URL schemes and Apple Maps unavailability on Android are documented adaptations.

## Per-project validation

| Project | Static validation | Tests | Build | Runtime/rendering |
|---|---|---|---|---|
| Apple | `git diff --check` passed; canonical YAML parsed | No test target exists | iPhone 17 / iOS 26.5 Debug build passed; Mac Catalyst Debug build passed with Apple Development signing preserved | Physical-device install, external-map deep links, and actual iPhone/iPad/Mac rendering were not run |
| Android | `git diff --check` passed; local YAML and ledger JSON parsed | 31 focused JVM tests passed across situation filter, map query/URI, recommendation pool, menu policy, and persistence | `assembleDebug` passed; affected Compose instrumentation APK compiled | Instrumentation was not executed; phone/tablet/TV rendering, D-pad flow, and external-map deep links were not run |

## Matchup ledger gate

- Durable ledger: `/Users/armsone/git/WhattoEat-Android/.parity/ledger.json`
- Apple repository ledger: absent; Android remains the registered Matchup target ledger.
- Existing three bottom-navigation rows and evidence/history were preserved as `matched`.
- Six affected rows are honestly recorded as `implemented_source_only` because no new capture pair or behavior/fallback trace exists.
- Structural validation: exit `0`, `9` rows total, `3` complete, `6` open.
- Completion gate: exit `3`, blocked by the six source-only rows.

Open rows:

1. `home_situation_filter.chips.rendering`
2. `result_situation_filter.chips.rendering`
3. `result_situation_filter.fallback_notice`
4. `recommendation_pool.situation_filtering.action`
5. `result_map_search.recommended_menu.action`
6. `result_map_search.cta.rendering`

## Errors and resolutions

| Stage | Observed error | Cause | Corrective action | Retry/result | Open? |
|---|---|---|---|---|---|
| Initial Android delegation | Gemini work timed out after leaving a partial draft | Agent response deadline elapsed before completion | Mencius inherited the existing diff, preserved it, completed and verified the Android work | Android focused tests/build passed | No |
| Apple delegation | Claude refused the repository path as unsafe | The canonical Apple path intentionally ends with a space and required exact safe handling | Newton inherited the Apple task using the exact quoted path | Apple static checks and two platform builds passed | No |
| First ledger validation | Validator exit `2`; three draft rows used invalid status `implemented_unverified` | Draft status was outside the ledger schema | Reclassified affected rows as `implemented_source_only`, expanded atomic rows, and preserved existing rows/history | Structural validator exit `0` | No |
| Cross-platform algorithm audit | Android accepted broad categories and a larger keyword set than Apple | Initial implementations encoded different evidence rules | Reconciled Android to Apple's verified-menu-only policy | First focused rerun exposed three stale expectations; tests were corrected to use verified/curated evidence and the final rerun passed 31/31 | No |
| First post-reconciliation Android test run | Three situation tests failed | Fixtures expected unverified aliases/categories that the canonical truthfulness rule now excludes | Changed fixtures to canonical or curated menu evidence without weakening production checks | Final focused test run and `assembleDebug` passed | No |
| Final Matchup gate | Validator exit `3` with six open rows | Required paired captures, behavior traces, and fallback fixtures were not collected in this scope | Kept rows open and reported exact missing evidence | Still open by design | Yes |

## Intentional platform differences

| Difference | Constraint | Closest equivalent and user impact | Review trigger |
|---|---|---|---|
| Apple Maps on Android | Apple provides no supported Android Maps app or native Android intent target | Android shows the existing Apple Maps failure explanation and lets the user choose an installed provider | Reassess if Apple publishes an official Android target |
| Native map URL schemes | Each OS/provider exposes different supported schemes | Both send the same menu-and-region meaning through the native scheme available on that platform | Reassess when a provider deprecates or changes a scheme |
| Google location context | Android can include current coordinates in `geo:` while Apple uses its supported Google URL scheme | Query meaning remains menu plus region; provider result ranking can differ | Review with provider API/scheme changes |

The different placement of situation controls and the map-search CTA is not classified as intentional: it is an open rendered/interaction parity item without OS justification.

## Unfinished items

| Capability/platform | Complete now | Missing evidence/action | Why incomplete | Exact next safe action |
|---|---|---|---|---|
| Situation controls, Apple/Android | Source implementation and shared contract | Paired iPhone/Android result captures; Android Home-state capture; iPad/Mac/tablet/TV layouts where rendering differs | No device or simulator capture was authorized/run in this handoff | Run deterministic catalogs with identical restaurant fixture, locale, text scale, and selected states; compare and update the three visual rows |
| Situation fallback, Apple/Android | Source fallback and Android unit coverage | Positive filtered result, forced-empty negative fixture, visible disclosure, persistence/relaunch trace | Fallback-capable rows require both positive and negative runtime evidence | Execute shared fixed fixtures on both platforms and save trace/capture hashes |
| Map search, Apple/Android | URL construction and missing-provider source paths | Runtime trace for Apple/Naver/Kakao/Google, missing app, open failure, and Apple-on-Android | External apps were not launched | On test devices with controlled provider availability, record query, transition, failure recovery, and side effects without fetching ratings/reviews |
| CTA route parity | Both platforms expose an explanatory action | Product decision on common route/placement, followed by paired captures | Android uses Result; Apple uses Decision, with no OS constraint | Choose one shared product route, minimally align the other member, rebuild, and recapture |
| Google TV D-pad and adaptive layouts | Android source remains Compose-clickable and buildable | Runtime focus traversal and responsive rendering evidence | No TV/device execution | Run the existing instrumentation catalog on a controlled TV/device only when device work is authorized |

## Participants and usage evidence

| Participant | Assignment | Outcome | Usage evidence |
|---|---|---|---|
| Gemini | Initial Android draft | Partial diff produced before timeout | Per-agent remaining usage unavailable |
| Newton | Apple implementation and build verification | Two Apple source files changed; iPhone and Mac Catalyst builds passed | Per-agent remaining usage unavailable |
| Mencius | Android completion, cross-platform contract/algorithm reconciliation, ledger validation, report | Source contract aligned; Android tests/build passed; ledger honestly left open | Per-agent remaining usage unavailable |
| TM | Requirements, delegation, integration and completion decision | Final integration pending this report | Per-agent remaining usage unavailable |

No provider-specific usage figures or per-agent attribution were exposed to this run, so none were invented.
