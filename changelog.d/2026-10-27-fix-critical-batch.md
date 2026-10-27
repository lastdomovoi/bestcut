### Fixed

- Decode ffmpeg/ffprobe output safely so legacy non-UTF-8 tags no longer drop files (#1).
- Normalize AVC/HEVC levels per codec; stop false "15.0 > max 5.1" warnings (#6).
- Use effective FPS instead of the container's nominal rate (#9).
- Untagged audio is "unknown", not "absent": no penalty, no deletion advice (#2).
- Parse Russian and absolute episode numbers, restrict parent-directory parsing, and send unparsed groups to review instead of deletion advice; remove the misleading `rm` pipeline hint (#3).
- Read Stereo3D layout from stream side data (#15).

### Added

- Unit/regression tests: `python3 -m unittest discover -s tests -t .`.
