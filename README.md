# bestcut

CLI tool for comparing video files by quality, size and device compatibility. Helps choose the best version when you have multiple releases of the same movie or TV series.

## Features

- **Quality scoring** — resolution, bitrate (normalized by codec efficiency), HDR, audio quality, subtitles
- **Fake multichannel detection** — analyzes per-channel RMS energy to expose fake 5.1/7.1 (mono or stereo packed into surround containers)
- **Size efficiency scoring** — penalizes bloated encodes where extra size doesn't translate to real quality improvement (logarithmic diminishing returns on BPP)
- **Upscale detection** — identifies video upscaled from lower resolution via BPP heuristics
- **Black bars / aspect ratio** — detects letterboxing/pillarboxing, shows real content area and aspect ratio
- **3D video** — MVC, stereo3d metadata, MKV stereo mode, filename tokens; scored per eye (SBS/top-bottom halve the frame), small bonus MVC > full > half
- **Audio quality analysis** — noise floor in pauses, clipping, band-limited lossless (`analyze_quality` in config)
- **Remaster detection** — Remastered/Restored/4K Scan in tags or filename adds a bonus
- **Real encoder detection** — x264/x265 from the bitstream, not the muxer (libmatroska)
- **Dolby Atmos / DTS:X detection** — spatial audio formats are prioritized in scoring
- **Lossless audio detection** — TrueHD, DTS-HD MA, FLAC, PCM
- **Primary language scoring** — configurable primary language (e.g. `rus`) with penalty for absence, bonus for multiple tracks
- **Integrity checking** — detects truncated files, encoding errors, audio/video desync
- **Device compatibility** — checks against 8 device profiles (Plex, Apple TV, Samsung/LG TV, browsers, Kodi on Android TV, etc.)
- **Series episode matching** — auto-groups files by series title with fuzzy matching across different release names
- **Color-coded output** — green for winning parameters, red for losing, with key differences summary
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
git clone https://github.com/lastdomovoi/bestcut.git
cd bestcut
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
| Resolution | 0–30 | Pixels relative to 4K. Upscaled video scored by estimated real resolution |
| Video quality | 0–25 | BPP with logarithmic diminishing returns: `25 × (1 − e^(−BPP/0.05))`. BPP normalized by codec efficiency (HEVC ×1.5, AV1 ×1.8 vs H.264). Fast growth to ~20 at BPP 0.08, saturates above ~0.12 |
| HDR | 0–10 | Dolby Vision (10) > HDR10+ (9) > HDR10 (8) > HLG (7) > 10-bit SDR (4) |
| Audio | −3–25 | Best track on primary language. Spatial (Atmos 8, DTS:X 7) + codec (0–6) + real channels (−3 mono to +4 7.1) + bitrate (0–4) + languages (0–3). Fake multichannel detected via RMS analysis and scored by actual channels |
| Subtitles | 0–5 | Language count + forced tracks |
| Compatibility | 0–10 | Penalty per device with issues |
| Size efficiency | 0 to −5 | Bloat penalty: `−min(5, log₂(BPP/0.10) × 2)`. Files with BPP > 0.10 are penalized proportionally to how much bigger they are than a good-quality reference encode. 1.5× bloat ≈ −1.2, 2× ≈ −2.0, 4× ≈ −4.0 |
| Integrity | penalty | −2 per error, −50 if truncated |
| Language tracks | −10 to +6 | −10 if primary language absent, +1.5 per extra primary track, +1.5 per extra language |

### BPP curve

Bits-per-pixel (BPP) measures actual encoding quality independent of resolution.  The scoring uses a logarithmic curve that reflects perceptual reality: quality gains above BPP ~0.08 are marginal.

```
Score = 25 × (1 − e^(−BPP / 0.05))

BPP     Score   Meaning
0.03    11.3    Low quality (DVD-level bitrate at HD resolution)
0.05    15.8    Acceptable
0.08    20.0    Good (transparent for most content)
0.10    21.6    Very good
0.12    22.7    Excellent (diminishing returns beyond)
0.15    23.8    Near-maximum
0.30+   ~25.0   Maximum (no perceptual gain vs 0.12)
```

### Fake multichannel detection

Audio tracks are analyzed by sampling 60 seconds of audio and measuring per-channel RMS energy.  If surround channels (center, LFE, rear) are silent (<−70 dB), the track is flagged as fake multichannel and scored by its actual channel count:

- `5.1 [FAKE → actually Mono]` — mono signal duplicated to all channels
- `5.1 [FAKE → actually Stereo]` — stereo signal in L/R, rest silent
- `Stereo [FAKE → actually Mono]` — same signal in both channels

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

Config location: `config.toml` in project root.

```toml
[general]
profile = "default"
verbose = false
check_integrity = "none"

[weights]
# Override profile weights
# audio = 2.0
# compatibility = 1.5
# file_size = 1.0  # 0 = disable size efficiency penalty

[devices]
# Limit compatibility checks to specific devices
# check = ["kodi_androidtv", "plex"]

[audio]
# Primary language — files without it get -10 penalty
primary_language = "rus"
# Bonus per extra primary track (max 3.0)
extra_primary_track_bonus = 1.5
# Additional desired languages (bonus if present)
extra_languages = ["eng"]
extra_language_bonus = 1.5
```

CLI arguments always override config file settings.

## Safety

- **Never deletes video files** — `--recommend-delete` only prints suggestions
- **`--clear-cache`** only removes JSON cache files from `~/.cache/bestcut/`
- No network access, no external services — everything runs locally via ffprobe/ffmpeg

## License

MIT
