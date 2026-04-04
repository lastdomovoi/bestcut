#!/usr/bin/env python3
"""video-compare — сравнение видеофайлов по качеству, размеру и совместимости.

Использование:
  video-compare file1.mkv file2.mkv           # сравнить два файла
  video-compare dir_a/ dir_b/                  # сравнить два релиза сериала
  video-compare --check movie.mkv              # проверка целостности
  video-compare --info movie.mkv               # подробная информация
  video-compare --json dir/                    # вывод в JSON
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
from probe import probe_file
from profiles import ScoringWeights, get_profile, list_profiles, parse_custom_weights
from scoring import calculate_score


def process_file(
    path: Path,
    check_integrity: str = "none",
    verbose: bool = False,
    weights: ScoringWeights | None = None,
) -> VideoFile:
    """Обработать один файл: probe + integrity + compatibility + score."""
    vf = probe_file(path)

    # Целостность
    if check_integrity == "quick":
        vf.integrity = check_quick(path)
    elif check_integrity == "full":
        vf.integrity = check_full(path)

    # Синхронность потоков (быстрая проверка, всегда)
    sync_issues = check_duration_sync(vf)
    if sync_issues:
        vf.integrity.warnings.extend(sync_issues)

    # Совместимость
    compat = check_compatibility(vf)
    vf.compatibility_score = compatibility_score(vf)
    all_issues = []
    for dev_id, issues in compat.items():
        for issue in issues:
            all_issues.append(f"[{dev_id}] {issue}")
    vf.compatibility_issues = all_issues

    # Скоринг
    calculate_score(vf, weights)

    return vf


def _resolve_weights(args: argparse.Namespace) -> ScoringWeights:
    """Получить веса из аргументов CLI."""
    if args.weights:
        return parse_custom_weights(args.weights)
    return get_profile(args.profile)


def cmd_compare(args: argparse.Namespace) -> int:
    """Сравнить файлы/дире��тории."""
    # Специальная команда: показать список профилей
    if args.list_profiles:
        print(list_profiles())
        return 0

    paths = [Path(p) for p in args.paths]
    weights = _resolve_weights(args)

    # Собрать все видеофайлы
    all_files: list[Path] = []
    for p in paths:
        found = scan_directory(p)
        if not found:
            print(f"Видеофайлы не найдены: {p}", file=sys.stderr)
        all_files.extend(found)

    if not all_files:
        print("Нет видеофайлов для сравнения.", file=sys.stderr)
        return 1

    # Прогресс в stderr чтобы не ломать JSON
    log = sys.stderr
    profile_name = args.profile if not args.weights else "custom"
    log.write(f"Профиль: {profile_name} | Найдено {len(all_files)} видеофайлов, анализ...\n")

    check_mode = "none"
    if args.check:
        check_mode = "full" if args.full_check else "quick"
        log.write(f"Проверка целостности: {'полная' if check_mode == 'full' else 'быстрая'}\n")

    # Обработать файлы
    video_files: list[VideoFile] = []
    for i, fp in enumerate(all_files, 1):
        log.write(f"  [{i}/{len(all_files)}] {fp.name}...")
        log.flush()
        try:
            vf = process_file(fp, check_integrity=check_mode, verbose=args.verbose, weights=weights)
            video_files.append(vf)
            log.write(f" {vf.score:.1f} баллов\n")
        except Exception as e:
            log.write(f" ОШИБКА: {e}\n")

    if not video_files:
        print("Не удалось обработать ни одного файла.", file=sys.stderr)
        return 1

    # JSON вывод
    if args.json:
        print(export_json(video_files))
        return 0

    # Группировка и сравнение
    groups = group_episodes(video_files)

    print(f"\n{'=' * 70}")
    print(f" РЕЗУЛЬТАТЫ СРАВНЕНИЯ")
    print(f"{'=' * 70}")

    for title, episodes in sorted(groups.items()):
        for ep_key, files in sorted(episodes.items(), key=lambda x: (x[0][0] or 0, x[0][1] or 0)):
            if len(files) == 1 and not args.verbose and not args.info:
                # Для одиночных файлов без verbose — краткая строка
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
                      f"{vf.file_size_human} | {vf.score:.1f} баллов")
                if not vf.integrity.is_ok and vf.integrity.checked:
                    print(f"    ⚠ Проблемы целостности: {vf.integrity.error_count} ошибок")
                continue

            # Для групп или verbose — полное сравнение
            print(format_group_comparison(title, ep_key, files, verbose=args.verbose, weights=weights))

    # Общая сводка для сериалов
    has_comparisons = any(
        len(files) > 1
        for episodes in groups.values()
        for files in episodes.values()
    )
    if has_comparisons:
        print(f"\n{'=' * 70}")
        print(" СВОДКА")
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

                # Простая статистика: считаем по путям (первый vs второй аргумент)
                if best.score - second.score < 2.0:
                    ties += 1
                else:
                    # Определяем по родительской директории
                    if len(paths) >= 2:
                        path_a = Path(paths[0]).resolve()
                        if str(best.path.resolve()).startswith(str(path_a)):
                            a_wins += 1
                        else:
                            b_wins += 1

        if len(paths) >= 2:
            print(f"  {paths[0].name}: {a_wins} побед")
            print(f"  {paths[1].name}: {b_wins} побед")
            if ties:
                print(f"  Ничьих: {ties}")

    return 0


def cmd_info(args: argparse.Namespace) -> int:
    """Подробная информация о файле."""
    for p in args.paths:
        path = Path(p)
        files = scan_directory(path)
        for fp in files:
            try:
                vf = process_file(fp, check_integrity="quick", verbose=True)
                print(format_file_info(vf, verbose=True))
                print()
            except Exception as e:
                print(f"ОШИБКА {fp}: {e}", file=sys.stderr)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Проверка целостности."""
    mode = "full" if args.full_check else "quick"
    errors_found = False

    for p in args.paths:
        path = Path(p)
        files = scan_directory(path)
        for fp in files:
            print(f"Проверка: {fp.name}...", end="", flush=True)
            try:
                vf = process_file(fp, check_integrity=mode)
                if vf.integrity.is_ok:
                    print(" ОК")
                else:
                    errors_found = True
                    print(f" {vf.integrity.error_count} ОШИБОК")
                    if vf.integrity.is_truncated:
                        print(f"  ⚠ ФАЙЛ ОБРЕЗАН")
                    for err in vf.integrity.errors:
                        print(f"  - {err}")

                sync_issues = vf.integrity.warnings
                for w in sync_issues:
                    print(f"  ⚠ {w}")

            except Exception as e:
                errors_found = True
                print(f" ОШИБКА: {e}")

    return 1 if errors_found else 0


def cmd_compat(args: argparse.Namespace) -> int:
    """Проверка совместимости."""
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
                        print(f"  {name}: {len(issues)} проблем")
                        for issue in issues:
                            print(f"    - {issue}")
                    else:
                        print(f"  {name}: ОК")
            except Exception as e:
                print(f"ОШИБКА {fp}: {e}", file=sys.stderr)
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="video-compare",
        description="Сравнение видеофайлов по качеству, размеру и совместимости",
    )
    parser.add_argument(
        "paths", nargs="*",
        help="Файлы или директории для сравнения",
    )
    parser.add_argument(
        "--check", "-c", action="store_true",
        help="Проверить целостность файлов",
    )
    parser.add_argument(
        "--full-check", "-C", action="store_true",
        help="Полная проверка целостности (медленная, декодирует весь файл)",
    )
    parser.add_argument(
        "--info", "-i", action="store_true",
        help="Подробная информация о файлах",
    )
    parser.add_argument(
        "--compat", action="store_true",
        help="Проверка совместимости с плеерами/устройствами",
    )
    parser.add_argument(
        "--json", "-j", action="store_true",
        help="Вывод в формате JSON",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Подробный вывод с разбивкой баллов",
    )
    parser.add_argument(
        "--profile", "-p", default="default",
        help="Профиль приоритетов: default, quality, atmos, compact, "
             "androidtv, universal, videophile (по умолчанию: default)",
    )
    parser.add_argument(
        "--weights", "-w",
        help="Кастомные веса: 'resolution=1.5,audio=2.0,file_size=1.0'",
    )
    parser.add_argument(
        "--list-profiles", action="store_true",
        help="Показать список доступных профилей приоритетов",
    )

    args = parser.parse_args()

    # Показать профили (не требует paths)
    if args.list_profiles:
        print(list_profiles())
        sys.exit(0)

    if not args.paths:
        parser.error("укажите файлы или директории для сравнения")

    # Проверка full-check подразумевает check
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
