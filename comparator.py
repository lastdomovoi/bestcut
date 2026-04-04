"""Сравнение видеофайлов и форматирование результатов."""

from __future__ import annotations

import json
from pathlib import Path

from models import VideoFile
from profiles import ScoringWeights
from scoring import bits_per_pixel, effective_bitrate, score_breakdown


def compare_pair(a: VideoFile, b: VideoFile) -> dict:
    """Сравнить два видеофайла и определить лучший."""
    winner_abs = a if a.score > b.score else b
    loser_abs = b if winner_abs is a else a

    # Эффективность: качество на гигабайт
    eff_a = a.score / a.file_size_gb if a.file_size_gb > 0 else 0
    eff_b = b.score / b.file_size_gb if b.file_size_gb > 0 else 0
    winner_eff = a if eff_a > eff_b else b

    return {
        "file_a": a,
        "file_b": b,
        "absolute_winner": winner_abs,
        "efficiency_winner": winner_eff,
        "score_a": a.score,
        "score_b": b.score,
        "efficiency_a": eff_a,
        "efficiency_b": eff_b,
        "score_diff": abs(a.score - b.score),
        "size_diff_mb": abs(a.file_size - b.file_size) / (1024 ** 2),
    }


def format_file_info(vf: VideoFile, verbose: bool = False, weights: ScoringWeights | None = None) -> str:
    """Форматировать информацию о файле для вывода."""
    lines = []
    lines.append(f"  Файл: {vf.path.name}")
    lines.append(f"  Размер: {vf.file_size_human} | Длительность: {vf.duration_human}")

    v = vf.video
    codec_info = v.codec.upper()
    if v.profile:
        codec_info += f" {v.profile}"
    if v.level and v.level != "0":
        codec_info += f" L{v.level}"

    hdr_str = ""
    if v.hdr_type:
        hdr_str = f" | HDR: {v.hdr_type.upper()}"
    elif v.bit_depth > 8:
        hdr_str = f" | {v.bit_depth}-bit"

    br_mbps = v.bitrate / 1_000_000 if v.bitrate else 0
    lines.append(
        f"  Видео: {codec_info} | {v.width}x{v.height} ({v.resolution_label}) | "
        f"{v.fps:.2f} fps | {br_mbps:.1f} Мбит/с{hdr_str}"
    )

    # Аудио
    for i, at in enumerate(vf.audio_tracks):
        br_kbps = at.bitrate / 1000 if at.bitrate else 0
        lang = at.language or "?"
        title_str = f" «{at.title}»" if at.title else ""
        spatial_str = f" [{at.spatial_format}]" if at.spatial_format else ""
        lossless_str = " (lossless)" if at.is_lossless and not at.spatial_format else ""
        lines.append(
            f"  Аудио #{i + 1}: {at.format_label} {at.channel_label} "
            f"({at.channels}ch) | {br_kbps:.0f} кбит/с | "
            f"{lang}{spatial_str}{lossless_str}{title_str}"
        )

    # Субтитры
    if vf.subtitle_tracks:
        subs = []
        for st in vf.subtitle_tracks:
            s = st.language or "?"
            if st.forced:
                s += "(F)"
            s += f"/{st.codec}"
            subs.append(s)
        lines.append(f"  Субтитры: {', '.join(subs)}")

    # Целостность
    if vf.integrity.checked:
        if vf.integrity.is_ok:
            lines.append("  Целостность: ОК")
        else:
            lines.append(f"  Целостность: {vf.integrity.error_count} ошибок")
            if vf.integrity.is_truncated:
                lines.append("  ⚠ ФАЙЛ ОБРЕЗАН")
            if verbose:
                for err in vf.integrity.errors[:5]:
                    lines.append(f"    - {err}")

    # Совместимость
    if vf.compatibility_issues:
        lines.append(f"  Совместимость: {len(vf.compatibility_issues)} проблем")
        if verbose:
            for issue in vf.compatibility_issues:
                lines.append(f"    - {issue}")

    # Баллы
    if verbose:
        breakdown = score_breakdown(vf, weights)
        lines.append("  Баллы:")
        for name, val in breakdown.items():
            lines.append(f"    {name}: {val:.1f}")

    lines.append(f"  ИТОГО: {vf.score:.1f} баллов")

    return "\n".join(lines)


def format_comparison(comp: dict, verbose: bool = False) -> str:
    """Форматировать результат сравнения двух файлов."""
    a = comp["file_a"]
    b = comp["file_b"]
    lines = []

    lines.append("=" * 70)
    lines.append(format_file_info(a, verbose))
    lines.append("-" * 40)
    lines.append(format_file_info(b, verbose))
    lines.append("-" * 40)

    winner = comp["absolute_winner"]
    loser = a if winner is b else b

    if comp["score_diff"] < 2.0:
        lines.append(f"Результат: примерно равны (разница {comp['score_diff']:.1f})")
    else:
        lines.append(
            f"Лучший по качеству: {winner.path.name} "
            f"({winner.score:.1f} vs {loser.score:.1f}, +{comp['score_diff']:.1f})"
        )

    eff_winner = comp["efficiency_winner"]
    if eff_winner is not winner:
        lines.append(
            f"Лучший по эффективности (качество/размер): {eff_winner.path.name} "
            f"({comp[f'efficiency_{"a" if eff_winner is a else "b"}']:.1f} vs "
            f"{comp[f'efficiency_{"b" if eff_winner is a else "a"}']:.1f} балл/ГБ)"
        )

    if comp["size_diff_mb"] > 100:
        lines.append(f"Разница в размере: {comp['size_diff_mb']:.0f} МБ")

    lines.append("=" * 70)
    return "\n".join(lines)


def format_group_comparison(
    title: str,
    episode_key: tuple[int | None, int | None],
    files: list[VideoFile],
    verbose: bool = False,
    weights: ScoringWeights | None = None,
) -> str:
    """Форматировать сравнение группы файлов (один эпизод, несколько версий)."""
    lines = []

    season, episode = episode_key
    if season is not None and episode is not None:
        header = f"{title} — S{season:02d}E{episode:02d}"
    elif episode is not None:
        header = f"{title} — E{episode:02d}"
    else:
        header = title

    lines.append(f"\n{'=' * 70}")
    lines.append(f" {header}")
    lines.append(f"{'=' * 70}")

    if len(files) == 1:
        lines.append(format_file_info(files[0], verbose, weights))
        lines.append(f"  (единственная версия)")
        return "\n".join(lines)

    # Сортировка по баллам
    ranked = sorted(files, key=lambda f: f.score, reverse=True)

    for i, vf in enumerate(ranked):
        marker = " ★ ЛУЧШИЙ" if i == 0 else ""
        lines.append(f"\n  #{i + 1}{marker}")
        lines.append(format_file_info(vf, verbose, weights))

    # Сводка
    best = ranked[0]
    lines.append(f"\n  Рекомендация: {best.path.name}")
    if len(ranked) > 1:
        diff = best.score - ranked[1].score
        lines.append(f"  Преимущество: +{diff:.1f} баллов над следующим")

    return "\n".join(lines)


def export_json(files: list[VideoFile]) -> str:
    """Экспортировать результаты в JSON."""
    result = []
    for vf in files:
        entry = {
            "path": str(vf.path),
            "file_size": vf.file_size,
            "file_size_human": vf.file_size_human,
            "duration": vf.duration,
            "duration_human": vf.duration_human,
            "container": vf.container,
            "video": {
                "codec": vf.video.codec,
                "profile": vf.video.profile,
                "level": vf.video.level,
                "resolution": f"{vf.video.width}x{vf.video.height}",
                "resolution_label": vf.video.resolution_label,
                "bitrate": vf.video.bitrate,
                "fps": vf.video.fps,
                "bit_depth": vf.video.bit_depth,
                "hdr_type": vf.video.hdr_type,
                "effective_bitrate": effective_bitrate(vf),
                "bits_per_pixel": bits_per_pixel(vf),
            },
            "audio_tracks": [
                {
                    "codec": at.codec,
                    "format_label": at.format_label,
                    "profile": at.profile,
                    "channels": at.channels,
                    "channel_layout": at.channel_label,
                    "bitrate": at.bitrate,
                    "language": at.language,
                    "has_atmos": at.has_atmos,
                    "has_dtsx": at.has_dtsx,
                    "is_lossless": at.is_lossless,
                    "spatial_format": at.spatial_format,
                }
                for at in vf.audio_tracks
            ],
            "subtitle_tracks": [
                {
                    "codec": st.codec,
                    "language": st.language,
                    "forced": st.forced,
                    "is_bitmap": st.is_bitmap,
                }
                for st in vf.subtitle_tracks
            ],
            "integrity": {
                "checked": vf.integrity.checked,
                "is_ok": vf.integrity.is_ok if vf.integrity.checked else None,
                "error_count": vf.integrity.error_count,
                "is_truncated": vf.integrity.is_truncated,
            },
            "compatibility_score": vf.compatibility_score,
            "compatibility_issues": vf.compatibility_issues,
            "score": vf.score,
            "score_breakdown": score_breakdown(vf),
            "series_title": vf.series_title,
            "season": vf.season,
            "episode": vf.episode,
        }
        result.append(entry)

    return json.dumps(result, ensure_ascii=False, indent=2)
