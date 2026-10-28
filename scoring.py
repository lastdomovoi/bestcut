"""Video file scoring algorithm for quality comparison."""

from __future__ import annotations

import math

from languages import (is_unknown_language, language_matches, normalize_language,
                       primary_language_status, track_language)
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


def rank_key(vf: VideoFile) -> tuple:
    """Sort key (higher = better): a damaged file never outranks an
    undamaged one, whatever its score."""
    return (not vf.integrity.damaged, vf.score)


def ranked_files(files: list[VideoFile]) -> list[VideoFile]:
    return sorted(files, key=rank_key, reverse=True)


def effective_bitrate(vf: VideoFile) -> float:
    """Calculate effective bitrate (normalized by codec efficiency)."""
    efficiency = CODEC_EFFICIENCY.get(vf.video.codec, 1.0)
    return vf.video.bitrate * efficiency


def bits_per_pixel(vf: VideoFile) -> float:
    """Bits per pixel per frame — resolution-independent quality metric.

    Counted over the active picture: black bars cost almost no bits and
    would otherwise inflate resolution and dilute BPP.
    """
    pixels = vf.video.active_pixels
    fps = vf.video.fps
    if pixels == 0 or fps == 0:
        return 0.0
    eff_br = effective_bitrate(vf)
    return eff_br / (pixels * fps)


def score_resolution(vf: VideoFile) -> float:
    """Resolution score (0-30 points). 4K = 30, 1080p ~ 7.5, 720p ~ 3.3.

    Upscaled video is scored by its estimated real resolution.
    3D video packed into one frame (SBS/top-bottom) is scored per eye.
    """
    if vf.video.is_upscaled and vf.video.real_height:
        # Use real resolution for scoring
        aspect = vf.video.width / vf.video.height if vf.video.height else 16 / 9
        real_w = int(vf.video.real_height * aspect)
        real_pixels = (real_w * vf.video.real_height) // vf.video.stereo_divisor
    else:
        real_pixels = vf.video.eye_pixels
    ref_pixels = 3840 * 2160  # 4K
    return min(30.0, (real_pixels / ref_pixels) * 30.0)


def score_video_quality(vf: VideoFile) -> float:
    """Video quality score based on effective bitrate (0-25 points).

    Uses bits-per-pixel (BPP) normalized by codec with logarithmic
    diminishing returns.  Perceptual quality gains above BPP ~0.08
    are marginal; above ~0.12 they are negligible.

    Approximate scores (H.264):
      BPP 0.03 → ~10,  0.05 → ~15,  0.08 → ~20,
      0.10 → ~22,  0.12 → ~23,  0.15 → ~24,  0.30 → ~25
    """
    bpp = bits_per_pixel(vf)
    if bpp <= 0:
        return 0.0
    # Logarithmic curve: fast growth to ~20 at BPP 0.08, then saturates
    # Formula: 25 * (1 - e^(-bpp/k)), k chosen so BPP=0.08 ≈ 20 pts
    k = 0.05
    score = 25.0 * (1.0 - math.exp(-bpp / k))
    return min(25.0, score)


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


def score_3d(vf: VideoFile) -> float:
    """Small bonus (0-2) for 3D by format fidelity: MVC > full > half."""
    v = vf.video
    if not v.is_3d:
        return 0.0
    if v.stereo_mode == "mvc":
        return 2.0
    if v.stereo_mode in ("side_by_side", "top_bottom"):
        return 1.5 if v.stereo_full_res else 1.0
    return 0.5


def score_remaster(vf: VideoFile) -> float:
    """Bonus (0 or 2) for a measured modern HDR master.

    Needs both indicators from probe.detect_remaster (native detail and
    verified HDR); nothing here depends on file names or tags.
    """
    return 2.0 if vf.is_remaster else 0.0


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


def select_best_audio(vf: VideoFile, primary_language: str = ""):
    """Audio track used for quality scoring (primary language preferred)."""
    if not vf.audio_tracks:
        return None
    pool = vf.audio_tracks
    if primary_language:
        pool = [at for at in pool if language_matches(track_language(at), primary_language)] or pool
    return max(pool, key=_audio_track_rank)


# Quietest window above this level is audible hiss; clipping share above
# CLIP_RATIO_MIN is distortion; lossless content below BAND_LIMIT_HF_DB
# (relative to overall RMS) above 16 kHz was probably transcoded from lossy.
NOISE_FLOOR_MAX_DB = -50.0
BAND_LIMIT_HF_DB = -70.0
CLIP_RATIO_MIN = 1e-4


def audio_quality_flags(a) -> list[str]:
    """Human-readable quality problems of an analyzed audio track."""
    flags = []
    if a.noise_floor_db is not None and a.noise_floor_db > NOISE_FLOOR_MAX_DB:
        flags.append(f"noisy {a.noise_floor_db:.0f} dB")
    if a.clip_ratio is not None and a.clip_ratio > CLIP_RATIO_MIN:
        flags.append(f"clipping {a.clip_ratio * 100:.2f}%")
    if (a.hf_level_db is not None and a.is_lossless
            and a.hf_level_db < BAND_LIMIT_HF_DB):
        flags.append("band-limited <16kHz")
    return flags


def score_audio_quality(a) -> float:
    """Penalty (<= 0, down to -5) for noisy, clipped or band-limited audio.

    Tracks that were not analyzed are not penalized.
    """
    penalty = 0.0
    if a.noise_floor_db is not None and a.noise_floor_db > NOISE_FLOOR_MAX_DB:
        penalty += min(2.0, (a.noise_floor_db - NOISE_FLOOR_MAX_DB) / 15.0 * 2.0)
    if a.clip_ratio is not None and a.clip_ratio > CLIP_RATIO_MIN:
        penalty += min(2.0, 1.0 + math.log10(a.clip_ratio / CLIP_RATIO_MIN) * 0.5)
    if (a.hf_level_db is not None and a.is_lossless
            and a.hf_level_db < BAND_LIMIT_HF_DB):
        penalty += 1.0
    return -penalty


def score_audio(vf: VideoFile, primary_language: str = "") -> float:
    """Audio score (0-25 points).

    Breakdown:
    - Spatial audio (0-8): Atmos/DTS:X
    - Codec + lossless (0-6): codec quality
    - Channels (0-4): surround sound
    - Bitrate (0-4): stream sufficiency
    - Multi-language (0-3): multiple language tracks
    - Quality penalty (-5..0): noise, clipping, band-limiting (if analyzed)

    When primary_language is set, the best track in that language is used
    for quality scoring (spatial, codec, channels, bitrate).
    """
    if not vf.audio_tracks:
        return 0.0

    best = select_best_audio(vf, primary_language)

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
    # Use effective_channels (real detected channels, not metadata)
    eff_ch = best.effective_channels
    if eff_ch <= 1:
        channel_score = -3.0  # mono is a significant downgrade
    else:
        channel_score = min(4.0, (eff_ch / 8) * 4.0)

    # Bitrate (0-4)
    if best.bitrate > 0:
        # TrueHD Atmos can be up to 18 Mbps, normalize to 5 Mbps
        bitrate_score = min(4.0, (best.bitrate / 5_000_000) * 4.0)
    elif best.is_lossless:
        bitrate_score = 3.0  # lossless without specified bitrate — OK
    else:
        bitrate_score = 1.0

    # Multi-language (0-3)
    languages = {normalize_language(track_language(at)) for at in vf.audio_tracks
                 if not is_unknown_language(track_language(at))}
    multi_score = min(3.0, len(languages) * 1.0)

    base = spatial_score + codec_score + channel_score + bitrate_score + multi_score
    # The quality penalty alone never pushes the audio score below zero.
    return base + max(score_audio_quality(best), -max(0.0, base))


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


def score_language_tracks(vf: VideoFile,
                         primary_language: str = "",
                         extra_languages: list[str] | None = None,
                         missing_primary_penalty: float = 10.0,
                         extra_primary_track_bonus: float = 1.5,
                         extra_language_bonus: float = 1.5) -> float:
    """Score based on audio language availability.

    - Penalty if primary language is confirmed absent (not if tags are missing).
    - Bonus for multiple tracks in primary language (dubs, commentary).
    - Bonus for each extra language present.
    """
    if not primary_language:
        return 0.0

    score = 0.0
    primary_tracks = [at for at in vf.audio_tracks
                      if language_matches(track_language(at), primary_language)]

    if not primary_tracks:
        # Penalize only confirmed absence; untagged tracks may well be
        # the preferred language, so "unknown" is neutral (needs review).
        if primary_language_status(vf, primary_language) == "absent":
            score -= missing_primary_penalty
    else:
        # Bonus for extra primary-language tracks (beyond the first)
        extra_count = len(primary_tracks) - 1
        if extra_count > 0:
            score += min(3.0, extra_count * extra_primary_track_bonus)

    # Bonus for extra languages
    if extra_languages:
        available = {normalize_language(track_language(at)) for at in vf.audio_tracks
                     if not is_unknown_language(track_language(at))}
        present = sum(1 for lang in extra_languages
                      if normalize_language(lang) in available)
        score += min(3.0, present * extra_language_bonus)

    return score


def calculate_score(vf: VideoFile, weights: ScoringWeights | None = None,
                    primary_language: str = "",
                    extra_languages: list[str] | None = None,
                    missing_primary_penalty: float = 10.0,
                    extra_primary_track_bonus: float = 1.5,
                    extra_language_bonus: float = 1.5) -> float:
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
        "audio": score_audio(vf, primary_language) * weights.audio,
        "subtitles": score_subtitles(vf) * weights.subtitles,
        "compatibility": vf.compatibility_score * weights.compatibility,
        "integrity": score_integrity(vf),  # penalties are not scaled
    }
    if vf.video.is_3d:
        components["stereo3d"] = score_3d(vf) * weights.stereo3d
    if vf.is_remaster:
        components["remaster"] = score_remaster(vf) * weights.remaster

    # Size efficiency penalty: penalize bloated files where extra size
    # doesn't translate to meaningful quality improvement.
    # Uses BPP efficiency: files with very high BPP (>0.12) get penalized
    # proportionally to their excess size vs a "reference" good encode.
    if vf.file_size_gb > 0 and vf.video.bitrate > 0:
        bpp = bits_per_pixel(vf)
        if bpp > 0:
            # Reference BPP for "good enough" quality (codec-adjusted)
            ref_bpp = 0.10
            if bpp > ref_bpp:
                # How much bigger is this file vs a ref_bpp encode?
                bloat_ratio = bpp / ref_bpp  # e.g. 3.75x for BPP 0.375
                # Logarithmic penalty: mild for 1.5x, moderate for 2x, harsh for 4x
                # Max penalty ~5 points at extreme bloat
                bloat_penalty = min(5.0, math.log2(bloat_ratio) * 2.0)
                components["size_efficiency"] = -bloat_penalty * weights.file_size

    # Legacy flat file size penalty (if file_size weight > 0 and no bloat detected)
    if "size_efficiency" not in components and weights.file_size > 0 and vf.file_size_gb > 0:
        size_penalty = min(15.0, (vf.file_size_gb / 50.0) * 15.0) * weights.file_size
        components["file_size_penalty"] = -size_penalty

    # Language track scoring
    if primary_language:
        lang_score = score_language_tracks(
            vf, primary_language, extra_languages,
            missing_primary_penalty, extra_primary_track_bonus,
            extra_language_bonus,
        )
        if lang_score != 0:
            components["language_tracks"] = lang_score

    total = sum(components.values())
    vf.score = max(0.0, total)
    return vf.score


def score_breakdown(vf: VideoFile, weights: ScoringWeights | None = None,
                    primary_language: str = "",
                    extra_languages: list[str] | None = None,
                    missing_primary_penalty: float = 10.0,
                    extra_primary_track_bonus: float = 1.5,
                    extra_language_bonus: float = 1.5) -> dict[str, float]:
    """Return score breakdown by component (with weights applied)."""
    if weights is None:
        weights = ScoringWeights()

    result = {
        f"Resolution (x{weights.resolution})": score_resolution(vf) * weights.resolution,
        f"Video quality (x{weights.video_quality})": score_video_quality(vf) * weights.video_quality,
        f"HDR (x{weights.hdr})": score_hdr(vf) * weights.hdr,
        f"Audio (x{weights.audio})": score_audio(vf, primary_language) * weights.audio,
        f"Subtitles (x{weights.subtitles})": score_subtitles(vf) * weights.subtitles,
        f"Compatibility (x{weights.compatibility})": vf.compatibility_score * weights.compatibility,
        "Integrity": score_integrity(vf),
    }
    if vf.video.is_3d:
        result[f"3D format (x{weights.stereo3d})"] = score_3d(vf) * weights.stereo3d
    if vf.is_remaster:
        result[f"Master-grade (x{weights.remaster})"] = score_remaster(vf) * weights.remaster

    # Size efficiency (bloat penalty)
    if vf.file_size_gb > 0 and vf.video.bitrate > 0:
        bpp = bits_per_pixel(vf)
        if bpp > 0:
            ref_bpp = 0.10
            if bpp > ref_bpp:
                bloat_ratio = bpp / ref_bpp
                bloat_penalty = min(5.0, math.log2(bloat_ratio) * 2.0)
                result["Size efficiency"] = -bloat_penalty * weights.file_size
    if "Size efficiency" not in result and weights.file_size > 0 and vf.file_size_gb > 0:
        size_penalty = min(15.0, (vf.file_size_gb / 50.0) * 15.0) * weights.file_size
        result[f"File size penalty (x{weights.file_size})"] = -size_penalty

    if primary_language:
        lang_score = score_language_tracks(
            vf, primary_language, extra_languages,
            missing_primary_penalty, extra_primary_track_bonus,
            extra_language_bonus,
        )
        result["Language tracks"] = lang_score

    return result
