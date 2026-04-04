"""Video file comparison and result formatting."""

from __future__ import annotations

import json
from pathlib import Path

from models import VideoFile
from profiles import ScoringWeights
from scoring import bits_per_pixel, effective_bitrate, score_breakdown


def compare_pair(a: VideoFile, b: VideoFile) -> dict:
    """Compare two video files and determine the winner."""
    winner_abs = a if a.score > b.score else b
    loser_abs = b if winner_abs is a else a

    # Efficiency: quality per gigabyte
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
    """Format file information for output."""
    lines = []
    lines.append(f"  File: {vf.path.name}")
    lines.append(f"  Size: {vf.file_size_human} | Duration: {vf.duration_human}")

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
        f"  Video: {codec_info} | {v.width}x{v.height} ({v.resolution_label}) | "
        f"{v.fps:.2f} fps | {br_mbps:.1f} Mbps{hdr_str}"
    )

    # Audio
    for i, at in enumerate(vf.audio_tracks):
        br_kbps = at.bitrate / 1000 if at.bitrate else 0
        lang = at.language or "?"
        title_str = f" «{at.title}»" if at.title else ""
        spatial_str = f" [{at.spatial_format}]" if at.spatial_format else ""
        lossless_str = " (lossless)" if at.is_lossless and not at.spatial_format else ""
        lines.append(
            f"  Audio #{i + 1}: {at.format_label} {at.channel_label} "
            f"({at.channels}ch) | {br_kbps:.0f} kbps | "
            f"{lang}{spatial_str}{lossless_str}{title_str}"
        )

    # Subtitles
    if vf.subtitle_tracks:
        subs = []
        for st in vf.subtitle_tracks:
            s = st.language or "?"
            if st.forced:
                s += "(F)"
            s += f"/{st.codec}"
            subs.append(s)
        lines.append(f"  Subtitles: {', '.join(subs)}")

    # Integrity
    if vf.integrity.checked:
        if vf.integrity.is_ok:
            lines.append("  Integrity: OK")
        else:
            lines.append(f"  Integrity: {vf.integrity.error_count} errors")
            if vf.integrity.is_truncated:
                lines.append("  ⚠ FILE TRUNCATED")
            if verbose:
                for err in vf.integrity.errors[:5]:
                    lines.append(f"    - {err}")

    # Compatibility
    if vf.compatibility_issues:
        lines.append(f"  Compatibility: {len(vf.compatibility_issues)} issues")
        if verbose:
            for issue in vf.compatibility_issues:
                lines.append(f"    - {issue}")

    # Score
    if verbose:
        breakdown = score_breakdown(vf, weights)
        lines.append("  Score:")
        for name, val in breakdown.items():
            lines.append(f"    {name}: {val:.1f}")

    lines.append(f"  TOTAL: {vf.score:.1f} points")

    return "\n".join(lines)


def format_comparison(comp: dict, verbose: bool = False) -> str:
    """Format comparison result of two files."""
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
        lines.append(f"Result: roughly equal (difference {comp['score_diff']:.1f})")
    else:
        lines.append(
            f"Best quality: {winner.path.name} "
            f"({winner.score:.1f} vs {loser.score:.1f}, +{comp['score_diff']:.1f})"
        )

    eff_winner = comp["efficiency_winner"]
    if eff_winner is not winner:
        lines.append(
            f"Best efficiency (quality/size): {eff_winner.path.name} "
            f"({comp[f'efficiency_{"a" if eff_winner is a else "b"}']:.1f} vs "
            f"{comp[f'efficiency_{"b" if eff_winner is a else "a"}']:.1f} pts/GB)"
        )

    if comp["size_diff_mb"] > 100:
        lines.append(f"Size difference: {comp['size_diff_mb']:.0f} MB")

    lines.append("=" * 70)
    return "\n".join(lines)


def _render_table(headers: list[str], rows: list[list[str]], highlight_row: int = 0) -> str:
    """Render a simple ASCII table. First row (highlight_row) gets a ★ marker."""
    # Calculate column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    # Header
    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    hdr = "| " + " | ".join(h.ljust(w) for h, w in zip(headers, col_widths)) + " |"
    lines = [sep, hdr, sep]

    # Rows
    for i, row in enumerate(rows):
        cells = []
        for j, (cell, w) in enumerate(zip(row, col_widths)):
            # Right-align numeric columns (all except first)
            if j == 0:
                cells.append(cell.ljust(w))
            else:
                cells.append(cell.rjust(w))
        lines.append("| " + " | ".join(cells) + " |")

    lines.append(sep)
    return "\n".join(lines)


def _audio_summary(vf: VideoFile) -> str:
    """Short audio summary for table cell (best track only)."""
    if not vf.audio_tracks:
        return "-"
    best = vf.audio_tracks[0]
    for at in vf.audio_tracks:
        if at.effective_channels > best.effective_channels:
            best = at
    parts = [best.format_label, best.channel_label]
    if best.spatial_format:
        parts.append(best.spatial_format)
    elif best.is_lossless:
        parts.append("lossless")
    return " ".join(parts)


def _audio_tracks_detail(vf: VideoFile) -> list[str]:
    """List of all audio tracks as formatted strings."""
    lines = []
    for i, at in enumerate(vf.audio_tracks):
        br_kbps = at.bitrate / 1000 if at.bitrate else 0
        lang = at.language or "?"
        parts = [f"{at.format_label} {at.channel_label}"]
        parts.append(f"{br_kbps:.0f}k")
        parts.append(lang)
        if at.spatial_format:
            parts.append(at.spatial_format)
        elif at.is_lossless:
            parts.append("lossless")
        if at.title:
            parts.append(f"«{at.title}»")
        lines.append(" ".join(parts))
    return lines


def _subs_summary(vf: VideoFile) -> str:
    """Short subtitles summary for table cell."""
    if not vf.subtitle_tracks:
        return "-"
    langs = []
    for st in vf.subtitle_tracks:
        s = st.language or "?"
        if st.forced:
            s += "(F)"
        langs.append(s)
    return ", ".join(langs)


def format_group_comparison(
    title: str,
    episode_key: tuple[int | None, int | None],
    files: list[VideoFile],
    verbose: bool = False,
    weights: ScoringWeights | None = None,
) -> str:
    """Format comparison of a file group (one episode, multiple versions)."""
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
        lines.append(f"  (only version)")
        return "\n".join(lines)

    # Sort by score
    ranked = sorted(files, key=lambda f: f.score, reverse=True)

    # Build comparison table
    v_br = lambda vf: f"{vf.video.bitrate / 1_000_000:.1f}" if vf.video.bitrate else "?"

    if verbose:
        bd = [score_breakdown(vf, weights) for vf in ranked]
        bd_keys = list(bd[0].keys())

        headers = ["", "#1 ★ BEST", *[f"#{i+2}" for i in range(len(ranked)-1)]]
        rows = [
            ["File", *[vf.path.name for vf in ranked]],
            ["Size", *[vf.file_size_human for vf in ranked]],
            ["Duration", *[vf.duration_human for vf in ranked]],
            ["Video", *[f"{vf.video.codec.upper()} {vf.video.resolution_label}" for vf in ranked]],
            ["Bitrate", *[f"{v_br(vf)} Mbps" for vf in ranked]],
            ["FPS", *[f"{vf.video.fps:.0f}" for vf in ranked]],
            ["HDR", *[vf.video.hdr_type.upper() if vf.video.hdr_type else "-" for vf in ranked]],
            ["Aspect", *[
                f"{vf.video.real_aspect_ratio}"
                + (f" (encoded {vf.video.encoded_aspect_ratio})" if vf.video.has_black_bars else "")
                for vf in ranked
            ]],
            ["Black bars", *[
                f"YES ({vf.video.crop_width}x{vf.video.crop_height} of {vf.video.width}x{vf.video.height})"
                if vf.video.has_black_bars else "-"
                for vf in ranked
            ]],
            ["3D", *[f"{vf.video.stereo_mode}" if vf.video.is_3d else "-" for vf in ranked]],
            ["Upscale", *[
                f"YES (real ~{vf.video.real_height}p)" if vf.video.is_upscaled and vf.video.real_height
                else ("YES" if vf.video.is_upscaled else "-")
                for vf in ranked
            ]],
        ]
        # All audio tracks
        max_audio = max(len(vf.audio_tracks) for vf in ranked) if ranked else 0
        for ai in range(max_audio):
            label = f"Audio #{ai + 1}"
            cells = []
            for vf in ranked:
                if ai < len(vf.audio_tracks):
                    at = vf.audio_tracks[ai]
                    tracks = _audio_tracks_detail(vf)
                    cells.append(tracks[ai])
                else:
                    cells.append("-")
            rows.append([label, *cells])

        rows.append(["Subtitles", *[_subs_summary(vf) for vf in ranked]])
        rows.append(["Compat issues", *[str(len(vf.compatibility_issues)) for vf in ranked]])
        rows.append(["", *["" for _ in ranked]])

        # Score breakdown
        for key in bd_keys:
            label = key.split("(")[0].strip()
            rows.append([label, *[f"{d[key]:.1f}" for d in bd]])

        rows.append(["", *["" for _ in ranked]])
        rows.append(["TOTAL", *[f"{vf.score:.1f}" for vf in ranked]])
    else:
        headers = ["", "#1 ★ BEST", *[f"#{i+2}" for i in range(len(ranked)-1)]]
        rows = [
            ["File", *[vf.path.name for vf in ranked]],
            ["Size", *[vf.file_size_human for vf in ranked]],
            ["Video", *[f"{vf.video.codec.upper()} {vf.video.resolution_label} {v_br(vf)} Mbps" for vf in ranked]],
            ["FPS", *[f"{vf.video.fps:.0f}" for vf in ranked]],
            ["Audio", *[_audio_summary(vf) for vf in ranked]],
            ["Subtitles", *[_subs_summary(vf) for vf in ranked]],
        ]
        # Show upscale/3D only if detected in any file
        if any(vf.video.is_upscaled for vf in ranked):
            rows.append(["Upscale", *[
                f"YES (~{vf.video.real_height}p)" if vf.video.is_upscaled and vf.video.real_height
                else ("-" if not vf.video.is_upscaled else "YES")
                for vf in ranked
            ]])
        if any(vf.video.is_3d for vf in ranked):
            rows.append(["3D", *[vf.video.stereo_mode if vf.video.is_3d else "-" for vf in ranked]])
        rows.extend([
            ["Compat issues", *[str(len(vf.compatibility_issues)) for vf in ranked]],
            ["TOTAL", *[f"{vf.score:.1f} pts" for vf in ranked]],
        ])

    lines.append(_render_table(headers, rows))

    # Recommendation
    best = ranked[0]
    diff = best.score - ranked[1].score
    if diff < 2.0:
        lines.append(f"  Result: roughly equal (difference {diff:.1f})")
    else:
        lines.append(f"  Recommendation: {best.path.name} (+{diff:.1f} pts)")

    # Duration difference warning
    if len(ranked) >= 2:
        durations = [vf.duration for vf in ranked if vf.duration > 0]
        if len(durations) >= 2:
            max_dur = max(durations)
            min_dur = min(durations)
            diff_sec = max_dur - min_dur
            if diff_sec > 10:
                diff_m, diff_s = divmod(int(diff_sec), 60)
                pct = (diff_sec / max_dur) * 100
                longest = [vf for vf in ranked if vf.duration == max_dur][0]
                shortest = [vf for vf in ranked if vf.duration == min_dur][0]
                if diff_sec > 300:
                    lines.append(
                        f"  !! Duration differs by {diff_m}m{diff_s:02d}s ({pct:.1f}%) — "
                        f"likely different edit/version"
                    )
                else:
                    lines.append(
                        f"  ~ Duration differs by {diff_m}m{diff_s:02d}s ({pct:.1f}%)")

    return "\n".join(lines)


def export_json(files: list[VideoFile]) -> str:
    """Export results to JSON."""
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
