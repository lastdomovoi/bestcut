"""Video file metadata extraction via ffprobe."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from models import AudioTrack, SubtitleTrack, VideoFile, VideoTrack


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
