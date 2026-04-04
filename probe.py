"""Video file metadata extraction via ffprobe."""

from __future__ import annotations

import json
import re
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


def detect_3d(path: Path, vf: VideoFile) -> None:
    """Detect 3D/stereoscopic video from metadata and stream info."""
    # Check MVC codec (H.264 MVC for Blu-ray 3D)
    if vf.video.codec in ("h264_mvc", "mvc"):
        vf.video.is_3d = True
        vf.video.stereo_mode = "mvc"
        return

    # Check stream side_data for stereo3d info (ffprobe)
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-select_streams", "v:0",
        "-show_frames", "-read_intervals", "%+#1",
        "-show_entries", "frame=side_data_list",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        for frame in data.get("frames", []):
            for sd in frame.get("side_data_list", []):
                sd_type = sd.get("side_data_type", "").lower()
                if "stereo3d" in sd_type or "stereo 3d" in sd_type:
                    vf.video.is_3d = True
                    vf.video.stereo_mode = sd.get("type", "unknown")
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
            tags = stream.get("tags", {})
            mode = tags.get("stereo_mode", "").strip()
            if mode and mode != "mono":
                vf.video.is_3d = True
                stereo_modes = {
                    "1": "side_by_side",
                    "2": "top_bottom",
                    "3": "top_bottom",
                    "side_by_side_left": "side_by_side",
                    "top_bottom": "top_bottom",
                }
                vf.video.stereo_mode = stereo_modes.get(mode, mode)
                return
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        pass

    # Check filename for 3D indicators
    name = path.name.lower()
    if any(tag in name for tag in ("3d", "sbs", "hsbs", "half-sbs",
                                    "ou", "hou", "half-ou", "tab")):
        vf.video.is_3d = True
        if "sbs" in name or "half-sbs" in name or "hsbs" in name:
            vf.video.stereo_mode = "side_by_side"
        elif "ou" in name or "hou" in name or "tab" in name:
            vf.video.stereo_mode = "top_bottom"
        else:
            vf.video.stereo_mode = "unknown"


def detect_all_real_channels(path: Path, vf: VideoFile) -> None:
    """Detect real channels for all audio tracks in a VideoFile."""
    for i, at in enumerate(vf.audio_tracks):
        if at.channels <= 1:
            continue
        dur = at.duration or vf.duration
        at.real_channels = detect_real_channels(path, i, at.channels, dur)


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
        file_size=int(fmt.get("size", 0)),
        duration=float(fmt.get("duration", 0)),
        overall_bitrate=int(fmt.get("bit_rate", 0)),
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
