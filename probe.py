"""Video file metadata extraction via ffprobe."""

from __future__ import annotations

import json
import re
import statistics
import subprocess
from pathlib import Path

from models import AudioTrack, SubtitleTrack, VideoFile, VideoTrack


# Channels with RMS below this threshold are considered silent.
SILENT_THRESHOLD_DB = -70.0

# If two channels differ by less than this, they carry the same signal.
IDENTICAL_THRESHOLD_DB = 1.0


def _run_ffprobe(path: Path, *extra_args: str) -> dict:
    """Run ffprobe and return JSON."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        *extra_args, str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed with error: {result.stderr.strip()}")
    return json.loads(result.stdout)


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
    """Detect actual number of active audio channels via RMS analysis.

    Samples 60 seconds from ~30% into the file to skip intros.
    Returns the real channel count, or None if detection fails.
    """
    if channels <= 1:
        return None  # Already mono, nothing to detect

    # Sample from 30% into the file, 60 seconds
    start = max(0, duration * 0.3)
    sample_duration = min(60, duration - start)
    if sample_duration < 5:
        start = 0
        sample_duration = min(60, duration)

    cmd = [
        "ffmpeg", "-v", "info",
        "-ss", str(int(start)),
        "-i", str(path),
        "-t", str(int(sample_duration)),
        "-map", f"0:a:{stream_index}",
        "-af", "astats=metadata=1:reset=0",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    stderr = result.stderr
    # Parse per-channel RMS levels from the final astats output.
    # astats prints "RMS level dB" per channel, then "Overall".
    rms_pattern = re.compile(r"RMS level dB:\s*([-\d.]+|inf|-inf)")
    all_rms = rms_pattern.findall(stderr)
    if not all_rms:
        return None

    # Take the last (channels + 1) values: per-channel + overall
    # We only need the per-channel values.
    tail = all_rms[-(channels + 1):]
    if len(tail) < channels + 1:
        tail = all_rms[-channels:] if len(all_rms) >= channels else all_rms

    ch_rms: list[float] = []
    for val in tail[:channels]:
        try:
            db = float(val)
            # Treat inf/-inf as silence
            if db == float("inf") or db == float("-inf"):
                db = -200.0
        except ValueError:
            db = -200.0
        ch_rms.append(db)

    if len(ch_rms) < channels:
        return None

    # Count active (non-silent) channels
    active = [i for i, db in enumerate(ch_rms) if db > SILENT_THRESHOLD_DB]

    if len(active) == 0:
        return 1  # All silent — treat as mono

    if len(active) == 1:
        return 1  # Only one channel has signal — mono

    if len(active) == 2:
        # Check if two active channels carry the same signal (mono→stereo)
        db_a, db_b = ch_rms[active[0]], ch_rms[active[1]]
        if abs(db_a - db_b) < IDENTICAL_THRESHOLD_DB:
            return 1  # Same signal in both — effectively mono
        return 2  # True stereo

    return len(active)


def detect_crop_and_upscale(path: Path, vf: VideoFile) -> None:
    """Detect black bars (letterboxing/pillarboxing) and upscale.

    Sets vf.video.crop_width, crop_height (real content area),
    vf.video.is_upscaled and vf.video.real_height.
    """
    height = vf.video.height
    duration = vf.duration

    # Sample from 40% into the file to avoid intros/credits
    start = max(0, int(duration * 0.4))

    # cropdetect — find actual content boundaries
    cmd = [
        "ffmpeg", "-v", "info",
        "-ss", str(start),
        "-i", str(path),
        "-t", "10",
        "-map", "0:v:0",
        "-vf", "cropdetect=24:2:0",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        crop_lines = re.findall(r"crop=(\d+):(\d+):\d+:\d+", result.stderr)
        if crop_lines:
            # Take last (most stable) crop value
            crop_w, crop_h = int(crop_lines[-1][0]), int(crop_lines[-1][1])
            vf.video.crop_width = crop_w
            vf.video.crop_height = crop_h
    except (subprocess.TimeoutExpired, OSError):
        pass

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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
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
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
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
        at.real_channels = detect_real_channels(path, i, at.channels, dur)


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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
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
        level = s.get("level", "")
        if isinstance(level, int) and level > 0:
            level = str(level / 10) if level > 9 else str(level)
        encoder = detect_video_encoder(path, s.get("tags", {}), fmt.get("tags", {}))

        vf.video = VideoTrack(
            codec=s.get("codec_name", ""),
            profile=s.get("profile", ""),
            level=str(level),
            width=int(s.get("width", 0)),
            height=int(s.get("height", 0)),
            bitrate=int(s.get("bit_rate", 0) or 0),
            fps=_parse_fps(s.get("r_frame_rate", "")),
            bit_depth=_parse_bit_depth(s),
            pix_fmt=s.get("pix_fmt", ""),
            color_space=s.get("color_space", ""),
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

    # Save to cache
    if use_cache:
        set_cached(path, vf)

    return vf
