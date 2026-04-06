# Deep Video Analysis — Design Spec

## Overview

Extend bestcut with frame-level video quality analysis (`--deep`), fuzzy movie search (`--find`), interactive CLI mode, HTML reports, and user voting for score calibration.

Zero new dependencies — only Python stdlib + ffmpeg.

## Architecture

### New modules

| Module | Purpose |
|---|---|
| `deep.py` | Frame-level analysis: sharpness, dust/scratches, shake, logo detection, AI artifacts, crop/stretch |
| `finder.py` | Find all versions of a movie by name with transliteration and fuzzy matching |
| `report.py` | HTML report generation + jpg frame extraction |
| `votes.py` | User voting collection, storage, weight calibration |

### Modified modules

| Module | Changes |
|---|---|
| `cli.py` | Interactive mode (no-args), `--deep`, `--find`, `--report`, `--calibrate` flags |
| `models.py` | New fields in `VideoFile`/`VideoTrack` for deep metrics |
| `scoring.py` | Penalties for logo, dust, shake, blur, stretch |
| `comparator.py` | Cross-version crop/stretch comparison |

## Modes of operation

### Default (no args) — interactive

Menu-driven CLI using stdlib `input()` + `readline` for tab-completion:

```
bestcut — interactive mode

1. Compare files/directories
2. Find all versions of a movie
3. Check device compatibility
4. Find duplicates to delete

Choice [1]:
```

Each choice collects paths/options interactively, then runs the same logic as CLI flags.

### Quick mode (default with args)

Current behavior — metadata via ffprobe, seconds per file. No frame decoding.

### Deep mode (`--deep`)

Frame-level analysis with sampling. Adds ~20-60 seconds per file.

## Deep analysis pipeline

### Sampling strategy

1. Compute 10 evenly-spaced timestamps, skipping first/last 2 minutes (intros, credits)
2. At each timestamp extract via `ffmpeg -f rawvideo`:
   - 2 consecutive grayscale frames (for shake analysis)
   - 1 color frame as jpg (for screenshots)
3. Frame size for analysis: scale to 640px wide (preserve aspect). Full resolution for screenshots.

### Analyzers

All operate on raw grayscale pixels via `array.array('B', data)`. No Pillow/numpy.

#### 1. Sharpness / blur

- Laplacian variance on grayscale frame
- Normalize by average brightness (dark scenes correction)
- Metric: `sharpness_score: float` — median across samples
- Higher = sharper

#### 2. AI artifact detection

- **Sharp/Noise ratio**: Laplacian variance / noise stddev in flat areas. AI upscale shows anomalously high values (sharp edges but no natural noise)
- **Texture flatness**: fraction of 8x8 blocks with stddev < threshold among non-black areas. AI upscale has more unnaturally flat regions
- Metric: `ai_upscale_probability: float` (0.0–1.0)
- Not a penalty — informational flag. Enhances existing `is_upscaled` detection
- Display: `"AI upscale detected (confidence: 82%)"`

#### 3. Dust and scratches

- **Dust**: per-pixel comparison with 5x5 neighborhood median. Bright outliers (pixel > median + threshold) = dust candidate. Count per frame
- **Scratches**: per-column mean absolute deviation from neighboring columns. Vertical stripe with high deviation across >30% of frame height = scratch
- Metrics: `dust_density: float` (particles per megapixel), `scratch_count: int`
- Penalty: -1 for high dust, -1 for scratches

#### 4. Shake / stabilization

- Pre-filter: only analyze frames from static scenes (YDIF < threshold from signalstats)
- Extract pairs of consecutive frames at each sample point
- Block matching on 8x8 grid: for each block find (dx, dy) minimizing SAD in ±4px search window
- Global frame shift = median of block shifts
- Metric: `shake_stddev: float` — stddev of global shifts across samples. >0.5px = noticeable shake
- Penalty: -2 for strong shake (>1.0px)

#### 5. Logo / watermark detection

- Extract 10 frames from distant points across the film
- Divide frame into 4 corner regions (~8% width × 10% height): top-left, top-right, bottom-left, bottom-right
- Per-corner: compute pixel-wise variance across the 10 frames. If variance is anomalously low (pixels stable while content changes) → logo detected
- Additional check: logo area brightness differs from surrounding frame content
- Metrics: `logo_detected: bool`, `logo_corner: str`, `logo_area_pct: float`
- Penalty: -2 for logo presence
- Visualization: red border around detected logo area on saved screenshots

#### 6. Ad overlay detection (future iteration)

- Detect periodic overlays in bottom strip (channel promos, tickers, "advertisement" banners)
- Approach: compare bottom 15% of frame between consecutive samples — anomalous brightness changes suggest overlays
- Lower priority — logo detection already flags TV rips
- Metrics (planned): `ad_overlay_detected: bool`, `ad_overlay_frequency: float`

#### 7. Crop / stretch detection

**Crop (cross-version comparison):**
- Compare `real_aspect_ratio` between versions in a group
- If one is 4:3 and another 16:9 for the same film → mark `cropped: True`
- Metric: `crop_loss_pct: float` (percentage of frame area lost)

**Stretch/squeeze:**
- On `--deep`: extract frames at same timestamp from two versions
- If aspect ratios differ but neither has black bars → one is stretched
- Confirmation: compare horizontal vs vertical edge density ratio. Horizontally stretched version has disproportionately more horizontal edges
- Metrics: `stretched: bool`, `stretch_direction: str` ("horizontal"/"vertical"), `stretch_factor: float`
- Penalty: -3 for stretch (worse than crop — distorts proportions)

## Movie search (`--find`)

### Invocation

```bash
python3 cli.py --find "Большая перемена" /Volumes/Torrents/
python3 cli.py --find "Большая перемена 1972" /Volumes/Torrents/
# Interactive mode — menu item 2
```

If a positional arg is not a valid file/directory path, treat as search query.

### Algorithm

1. Parse query: extract title + optional year (`(19|20)\d{2}`)
2. Transliterate: built-in table cyrillic↔latin (GOST 7.79 + popular variants: щ→shch/sch, я→ya/ia, ж→zh, й→y/j). Generate multiple transliteration variants
3. Normalize: replace `.`, `_`, `-`, spaces → single separator. Strip quality tags (1080p, BDRip, x264, rip, by, upscale, AI, PAR, WEB-DL, etc.)
4. Walk directory tree: `os.walk()`. For each video file / directory containing video files — normalize name
5. Compare: `difflib.SequenceMatcher` ratio > 0.6 = candidate
6. Year filter: if year in query, keep only results containing that year in filename/dirname. If no year in query, show all results grouped by year
7. Group results as versions of the same film
8. Offer to compare found versions

## HTML report (`--report`)

Triggered by `--report` flag or interactive menu choice. Requires `--deep`.

### Output structure

```
bestcut-report/
├── index.html
├── frames/
│   ├── 00-05-00_Version_Name.jpg
│   └── ...
└── votes.json
```

### HTML content (single file, inline CSS/JS, no external resources)

1. **Summary table** — all versions, scores, key metrics, winner highlighted
2. **Deep metrics** — sharpness, noise, dust, shake, logo, AI artifacts — color-coded (green/yellow/red)
3. **Side-by-side frames** — timestamp slider, all versions in a row. Click to enlarge. Logo areas marked with red border
4. **Voting** — "better" button under each version per timestamp + overall vote. Results saved to `votes.json` via JS download trick (no server)

### No dependencies

Pure HTML + vanilla JS + inline CSS. Images via relative paths to `frames/`.

## User voting and calibration

### Vote collection

After comparison (CLI or interactive):
```
Agree with ranking? [Y/n/pick]
```
- `Y` — confirm automatic ranking
- `n` — disagree (records discrepancy)
- `pick` — select best version by number

In HTML report: per-timestamp and overall voting buttons.

### Storage

`~/.cache/bestcut/votes.json` — array of records:
```json
{
  "film": "Большая перемена",
  "auto_winner": "AI_Upscale",
  "user_winner": "WizardWhite",
  "auto_scores": {"AI_Upscale": 67.2, "WizardWhite": 64.1},
  "deep_metrics": {"sharpness": {}, "noise": {}},
  "timestamp": "2026-04-07"
}
```

### Calibration

`python3 cli.py --calibrate`:
1. Analyze accumulated votes
2. Show where auto-ranking disagrees with user
3. Recommend weight adjustments: `"User prefers lower sharpness → reduce sharpness weight from 1.0 to 0.7?"`
4. `--apply-calibration` writes adjusted weights to `config.toml`

First iteration: vote collection + simple discrepancy statistics. Automated weight regression — future work.

## Scoring changes

New penalty/bonus components added to `scoring.py`:

| Component | Range | Condition |
|---|---|---|
| Sharpness | 0 to -3 | Proportional to deviation from best version in group |
| Dust/scratches | 0 to -2 | -1 high dust density, -1 scratches present |
| Shake | 0 to -2 | -2 for strong shake (>1.0px stddev) |
| Logo | 0 to -2 | -2 for channel logo detected |
| Stretch | 0 to -3 | -3 for horizontal/vertical stretch |
| Crop loss | 0 to -1 | -1 for >10% frame area lost vs other versions |

Deep penalties only apply when `--deep` is used. Quick mode scoring unchanged.

## CLI flags summary

| Flag | Description |
|---|---|
| `--deep` | Enable frame-level analysis (sampling: 10 points × 2 frames) |
| `--deep-output DIR` | Directory for screenshots (default: `./bestcut-report/`) |
| `--report` | Generate HTML report (implies `--deep`) |
| `--find QUERY [DIR]` | Search for movie versions by name |
| `--calibrate` | Show vote statistics and weight recommendations |
| `--apply-calibration` | Write calibrated weights to config.toml |
| (no args) | Interactive mode |
