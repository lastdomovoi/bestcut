"""Video file scoring algorithm for quality comparison."""

from __future__ import annotations

from models import VideoFile
from profiles import ScoringWeights

# Codec efficiency coefficients relative to H.264.
# At the same bitrate HEVC provides ~1.5x better quality.
CODEC_EFFICIENCY = {
    "h264": 1.0,
    "hevc": 1.5,
    "h265": 1.5,
    "av1": 1.8,
    "vp9": 1.4,
    "vp8": 0.9,
    "mpeg4": 0.7,
    "mpeg2video": 0.5,
    "mpeg1video": 0.4,
    "wmv3": 0.6,
    "vc1": 0.7,
}

# Base audio codec rank (without spatial/lossless bonuses)
AUDIO_CODEC_RANK = {
    "truehd": 10,
    "flac": 8,
    "pcm_s24le": 8,
    "pcm_s32le": 8,
    "alac": 8,
    "eac3": 7,
    "dts": 6,   # base DTS; DTS-HD MA/DTS:X get bonuses
    "dca": 6,
    "ac3": 5,
    "aac": 5,
    "opus": 5,
    "vorbis": 4,
    "mp3": 3,
    "mp2": 2,
    "wmav2": 1,
}


def effective_bitrate(vf: VideoFile) -> float:
    """Calculate effective bitrate (normalized by codec efficiency)."""
    efficiency = CODEC_EFFICIENCY.get(vf.video.codec, 1.0)
    return vf.video.bitrate * efficiency


def bits_per_pixel(vf: VideoFile) -> float:
    """Bits per pixel per frame — resolution-independent quality metric."""
    pixels = vf.video.pixels
    fps = vf.video.fps
    if pixels == 0 or fps == 0:
        return 0.0
    eff_br = effective_bitrate(vf)
    return eff_br / (pixels * fps)


def score_resolution(vf: VideoFile) -> float:
    """Resolution score (0-30 points). 4K = 30, 1080p ~ 7.5, 720p ~ 3.3."""
    ref_pixels = 3840 * 2160  # 4K
    return min(30.0, (vf.video.pixels / ref_pixels) * 30.0)


def score_video_quality(vf: VideoFile) -> float:
    """Video quality score based on effective bitrate (0-25 points).

    Uses bits-per-pixel (BPP) normalized by codec.
    Good BPP for H.264: 0.05-0.15, excellent: >0.15.
    """
    bpp = bits_per_pixel(vf)
    if bpp <= 0:
        return 0.0
    # Non-linear scale: fast growth up to 0.10, then slows down
    # 0.05 = ~12.5, 0.10 = ~20, 0.15 = ~25
    normalized = min(1.0, bpp / 0.15)
    return normalized * 25.0


def score_hdr(vf: VideoFile) -> float:
    """HDR score (0-10 points)."""
    hdr_scores = {
        "dolby_vision": 10.0,
        "hdr10+": 9.0,
        "hdr10": 8.0,
        "hlg": 7.0,
    }
    score = hdr_scores.get(vf.video.hdr_type, 0.0)
    # Bonus for 10-bit even without HDR
    if score == 0 and vf.video.bit_depth >= 10:
        score = 4.0
    return score


def _audio_track_rank(a) -> tuple:
    """Audio track rank for sorting (higher = better)."""
    # Priority: Atmos > DTS:X > lossless > lossy
    spatial = 2 if a.has_atmos else (1 if a.has_dtsx else 0)
    lossless = 1 if a.is_lossless else 0
    codec_rank = AUDIO_CODEC_RANK.get(a.codec, 0)
    # DTS-HD MA gets a higher rank than base DTS
    if a.codec in ("dts", "dca") and a.is_lossless:
        codec_rank = 9
    return (spatial, lossless, codec_rank, a.channels, a.bitrate)


def score_audio(vf: VideoFile) -> float:
    """Audio score (0-25 points).

    Breakdown:
    - Spatial audio (0-8): Atmos/DTS:X
    - Codec + lossless (0-6): codec quality
    - Channels (0-4): surround sound
    - Bitrate (0-4): stream sufficiency
    - Multi-language (0-3): multiple language tracks
    """
    if not vf.audio_tracks:
        return 0.0

    # Best audio track
    best = max(vf.audio_tracks, key=_audio_track_rank)

    # Spatial audio (0-8)
    if best.has_atmos:
        spatial_score = 8.0   # Dolby Atmos — maximum
    elif best.has_dtsx:
        spatial_score = 7.0   # DTS:X — close to Atmos
    else:
        spatial_score = 0.0

    # Codec + lossless (0-6)
    base_rank = AUDIO_CODEC_RANK.get(best.codec, 0)
    if best.codec in ("dts", "dca") and best.is_lossless:
        base_rank = 9  # DTS-HD MA
    codec_score = min(5.0, base_rank * 0.5)
    if best.is_lossless:
        codec_score += 1.0  # lossless bonus
    codec_score = min(6.0, codec_score)

    # Channels (-3 to 4): 7.1=4, 5.1=3, stereo=1, mono=-3 (penalty)
    if best.channels <= 1:
        channel_score = -3.0  # mono is a significant downgrade
    else:
        channel_score = min(4.0, (best.channels / 8) * 4.0)

    # Bitrate (0-4)
    if best.bitrate > 0:
        # TrueHD Atmos can be up to 18 Mbps, normalize to 5 Mbps
        bitrate_score = min(4.0, (best.bitrate / 5_000_000) * 4.0)
    elif best.is_lossless:
        bitrate_score = 3.0  # lossless without specified bitrate — OK
    else:
        bitrate_score = 1.0

    # Multi-language (0-3)
    languages = {at.language for at in vf.audio_tracks if at.language}
    multi_score = min(3.0, len(languages) * 1.0)

    return spatial_score + codec_score + channel_score + bitrate_score + multi_score


def score_subtitles(vf: VideoFile) -> float:
    """Subtitles score (0-5 points)."""
    if not vf.subtitle_tracks:
        return 0.0

    languages = {st.language for st in vf.subtitle_tracks if st.language}
    has_forced = any(st.forced for st in vf.subtitle_tracks)

    score = min(3.5, len(languages) * 1.0)
    if has_forced:
        score += 1.5

    return min(5.0, score)


def score_integrity(vf: VideoFile) -> float:
    """Penalty for integrity issues (negative value)."""
    penalty = 0.0
    if vf.integrity.checked:
        if vf.integrity.is_truncated:
            penalty += 50.0
        penalty += min(30.0, vf.integrity.error_count * 2.0)
    return -penalty


def calculate_score(vf: VideoFile, weights: ScoringWeights | None = None) -> float:
    """Calculate overall video file quality score.

    Weights — multipliers for each component.
    file_size > 0 adds a file size penalty.
    """
    if weights is None:
        weights = ScoringWeights()

    components = {
        "resolution": score_resolution(vf) * weights.resolution,
        "video_quality": score_video_quality(vf) * weights.video_quality,
        "hdr": score_hdr(vf) * weights.hdr,
        "audio": score_audio(vf) * weights.audio,
        "subtitles": score_subtitles(vf) * weights.subtitles,
        "compatibility": vf.compatibility_score * weights.compatibility,
        "integrity": score_integrity(vf),  # penalties are not scaled
    }

    # File size penalty (if enabled)
    if weights.file_size > 0 and vf.file_size_gb > 0:
        # Normalize: 1 GB = 0 penalty, 50 GB = -15 penalty
        size_penalty = min(15.0, (vf.file_size_gb / 50.0) * 15.0) * weights.file_size
        components["file_size_penalty"] = -size_penalty

    total = sum(components.values())
    vf.score = max(0.0, total)
    return vf.score


def score_breakdown(vf: VideoFile, weights: ScoringWeights | None = None) -> dict[str, float]:
    """Return score breakdown by component (with weights applied)."""
    if weights is None:
        weights = ScoringWeights()

    result = {
        f"Resolution (x{weights.resolution})": score_resolution(vf) * weights.resolution,
        f"Video quality (x{weights.video_quality})": score_video_quality(vf) * weights.video_quality,
        f"HDR (x{weights.hdr})": score_hdr(vf) * weights.hdr,
        f"Audio (x{weights.audio})": score_audio(vf) * weights.audio,
        f"Subtitles (x{weights.subtitles})": score_subtitles(vf) * weights.subtitles,
        f"Compatibility (x{weights.compatibility})": vf.compatibility_score * weights.compatibility,
        "Integrity": score_integrity(vf),
    }

    if weights.file_size > 0 and vf.file_size_gb > 0:
        size_penalty = min(15.0, (vf.file_size_gb / 50.0) * 15.0) * weights.file_size
        result[f"File size penalty (x{weights.file_size})"] = -size_penalty

    return result
