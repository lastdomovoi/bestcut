# TODO

Local index of GitHub Issues. Each task appears once; the linked issue retains its full requirements, examples and acceptance criteria.
Partial implementations remain open until the complete scope passes acceptance.

## Critical (security, bugs)

- [ ] [#2: Distinguish unknown audio language from confirmed absence of Russian](https://github.com/lastdomovoi/bestcut/issues/2) — present/absent/unknown status, tag or saved confirmation as proof (`--set-language FILE TRACK LANG`, validated, atomic store), title only a hint, output shows `rus (tag)` / `unknown (untagged)` / `unknown (title suggests rus)`; unknown blocks deletion advice. Open: spoken-language identification from speech samples.
- [ ] [#3: --recommend-delete conflates unparsed TV episodes and suggests deleting non-duplicates](https://github.com/lastdomovoi/bestcut/issues/3) — All three filename formats parse; season offsets learned from `sXXeYY.(NNN)` place `Interny-13.(01.seriya)` on the same absolute number; a 278-episode fixture resolves to 278 identities with 3 files each; unparsed groups and ambiguous episodes go to review. Open: the real library acceptance run.
- [ ] [#5: Use episode-duration sequences to detect wrong order, missing episodes and filename shifts](https://github.com/lastdomovoi/bestcut/issues/5) — Duration screening (large delta, better-fitting neighbour, duplicate durations) plus content verification maps shifted, swapped, duplicated, partial and unmatched files, reports gaps and persistent offsets, and blocks deletion advice for ambiguous episodes; nothing is renamed. Open: the real Интерны workset and audio-side verification.
- [ ] [#7: Make scoring black-bar-aware and compare real active-image quality](https://github.com/lastdomovoi/bestcut/issues/7) — Resolution and BPP now use the stable active picture (6 scenes, outlier-robust, uncertain when scenes disagree) with pillarbox/letterbox/dark-scene/crop-free tests; aligned detail/sharpness/artifact comparison between releases is still open.
- [ ] [#8: Compare synchronized video sequences before classifying duplicates or recommending deletion](https://github.com/lastdomovoi/bestcut/issues/8) — `sequence.py` aligns timelines (global + piecewise offsets, inserted/removed/replaced/glitch stretches), takes SSIM/PSNR at refined checkpoints and classifies byte-identical / payload-identical / decoded-equivalent / same-cut re-encode / different cut / different content; `--recommend-delete` now requires it. Open: frame-count/VFR-aware alignment, reordered/duplicated frame detection, the Running Man 714 acceptance pair.
- [ ] [#10: Detect different masters and color grading before recommending deletion](https://github.com/lastdomovoi/bestcut/issues/10) — Stable colour/contrast differences over aligned scenes and missing or contradictory range/matrix/transfer/primaries tags force review instead of deletion advice. Open: gamut/transfer plausibility checks, gamma estimate, the Moulin Rouge! acceptance pair.
- [ ] [#11: Detect and penalize advertising insertions when comparing cuts](https://github.com/lastdomovoi/bestcut/issues/11) — Inserted/extra segments, equal-length replaced blocks, short glitches and persistent overlays are located with timestamps; an established overlay carrier is penalized and never kept over a clean copy; ambiguous direction requires review. Open: audio-side divergences, ad vs. legitimate-content classification, representative aligned samples.
- [ ] [#12: Make full corruption detection mandatory and reject damaged files](https://github.com/lastdomovoi/bestcut/issues/12) — Full decode of video and every audio track with per-stream/track attribution and error span, frozen-video detection (warning, with stalls closed at end of file), damaged files never win or get deleted without a verified-clean replacement; `--recommend-delete` forces the full check. Open: storage-read-error fixture, freeze-vs-corruption correlation.
- [ ] [#13: Inspect every audio track thoroughly before scoring or deletion advice](https://github.com/lastdomovoi/bestcut/issues/13) — Multi-scene energy plus correlation (identical, phase-inverted, near-duplicate, matrixed), per-track decode errors and timestamp discontinuities (parallel, tolerant of missing packet durations). Open: the Moulin Rouge! pair, never deleting the only preferred-language track on one sample.
- [ ] [#14: Detect redundant same-cut re-encodes and measure marginal quality per byte](https://github.com/lastdomovoi/bestcut/issues/14) — Aligned SSIM/PSNR plus detail (Laplacian) ratio, size ratio, geometry, identical/changed streams and unique tracks produce redundant-larger / better-larger / equivalent verdicts; the compact copy is kept over a transparent larger one. Open: noise/artifact evidence, VMAF/XPSNR, the Все реки текут acceptance pair.

## Improvements

- [ ] [#4: Add fast series-directory discovery across media library roots](https://github.com/lastdomovoi/bestcut/issues/4) — `--find-series NAME [--alias A] [--json] ROOT...` lists matching directories (whole-word, Unicode-normalized, symlink-aware, pruned at matches, depth-limited) without ffprobe; missing/unreadable roots are reported apart from no match. Open: acceptance run on the real shares.
- [ ] [#15: 3D video comparison](https://github.com/lastdomovoi/bestcut/issues/15) — Per-eye geometry/scoring, MKV `left_right`/`top_bottom`, explicit 2D, MVC (dependent-view "Stereo High"/"Multiview High" profile) and filename false positives are implemented with regression tests. Open: a real MVC sample.
- [ ] [#16: Audio quality analysis](https://github.com/lastdomovoi/bestcut/issues/16) — Noise floor/SNR, clipping, band-limiting with a bandwidth estimate (steep multi-band probes) and click/crackle detection (isolated spikes, periodic and transient content excluded) are implemented. Open: acceptance on real vinyl/tape material and speech-heavy content.
- [ ] [#17: Digital remaster detection](https://github.com/lastdomovoi/bestcut/issues/17) — Measured native-detail/HDR heuristic is implemented; broader remaster certainty and SDR coverage remain open. Names/tags are not proof.

## Technical debt

## Future ideas

## Done

Completed tasks: [monthly archive](docs/todo-archive/2026-10.md).
