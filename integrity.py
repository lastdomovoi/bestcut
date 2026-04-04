"""Проверка целостности видеофайлов."""

from __future__ import annotations

import subprocess
from pathlib import Path

from models import IntegrityResult


def check_quick(path: Path) -> IntegrityResult:
    """Быстрая проверка: заголовки + начало + конец файла."""
    result = IntegrityResult(checked=True)
    path = Path(path)

    # 1. Проверка заголовков через ffprobe
    probe = subprocess.run(
        ["ffprobe", "-v", "error", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if probe.stderr.strip():
        for line in probe.stderr.strip().splitlines():
            result.errors.append(f"[заголовки] {line.strip()}")
            result.error_count += 1

    # 2. Декодирование первых 30 секунд
    head_check = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", "0", "-t", "30",
         "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    if head_check.stderr.strip():
        for line in head_check.stderr.strip().splitlines():
            result.errors.append(f"[начало] {line.strip()}")
            result.error_count += 1

    # 3. Декодирование последних 30 секунд
    tail_check = subprocess.run(
        ["ffmpeg", "-v", "error", "-sseof", "-30",
         "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    if tail_check.returncode != 0 or tail_check.stderr.strip():
        stderr = tail_check.stderr.strip()
        if stderr:
            for line in stderr.splitlines():
                result.errors.append(f"[конец] {line.strip()}")
                result.error_count += 1
        if tail_check.returncode != 0:
            result.is_truncated = True
            result.errors.append("[конец] файл обрезан — не удалось декодировать последние 30 сек")

    return result


def check_full(path: Path) -> IntegrityResult:
    """Полная проверка: декодирование всего файла."""
    result = IntegrityResult(checked=True)
    path = Path(path)

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True, timeout=7200,  # до 2 часов
    )
    if proc.stderr.strip():
        lines = proc.stderr.strip().splitlines()
        for line in lines:
            result.errors.append(line.strip())
        result.error_count = len(lines)

    if proc.returncode != 0 and result.error_count == 0:
        result.is_truncated = True
        result.errors.append("ffmpeg завершился с ненулевым кодом, возможно файл повреждён")
        result.error_count = 1

    return result


def check_duration_sync(video_file) -> list[str]:
    """Проверить синхронность длительности видео- и аудиопотоков."""
    issues = []
    vdur = video_file.video.duration
    if vdur <= 0:
        vdur = video_file.duration

    for i, at in enumerate(video_file.audio_tracks):
        adur = at.duration
        if adur <= 0:
            continue
        diff = abs(vdur - adur)
        if diff > 5.0:
            lang = at.language or f"#{i}"
            issues.append(
                f"Рассинхрон: видео {vdur:.1f}с, аудио [{lang}] {adur:.1f}с "
                f"(разница {diff:.1f}с)"
            )
        elif diff > 2.0:
            lang = at.language or f"#{i}"
            issues.append(
                f"Небольшой рассинхрон: видео {vdur:.1f}с, аудио [{lang}] {adur:.1f}с "
                f"(разница {diff:.1f}с)"
            )

    return issues
