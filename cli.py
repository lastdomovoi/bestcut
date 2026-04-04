#!/usr/bin/env python3
"""video-compare — compare video files by quality, size and compatibility.

Usage:
  video-compare file1.mkv file2.mkv           # compare two files
  video-compare dir_a/ dir_b/                  # compare two series releases
  video-compare --check movie.mkv              # integrity check
  video-compare --info movie.mkv               # detailed info
  video-compare --json dir/                    # JSON output
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from comparator import (
    compare_pair,
    export_json,
    format_comparison,
    format_file_info,
    format_group_comparison,
)
from compatibility import check_compatibility, compatibility_score
from episode_matcher import group_episodes, scan_directory
from integrity import check_duration_sync, check_full, check_quick
from models import VideoFile
from probe import detect_3d, detect_all_real_channels, detect_crop_and_upscale, probe_file
from profiles import ScoringWeights, get_profile, list_profiles, parse_custom_weights
from scoring import calculate_score


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

    # Detect real audio channels (fake 5.1 detection)
    detect_all_real_channels(path, vf)

    # Detect crop/upscale and 3D
    detect_crop_and_upscale(path, vf)
    detect_3d(path, vf)

    # Integrity
    if check_integrity == "quick":
        vf.integrity = check_quick(path)
    elif check_integrity == "full":
        vf.integrity = check_full(path)

    # Stream duration sync (quick check, always)
    sync_issues = check_duration_sync(vf)
    if sync_issues:
        vf.integrity.warnings.extend(sync_issues)

    # Compatibility
    compat = check_compatibility(vf)
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

    # Process files
    video_files: list[VideoFile] = []
    total = len(all_files)

    if total >= 4 and not check_mode.endswith("full"):
        # Parallel processing for 4+ files (unless full check)
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
        print(export_json(video_files))
        return 0

    # Grouping and comparison
    groups = group_episodes(video_files)

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
            print(format_group_comparison(title, ep_key, files, verbose=args.verbose, weights=weights))

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
                ranked = sorted(files, key=lambda f: f.score, reverse=True)
                best = ranked[0]
                second = ranked[1]

                # Simple stats: count by paths (first vs second argument)
                if best.score - second.score < 2.0:
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
        _print_delete_recommendations(groups)

    return 0


def _print_delete_recommendations(
    groups: dict,
) -> None:
    """Print deletion recommendations for worst duplicates."""
    to_delete: list[VideoFile] = []
    to_keep: list[VideoFile] = []

    for title, episodes in groups.items():
        for ep_key, files in episodes.items():
            if len(files) < 2:
                continue
            ranked = sorted(files, key=lambda f: f.score, reverse=True)
            to_keep.append(ranked[0])
            # All except the best — deletion candidates
            for vf in ranked[1:]:
                to_delete.append(vf)

    if not to_delete:
        print("\n  No duplicates found — nothing to delete.")
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
        ep_str = ""
        if vf.season is not None:
            ep_str = f" S{vf.season:02d}E{vf.episode:02d}"
        elif vf.episode is not None:
            ep_str = f" E{vf.episode:02d}"
        print(f"  DEL  {vf.path}")
        print(f"       {vf.file_size_human} | {vf.video.resolution_label} "
              f"{vf.video.codec.upper()} | {vf.score:.1f} points")

    print(f"\n  Keep ({len(to_keep)} files):")
    for vf in to_keep[:5]:
        print(f"  KEEP {vf.path.name} ({vf.file_size_human}, {vf.score:.1f})")
    if len(to_keep) > 5:
        print(f"  ... and {len(to_keep) - 5} more")

    print(f"\n  To delete, run:")
    print(f"  video-compare ... --recommend-delete --json | jq -r '.[] | .path' | xargs rm")
    print(f"\n  ⚠ WARNING: review the list before deleting!")


def cmd_info(args: argparse.Namespace) -> int:
    """Detailed file information."""
    for p in args.paths:
        path = Path(p)
        files = scan_directory(path)
        for fp in files:
            try:
                vf = process_file(fp, check_integrity="quick", verbose=True)
                print(format_file_info(vf, verbose=True))
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
        prog="video-compare",
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
