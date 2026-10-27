# TODO

Local index of GitHub Issues. Each task appears once; the linked issue retains its full requirements, examples and acceptance criteria.
Partial implementations remain open until the complete scope passes acceptance.

## Critical (security, bugs)

- [ ] [#1: Handle legacy non-UTF-8 ffmpeg/ffprobe output without dropping media files](https://github.com/lastdomovoi/bestcut/issues/1) — Keep raw evidence and trustworthy stream data when legacy ffmpeg/ffprobe text cannot be decoded.
- [ ] [#2: Distinguish unknown audio language from confirmed absence of Russian](https://github.com/lastdomovoi/bestcut/issues/2) — Treat unknown audio language separately from confirmed absence; preserve review when language evidence is missing.
- [ ] [#3: --recommend-delete conflates unparsed TV episodes and suggests deleting non-duplicates](https://github.com/lastdomovoi/bestcut/issues/3) — Finish Russian/absolute episode parsing and safe unparsed-group handling, including parent-directory edge cases.
- [ ] [#5: Use episode-duration sequences to detect wrong order, missing episodes and filename shifts](https://github.com/lastdomovoi/bestcut/issues/5) — Verify neighboring episode/content matches; duration alone must not authorize renaming or deletion.
- [ ] [#6: Correct codec-specific video-level parsing](https://github.com/lastdomovoi/bestcut/issues/6) — Normalize HEVC and AVC levels separately; preserve real compatibility warnings.
- [ ] [#7: Make scoring black-bar-aware and compare real active-image quality](https://github.com/lastdomovoi/bestcut/issues/7) — Score stable active-image geometry and aligned visual quality instead of encoded black pixels.
- [ ] [#8: Compare synchronized video sequences before classifying duplicates or recommending deletion](https://github.com/lastdomovoi/bestcut/issues/8) — Establish decoded content and timeline identity before duplicate classification or deletion advice.
- [ ] [#9: Determine actual FPS from timestamps and frame count instead of trusting `r_frame_rate`](https://github.com/lastdomovoi/bestcut/issues/9) — Separate nominal FPS from measured playback cadence and malformed timestamps.
- [ ] [#10: Detect different masters and color grading before recommending deletion](https://github.com/lastdomovoi/bestcut/issues/10) — Compare aligned color/detail evidence and flag missing or contradictory color metadata for review.
- [ ] [#11: Detect and penalize advertising insertions when comparing cuts](https://github.com/lastdomovoi/bestcut/issues/11) — Locate replaced, inserted and overlaid ads; preserve the clean version when the direction is uncertain.
- [ ] [#12: Make full corruption detection mandatory and reject damaged files](https://github.com/lastdomovoi/bestcut/issues/12) — Require full-stream validation; damaged files cannot win or replace an unverified sole copy.
- [ ] [#13: Inspect every audio track thoroughly before scoring or deletion advice](https://github.com/lastdomovoi/bestcut/issues/13) — Inspect every track across multiple scenes, including channel correlation, language and decode errors.
- [ ] [#14: Detect redundant same-cut re-encodes and measure marginal quality per byte](https://github.com/lastdomovoi/bestcut/issues/14) — Measure useful quality per byte only after same-cut identity, track uniqueness and full integrity checks.

## Improvements

- [ ] [#4: Add fast series-directory discovery across media library roots](https://github.com/lastdomovoi/bestcut/issues/4) — Find matching release directories without probing or traversing unrelated media files.
- [ ] [#15: 3D video comparison](https://github.com/lastdomovoi/bestcut/issues/15) — Per-eye geometry/scoring is implemented; MKV stereo metadata still fails detection and needs regression coverage.
- [ ] [#16: Audio quality analysis](https://github.com/lastdomovoi/bestcut/issues/16) — Noise/SNR, clipping and band-limiting are implemented; click/crackle detection and remaining spectral coverage stay open.
- [ ] [#17: Digital remaster detection](https://github.com/lastdomovoi/bestcut/issues/17) — Measured native-detail/HDR heuristic is implemented; broader remaster certainty and SDR coverage remain open. Names/tags are not proof.

## Technical debt

## Future ideas

## Done

Completed tasks: [monthly archive](docs/todo-archive/2026-10.md).
