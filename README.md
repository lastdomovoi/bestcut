# video-compare

CLI tool for comparing video files by quality, size and device compatibility. Helps choose the best version when you have multiple releases of the same movie or TV series.

## Features

- **Quality scoring** — resolution, bitrate (normalized by codec efficiency), HDR, audio quality, subtitles
- **Dolby Atmos / DTS:X detection** — spatial audio formats are prioritized in scoring
- **Lossless audio detection** — TrueHD, DTS-HD MA, FLAC, PCM
- **Integrity checking** — detects truncated files, encoding errors, audio/video desync
- **Device compatibility** — checks against 8 device profiles (Plex, Apple TV, Samsung/LG TV, browsers, Kodi on Android TV, etc.)
- **Series episode matching** — auto-groups files by series title and S01E01/1x01/anime-style episode numbers
- **Priority profiles** — presets for different use cases (quality, Atmos, compact, Android TV, videophile)
- **Custom weights** — fine-tune scoring with per-component multipliers
- **Duplicate cleanup** — `--recommend-delete` shows which versions to remove (read-only, never deletes files)
- **Caching** — probe results are cached by file hash, instant on re-runs
- **Parallel processing** — uses multiple threads for 4+ files
- **JSON export** — machine-readable output for scripting

## Requirements

- Python 3.11+
- ffmpeg / ffprobe (must be in PATH)

## Installation

```bash
git clone https://github.com/lastdomovoi/video-compare.git
cd video-compare
```

No dependencies beyond Python stdlib and ffmpeg.

## Usage

### Compare two files

```bash
python3 cli.py movie_v1.mkv movie_v2.mkv
```

### Compare two releases of a TV series

```bash
python3 cli.py ./breaking-bad-720p/ ./breaking-bad-1080p/
```

Episodes are matched automatically by S01E01 / 1x01 / anime-style numbering across directories.

### Detailed info with score breakdown

```bash
python3 cli.py -v file1.mkv file2.mkv
```

### Check file integrity

```bash
# Quick check (headers + first/last 30 seconds)
python3 cli.py --check movie.mkv

# Full check (decodes entire file, slow)
python3 cli.py --full-check movie.mkv
```

### Check device compatibility

```bash
python3 cli.py --compat movie.mkv
```

Output:

```
movie.mkv:
  Plex (Direct Play): OK
  Apple TV 4K: 2 issues
    - Container matroska,webm not supported
    - MKV not natively supported (use Infuse/VLC)
  Kodi on Android TV (Homatics/Shield/AMLogic): OK
  ...
```

### Find duplicates to delete

```bash
python3 cli.py -D ./movies/
```

Shows which versions are worse and can be removed. **Never deletes files** — only prints recommendations.

### Use a priority profile

```bash
# Prioritize Dolby Atmos and lossless audio
python3 cli.py -p atmos dir_a/ dir_b/

# Prioritize Android TV / Kodi compatibility
python3 cli.py -p androidtv dir_a/ dir_b/

# Custom weights
python3 cli.py -w 'audio=3.0,compatibility=0.5,file_size=1.0' dir_a/ dir_b/
```

### JSON output

```bash
python3 cli.py --json dir/ 2>/dev/null > results.json
```

### List available profiles

```bash
python3 cli.py --list-profiles
```

## Scoring system

Total score is a weighted sum of components (default weights = 1.0):

| Component | Range | What it measures |
|---|---|---|
| Resolution | 0–30 | Pixels relative to 4K |
| Video quality | 0–25 | Bits-per-pixel normalized by codec (HEVC x1.5, AV1 x1.8 vs H.264) |
| HDR | 0–10 | Dolby Vision > HDR10+ > HDR10 > HLG > 10-bit SDR |
| Audio | 0–25 | Spatial (Atmos 8, DTS:X 7) + codec + lossless + channels + bitrate + languages |
| Subtitles | 0–5 | Language count + forced tracks |
| Compatibility | 0–10 | Penalty per device with issues |
| Integrity | penalty | -2 per error, -50 if truncated |
| File size | optional | Penalty for large files (enabled via `file_size` weight) |

## Priority profiles

| Profile | Use case |
|---|---|
| `default` | Balanced |
| `quality` | Maximum quality — resolution, bitrate and HDR prioritized |
| `atmos` | Audio priority — Dolby Atmos, DTS:X, lossless x2.5 |
| `compact` | Minimum size, penalizes large files |
| `androidtv` | Kodi / Android TV compatibility x2.0 |
| `universal` | Maximum compatibility with all devices x2.5 |
| `videophile` | Everything maxed: 4K HDR Atmos, size irrelevant |

## Device compatibility checks

Checks against: **Plex**, **Apple TV 4K**, **Samsung Smart TV**, **LG Smart TV**, **browsers**, **VLC**, **Kodi (desktop)**, **Kodi on Android TV** (Homatics / Shield / AMLogic).

Android TV specific checks:
- H.264 10-bit (Hi10P) — no hardware decoding
- HEVC 4K@60fps — not all budget SoCs can handle it
- AV1 — only on newer SoCs (S905X4+)
- TrueHD/Atmos, DTS-HD MA — HDMI passthrough only
- Dolby Vision — certified devices only
- PGS subtitles in 4K — may cause frame drops

## Configuration

Create a config file with defaults:

```bash
python3 cli.py --init-config
```

Config location: `~/.config/video-compare/config.toml`

```toml
[general]
profile = "androidtv"
verbose = false
check_integrity = "quick"

[weights]
# Override profile weights
# audio = 2.0
# compatibility = 1.5

[devices]
# Limit compatibility checks to specific devices
# check = ["kodi_androidtv", "plex"]
```

CLI arguments always override config file settings.

## Safety

- **Never deletes video files** — `--recommend-delete` only prints suggestions
- **`--clear-cache`** only removes JSON cache files from `~/.cache/video-compare/`
- No network access, no external services — everything runs locally via ffprobe/ffmpeg

## License

MIT
