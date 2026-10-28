"""Video file metadata extraction via ffprobe."""

from __future__ import annotations

import json
import re
import statistics
import subprocess
from pathlib import Path

from channels import analyze_channels
from procutil import run_tool
from models import AudioTrack, SubtitleTrack, VideoFile, VideoTrack


# Channels with RMS below this threshold are considered silent.
SILENT_THRESHOLD_DB = -70.0



def _run_ffprobe(path: Path, *extra_args: str) -> dict:
    """Run ffprobe and return JSON."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        *extra_args, str(path),
    ]
    result = run_tool(cmd, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed with error: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    if result.decode_note:
        data["_decode_note"] = result.decode_note
    return data


# Known video encoders, matched against tag strings (first match wins).
_ENCODER_PATTERNS = [
    (re.compile(r"x265", re.I), "x265"),
    (re.compile(r"x264", re.I), "x264"),
    (re.compile(r"svt-?av1", re.I), "SVT-AV1"),
    (re.compile(r"rav1e", re.I), "rav1e"),
    (re.compile(r"libaom|aomenc", re.I), "libaom"),
    (re.compile(r"libvpx", re.I), "libvpx"),
    (re.compile(r"nvenc", re.I), "NVENC"),
    (re.compile(r"qsv|quick ?sync", re.I), "QSV"),
    (re.compile(r"videotoolbox", re.I), "VideoToolbox"),
    (re.compile(r"xvid", re.I), "Xvid"),
    (re.compile(r"divx", re.I), "DivX"),
]

# Muxer / library strings: they say how the file was written, not encoded.
_MUXER_RE = re.compile(
    r"libebml|libmatroska|mkvmerge|mkvtoolnix|lavf|mp4box|gpac",
    re.I,
)

# Encoder banners embedded in the bitstream (SEI user data).
_SEI_PATTERNS = [
    (re.compile(rb"x265 \(build \d+\)|x265 \[info\]"), "x265"),
    (re.compile(rb"x264 - core \d+"), "x264"),
]
_SEI_SCAN_BYTES = 4 * 1024 * 1024


def _lower_tags(tags: dict | None) -> dict[str, str]:
    """Tags with lowercase keys (ffprobe key case differs by container)."""
    return {str(k).lower(): str(v) for k, v in (tags or {}).items()}


def _known_encoder(candidates: list[str]) -> str:
    """Encoder named in tag strings (x264, x265...), or ""."""
    for text in candidates:
        for pattern, name in _ENCODER_PATTERNS:
            if pattern.search(text):
                return name
    return ""


def _first_non_muxer(candidates: list[str]) -> str:
    """First tag string that is not a muxer/library name, or ""."""
    for text in candidates:
        if not _MUXER_RE.search(text):
            return text[:60]
    return ""


def _scan_encoder_signature(path: Path) -> str:
    """Look for an x264/x265 banner in the first bytes of the file."""
    try:
        with open(path, "rb") as f:
            head = f.read(_SEI_SCAN_BYTES)
    except OSError:
        return ""
    for pattern, name in _SEI_PATTERNS:
        if pattern.search(head):
            return name
    return ""


def detect_video_encoder(path: Path, stream_tags: dict, format_tags: dict) -> str:
    """Identify the video encoder (x264, x265, ...), not the muxer.

    Order: tags that name an encoder, bitstream banner (reads the head of
    the file), then any tag string that is not a muxer.
    """
    st = _lower_tags(stream_tags)
    ft = _lower_tags(format_tags)
    candidates = [c.strip() for c in (
        st.get("encoder", ""),
        st.get("_statistics_writing_app", ""),
        ft.get("writing_application", ""),
        ft.get("encoder", ""),
    ) if c and c.strip()]
    return (_known_encoder(candidates)
            or _scan_encoder_signature(path)
            or _first_non_muxer(candidates))


def _parse_fps(rate_str: str) -> float:
    """Parse string like '24000/1001' or '25/1' into float."""
    if not rate_str or rate_str == "0/0":
        return 0.0
    if "/" in rate_str:
        num, den = rate_str.split("/", 1)
        den_f = float(den)
        return float(num) / den_f if den_f else 0.0
    return float(rate_str)


# Frame rates players really use; measured rates within 0.3% snap to these.
STANDARD_FPS = (23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 50.0, 59.94, 60.0,
                119.88, 120.0)


def effective_fps(stream: dict, container_duration: float = 0.0) -> float:
    """Actual playback frame rate, not the container's nominal one.

    `r_frame_rate` is the lowest rate that can represent every timestamp
    and is often doubled (e.g. 60000/1001 for 29.97 fps content).  Prefer
    frame count / duration, then the timestamp-derived `avg_frame_rate`,
    and use the nominal rate only when nothing else is available.
    """
    nominal = _parse_fps(stream.get("r_frame_rate", ""))
    avg = _parse_fps(stream.get("avg_frame_rate", ""))
    measured = 0.0
    try:
        frames = int(stream.get("nb_frames") or 0)
    except (TypeError, ValueError):
        frames = 0
    # Only the stream's own duration: the container may be padded by a
    # longer audio track, which would understate the rate.
    duration = float(stream.get("duration") or 0)
    if frames > 1 and duration > 0:
        measured = frames / duration
    fps = measured or avg or nominal
    nearest = min(STANDARD_FPS, key=lambda std: abs(fps - std))
    if fps > 0 and abs(fps - nearest) <= nearest * 0.003:
        return nearest
    return round(fps, 3)


def normalize_level(codec: str, level) -> str:
    """Level as the usual decimal string, per codec convention.

    AVC level_idc is 10x the level (41 -> 4.1); HEVC general_level_idc is
    30x the level (150 -> 5.0, 153 -> 5.1).  Unknown / reserved values
    (<= 0, -99) give "".
    """
    try:
        value = float(level)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if codec in ("hevc", "h265"):
        return f"{value / 30:.1f}"
    if codec == "h264":
        if value == 9:
            return "1.0"  # level 1b
        return f"{value / 10:.1f}" if value > 9 else f"{value:.1f}"
    if codec in ("av1", "vp9", "vp8"):
        return ""  # level scales not comparable with compat limits
    return str(level)


def _detect_hdr(stream: dict, frames_data: dict | None) -> str:
    """Detect HDR type from stream and frame metadata."""
    color_transfer = stream.get("color_transfer", "")
    color_primaries = stream.get("color_primaries", "")

    # Check Dolby Vision via frame side_data
    if frames_data:
        for frame in frames_data.get("frames", []):
            for sd in frame.get("side_data_list", []):
                sd_type = sd.get("side_data_type", "")
                if "dovi" in sd_type.lower() or "dolby vision" in sd_type.lower():
                    return "dolby_vision"

    # HDR10 / HLG
    if color_transfer == "smpte2084" and color_primaries == "bt2020":
        # Check HDR10+ via side_data
        if frames_data:
            for frame in frames_data.get("frames", []):
                for sd in frame.get("side_data_list", []):
                    if "hdr10+" in sd.get("side_data_type", "").lower():
                        return "hdr10+"
        return "hdr10"

    if color_transfer == "arib-std-b67":
        return "hlg"

    return ""


def _parse_bit_depth(stream: dict) -> int:
    """Extract bit depth from stream."""
    # bits_per_raw_sample — most reliable
    raw = stream.get("bits_per_raw_sample")
    if raw and raw != "N/A":
        try:
            return int(raw)
        except ValueError:
            pass
    # By pix_fmt
    pix_fmt = stream.get("pix_fmt", "")
    if "10le" in pix_fmt or "10be" in pix_fmt:
        return 10
    if "12le" in pix_fmt or "12be" in pix_fmt:
        return 12
    return 8


def _detect_atmos(stream: dict) -> bool:
    """Detect Dolby Atmos in audio stream.

    Atmos is carried in two ways:
    - TrueHD + Atmos: side_data or profile contains "Atmos"
    - EAC3 + JOC (Joint Object Coding): profile contains "atmos" or
      side_data has EMDF/JOC markers
    Also commonly marked in track title.
    """
    codec = stream.get("codec_name", "")
    profile = stream.get("profile", "").lower()
    tags = stream.get("tags", {})
    title = tags.get("title", "").lower()

    # By profile (ffprobe sometimes shows it)
    if "atmos" in profile:
        return True

    # By track title (common marking method)
    if "atmos" in title:
        return True

    # EAC3 JOC = Atmos
    if codec == "eac3" and "joc" in profile:
        return True

    # By side_data_list (if present)
    for sd in stream.get("side_data_list", []):
        sd_type = sd.get("side_data_type", "").lower()
        if "atmos" in sd_type or "joc" in sd_type:
            return True

    return False


def _detect_dtsx(stream: dict) -> bool:
    """Detect DTS:X in audio stream."""
    profile = stream.get("profile", "").lower()
    tags = stream.get("tags", {})
    title = tags.get("title", "").lower()

    if "dts:x" in profile or "dts-x" in profile or "dtsx" in profile:
        return True
    if "dts:x" in title or "dts-x" in title or "dtsx" in title:
        return True

    return False


def detect_real_channels(path: Path, stream_index: int, channels: int,
                         duration: float) -> int | None:
    """Real number of independent channels, or None if undetermined.

    Thin wrapper over channels.analyze_channels (multi-scene energy plus
    inter-channel correlation).
    """
    return analyze_channels(path, stream_index, channels, duration).real_channels


# Black-bar detection samples this many places, a few seconds each.
CROP_POINTS = (0.1, 0.25, 0.4, 0.55, 0.7, 0.85)
CROP_SAMPLE_SEC = 4

# A sample agrees with the final crop when both sides are within this share.
CROP_AGREE = 0.015
# Share of usable samples that must agree for the crop to be trusted.
CROP_MIN_AGREEMENT = 0.5


def stable_crop(samples: list[tuple[int, int]]) -> tuple[int, int] | None:
    """Combine per-sample crop boxes into one stable (width, height).

    Each sample is the content box one scene shows.  Dark scenes give
    too-small boxes and burned-in subtitles or logos too-large ones, so
    neither extreme is taken: the largest group of agreeing boxes wins.
    It is trusted only if it covers enough samples and is not tied with a
    different group (e.g. mixed aspect ratios); otherwise None.
    """
    valid = [(w, h) for w, h in samples if w >= 16 and h >= 16]
    if len(valid) < 2:
        return None

    def near(a, b):
        return (abs(a[0] - b[0]) <= max(a[0], b[0]) * CROP_AGREE
                and abs(a[1] - b[1]) <= max(a[1], b[1]) * CROP_AGREE)

    groups = [[v for v in valid if near(v, c)] for c in valid]
    best = max(groups, key=lambda g: (len(g), g[0][0] * g[0][1]))
    if len(best) / len(valid) < CROP_MIN_AGREEMENT:
        return None
    # A different group of the same size makes the answer ambiguous
    for g in groups:
        if len(g) == len(best) and not near(g[0], best[0]):
            return None
    return max(v[0] for v in best), max(v[1] for v in best)


def measure_crop(path: Path, vf: VideoFile) -> tuple[int, int] | None:
    """Active picture size from several scenes, or None if unreliable."""
    if vf.duration <= 0:
        return None
    samples: list[tuple[int, int]] = []
    for point in CROP_POINTS:
        cmd = ["ffmpeg", "-v", "info", "-nostdin", "-an", "-sn",
               "-ss", str(int(vf.duration * point)), "-t", str(CROP_SAMPLE_SEC),
               "-i", str(path), "-map", "0:v:0",
               "-vf", "cropdetect=24:2:0", "-f", "null", "-"]
        try:
            result = run_tool(cmd, timeout=60)
        except (subprocess.TimeoutExpired, OSError):
            continue
        lines = re.findall(r"crop=(\d+):(\d+):\d+:\d+", result.stderr)
        if lines:  # last value of a sample is its most settled one
            samples.append((int(lines[-1][0]), int(lines[-1][1])))
    return stable_crop(samples)


def detect_crop_and_upscale(path: Path, vf: VideoFile) -> None:
    """Detect black bars (letterboxing/pillarboxing) and upscale.

    Sets vf.video.crop_width, crop_height (real content area),
    vf.video.is_upscaled and vf.video.real_height.
    """
    height = vf.video.height

    crop = measure_crop(path, vf)
    vf.video.crop_uncertain = crop is None
    vf.video.crop_width, vf.video.crop_height = crop if crop else (None, None)

    # Upscale detection via BPP heuristic
    if height <= 480:
        return  # Nothing to detect for SD content

    from scoring import bits_per_pixel, CODEC_EFFICIENCY
    bpp = bits_per_pixel(vf)
    if bpp > 0:
        thresholds = {
            2160: 0.015,
            1440: 0.020,
            1080: 0.025,
            720: 0.035,
        }
        threshold = 0.025
        for h, t in sorted(thresholds.items()):
            if height >= h:
                threshold = t
        if bpp < threshold:
            vf.video.is_upscaled = True
            eff = CODEC_EFFICIENCY.get(vf.video.codec, 1.0)
            eff_br = vf.video.bitrate * eff
            fps = vf.video.fps or 25.0
            target_bpp = 0.06
            if eff_br > 0 and fps > 0:
                real_pixels = eff_br / (fps * target_bpp)
                aspect = vf.video.width / vf.video.height if vf.video.height else 16 / 9
                real_h = int((real_pixels / aspect) ** 0.5)
                for std in (2160, 1440, 1080, 720, 576, 480, 360):
                    if real_h >= std * 0.85:
                        vf.video.real_height = std
                        break
                else:
                    vf.video.real_height = real_h


_STEREO_MODE_NAMES = {
    # ffmpeg stereo3d side data "type"
    "side by side": "side_by_side",
    "side by side (quincunx subsampling)": "side_by_side",
    "top and bottom": "top_bottom",
    "frame alternate": "frame_sequential",
    # MKV StereoMode tag as exposed by ffmpeg
    "left_right": "side_by_side",
    "right_left": "side_by_side",
    "top_bottom": "top_bottom",
    "bottom_top": "top_bottom",
    # Numeric Matroska StereoMode values and legacy names
    "1": "side_by_side", "11": "side_by_side", "side_by_side_left": "side_by_side",
    "2": "top_bottom", "3": "top_bottom",
}

# Filename tokens (split on non-alphanumerics) -> stereo mode.
_NAME_SBS = {"sbs", "hsbs", "fsbs", "halfsbs", "fullsbs"}
_NAME_TAB = {"hou", "fou", "htab", "ftab", "halfou", "fullou",
             "halftab", "fulltab"}
_NAME_BARE = {"ou": "top_bottom", "tab": "top_bottom"}  # need a "3d" token too


def _normalize_stereo_mode(raw: str) -> str:
    """Map ffmpeg / MKV stereo mode names to our canonical names."""
    key = raw.strip().lower()
    if key in _STEREO_MODE_NAMES:
        return _STEREO_MODE_NAMES[key]
    return key.replace(" ", "_") or "unknown"


def _stereo_mode_from_name(name: str) -> str:
    """Return stereo mode implied by the filename, or "" if none."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]
    # Join "half-sbs" style pairs: ["half", "sbs"] -> "halfsbs"
    joined = set(tokens)
    for a, b in zip(tokens, tokens[1:]):
        joined.add(a + b)
    if "mvc" in joined:
        return "mvc"
    if joined & _NAME_SBS:
        return "side_by_side"
    if joined & _NAME_TAB:
        return "top_bottom"
    if "3d" in joined:
        for tok, mode in _NAME_BARE.items():
            if tok in joined:
                return mode
        return "unknown"
    return ""


def detect_3d(path: Path, vf: VideoFile) -> None:
    """Detect 3D/stereoscopic video from metadata and stream info."""
    # Check MVC codec (H.264 MVC for Blu-ray 3D)
    if vf.video.codec in ("h264_mvc", "mvc"):
        vf.video.is_3d = True
        vf.video.stereo_mode = "mvc"
        return

    # Stereo3D side data: the layout type is on the stream; frame-level
    # entries only say that the marker exists.
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-select_streams", "v:0",
        "-show_entries", "stream_side_data",
        str(path),
    ]
    try:
        result = run_tool(cmd, timeout=30)
        data = json.loads(result.stdout)
        for stream in data.get("streams", []):
            for sd in stream.get("side_data_list", []):
                if "stereo 3d" not in sd.get("side_data_type", "").lower():
                    continue
                mode = str(sd.get("type", "")).strip()
                if mode.lower() == "2d":
                    return  # metadata says flat; ignore filename hints
                if mode:
                    vf.video.is_3d = True
                    vf.video.stereo_mode = _normalize_stereo_mode(mode)
                    return
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        pass

    # Check MKV tags for StereoMode
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-select_streams", "v:0",
        "-show_entries", "stream_tags=stereo_mode",
        str(path),
    ]
    try:
        result = run_tool(cmd, timeout=15)
        data = json.loads(result.stdout)
        for stream in data.get("streams", []):
            mode = _lower_tags(stream.get("tags")).get("stereo_mode", "").strip()
            if mode and mode.lower() not in ("mono", "0"):
                vf.video.is_3d = True
                vf.video.stereo_mode = _normalize_stereo_mode(mode)
                return
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        pass

    # Check filename for 3D indicators
    mode = _stereo_mode_from_name(path.name)
    if mode:
        vf.video.is_3d = True
        vf.video.stereo_mode = mode


# --- Master-grade detection (measured from content) --------------------

# Round trip through half resolution: above this PSNR (dB) nothing is lost,
# so the picture has no detail above half of the native sampling.
UPSCALE_PSNR_DB = 42.0

# PQ (HDR10/HDR10+): 1000 nits sits near 0.75 of the code range, while
# SDR-graded material in a PQ container peaks near 0.58.
HDR_REAL_PEAK = 0.68

# Sample points (fraction of duration) and frames per point; the median
# over points keeps one dark or soft scene from deciding the verdict.
PICTURE_POINTS = (0.25, 0.5, 0.75)
PICTURE_FRAMES = 4


def measure_picture(path: Path, vf: VideoFile) -> bool:
    """Measure detail (half-res round-trip PSNR) and peak luma.

    Fills vf.video.detail_psnr / peak_luma and marks the picture as
    measured, even on failure, so it is not retried on every run.
    Returns True if the state changed (worth caching).
    """
    v = vf.video
    if v.picture_measured:
        return False
    v.picture_measured = True
    if not (v.width and v.height and vf.duration > 0):
        return True

    graph = ("[0:v:0]split=3[a][b][d];"
             "[b]scale=trunc(iw/4)*2:trunc(ih/4)*2:flags=bicubic,"
             f"scale={v.width}:{v.height}:flags=bicubic[c];"
             "[a][c]psnr[p];"
             "[d]signalstats,metadata=print:key=lavfi.signalstats.YMAX:file=-[q]")
    psnrs: list[float] = []
    peaks: list[float] = []
    for point in PICTURE_POINTS:
        cmd = ["ffmpeg", "-v", "info", "-nostdin", "-an", "-sn",
               "-ss", str(int(vf.duration * point)), "-i", str(path),
               "-frames:v", str(PICTURE_FRAMES), "-filter_complex", graph,
               "-map", "[p]", "-f", "null", "-",
               "-map", "[q]", "-f", "null", "-"]
        try:
            run = run_tool(cmd, timeout=120)
        except (subprocess.TimeoutExpired, OSError):
            continue
        m = re.search(r"PSNR (?:y|gray|r):(inf|[\d.]+)", run.stderr)
        ymax = [float(x) for x in re.findall(r"YMAX=([\d.]+)", run.stdout)]
        if m and ymax:
            psnrs.append(100.0 if m.group(1) == "inf" else float(m.group(1)))
            peaks.append(statistics.median(ymax))
    if psnrs:
        v.detail_psnr = statistics.median(psnrs)
        v.peak_luma = statistics.median(peaks) / ((1 << max(8, v.bit_depth)) - 1)
    return True


def detect_remaster(path: Path, vf: VideoFile) -> None:
    """Infer a modern HDR master from measurements only.

    There is no reference to compare with, so this is an inference; names
    and tags are ignored because they are trivially faked.  Both must hold:
    - native detail: HD picture that loses detail in a half-res round trip
      (a plain upscale does not)
    - verified HDR: HDR10/HDR10+ whose highlights really use the HDR range
      (an SDR grade in an HDR container does not)
    """
    v = vf.video
    evidence: list[str] = []
    if v.height >= 720 and v.detail_psnr is not None \
            and v.detail_psnr < UPSCALE_PSNR_DB:
        evidence.append(f"native detail ({v.detail_psnr:.0f} dB)")
    if (v.hdr_type in ("hdr10", "hdr10+") and v.peak_luma is not None
            and v.peak_luma > HDR_REAL_PEAK):
        evidence.append(f"verified {v.hdr_type.upper()}")
    vf.remaster_evidence = evidence


def detect_source_type(path: Path, vf: VideoFile) -> None:
    """Parse source type from filename (informational only, not scored)."""
    name = path.name.upper()
    # Ordered by specificity — first match wins
    sources = [
        ("REMUX", "Remux"),
        ("BDREMUX", "Remux"),
        ("BD-REMUX", "Remux"),
        ("BDRIP", "BDRip"),
        ("BD-RIP", "BDRip"),
        ("BRRIP", "BDRip"),
        ("BLURAY", "BluRay"),
        ("BLU-RAY", "BluRay"),
        ("WEB-DL", "WEB-DL"),
        ("WEBDL", "WEB-DL"),
        ("WEBRIP", "WEBRip"),
        ("WEB-RIP", "WEBRip"),
        ("WEB", "WEB"),
        ("HDTV", "HDTV"),
        ("PDTV", "PDTV"),
        ("DVDRIP", "DVDRip"),
        ("DVD-RIP", "DVDRip"),
        ("DVD5", "DVD"),
        ("DVD9", "DVD"),
        ("DVDSCR", "DVDScr"),
        ("HDRIP", "HDRip"),
        ("HD-RIP", "HDRip"),
        ("TELECINE", "TC"),
        ("TELESYNC", "TS"),
        ("CAMRIP", "CAMRip"),
        ("SATRIP", "SatRip"),
        ("TVRIP", "TVRip"),
        ("IPTVRIP", "IPTVRip"),
        ("LASERDISC", "LaserDisc"),
        ("LDREMUX", "LaserDisc"),
        ("VHS", "VHS"),
        ("VHSRIP", "VHSRip"),
    ]
    for tag, label in sources:
        if tag in name:
            vf.source_type = label
            return


def detect_all_real_channels(path: Path, vf: VideoFile) -> None:
    """Detect real channels for all audio tracks in a VideoFile."""
    for i, at in enumerate(vf.audio_tracks):
        if at.channels <= 1:
            continue
        dur = at.duration or vf.duration
        analysis = analyze_channels(path, i, at.channels, dur)
        at.real_channels = analysis.real_channels
        at.channel_confidence = analysis.confidence
        at.channel_notes = analysis.notes


# --- Audio quality -----------------------------------------------------

# Sample length for quality analysis (seconds).
AUDIO_QUALITY_SAMPLE_SEC = 30

# Level reported for windows of digital silence (astats prints -inf).
DIGITAL_SILENCE_DB = -120.0

# Quietest window must be this far below the average to count as a pause.
MIN_PAUSE_DEPTH_DB = 12.0

# Content above this frequency is expected in real full-band audio.
HF_CUTOFF_HZ = 16000



def _astats_blocks(stderr: str, instance: str) -> dict[str, dict[str, float]]:
    """Parse one named astats instance into {"Overall"|"1"|"2"...: values}."""
    blocks: dict[str, dict[str, float]] = {}
    current: dict[str, float] | None = None
    prefix = f"[astats@{instance} @"
    for line in stderr.splitlines():
        if not line.startswith(prefix):
            continue
        body = line.split("] ", 1)[-1].strip()
        if body == "Overall":
            current = blocks.setdefault("Overall", {})
        elif body.startswith("Channel:"):
            current = blocks.setdefault(body.split(":", 1)[1].strip(), {})
        elif current is not None and ":" in body:
            key, _, val = body.partition(":")
            try:
                current[key.strip()] = float(val.strip())
            except ValueError:
                pass  # non-numeric lines such as "Bit depth"
    return blocks


def _noise_floor(blocks: dict[str, dict[str, float]]) -> tuple[float, float] | None:
    """(noise floor dB, SNR dB) from per-channel quietest windows.

    Silent channels (LFE, unused surrounds) are skipped, and the median of
    the remaining channels is used.  A channel counts only if its quietest
    window is well below its average, i.e. the sample contains pauses.
    """
    troughs: list[float] = []
    snrs: list[float] = []
    for name, vals in blocks.items():
        if name == "Overall":
            continue
        rms, trough = vals.get("RMS level dB"), vals.get("RMS trough dB")
        if rms is None or trough is None:
            continue
        if rms == float("-inf") or rms < SILENT_THRESHOLD_DB:
            continue
        if trough == float("-inf"):
            trough = DIGITAL_SILENCE_DB
        if rms - trough >= MIN_PAUSE_DEPTH_DB:
            troughs.append(trough)
            snrs.append(rms - trough)
    if not troughs:
        return None
    return statistics.median(troughs), statistics.median(snrs)


def analyze_audio_quality(path: Path, stream_index: int, duration: float,
                          sample_rate: int = 0,
                          check_bandwidth: bool = False,
                          ) -> dict[str, float | None] | None:
    """Estimate noise, clipping and band-limiting of one audio stream.

    Single ffmpeg pass over a short sample: astats on the full signal and,
    if check_bandwidth, on the signal above HF_CUTOFF_HZ.  Returns None on
    failure.
    """
    start = max(0, duration * 0.3)
    length = min(AUDIO_QUALITY_SAMPLE_SEC, duration - start)
    if length < 5:
        start = 0
        length = min(AUDIO_QUALITY_SAMPLE_SEC, duration)
    if length <= 0:
        return None

    with_hf = check_bandwidth and (
        sample_rate == 0 or sample_rate / 2 > HF_CUTOFF_HZ * 1.1)
    graph = f"[0:a:{stream_index}]"
    if with_hf:
        graph += (f"asplit[a][b];[a]astats@full[ao];"
                  f"[b]highpass=f={HF_CUTOFF_HZ},astats@hf[bo]")
    else:
        graph += "astats@full[ao]"
    # -ss/-t before -i limit the input, so every output sees the same span.
    cmd = ["ffmpeg", "-v", "info", "-nostdin",
           "-ss", str(int(start)), "-t", str(int(length)), "-i", str(path),
           "-filter_complex", graph, "-map", "[ao]", "-f", "null", "-"]
    if with_hf:
        cmd += ["-map", "[bo]", "-f", "null", "-"]
    try:
        result = run_tool(cmd, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return None

    full_blocks = _astats_blocks(result.stderr, "full")
    full = full_blocks.get("Overall", {})
    rms = full.get("RMS level dB")
    if rms is None:
        return None

    out: dict[str, float | None] = {
        "snr_db": None, "noise_floor_db": None,
        "clip_ratio": None, "hf_level_db": None,
    }
    noise = _noise_floor(full_blocks)
    if noise is not None:
        out["noise_floor_db"], out["snr_db"] = noise
    peak = full.get("Peak level dB")
    samples = full.get("Number of samples")
    if peak is not None and peak > -0.5 and samples:
        out["clip_ratio"] = full.get("Peak count", 0.0) / samples
    else:
        out["clip_ratio"] = 0.0

    if with_hf:
        hf = _astats_blocks(result.stderr, "hf").get("Overall", {}).get("RMS level dB")
        if hf is not None and rms != float("-inf"):
            out["hf_level_db"] = max(-200.0, hf) - rms
    return out


def detect_audio_quality(path: Path, vf: VideoFile, index: int | None) -> bool:
    """Analyze audio quality of one track (the one scoring will use).

    Skips tracks already analyzed (restored from cache).  Returns True if
    new data was collected.
    """
    if index is None or not (0 <= index < len(vf.audio_tracks)):
        return False
    at = vf.audio_tracks[index]
    if at.clip_ratio is not None:
        return False
    stats = analyze_audio_quality(path, index, at.duration or vf.duration,
                                  at.sample_rate, check_bandwidth=at.is_lossless)
    if stats is None:
        return False
    at.snr_db = stats["snr_db"]
    at.noise_floor_db = stats["noise_floor_db"]
    at.clip_ratio = stats["clip_ratio"]
    at.hf_level_db = stats["hf_level_db"]
    return True


def probe_file(path: Path, use_cache: bool = True) -> VideoFile:
    """Extract full video file information."""
    from cache import get_cached, set_cached

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    # Check cache
    if use_cache:
        cached = get_cached(path)
        if cached is not None:
            return cached

    # Core metadata
    data = _run_ffprobe(
        path,
        "-show_format", "-show_streams",
    )

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    note = data.get("_decode_note", "")

    vf = VideoFile(
        path=path,
        container=fmt.get("format_name", ""),
        file_size=int(fmt.get("size") or 0),
        duration=float(fmt.get("duration") or 0),
        overall_bitrate=int(fmt.get("bit_rate") or 0),
    )

    # Collect HDR data from frames (first video stream only)
    frames_data = None
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    if video_streams:
        try:
            frames_data = _run_ffprobe(
                path,
                "-select_streams", "v:0",
                "-show_frames",
                "-read_intervals", "%+#1",
                "-show_entries", "frame=side_data_list,pix_fmt,color_space,color_primaries,color_transfer",
            )
        except Exception:
            frames_data = None

    # Video stream (first)
    for s in streams:
        if s.get("codec_type") != "video":
            continue
        level = normalize_level(s.get("codec_name", ""), s.get("level"))
        encoder = detect_video_encoder(path, s.get("tags", {}), fmt.get("tags", {}))

        vf.video = VideoTrack(
            codec=s.get("codec_name", ""),
            profile=s.get("profile", ""),
            level=str(level),
            width=int(s.get("width", 0)),
            height=int(s.get("height", 0)),
            bitrate=int(s.get("bit_rate", 0) or 0),
            fps=effective_fps(s, vf.duration),
            nominal_fps=_parse_fps(s.get("r_frame_rate", "")),
            bit_depth=_parse_bit_depth(s),
            pix_fmt=s.get("pix_fmt", ""),
            color_space=s.get("color_space", ""),
            color_range=s.get("color_range", ""),
            nb_frames=int(s.get("nb_frames") or 0) if str(s.get("nb_frames", "0")).isdigit() else 0,
            color_transfer=s.get("color_transfer", ""),
            color_primaries=s.get("color_primaries", ""),
            hdr_type=_detect_hdr(s, frames_data),
            encoder=encoder,
            duration=float(s.get("duration", 0) or 0),
        )
        # If video bitrate is missing — estimate from overall
        if vf.video.bitrate == 0 and vf.overall_bitrate > 0:
            audio_total = sum(
                int(a.get("bit_rate", 0) or 0)
                for a in streams if a.get("codec_type") == "audio"
            )
            vf.video.bitrate = max(0, vf.overall_bitrate - audio_total)
        break

    # Audio streams
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        tags = s.get("tags", {})
        disp = s.get("disposition", {})
        codec_name = s.get("codec_name", "")
        profile = s.get("profile", "")

        # Detect Atmos / DTS:X / lossless
        has_atmos = _detect_atmos(s)
        has_dtsx = _detect_dtsx(s)
        is_lossless = codec_name in ("truehd", "flac", "pcm_s16le", "pcm_s24le",
                                      "pcm_s32le", "pcm_f32le", "alac")
        if codec_name in ("dts", "dca"):
            p = profile.lower()
            if "hd ma" in p or "hd_ma" in p or p == "ma":
                is_lossless = True

        vf.audio_tracks.append(AudioTrack(
            codec=codec_name,
            profile=profile,
            bitrate=int(s.get("bit_rate", 0) or 0),
            channels=int(s.get("channels", 0)),
            channel_layout=s.get("channel_layout", ""),
            sample_rate=int(s.get("sample_rate", 0) or 0),
            language=tags.get("language", ""),
            title=tags.get("title", ""),
            default=bool(disp.get("default", 0)),
            duration=float(s.get("duration", 0) or 0),
            has_atmos=has_atmos,
            has_dtsx=has_dtsx,
            is_lossless=is_lossless,
        ))

    # Subtitles
    for s in streams:
        if s.get("codec_type") != "subtitle":
            continue
        tags = s.get("tags", {})
        disp = s.get("disposition", {})
        vf.subtitle_tracks.append(SubtitleTrack(
            codec=s.get("codec_name", ""),
            language=tags.get("language", ""),
            title=tags.get("title", ""),
            forced=bool(disp.get("forced", 0)),
            default=bool(disp.get("default", 0)),
        ))

    if note:
        vf.metadata_warnings.append(
            f"metadata text is {note}; tag text may be damaged, stream data unaffected")

    # Save to cache
    if use_cache:
        set_cached(path, vf)

    return vf
