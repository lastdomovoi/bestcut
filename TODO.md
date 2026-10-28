# TODO

Local index of GitHub Issues. Each task appears once; the linked issue retains its full requirements, examples and acceptance criteria.
Partial implementations remain open until the complete scope passes acceptance.

## Critical (security, bugs)

- [ ] [#2: Distinguish unknown audio language from confirmed absence of Russian](https://github.com/lastdomovoi/bestcut/issues/2) — present/absent/unknown status is implemented (no penalty and no deletion advice for untagged tracks, region/ISO aliases); spoken-language identification and saved user overrides remain open.
- [ ] [#3: --recommend-delete conflates unparsed TV episodes and suggests deleting non-duplicates](https://github.com/lastdomovoi/bestcut/issues/3) — Russian `Сезон №N. Серия №NNN` and `sXXeYY.(NNN)` absolute numbers, safe parent-directory parsing and a review-instead-of-delete guard for unparsed groups are implemented; `Interny-13.(01.seriya)` season parsing and the 278-episode fixture remain open.
- [ ] [#5: Use episode-duration sequences to detect wrong order, missing episodes and filename shifts](https://github.com/lastdomovoi/bestcut/issues/5) — Verify neighboring episode/content matches; duration alone must not authorize renaming or deletion.
- [ ] [#7: Make scoring black-bar-aware and compare real active-image quality](https://github.com/lastdomovoi/bestcut/issues/7) — Resolution and BPP now use the stable active picture (6 scenes, outlier-robust, uncertain when scenes disagree) with pillarbox/letterbox/dark-scene/crop-free tests; aligned detail/sharpness/artifact comparison between releases is still open.
- [ ] [#8: Compare synchronized video sequences before classifying duplicates or recommending deletion](https://github.com/lastdomovoi/bestcut/issues/8) — Establish decoded content and timeline identity before duplicate classification or deletion advice.
- [ ] [#10: Detect different masters and color grading before recommending deletion](https://github.com/lastdomovoi/bestcut/issues/10) — Compare aligned color/detail evidence and flag missing or contradictory color metadata for review.
- [ ] [#11: Detect and penalize advertising insertions when comparing cuts](https://github.com/lastdomovoi/bestcut/issues/11) — Locate replaced, inserted and overlaid ads; preserve the clean version when the direction is uncertain.
- [ ] [#12: Make full corruption detection mandatory and reject damaged files](https://github.com/lastdomovoi/bestcut/issues/12) — Full decode of video and all audio with per-stream attribution and error span; damaged files never win and are never offered for deletion without a verified-clean replacement; `--recommend-delete` forces the full check. Freeze/stall and storage-read-error fixtures remain open.
- [ ] [#13: Inspect every audio track thoroughly before scoring or deletion advice](https://github.com/lastdomovoi/bestcut/issues/13) — Multi-scene energy plus inter-channel correlation (identical, phase-inverted, near-duplicate, matrixed) with confidence and review notes; fixes the L/R-level "mono" misdetection. Decode-error and timestamp-discontinuity checks per track, and the Moulin Rouge! pair, remain open.
- [ ] [#14: Detect redundant same-cut re-encodes and measure marginal quality per byte](https://github.com/lastdomovoi/bestcut/issues/14) — Measure useful quality per byte only after same-cut identity, track uniqueness and full integrity checks.

## Improvements

- [ ] [#4: Add fast series-directory discovery across media library roots](https://github.com/lastdomovoi/bestcut/issues/4) — Find matching release directories without probing or traversing unrelated media files.
- [ ] [#15: 3D video comparison](https://github.com/lastdomovoi/bestcut/issues/15) — Per-eye geometry/scoring and MKV `left_right`/`top_bottom` detection (stream side data) with regression tests are implemented; MVC fixtures remain open.
- [ ] [#16: Audio quality analysis](https://github.com/lastdomovoi/bestcut/issues/16) — Noise/SNR, clipping and band-limiting are implemented; click/crackle detection and remaining spectral coverage stay open.
- [ ] [#17: Digital remaster detection](https://github.com/lastdomovoi/bestcut/issues/17) — Measured native-detail/HDR heuristic is implemented; broader remaster certainty and SDR coverage remain open. Names/tags are not proof.

## Technical debt

## Future ideas

## Done

Completed tasks: [monthly archive](docs/todo-archive/2026-10.md).
