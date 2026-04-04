"""Алгоритм скоринга видеофайлов для сравнения качества."""

from __future__ import annotations

from models import VideoFile
from profiles import ScoringWeights

# Коэффициенты эффективности кодеков относительно H.264.
# При одинаковом битрейте HEVC даёт ~1.5x лучшее качество.
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

# Базовый ранг аудиокодеков (без учёта spatial/lossless бонусов)
AUDIO_CODEC_RANK = {
    "truehd": 10,
    "flac": 8,
    "pcm_s24le": 8,
    "pcm_s32le": 8,
    "alac": 8,
    "eac3": 7,
    "dts": 6,   # базовый DTS; DTS-HD MA/DTS:X получают бонусы
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
    """Вычислить эффективный битрейт (нормализованный по эффективности кодека)."""
    efficiency = CODEC_EFFICIENCY.get(vf.video.codec, 1.0)
    return vf.video.bitrate * efficiency


def bits_per_pixel(vf: VideoFile) -> float:
    """Битрейт на пиксель на кадр — мера качества, не зависящая от разрешения."""
    pixels = vf.video.pixels
    fps = vf.video.fps
    if pixels == 0 or fps == 0:
        return 0.0
    eff_br = effective_bitrate(vf)
    return eff_br / (pixels * fps)


def score_resolution(vf: VideoFile) -> float:
    """Оценка разрешения (0-30 баллов). 4K = 30, 1080p ≈ 7.5, 720p ≈ 3.3."""
    ref_pixels = 3840 * 2160  # 4K
    return min(30.0, (vf.video.pixels / ref_pixels) * 30.0)


def score_video_quality(vf: VideoFile) -> float:
    """Оценка качества видео по эффективному битрейту (0-25 баллов).

    Используем bits-per-pixel (BPP) нормализованный по кодеку.
    Хороший BPP для H.264: 0.05-0.15, отличный: >0.15.
    """
    bpp = bits_per_pixel(vf)
    if bpp <= 0:
        return 0.0
    # Нелинейная шкала: быстрый рост до 0.10, потом замедление
    # 0.05 = ~12.5, 0.10 = ~20, 0.15 = ~25
    normalized = min(1.0, bpp / 0.15)
    return normalized * 25.0


def score_hdr(vf: VideoFile) -> float:
    """Оценка HDR (0-10 баллов)."""
    hdr_scores = {
        "dolby_vision": 10.0,
        "hdr10+": 9.0,
        "hdr10": 8.0,
        "hlg": 7.0,
    }
    score = hdr_scores.get(vf.video.hdr_type, 0.0)
    # Бонус за 10-bit даже без HDR
    if score == 0 and vf.video.bit_depth >= 10:
        score = 4.0
    return score


def _audio_track_rank(a) -> tuple:
    """Ранг аудиотрека для сортировки (больше = лучше)."""
    # Приоритет: Atmos > DTS:X > lossless > lossy
    spatial = 2 if a.has_atmos else (1 if a.has_dtsx else 0)
    lossless = 1 if a.is_lossless else 0
    codec_rank = AUDIO_CODEC_RANK.get(a.codec, 0)
    # DTS-HD MA получает ранг выше базового DTS
    if a.codec in ("dts", "dca") and a.is_lossless:
        codec_rank = 9
    return (spatial, lossless, codec_rank, a.channels, a.bitrate)


def score_audio(vf: VideoFile) -> float:
    """Оценка аудио (0-25 баллов).

    Разбивка:
    - Пространственное аудио (0-8): Atmos/DTS:X
    - Кодек + lossless (0-6): качество кодека
    - Каналы (0-4): объёмность
    - Битрейт (0-4): достаточность потока
    - Мультиязычность (0-3): наличие нескольких языков
    """
    if not vf.audio_tracks:
        return 0.0

    # Лучший аудиотрек
    best = max(vf.audio_tracks, key=_audio_track_rank)

    # Пространственное аудио (0-8)
    if best.has_atmos:
        spatial_score = 8.0   # Dolby Atmos — максимум
    elif best.has_dtsx:
        spatial_score = 7.0   # DTS:X — близко к Atmos
    else:
        spatial_score = 0.0

    # Кодек + lossless (0-6)
    base_rank = AUDIO_CODEC_RANK.get(best.codec, 0)
    if best.codec in ("dts", "dca") and best.is_lossless:
        base_rank = 9  # DTS-HD MA
    codec_score = min(5.0, base_rank * 0.5)
    if best.is_lossless:
        codec_score += 1.0  # бонус за lossless
    codec_score = min(6.0, codec_score)

    # Каналы (0-4): 7.1=4, 5.1=3, stereo=1
    channel_score = min(4.0, (best.channels / 8) * 4.0)

    # Битрейт (0-4)
    if best.bitrate > 0:
        # TrueHD Atmos может быть до 18 Мбит/с, нормализуем по 5 Мбит/с
        bitrate_score = min(4.0, (best.bitrate / 5_000_000) * 4.0)
    elif best.is_lossless:
        bitrate_score = 3.0  # lossless без указанного битрейта — ОК
    else:
        bitrate_score = 1.0

    # Мультиязычность (0-3)
    languages = {at.language for at in vf.audio_tracks if at.language}
    multi_score = min(3.0, len(languages) * 1.0)

    return spatial_score + codec_score + channel_score + bitrate_score + multi_score


def score_subtitles(vf: VideoFile) -> float:
    """Оценка субтитров (0-5 баллов)."""
    if not vf.subtitle_tracks:
        return 0.0

    languages = {st.language for st in vf.subtitle_tracks if st.language}
    has_forced = any(st.forced for st in vf.subtitle_tracks)

    score = min(3.5, len(languages) * 1.0)
    if has_forced:
        score += 1.5

    return min(5.0, score)


def score_integrity(vf: VideoFile) -> float:
    """Штраф за проблемы целостности (отрицательное значение)."""
    penalty = 0.0
    if vf.integrity.checked:
        if vf.integrity.is_truncated:
            penalty += 50.0
        penalty += min(30.0, vf.integrity.error_count * 2.0)
    return -penalty


def calculate_score(vf: VideoFile, weights: ScoringWeights | None = None) -> float:
    """Рассчитать общий балл качества видеофайла.

    Веса (weights) — множители для каждого компонента.
    file_size > 0 добавляет штраф за размер файла.
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
        "integrity": score_integrity(vf),  # штрафы не масштабируются
    }

    # Штраф за размер файла (если включён)
    if weights.file_size > 0 and vf.file_size_gb > 0:
        # Нормализуем: 1 ГБ = 0 штрафа, 50 ГБ = -15 штрафа
        size_penalty = min(15.0, (vf.file_size_gb / 50.0) * 15.0) * weights.file_size
        components["file_size_penalty"] = -size_penalty

    total = sum(components.values())
    vf.score = max(0.0, total)
    return vf.score


def score_breakdown(vf: VideoFile, weights: ScoringWeights | None = None) -> dict[str, float]:
    """Вернуть разбивку баллов по компонентам (с учётом весов)."""
    if weights is None:
        weights = ScoringWeights()

    result = {
        f"Разрешение (×{weights.resolution})": score_resolution(vf) * weights.resolution,
        f"Качество видео (×{weights.video_quality})": score_video_quality(vf) * weights.video_quality,
        f"HDR (×{weights.hdr})": score_hdr(vf) * weights.hdr,
        f"Аудио (×{weights.audio})": score_audio(vf) * weights.audio,
        f"Субтитры (×{weights.subtitles})": score_subtitles(vf) * weights.subtitles,
        f"Совместимость (×{weights.compatibility})": vf.compatibility_score * weights.compatibility,
        "Целостность": score_integrity(vf),
    }

    if weights.file_size > 0 and vf.file_size_gb > 0:
        size_penalty = min(15.0, (vf.file_size_gb / 50.0) * 15.0) * weights.file_size
        result[f"Штраф за размер (×{weights.file_size})"] = -size_penalty

    return result
