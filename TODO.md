# TODO

## Critical (security, bugs)

## Improvements

## Technical debt

## Future ideas

## Done

- [x] **3D video comparison** — per-eye effective resolution (SBS/top-bottom halve a frame axis), full vs half detection, format bonus (MVC > full > half); fixed stereo mode names and filename false positives _(2026-10-27)_
- [x] **Audio quality analysis** — noise floor in pauses, clipping ratio and band-limiting (<16 kHz on lossless) via ffmpeg astats; penalty folded into audio score. Not done: click/crackle detection _(2026-10-27)_
- [x] **Digital remaster detection** — tags/filename evidence (Remastered, Restored, 4K Scan); bonus raised for modern characteristics _(2026-10-27)_
- [x] **Real video encoder detection** — x264/x265 banner from bitstream, then stream/container tags, muxer names ignored _(2026-10-27)_
