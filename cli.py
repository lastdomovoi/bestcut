#!/usr/bin/env python3
"""bestcut — compare video files by quality, size and compatibility.

Usage:
  bestcut file1.mkv file2.mkv           # compare two files
  bestcut dir_a/ dir_b/                  # compare two series releases
  bestcut --check movie.mkv              # integrity check
  bestcut --info movie.mkv               # detailed info
  bestcut --json dir/                    # JSON output
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from comparator import (
    compare_pair,
    format_sequence_report,
    export_json,
    format_comparison,
    format_file_info,
    format_group_comparison,
)
from compatibility import check_compatibility, compatibility_score
from cache import set_cached
from episode_matcher import group_episodes, scan_directory
import episode_sequence
from integrity import check_duration_sync, check_full, check_quick
from models import VideoFile
from probe import (detect_3d, detect_all_real_channels, detect_audio_quality,
                    detect_crop_and_upscale, detect_remaster, detect_source_type,
                    measure_cadence, measure_picture, probe_file)
from profiles import ScoringWeights, get_profile, list_profiles, parse_custom_weights
from sequence import SAFE_RELATIONS
from scoring import calculate_score, rank_key, ranked_files, select_best_audio


def process_file(
    path: Path,
    check_integrity: str = "none",
    verbose: bool = False,
    weights: ScoringWeights | None = None,
    cfg: "Config | None" = None,
) -> VideoFile:
    """Process a single file: probe + integrity + compatibility + score."""
    from config import Config
    if cfg is None:
        cfg = Config()

    vf = probe_file(path)
    from languages import apply_overrides
    apply_overrides(vf)

    # Detect real audio channels (fake 5.1 detection)
    detect_all_real_channels(path, vf)

    # Detect crop/upscale, 3D, source type
    detect_crop_and_upscale(path, vf)
    detect_3d(path, vf)
    detect_source_type(path, vf)
    new_data = False

    # Noise / clipping / band-limit analysis of the track scoring will use
    if cfg.analyze_audio_quality:
        best_audio = select_best_audio(vf, cfg.primary_language)
        if best_audio is not None:
            if detect_audio_quality(path, vf, vf.audio_tracks.index(best_audio)):
                new_data = True

    # Picture measurements, then the master-grade verdict built on them
    if cfg.analyze_picture and measure_picture(path, vf):
        new_data = True
    if cfg.analyze_picture and measure_cadence(path, vf):
        new_data = True
    if new_data:
        set_cached(path, vf)  # keep the measurements for the next run
    detect_remaster(path, vf)

    # Integrity
    if check_integrity == "quick":
        vf.integrity = check_quick(path)
    elif check_integrity == "full":
        vf.integrity = check_full(path, vf.duration, len(vf.audio_tracks))

    # Stream duration sync (quick check, always)
    sync_issues = check_duration_sync(vf)
    if sync_issues:
        vf.integrity.warnings.extend(sync_issues)

    # Compatibility
    compat = check_compatibility(vf, devices=cfg.devices or None)
    vf.compatibility_score = compatibility_score(vf)
    all_issues = []
    for dev_id, issues in compat.items():
        for issue in issues:
            all_issues.append(f"[{dev_id}] {issue}")
    vf.compatibility_issues = all_issues

    # Scoring
    calculate_score(
        vf, weights,
        primary_language=cfg.primary_language,
        extra_languages=cfg.extra_languages,
        missing_primary_penalty=cfg.missing_primary_penalty,
        extra_primary_track_bonus=cfg.extra_primary_track_bonus,
        extra_language_bonus=cfg.extra_language_bonus,
    )

    return vf


def _resolve_weights(args: argparse.Namespace, cfg=None) -> ScoringWeights:
    """Get weights: CLI args > config file > default profile."""
    if args.weights:
        return parse_custom_weights(args.weights)
    if cfg and cfg.weights and args.profile == "default":
        return cfg.weights
    return get_profile(args.profile)


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare files/directories."""
    # Special command: show profile list
    if args.list_profiles:
        print(list_profiles())
        return 0

    from config import Config
    cfg = getattr(args, '_cfg', None) or Config()
    paths = [Path(p) for p in args.paths]
    weights = _resolve_weights(args, cfg)

    # Collect all video files
    all_files: list[Path] = []
    for p in paths:
        found = scan_directory(p)
        if not found:
            print(f"No video files found: {p}", file=sys.stderr)
        all_files.extend(found)

    if not all_files:
        print("No video files to compare.", file=sys.stderr)
        return 1

    # Progress to stderr to avoid breaking JSON
    log = sys.stderr
    profile_name = args.profile if not args.weights else "custom"
    log.write(f"Profile: {profile_name} | Found {len(all_files)} video files, analyzing...\n")

    check_mode = "none"
    if args.check:
        check_mode = "full" if args.full_check else "quick"
        log.write(f"Integrity check: {'full' if check_mode == 'full' else 'quick'}\n")
    if args.recommend_delete and check_mode != "full":
        # Deletion advice is only valid for fully decoded, verified files.
        check_mode = "full"
        log.write("Integrity check: full (required for deletion advice)\n")

    # Process files
    video_files: list[VideoFile] = []
    total = len(all_files)

    if total >= 4:
        # Parallel processing for 4+ files
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import os

        workers = min(os.cpu_count() or 4, total, 8)
        log.write(f"  Parallel processing ({workers} threads)...\n")
        log.flush()

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for fp in all_files:
                fut = pool.submit(process_file, fp, check_mode, args.verbose, weights, cfg)
                futures[fut] = fp

            done = 0
            for fut in as_completed(futures):
                done += 1
                fp = futures[fut]
                try:
                    vf = fut.result()
                    video_files.append(vf)
                    log.write(f"  [{done}/{total}] {fp.name}... {vf.score:.1f} points\n")
                except Exception as e:
                    log.write(f"  [{done}/{total}] {fp.name}... ERROR: {e}\n")
                log.flush()
    else:
        # Sequential processing
        for i, fp in enumerate(all_files, 1):
            log.write(f"  [{i}/{total}] {fp.name}...")
            log.flush()
            try:
                vf = process_file(fp, check_integrity=check_mode, verbose=args.verbose, weights=weights, cfg=cfg)
                video_files.append(vf)
                log.write(f" {vf.score:.1f} points\n")
            except Exception as e:
                log.write(f" ERROR: {e}\n")

    if not video_files:
        print("Failed to process any files.", file=sys.stderr)
        return 1

    # JSON output
    if args.json:
        print(export_json(video_files, cfg=cfg))
        return 0

    # Grouping and comparison
    groups = group_episodes(video_files)
    content_comparer = make_comparer(log, use_cache=not args.no_cache)
    # Episode order across releases: durations screen, pictures verify
    episode_sequence.assign_releases(video_files, paths)
    verify_order = args.content or args.recommend_delete
    seq_matcher = None
    if verify_order:
        from sequence import content_match
        def seq_matcher(a, b):
            log.write(f"  checking episode order: {a.path.name} vs {b.path.name}...\n")
            log.flush()
            return content_match(a, b, use_cache=not args.no_cache)
    seq_reports = episode_sequence.analyze_groups(groups, seq_matcher)

    print(f"\n{'=' * 70}")
    print(f" COMPARISON RESULTS")
    print(f"{'=' * 70}")

    for title, episodes in sorted(groups.items()):
        for ep_key, files in sorted(episodes.items(), key=lambda x: (x[0][0] or 0, x[0][1] or 0)):
            if len(files) == 1 and not args.verbose and not args.info:
                # Single files without verbose — short summary line
                vf = files[0]
                season, episode = ep_key
                if season is not None:
                    ep_str = f"S{season:02d}E{episode:02d}"
                elif episode is not None:
                    ep_str = f"E{episode:02d}"
                else:
                    ep_str = ""
                label = f"{title} {ep_str}".strip()
                print(f"\n  {label}: {vf.path.name}")
                print(f"    {vf.video.resolution_label} {vf.video.codec.upper()} | "
                      f"{vf.file_size_human} | {vf.score:.1f} points")
                if not vf.integrity.is_ok and vf.integrity.checked:
                    print(f"    ⚠ Integrity issues: {vf.integrity.error_count} errors")
                continue

            # Groups or verbose — full comparison
            print(format_group_comparison(title, ep_key, files, verbose=args.verbose, weights=weights, cfg=cfg))
            if args.content and len(files) > 1:
                ranked = ranked_files(files)
                for other in ranked[1:]:
                    print(format_sequence_report(content_comparer(ranked[0], other)))

    seq_text = episode_sequence.format_reports(seq_reports)
    if seq_text:
        print(seq_text)

    # Overall summary for series
    has_comparisons = any(
        len(files) > 1
        for episodes in groups.values()
        for files in episodes.values()
    )
    if has_comparisons:
        print(f"\n{'=' * 70}")
        print(" SUMMARY")
        print(f"{'=' * 70}")

        total_a_size = 0
        total_b_size = 0
        a_wins = b_wins = ties = 0

        for title, episodes in groups.items():
            for ep_key, files in episodes.items():
                if len(files) < 2:
                    continue
                ranked = ranked_files(files)
                best = ranked[0]
                second = ranked[1]

                # Simple stats: count by paths (first vs second argument)
                decisive = (not best.integrity.damaged and (
                    second.integrity.damaged or best.score - second.score >= 2.0))
                if not decisive:
                    ties += 1
                else:
                    # Determine by parent directory
                    if len(paths) >= 2:
                        path_a = Path(paths[0]).resolve()
                        if str(best.path.resolve()).startswith(str(path_a)):
                            a_wins += 1
                        else:
                            b_wins += 1

        if len(paths) >= 2:
            print(f"  {paths[0].name}: {a_wins} wins")
            print(f"  {paths[1].name}: {b_wins} wins")
            if ties:
                print(f"  Ties: {ties}")

    # Deletion recommendations
    if args.recommend_delete:
        _print_delete_recommendations(groups, cfg.primary_language, content_comparer,
                                      episode_sequence.blocked_keys(seq_reports))

    return 0


# Durations of one episode/movie in two releases differ by less than this
# share; larger gaps mean different cuts or different content.
DURATION_TOLERANCE = 0.02


def _group_review_reason(ep_key, files: list[VideoFile],
                         primary_language: str = "") -> str:
    """Why a group must not produce deletion advice ("" if it may)."""
    if ep_key == (None, None):
        # No proven episode identity: similar titles alone merged these.
        dirs = [vf.path.parent for vf in files]
        if len(set(dirs)) < len(dirs):
            return "unparsed names, several files share a directory (different episodes?)"
        durations = [vf.duration for vf in files]
        if min(durations) <= 0:
            return "unparsed names and unknown duration (not proven duplicates)"
        if (max(durations) - min(durations)) > max(durations) * DURATION_TOLERANCE:
            return "unparsed names with different durations (not proven duplicates)"
    if primary_language:
        from languages import primary_language_status
        if any(primary_language_status(vf, primary_language) == "unknown" for vf in files):
            return f"audio language of some tracks unknown (untagged), '{primary_language}' unconfirmed"
    return ""


def make_comparer(log=None, use_cache: bool = True):
    """Memoized synchronized comparison (sequence.compare_files).

    Failures become an "unknown" comparison that needs review.
    """
    from sequence import SequenceComparison, compare_files
    memo: dict = {}

    def compare(a: VideoFile, b: VideoFile):
        key = (str(a.path), str(b.path))
        if key not in memo:
            if log is not None:
                log.write(f"  comparing content: {a.path.name} vs {b.path.name}...\n")
                log.flush()
            try:
                memo[key] = compare_files(a, b, use_cache=use_cache)
            except Exception as e:  # never let one pair abort the run
                comp = SequenceComparison(a.path, b.path)
                comp.review_reasons = [f"content comparison failed: {e}"]
                memo[key] = comp
        return memo[key]

    return compare


# Points removed from the file that carries an established overlay / ad
OVERLAY_PENALTY = 5.0


def _apply_overlay_penalty(comp, files: list[VideoFile]) -> None:
    owner = next((f for f in files if f.path == comp.overlay_owner), None)
    if owner is not None and owner.content_penalty == 0:
        owner.content_penalty = OVERLAY_PENALTY
        owner.score = max(0.0, owner.score - OVERLAY_PENALTY)


def _describe(comp) -> str:
    bits = [comp.relation.replace("_", " ")]
    if comp.ssim_mean is not None:
        bits.append(f"SSIM {comp.ssim_mean:.3f}")
    if comp.redundancy.verdict:
        bits.append(comp.redundancy.verdict.replace("_", " "))
    return ", ".join(bits)


def _verify_group(keeper: VideoFile, candidates: list[VideoFile], comparer,
                  to_delete_notes: list) -> tuple[VideoFile, list[VideoFile]]:
    """Prove with the pictures that candidates hold the keeper's video.

    Returns (keeper, deletable).  Everything unproven is appended to
    `to_delete_notes` as a review entry.  Overlay penalties are applied
    first so the carrier of a logo/ad bug cannot be picked as keeper, and
    a transparent larger re-encode gives way to the compact copy.
    """
    everyone = [keeper, *candidates]
    comps = {}
    pair_list = ([(a, b) for i, a in enumerate(everyone) for b in everyone[i + 1:]]
                 if len(everyone) <= 4 else [(keeper, c) for c in candidates])
    for a, b in pair_list:
        comp = comparer(a, b)
        comps[frozenset((a.path, b.path))] = comp
        _apply_overlay_penalty(comp, [a, b])

    def comp_of(x, y):
        return comps.get(frozenset((x.path, y.path))) or comparer(x, y)

    everyone = ranked_files(everyone)
    keeper, candidates = everyone[0], everyone[1:]

    # A transparent larger re-encode adds nothing: keep the compact copy
    # (only if nothing the keeper has would be lost).
    for _ in range(len(candidates)):
        swap = None
        for f in candidates:
            comp = comp_of(keeper, f)
            if (comp.safe_to_dedupe
                    and comp.redundancy.verdict == "redundant_larger"
                    and comp.redundancy.redundant_path == keeper.path
                    and f.integrity.verified_clean
                    and not comp.unique_in(keeper.path)):
                swap = f
                break
        if swap is None:
            break
        candidates = [c for c in candidates if c is not swap] + [keeper]
        keeper = swap

    deletable = []
    for f in candidates:
        comp = comp_of(keeper, f)
        others = [r for r in comp.review_reasons if r != comp.overlay_note]
        if comp.overlay_owner == keeper.path:
            to_delete_notes.append((f"{keeper.path.name} carries an overlay/ad the other "
                                    f"lacks: {comp.overlay_note}", [keeper, f]))
        elif comp.unique_in(f.path):
            to_delete_notes.append(("deleting would lose: " + "; ".join(comp.unique_in(f.path)),
                                    [keeper, f]))
        elif (comp.overlay_owner == f.path and comp.relation in SAFE_RELATIONS
                and not others):
            f.content_note = _describe(comp) + f", carries an overlay ({comp.overlay_note})"
            deletable.append(f)
        elif comp.safe_to_dedupe:
            f.content_note = _describe(comp)
            deletable.append(f)
        else:
            to_delete_notes.append(("; ".join(comp.review_reasons) or
                                    f"not proven to be the same video ({comp.relation})",
                                    [keeper, f]))
    return keeper, deletable


def deletion_candidates(groups: dict, primary_language: str = "", comparer=None,
                        blocked: dict | None = None):
    """Split groups into (keep, delete, review) lists.

    review: [(reason, files)] for groups that need a human decision.
    A file is deleted only against a fully decoded, error-free keeper; a
    damaged file with no such replacement is never offered for deletion.

    blocked: {(title, episode_key): reason} from episode_sequence; those
    episodes get no deletion advice.

    comparer(a, b) -> SequenceComparison proves the pair holds the same
    video before anything is deleted.  Without one the caller must have
    established that already; the CLI always passes one.
    """
    to_keep: list[VideoFile] = []
    to_delete: list[VideoFile] = []
    review: list[tuple[str, list[VideoFile]]] = []
    for title, episodes in groups.items():
        for ep_key, files in episodes.items():
            if len(files) < 2:
                continue
            if blocked and (title, ep_key) in blocked:
                review.append((blocked[(title, ep_key)], files))
                continue
            reason = _group_review_reason(ep_key, files, primary_language)
            if reason:
                review.append((reason, files))
                continue
            ranked = ranked_files(files)
            keeper = ranked[0]
            if not keeper.integrity.verified_clean:
                review.append(("no verified-clean copy (full integrity check "
                               "needed; damaged files need recovery or "
                               "re-download)", files))
                continue
            others = ranked[1:]
            unverified = [f for f in others if not f.integrity.full_checked]
            if unverified:
                review.append(("copies not fully verified: " + ", ".join(
                    f.path.name for f in unverified), unverified))
            candidates = [f for f in others if f.integrity.full_checked]

            if comparer is not None:
                keeper, candidates = _verify_group(keeper, candidates, comparer,
                                                   to_delete_notes=review)
            if candidates:
                to_keep.append(keeper)
                to_delete.extend(candidates)
    return to_keep, to_delete, review


def damaged_without_replacement(groups: dict) -> list[VideoFile]:
    """Damaged files that have no fully verified clean counterpart."""
    result = []
    for episodes in groups.values():
        for files in episodes.values():
            has_clean = any(f.integrity.verified_clean for f in files)
            if not has_clean:
                result.extend(f for f in files if f.integrity.damaged)
    return result


def _print_delete_recommendations(
    groups: dict,
    primary_language: str = "",
    comparer=None,
    blocked: dict | None = None,
) -> None:
    """Print deletion recommendations for worst duplicates."""
    to_keep, to_delete, review = deletion_candidates(groups, primary_language, comparer,
                                                     blocked)

    broken = damaged_without_replacement(groups)
    if broken:
        print(f"\n{'=' * 70}")
        print(" DAMAGED — RECOVER OR RE-DOWNLOAD (kept, never offered for deletion)")
        print(f"{'=' * 70}")
        for vf in broken:
            ig = vf.integrity
            when = ""
            if ig.first_error_time is not None:
                when = f", errors {ig.first_error_time:.0f}s–{ig.last_error_time:.0f}s"
            kind = "truncated" if ig.is_truncated else f"{ig.error_count} errors"
            print(f"  ! {vf.path}  [{kind}; {', '.join(ig.streams_affected()) or 'file'}{when}]")

    if review:
        print(f"\n{'=' * 70}")
        print(" NEEDS REVIEW (no deletion advice)")
        print(f"{'=' * 70}")
        for reason, files in review:
            print(f"  ? {reason}")
            for vf in files:
                print(f"      {vf.path}")

    if not to_delete:
        print("\n  No verified duplicates found — nothing to delete.")
        return

    total_save = sum(vf.file_size for vf in to_delete)
    total_save_gb = total_save / (1024 ** 3)

    print(f"\n{'=' * 70}")
    print(" DELETION RECOMMENDATIONS")
    print(f"{'=' * 70}")
    print(f"  Found {len(to_delete)} duplicate files for deletion")
    print(f"  Space savings: {total_save_gb:.2f} GB")
    print()

    for vf in sorted(to_delete, key=lambda f: f.file_size, reverse=True):
        print(f"  DEL  {vf.path}")
        print(f"       {vf.file_size_human} | {vf.video.resolution_label} "
              f"{vf.video.codec.upper()} | {vf.score:.1f} points")
        if vf.content_note:
            print(f"       verified: {vf.content_note}")

    print(f"\n  Keep ({len(to_keep)} files):")
    for vf in to_keep[:5]:
        print(f"  KEEP {vf.path.name} ({vf.file_size_human}, {vf.score:.1f})")
    if len(to_keep) > 5:
        print(f"  ... and {len(to_keep) - 5} more")

    print(f"\n  ⚠ WARNING: bestcut never deletes files; review the list before removing anything!")


def _cmd_set_language(media: str, track: str, lang: str) -> int:
    """--set-language FILE TRACK LANG: remember a confirmed audio language."""
    from languages import OverrideStoreError, normalize_language, save_override
    path = Path(media)
    try:
        index = int(track) - 1
    except ValueError:
        print("TRACK must be a number (1 = first audio track)", file=sys.stderr)
        return 2
    if not path.is_file():
        print(f"File not found: {media}", file=sys.stderr)
        return 2
    try:
        vf = probe_file(path)
    except Exception as e:
        print(f"Cannot read {media}: {e}", file=sys.stderr)
        return 2
    if not 0 <= index < len(vf.audio_tracks):
        print(f"{media} has {len(vf.audio_tracks)} audio track(s); "
              f"track {track} does not exist", file=sys.stderr)
        return 2
    try:
        save_override(path, index, "" if lang == "-" else lang)
    except (ValueError, OverrideStoreError) as e:
        print(f"Not saved: {e}", file=sys.stderr)
        return 2
    print(f"Track {track} of {media}: "
          + ("confirmation removed" if lang == "-" else f"confirmed as {normalize_language(lang)}"))
    return 0


def _cmd_find_series(args: argparse.Namespace) -> int:
    """--find-series: exit 0 = found, 1 = nothing found, 2 = roots missing/unreadable."""
    import json
    from discovery import find_series
    if not args.paths:
        print("--find-series needs at least one root directory", file=sys.stderr)
        return 2
    result = find_series(args.find_series, args.alias, [Path(p) for p in args.paths],
                         max_depth=args.max_depth)
    if args.json:
        print(json.dumps(result.to_json(), ensure_ascii=False, indent=2))
    else:
        for p in result.matches:
            print(p)
        for p in result.missing_roots:
            print(f"MISSING ROOT: {p}", file=sys.stderr)
        for p, err in result.unreadable:
            print(f"UNREADABLE: {p} ({err})", file=sys.stderr)
        if not result.matches and result.searched_ok:
            print("No matching directories found.", file=sys.stderr)
    if not result.searched_ok:
        return 2
    return 0 if result.matches else 1


def cmd_info(args: argparse.Namespace) -> int:
    """Detailed file information."""
    from config import Config
    cfg = getattr(args, '_cfg', None) or Config()
    weights = _resolve_weights(args, cfg)
    for p in args.paths:
        path = Path(p)
        files = scan_directory(path)
        for fp in files:
            try:
                vf = process_file(fp, check_integrity="quick", verbose=True,
                                  weights=weights, cfg=cfg)
                print(format_file_info(vf, verbose=True, weights=weights, cfg=cfg))
                print()
            except Exception as e:
                print(f"ERROR {fp}: {e}", file=sys.stderr)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Integrity check."""
    mode = "full" if args.full_check else "quick"
    errors_found = False

    for p in args.paths:
        path = Path(p)
        files = scan_directory(path)
        for fp in files:
            print(f"Checking: {fp.name}...", end="", flush=True)
            try:
                vf = process_file(fp, check_integrity=mode)
                if vf.integrity.is_ok:
                    print(" OK")
                else:
                    errors_found = True
                    print(f" {vf.integrity.error_count} ERRORS")
                    if vf.integrity.is_truncated:
                        print(f"  ⚠ FILE TRUNCATED")
                    ig = vf.integrity
                    if ig.issues:
                        span = ""
                        if ig.first_error_time is not None:
                            span = f", {ig.first_error_time:.0f}s–{ig.last_error_time:.0f}s"
                        print(f"  affected: {', '.join(ig.streams_affected())}{span}")
                    for err in vf.integrity.errors:
                        print(f"  - {err}")

                sync_issues = vf.integrity.warnings
                for w in sync_issues:
                    print(f"  ⚠ {w}")

            except Exception as e:
                errors_found = True
                print(f" ERROR: {e}")

    return 1 if errors_found else 0


def cmd_compat(args: argparse.Namespace) -> int:
    """Compatibility check."""
    for p in args.paths:
        path = Path(p)
        files = scan_directory(path)
        for fp in files:
            try:
                vf = process_file(fp)
                print(f"\n{fp.name}:")
                compat = check_compatibility(vf)
                for dev_id, issues in compat.items():
                    from compatibility import DEVICE_PROFILES
                    name = DEVICE_PROFILES[dev_id]["name"]
                    if issues:
                        print(f"  {name}: {len(issues)} issues")
                        for issue in issues:
                            print(f"    - {issue}")
                    else:
                        print(f"  {name}: OK")
            except Exception as e:
                print(f"ERROR {fp}: {e}", file=sys.stderr)
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="bestcut",
        description="Compare video files by quality, size and compatibility",
    )
    parser.add_argument(
        "paths", nargs="*",
        help="Files or directories to compare",
    )
    parser.add_argument(
        "--check", "-c", action="store_true",
        help="Check file integrity",
    )
    parser.add_argument(
        "--full-check", "-C", action="store_true",
        help="Full integrity check (slow, decodes entire file)",
    )
    parser.add_argument(
        "--info", "-i", action="store_true",
        help="Detailed file information",
    )
    parser.add_argument(
        "--compat", action="store_true",
        help="Check compatibility with players/devices",
    )
    parser.add_argument(
        "--json", "-j", action="store_true",
        help="Output in JSON format",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose output with score breakdown",
    )
    parser.add_argument(
        "--profile", "-p", default="default",
        help="Priority profile: default, quality, atmos, compact, "
             "androidtv, universal, videophile (default: default)",
    )
    parser.add_argument(
        "--weights", "-w",
        help="Custom weights: 'resolution=1.5,audio=2.0,file_size=1.0'",
    )
    parser.add_argument(
        "--list-profiles", action="store_true",
        help="Show available priority profiles",
    )
    parser.add_argument(
        "--recommend-delete", "-D", action="store_true",
        help="Show deletion recommendations for worst duplicates",
    )
    parser.add_argument(
        "--content", action="store_true",
        help="Compare the actual video of duplicate groups (aligned frames, "
             "SSIM, colour, inserted/replaced segments); slow",
    )
    parser.add_argument(
        "--set-language", nargs=3, metavar=("FILE", "TRACK", "LANG"),
        help="Confirm the spoken language of an audio track (1-based track, "
             "e.g. --set-language movie.avi 1 rus); use '-' to remove",
    )
    parser.add_argument(
        "--find-series", metavar="NAME",
        help="List directories of a series under the given roots (no probing); "
             "positional arguments are the roots to search",
    )
    parser.add_argument(
        "--alias", action="append", default=[], metavar="NAME",
        help="Alternative name for --find-series (repeatable)",
    )
    parser.add_argument(
        "--max-depth", type=int, default=4,
        help="Directory depth searched by --find-series (default 4)",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Disable cache (always run ffprobe from scratch)",
    )
    parser.add_argument(
        "--clear-cache", action="store_true",
        help="Clear analysis results cache",
    )
    parser.add_argument(
        "--init-config", action="store_true",
        help="Create config file with default settings",
    )

    args = parser.parse_args()

    # Commands without paths
    if args.list_profiles:
        print(list_profiles())
        sys.exit(0)

    if args.find_series:
        return_code = _cmd_find_series(args)
        sys.exit(return_code)

    if args.set_language:
        sys.exit(_cmd_set_language(*args.set_language))

    if args.clear_cache:
        from cache import clear_cache
        count = clear_cache()
        print(f"Cache cleared: {count} entries removed")
        sys.exit(0)

    if args.init_config:
        from config import Config
        path = Config().create_default_config()
        print(f"Config created: {path}")
        sys.exit(0)

    if not args.paths:
        parser.error("specify files or directories to compare")

    # Load config
    from config import Config
    cfg = Config.load()

    # Apply config values as defaults (CLI arguments take priority)
    if cfg.profile != "default" and args.profile == "default":
        args.profile = cfg.profile
    args._cfg = cfg  # pass to subcommands
    if cfg.verbose and not args.verbose:
        args.verbose = True
    if cfg.check_integrity != "none" and not args.check:
        args.check = True
        if cfg.check_integrity == "full":
            args.full_check = True

    # full-check implies check
    if args.full_check:
        args.check = True

    if args.compat:
        sys.exit(cmd_compat(args))
    elif args.info:
        sys.exit(cmd_info(args))
    elif args.check and len(args.paths) == 1 and not args.json:
        sys.exit(cmd_check(args))
    else:
        sys.exit(cmd_compare(args))


if __name__ == "__main__":
    main()
