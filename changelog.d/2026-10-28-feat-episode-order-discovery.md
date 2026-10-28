### Added

- `--find-series NAME [--alias A] [--json] ROOT...`: fast read-only series directory discovery, no ffprobe (#4).
- Episode-order check across releases (`episode_sequence.py`): duration screening, content verification, remap candidates, gaps, persistent shifts; ambiguous episodes get no deletion advice (#5).
- `--set-language FILE TRACK LANG` saves a confirmed audio language; output shows the evidence for every track (#2).

### Fixed

- Russian `Серия №181` and `sXXeYY.(NNN)` absolute numbers, season offsets for `Interny-13.(01.seriya)`, 278-episode fixture (#3).
- Picture matching thresholds recalibrated on re-encoded, rescaled and graded copies; global alignment tolerates inserted stretches.
