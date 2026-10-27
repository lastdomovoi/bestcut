"""Video file comparison and result formatting."""

from __future__ import annotations

import json
from pathlib import Path

from models import VideoFile
from profiles import ScoringWeights
from scoring import audio_quality_flags, bits_per_pixel, effective_bitrate, score_breakdown


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


def format_file_info(vf: VideoFile, verbose: bool = False, weights: ScoringWeights | None = None, cfg=None) -> str:
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
            f"{lang}{spatial_str}{lossless_str}{title_str} {_audio_quality_label(at)}".rstrip()
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
        _lang_kw = {}
        if cfg and cfg.primary_language:
            _lang_kw = {
                "primary_language": cfg.primary_language,
                "extra_languages": cfg.extra_languages,
                "missing_primary_penalty": cfg.missing_primary_penalty,
                "extra_primary_track_bonus": cfg.extra_primary_track_bonus,
                "extra_language_bonus": cfg.extra_language_bonus,
            }
        breakdown = score_breakdown(vf, weights, **_lang_kw)
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
        ew_key = "efficiency_a" if eff_winner is a else "efficiency_b"
        el_key = "efficiency_b" if eff_winner is a else "efficiency_a"
        lines.append(
            f"Best efficiency (quality/size): {eff_winner.path.name} "
            f"({comp[ew_key]:.1f} vs {comp[el_key]:.1f} pts/GB)"
        )

    if comp["size_diff_mb"] > 100:
        lines.append(f"Size difference: {comp['size_diff_mb']:.0f} MB")

    lines.append("=" * 70)
    return "\n".join(lines)


import re as _re

# ANSI color codes
_GREEN = "\033[32m"
_RED = "\033[31m"
_RESET = "\033[0m"
_BOLD = "\033[1m"


def _colorize_row(label: str, cells: list[str], higher_is_better: bool = True,
                  threshold: float = 0.1) -> list[str]:
    """Colorize cells: green for best, red for worst if they differ significantly.

    Tries to parse numeric values from cells. If all cells are parseable and
    differ by more than threshold (relative), apply colors.
    """
    if len(cells) < 2:
        return cells

    # Try to extract numeric values
    vals: list[float | None] = []
    for c in cells:
        # Strip ANSI, units, and common suffixes
        clean = _re.sub(r"\033\[[0-9;]*m", "", c).strip()
        clean = _re.sub(r"\s*(Mbps|GB|MB|kbps|kHz|k|pts|points|tracks|issues|bit|-bit|%)\s*$", "", clean, flags=_re.IGNORECASE)
        clean = clean.replace(",", "").strip()
        if clean in ("-", "", "?"):
            vals.append(None)
            continue
        try:
            vals.append(float(clean))
        except ValueError:
            vals.append(None)

    # Need at least 2 valid numbers
    valid = [v for v in vals if v is not None]
    if len(valid) < 2:
        return cells

    vmin, vmax = min(valid), max(valid)
    if vmax == 0:
        return cells
    # Check if difference is significant
    rel_diff = (vmax - vmin) / vmax if vmax != 0 else 0
    if rel_diff < threshold:
        return cells

    # Apply colors
    result = []
    for c, v in zip(cells, vals):
        if v is None:
            result.append(c)
            continue
        if higher_is_better:
            if v == vmax:
                result.append(f"{_GREEN}{c}{_RESET}")
            elif v == vmin:
                result.append(f"{_RED}{c}{_RESET}")
            else:
                result.append(c)
        else:
            if v == vmin:
                result.append(f"{_GREEN}{c}{_RESET}")
            elif v == vmax:
                result.append(f"{_RED}{c}{_RESET}")
            else:
                result.append(c)
    return result


# Rules: label -> (higher_is_better, threshold)
_COLOR_RULES: dict[str, tuple[bool, float]] = {
    "Bitrate": (True, 0.1),
    "BPP": (True, 0.15),
    "FPS": (True, 0.1),            # Context-dependent, but show diff
    "Bit depth": (True, 0.05),
    "Compat issues": (False, 0.2),
    "Audio (rus)": (True, 0.3),
    "Audio total": (True, 0.3),
    # Score breakdown rows
    "Resolution": (True, 0.1),
    "Video quality": (True, 0.1),
    "HDR": (True, 0.1),
    "Audio": (True, 0.1),
    "Subtitles": (True, 0.1),
    "Compatibility": (True, 0.1),
    "Integrity": (True, 0.1),
    "Language tracks": (True, 0.1),
    "Size efficiency": (True, 0.1),
    "3D format": (True, 0.1),
    "Remaster": (True, 0.1),
    "TOTAL": (True, 0.01),
}


def _visible_len(s: str) -> int:
    """Length of string excluding ANSI escape codes."""
    return len(_re.sub(r"\033\[[0-9;]*m", "", s))


def _render_table(headers: list[str], rows: list[list[str]], highlight_row: int = 0) -> str:
    """Render a simple ASCII table. First row (highlight_row) gets a ★ marker."""
    # Calculate column widths based on visible (non-ANSI) length
    col_widths = [_visible_len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], _visible_len(cell))

    # Header
    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    hdr = "| " + " | ".join(h.ljust(w) for h, w in zip(headers, col_widths)) + " |"
    lines = [sep, hdr, sep]

    # Rows
    for i, row in enumerate(rows):
        cells = []
        for j, (cell, w) in enumerate(zip(row, col_widths)):
            vlen = _visible_len(cell)
            pad = w - vlen
            # Right-align numeric columns (all except first)
            if j == 0:
                cells.append(cell + " " * pad)
            else:
                cells.append(" " * pad + cell)
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
    if best.language:
        parts.append(best.language)
    return " ".join(parts)


def _stereo_label(vf: VideoFile) -> str:
    """3D cell: layout, full/half resolution and per-eye size."""
    v = vf.video
    if not v.is_3d:
        return "-"
    label = v.stereo_mode
    if v.stereo_divisor > 1:
        label += f" {'full' if v.stereo_full_res else 'half'} ({v.eye_width}x{v.eye_height}/eye)"
    return label


def _audio_quality_label(at) -> str:
    """Short audio quality note (noise / clipping / band-limited)."""
    flags = audio_quality_flags(at)
    return f"[{', '.join(flags)}]" if flags else ""


def _audio_tracks_detail(vf: VideoFile) -> list[str]:
    """List of all audio tracks as formatted strings."""
    lines = []
    for i, at in enumerate(vf.audio_tracks):
        br_kbps = at.bitrate / 1000 if at.bitrate else 0
        lang = at.language or "?"
        sr = f"{at.sample_rate // 1000}kHz" if at.sample_rate else ""
        parts = [f"{at.format_label} {at.channel_label}"]
        parts.append(f"{br_kbps:.0f}k")
        if sr:
            parts.append(sr)
        parts.append(lang)
        if at.spatial_format:
            parts.append(at.spatial_format)
        elif at.is_lossless:
            parts.append("lossless")
        if at.title:
            parts.append(f"«{at.title}»")
        quality = _audio_quality_label(at)
        if quality:
            parts.append(quality)
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
    cfg=None,
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
        _lang_kw = {}
        if cfg and cfg.primary_language:
            _lang_kw = {
                "primary_language": cfg.primary_language,
                "extra_languages": cfg.extra_languages,
                "missing_primary_penalty": cfg.missing_primary_penalty,
                "extra_primary_track_bonus": cfg.extra_primary_track_bonus,
                "extra_language_bonus": cfg.extra_language_bonus,
            }
        bd = [score_breakdown(vf, weights, **_lang_kw) for vf in ranked]
        # Merge all keys preserving order
        bd_keys = []
        seen = set()
        for d in bd:
            for k in d:
                if k not in seen:
                    bd_keys.append(k)
                    seen.add(k)

        headers = ["", "#1 ★ BEST", *[f"#{i+2}" for i in range(len(ranked)-1)]]
        _bpp = lambda vf: f"{bits_per_pixel(vf):.4f}" if bits_per_pixel(vf) > 0 else "?"
        rows = [
            ["File", *[vf.path.name for vf in ranked]],
            ["Source", *[vf.source_type or "-" for vf in ranked]],
            ["Remastered", *[", ".join(vf.remaster_evidence) if vf.is_remaster else "-" for vf in ranked]],
            ["Container", *[vf.container.split(",")[0] for vf in ranked]],
            ["Size", *[vf.file_size_human for vf in ranked]],
            ["Duration", *[vf.duration_human for vf in ranked]],
            ["Video", *[f"{vf.video.codec.upper()} {vf.video.profile} L{vf.video.level}" if vf.video.profile else f"{vf.video.codec.upper()}" for vf in ranked]],
            ["Encoder", *[vf.video.encoder or "-" for vf in ranked]],
            ["Resolution", *[f"{vf.video.width}x{vf.video.height} ({vf.video.resolution_label})" for vf in ranked]],
            ["Bitrate", *[f"{v_br(vf)} Mbps" for vf in ranked]],
            ["BPP", *[_bpp(vf) for vf in ranked]],
            ["FPS", *[f"{vf.video.fps:.2f}" for vf in ranked]],
            ["Bit depth", *[f"{vf.video.bit_depth}-bit" for vf in ranked]],
            ["Color space", *[vf.video.color_space or "-" for vf in ranked]],
            ["Color transfer", *[vf.video.color_transfer or "-" for vf in ranked]],
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
            ["3D", *[_stereo_label(vf) for vf in ranked]],
            ["Upscale", *[
                f"YES (real ~{vf.video.real_height}p)" if vf.video.is_upscaled and vf.video.real_height
                else ("YES" if vf.video.is_upscaled else "-")
                for vf in ranked
            ]],
        ]
        # Audio summary in main table
        rows.append(["Audio (best)", *[_audio_summary(vf) for vf in ranked]])
        if cfg and cfg.primary_language:
            lang = cfg.primary_language
            rows.append([f"Audio ({lang})", *[
                f"{sum(1 for at in vf.audio_tracks if at.language == lang)} tracks"
                for vf in ranked
            ]])
        rows.append(["Audio total", *[f"{len(vf.audio_tracks)} tracks" for vf in ranked]])
        rows.append(["Subtitles", *[_subs_summary(vf) for vf in ranked]])
        rows.append(["Compat issues", *[str(len(vf.compatibility_issues)) for vf in ranked]])
        rows.append(["", *["" for _ in ranked]])

        # Score breakdown
        for key in bd_keys:
            label = key.split("(")[0].strip()
            rows.append([label, *[f"{d.get(key, 0.0):.1f}" for d in bd]])

        rows.append(["", *["" for _ in ranked]])
        # Use vf.score (clamped to 0) — matches calculate_score behavior
        rows.append(["TOTAL", *[f"{max(0.0, vf.score):.1f}" for vf in ranked]])

        # Apply color highlighting
        for i, row in enumerate(rows):
            label = row[0]
            rule = _COLOR_RULES.get(label)
            if rule:
                higher_better, thresh = rule
                row[1:] = _colorize_row(label, row[1:], higher_better, thresh)

        # Bold TOTAL row after colorization
        for i, row in enumerate(rows):
            if row[0] == "TOTAL":
                row[1:] = [f"{_BOLD}{c}{_RESET}" for c in row[1:]]

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
            rows.append(["3D", *[_stereo_label(vf) for vf in ranked]])
        if any(vf.is_remaster for vf in ranked):
            rows.append(["Remastered", *["YES" if vf.is_remaster else "-" for vf in ranked]])
        rows.extend([
            ["Compat issues", *[str(len(vf.compatibility_issues)) for vf in ranked]],
            ["TOTAL", *[f"{vf.score:.1f} pts" for vf in ranked]],
        ])

    lines.append(_render_table(headers, rows))

    # Audio detail table (verbose only)
    if verbose and len(ranked) >= 2:
        max_audio = max(len(vf.audio_tracks) for vf in ranked) if ranked else 0
        if max_audio > 0:
            lines.append(f"\n  Audio tracks:")
            a_headers = ["", *[f"#{i+1}" for i in range(len(ranked))]]
            a_rows = []
            for ai in range(max_audio):
                cells = []
                for vf in ranked:
                    if ai < len(vf.audio_tracks):
                        tracks = _audio_tracks_detail(vf)
                        cells.append(tracks[ai])
                    else:
                        cells.append("-")
                a_rows.append([f"Track #{ai+1}", *cells])
            lines.append(_render_table(a_headers, a_rows))

    # Recommendation
    best = ranked[0]
    if len(ranked) < 2:
        lines.append(f"  (only version)")
        return "\n".join(lines)
    diff = best.score - ranked[1].score
    if diff < 2.0:
        lines.append(f"  Result: roughly equal (difference {diff:.1f})")
    else:
        lines.append(f"  {_GREEN}{_BOLD}Recommendation: {best.path.name} (+{diff:.1f} pts){_RESET}")

    # Key differences summary
    if len(ranked) >= 2:
        diffs = _summarize_differences(ranked)
        if diffs:
            lines.append(f"\n  Key differences:")
            for d in diffs:
                lines.append(f"    {d}")

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
                if diff_sec > 300:
                    lines.append(
                        f"  {_RED}!! Duration differs by {diff_m}m{diff_s:02d}s ({pct:.1f}%) — "
                        f"likely different edit/version{_RESET}"
                    )
                else:
                    lines.append(
                        f"  ~ Duration differs by {diff_m}m{diff_s:02d}s ({pct:.1f}%)")

    return "\n".join(lines)


def _summarize_differences(ranked: list[VideoFile]) -> list[str]:
    """Generate a list of key differences between ranked files."""
    best = ranked[0]
    rest = ranked[1:]
    diffs = []

    for other in rest:
        tag = other.path.name

        # Video bitrate
        if best.video.bitrate and other.video.bitrate:
            br_ratio = best.video.bitrate / other.video.bitrate if other.video.bitrate else 0
            if br_ratio > 1.2:
                diffs.append(f"{_GREEN}+{_RESET} Video bitrate {best.video.bitrate/1e6:.1f} vs {other.video.bitrate/1e6:.1f} Mbps")
            elif br_ratio < 0.8:
                diffs.append(f"{_RED}-{_RESET} Video bitrate {best.video.bitrate/1e6:.1f} vs {other.video.bitrate/1e6:.1f} Mbps")

        # Resolution
        if best.video.height != other.video.height:
            diffs.append(f"Resolution: {best.video.resolution_label} vs {other.video.resolution_label}")

        # Codec
        if best.video.codec != other.video.codec:
            diffs.append(f"Codec: {best.video.codec.upper()} vs {other.video.codec.upper()}")

        # FPS
        if best.video.fps != other.video.fps:
            diffs.append(f"FPS: {best.video.fps:.0f} vs {other.video.fps:.0f}")

        # HDR
        if best.video.hdr_type != other.video.hdr_type:
            diffs.append(f"HDR: {best.video.hdr_type or 'SDR'} vs {other.video.hdr_type or 'SDR'}")

        # Aspect ratio / black bars
        if best.video.has_black_bars != other.video.has_black_bars:
            other_num = ranked.index(other) + 1
            if other.video.has_black_bars:
                diffs.append(f"{_RED}-{_RESET} #{other_num} has black bars")
            else:
                diffs.append(f"{_RED}-{_RESET} #1 has black bars")

        # Upscale
        if other.video.is_upscaled and not best.video.is_upscaled:
            h = f" (~{other.video.real_height}p)" if other.video.real_height else ""
            diffs.append(f"{_RED}-{_RESET} #{ranked.index(other)+1} is upscaled{h}")

        # Audio: fake channels
        for at in other.audio_tracks:
            if at.real_channels is not None and at.real_channels < at.channels:
                diffs.append(f"{_RED}-{_RESET} #{ranked.index(other)+1} has fake {at.channel_label}")
                break

        # Audio channels (best track)
        best_ch = max((at.effective_channels for at in best.audio_tracks), default=0)
        other_ch = max((at.effective_channels for at in other.audio_tracks), default=0)
        if best_ch != other_ch:
            b_label = "Mono" if best_ch <= 1 else f"{best_ch}ch"
            o_label = "Mono" if other_ch <= 1 else f"{other_ch}ch"
            if best_ch > other_ch:
                diffs.append(f"{_GREEN}+{_RESET} Audio: {b_label} vs {o_label}")
            else:
                diffs.append(f"{_RED}-{_RESET} Audio: {b_label} vs {o_label}")

        # Subtitles
        best_subs = len(best.subtitle_tracks)
        other_subs = len(other.subtitle_tracks)
        if best_subs == 0 and other_subs > 0:
            diffs.append(f"{_RED}-{_RESET} #1 has no subtitles, #{ranked.index(other)+1} has {other_subs}")
        elif other_subs == 0 and best_subs > 0:
            diffs.append(f"{_GREEN}+{_RESET} #1 has subtitles, #{ranked.index(other)+1} has none")

        # Size
        if best.file_size and other.file_size:
            size_ratio = best.file_size / other.file_size
            if size_ratio > 1.3:
                diffs.append(f"Size: #1 is {size_ratio:.1f}x larger ({best.file_size_human} vs {other.file_size_human})")
            elif size_ratio < 0.7:
                diffs.append(f"{_GREEN}+{_RESET} Size: #1 is {1/size_ratio:.1f}x smaller")

    return diffs


def export_json(files: list[VideoFile], cfg=None) -> str:
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
            "source_type": vf.source_type,
            "is_remaster": vf.is_remaster,
            "remaster_evidence": vf.remaster_evidence,
            "video": {
                "codec": vf.video.codec,
                "profile": vf.video.profile,
                "level": vf.video.level,
                "encoder": vf.video.encoder,
                "resolution": f"{vf.video.width}x{vf.video.height}",
                "resolution_label": vf.video.resolution_label,
                "bitrate": vf.video.bitrate,
                "fps": vf.video.fps,
                "bit_depth": vf.video.bit_depth,
                "color_space": vf.video.color_space,
                "color_transfer": vf.video.color_transfer,
                "hdr_type": vf.video.hdr_type,
                "is_3d": vf.video.is_3d,
                "stereo_mode": vf.video.stereo_mode,
                "stereo_full_res": vf.video.stereo_full_res,
                "eye_resolution": f"{vf.video.eye_width}x{vf.video.eye_height}",
                "is_upscaled": vf.video.is_upscaled,
                "real_height": vf.video.real_height,
                "crop_width": vf.video.crop_width,
                "crop_height": vf.video.crop_height,
                "has_black_bars": vf.video.has_black_bars,
                "aspect_ratio": vf.video.real_aspect_ratio,
                "effective_bitrate": effective_bitrate(vf),
                "bits_per_pixel": bits_per_pixel(vf),
            },
            "audio_tracks": [
                {
                    "codec": at.codec,
                    "format_label": at.format_label,
                    "profile": at.profile,
                    "channels": at.channels,
                    "real_channels": at.real_channels,
                    "snr_db": at.snr_db,
                    "noise_floor_db": at.noise_floor_db,
                    "clip_ratio": at.clip_ratio,
                    "hf_level_db": at.hf_level_db,
                    "effective_channels": at.effective_channels,
                    "channel_layout": at.channel_label,
                    "bitrate": at.bitrate,
                    "sample_rate": at.sample_rate,
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
            "score_breakdown": score_breakdown(vf, **(
                {"primary_language": cfg.primary_language,
                 "extra_languages": cfg.extra_languages,
                 "missing_primary_penalty": cfg.missing_primary_penalty,
                 "extra_primary_track_bonus": cfg.extra_primary_track_bonus,
                 "extra_language_bonus": cfg.extra_language_bonus}
                if cfg and cfg.primary_language else {}
            )),
            "series_title": vf.series_title,
            "season": vf.season,
            "episode": vf.episode,
        }
        result.append(entry)

    return json.dumps(result, ensure_ascii=False, indent=2)
