# TODO

## Critical (security, bugs)

## Improvements

- [ ] **3D video comparison** — score 3D formats (SBS, top-bottom, MVC), account for effective resolution (SBS halves width), factor into scoring. Currently 3D is detected but not scored _(added: 2026-04-05)_
- [ ] **Audio quality analysis** — SNR (signal-to-noise ratio) via ffmpeg astats, detect clicks/crackling, spectral analysis. Penalty for noisy audio _(added: 2026-04-05)_
- [ ] **Digital remaster detection** — bonus for remastered releases by metadata (encoder/title tags), characteristics (newer codec, higher bit depth, better color space), filename patterns (Remastered, Restored, 4K Scan) _(added: 2026-04-05)_
- [ ] **Real video encoder detection** — currently encoder shows muxer (libebml/libmatroska), need to parse encoding_tool or writing_application from MKV tags to identify x264/x265/rav1e _(added: 2026-04-05)_

## Technical debt

## Future ideas

## Done
