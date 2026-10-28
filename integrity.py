"""Video file integrity checking."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from models import IntegrityIssue, IntegrityResult
from procutil import run_tool

# Issues kept per file; error_count still counts every line.
MAX_STORED_ISSUES = 200

# A file decoded to less than this share of its container duration was
# cut short even if ffmpeg reported no error.
MIN_DECODED_SHARE = 0.98

# Demuxers / muxers: their messages concern the container, not a stream.
_CONTAINER_NAMES = {"matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2", "avi", "mpegts",
                    "mpeg", "flv", "asf", "ogg", "wav", "null", "matroska", "mp4",
                    "mov", "mpegps", "ivf"}
_VIDEO_HINTS = ("h26", "hevc", "mpeg1", "mpeg2", "mpeg4", "vp8", "vp9", "av1",
                "vc1", "wmv", "mjpeg", "theora", "prores", "cuvid", "nvdec",
                "qsv", "dav1d", "video", "dvvideo", "msmpeg", "xvid", "divx")
_AUDIO_HINTS = ("aac", "ac3", "eac3", "dca", "dts", "mp3", "mp2", "flac",
                "truehd", "mlp", "opus", "vorbis", "wma", "pcm", "alac", "audio",
                "cook", "atrac", "amr")
# "[h264 @ 0x..]" and ffmpeg 7's "[vist#0:0/h264 @ 0x..]" / "[aist#0:1/aac @ ..]"
_DECODER_RE = re.compile(r"^\[([\w.,\-#:/]+) @ [^\]]+\]")
_PROGRESS_RE = re.compile(
    r"^(frame|fps|stream_\d+_\d+_q|bitrate|total_size|out_time_us|out_time_ms|"
    r"out_time|dup_frames|drop_frames|speed|progress)=")


def classify_stream(line: str) -> str:
    """Which stream an ffmpeg error line belongs to, from the decoder tag."""
    m = _DECODER_RE.match(line)
    if not m:
        return "unknown"
    tag = m.group(1).lower()
    if tag in _CONTAINER_NAMES:
        return "container"
    if tag.startswith("vist#"):
        return "video"
    if tag.startswith("aist#"):
        return "audio"
    name = tag.rsplit("/", 1)[-1]
    if name in _CONTAINER_NAMES:
        return "container"
    if any(h in name for h in _VIDEO_HINTS):
        return "video"
    if any(h in name for h in _AUDIO_HINTS):
        return "audio"
    return "unknown"


# Muxer complaints about timestamps: reported separately from decode errors,
# since a decodable stream with odd DTS values is not corrupted media.
_TIMESTAMP_RE = re.compile(
    r"non[- ]?monoton\w* (?:increasing )?dts|timestamp discontinuity", re.I)


def _add_issue(result: IntegrityResult, line: str, stage: str,
               time: float | None = None) -> None:
    line = line.strip()
    if _TIMESTAMP_RE.search(line):
        result.timestamp_issues += 1
        return
    result.errors.append(f"[{stage}] {line}" if stage else line)
    result.error_count += 1
    stream = classify_stream(line)
    if stream not in result.affected_streams:
        result.affected_streams.append(stream)
    if time is not None:
        if result.first_error_time is None or time < result.first_error_time:
            result.first_error_time = time
        if result.last_error_time is None or time > result.last_error_time:
            result.last_error_time = time
    if len(result.issues) < MAX_STORED_ISSUES:
        result.issues.append(IntegrityIssue(stream, line, time))


def check_quick(path: Path) -> IntegrityResult:
    """Quick check: headers + beginning + end of file."""
    result = IntegrityResult(checked=True)
    path = Path(path)

    # 1. Header check via ffprobe
    probe = run_tool(["ffprobe", "-v", "error", str(path)], timeout=30)
    for line in probe.stderr.strip().splitlines():
        _add_issue(result, line, "headers")

    # 2. Decode first 30 seconds
    head_check = run_tool(
        ["ffmpeg", "-v", "error", "-ss", "0", "-t", "30",
         "-i", str(path), "-f", "null", "-"], timeout=120)
    for line in head_check.stderr.strip().splitlines():
        _add_issue(result, line, "start")

    # 3. Decode last 30 seconds
    tail_check = run_tool(
        ["ffmpeg", "-v", "error", "-sseof", "-30",
         "-i", str(path), "-f", "null", "-"], timeout=120)
    if tail_check.returncode != 0 or tail_check.stderr.strip():
        for line in tail_check.stderr.strip().splitlines():
            _add_issue(result, line, "end")
        if tail_check.returncode != 0:
            result.is_truncated = True
            result.errors.append("[end] file truncated — failed to decode last 30 seconds")
            result.error_count += 1

    _note_timestamps(result)
    return result


def _note_timestamps(result: IntegrityResult) -> None:
    if result.timestamp_issues:
        result.warnings.append(
            f"{result.timestamp_issues} non-monotonic timestamp reports "
            "(playback may stutter; media decodes without errors)")


def check_full(path: Path, expected_duration: float = 0.0) -> IntegrityResult:
    """Full check: decode the entire video and every audio track.

    Errors are attributed to a stream and carry the playback position at
    which ffmpeg reported them (from -progress, so approximate).  If the
    decode cannot finish (timeout) the file stays unverified.
    """
    result = IntegrityResult(checked=True)
    path = Path(path)

    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-progress", "pipe:2",
           "-stats_period", "1", "-i", str(path),
           "-map", "0:v?", "-map", "0:a?", "-f", "null", "-"]
    try:
        proc = run_tool(cmd, timeout=7200)  # up to 2 hours
    except subprocess.TimeoutExpired:
        result.checked = False
        result.warnings.append("full integrity check timed out; file is unverified")
        return result

    position = 0.0
    for line in proc.stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        if _PROGRESS_RE.match(line):
            if line.startswith("out_time_us="):
                try:
                    position = max(position, int(line.split("=", 1)[1]) / 1e6)
                except ValueError:
                    pass
            continue
        _add_issue(result, line, "", position)

    if proc.returncode != 0 and result.error_count == 0:
        result.is_truncated = True
        result.errors.append("ffmpeg exited with non-zero code, file may be corrupted")
        result.error_count = 1
    if (expected_duration > 0 and position > 0
            and position < expected_duration * MIN_DECODED_SHARE):
        result.is_truncated = True
        result.errors.append(
            f"decoded only {position:.0f}s of {expected_duration:.0f}s — premature end of file")
        result.error_count += 1

    result.full_checked = True
    _note_timestamps(result)
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
