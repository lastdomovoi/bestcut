# TODO

## Critical (security, bugs)

## Improvements

## Technical debt

## Future ideas

## Done

- [x] **3D video comparison** — per-eye effective resolution (SBS/top-bottom halve a frame axis), full vs half detection, format bonus (MVC > full > half); fixed stereo mode names and filename false positives _(2026-10-27)_
- [x] **Audio quality analysis** — noise floor in pauses, clipping ratio and band-limiting (<16 kHz on lossless) via ffmpeg astats; penalty folded into audio score. Not done: click/crackle detection _(2026-10-27)_
- [x] **Digital remaster detection** — inferred only from measured content (names/tags are ignored, they can be faked): native detail via half-res round-trip PSNR on 3 sample points plus verified HDR10 peak luma; both required, +2 bonus. A heuristic: with no reference it finds modern HDR masters, not SDR remasters _(2026-10-27)_
- [x] **Real video encoder detection** — x264/x265 banner from bitstream, then stream/container tags, muxer names ignored _(2026-10-27)_
