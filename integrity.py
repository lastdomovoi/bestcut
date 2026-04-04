"""Video file integrity checking."""

from __future__ import annotations

import subprocess
from pathlib import Path

from models import IntegrityResult


def check_quick(path: Path) -> IntegrityResult:
    """Quick check: headers + beginning + end of file."""
    result = IntegrityResult(checked=True)
    path = Path(path)

    # 1. Header check via ffprobe
    probe = subprocess.run(
        ["ffprobe", "-v", "error", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if probe.stderr.strip():
        for line in probe.stderr.strip().splitlines():
            result.errors.append(f"[headers] {line.strip()}")
            result.error_count += 1

    # 2. Decode first 30 seconds
    head_check = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", "0", "-t", "30",
         "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    if head_check.stderr.strip():
        for line in head_check.stderr.strip().splitlines():
            result.errors.append(f"[start] {line.strip()}")
            result.error_count += 1

    # 3. Decode last 30 seconds
    tail_check = subprocess.run(
        ["ffmpeg", "-v", "error", "-sseof", "-30",
         "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    if tail_check.returncode != 0 or tail_check.stderr.strip():
        stderr = tail_check.stderr.strip()
        if stderr:
            for line in stderr.splitlines():
                result.errors.append(f"[end] {line.strip()}")
                result.error_count += 1
        if tail_check.returncode != 0:
            result.is_truncated = True
            result.errors.append("[end] file truncated — failed to decode last 30 seconds")

    return result


def check_full(path: Path) -> IntegrityResult:
    """Full check: decode entire file."""
    result = IntegrityResult(checked=True)
    path = Path(path)

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True, timeout=7200,  # up to 2 hours
    )
    if proc.stderr.strip():
        lines = proc.stderr.strip().splitlines()
        for line in lines:
            result.errors.append(line.strip())
        result.error_count = len(lines)

    if proc.returncode != 0 and result.error_count == 0:
        result.is_truncated = True
        result.errors.append("ffmpeg exited with non-zero code, file may be corrupted")
        result.error_count = 1

    return result


def check_duration_sync(video_file) -> list[str]:
    """Check video and audio stream duration sync."""
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
                f"Desync: video {vdur:.1f}s, audio [{lang}] {adur:.1f}s "
                f"(difference {diff:.1f}s)"
            )
        elif diff > 2.0:
            lang = at.language or f"#{i}"
            issues.append(
                f"Minor desync: video {vdur:.1f}s, audio [{lang}] {adur:.1f}s "
                f"(difference {diff:.1f}s)"
            )

    return issues
