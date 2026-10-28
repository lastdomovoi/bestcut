"""Subprocess helper that survives legacy (non-UTF-8) ffmpeg/ffprobe text."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class ToolResult:
    returncode: int
    stdout: str
    stderr: str
    # "" if everything was valid UTF-8, else how the text was recovered
    decode_note: str = ""


def decode_text(data: bytes) -> tuple[str, str]:
    """Decode tool output: UTF-8, then CP1251 (legacy Russian tags), then
    UTF-8 with replacement.  Returns (text, note)."""
    try:
        return data.decode("utf-8"), ""
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1251"), "not valid UTF-8, decoded as cp1251 (a guess)"
    except UnicodeDecodeError:
        return (data.decode("utf-8", errors="replace"),
                "not valid UTF-8, undecodable bytes replaced")


def run_tool(cmd: list[str], timeout: float) -> ToolResult:
    """Run a command capturing bytes and decode them with a safe policy.

    Raises subprocess.TimeoutExpired / OSError like subprocess.run.
    """
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    out, out_note = decode_text(proc.stdout or b"")
    err, err_note = decode_text(proc.stderr or b"")
    return ToolResult(proc.returncode, out, err, out_note or err_note)


@dataclass
class BytesResult:
    returncode: int
    stdout: bytes
    stderr: str


def run_tool_bytes(cmd: list[str], timeout: float) -> BytesResult:
    """Like run_tool, but stdout (e.g. raw PCM) is returned undecoded."""
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    err, _ = decode_text(proc.stderr or b"")
    return BytesResult(proc.returncode, proc.stdout or b"", err)
