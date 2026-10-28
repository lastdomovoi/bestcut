"""Frame cadence: timestamps, variable frame rate, telecine, repeated frames.

`r_frame_rate` only says which rate can represent every timestamp.  This
module looks at the timestamps themselves and at the pictures:

  * duplicate / non-monotonic timestamps and gaps (dropped frames)
  * constant vs variable frame rate, and the rate the timestamps imply
  * hard telecine (repeated-field analysis)
  * share of repeated pictures (e.g. 24p padded to 30 fps)

Everything is measured on a few short windows, so it stays cheap.
"""

from __future__ import annotations

import re
import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from procutil import run_tool

REORDER_DEPTH = 16            # most H.264/HEVC streams reorder fewer frames than this
PACKET_WINDOW = 600            # packets read per window
WINDOW_FRACTIONS = (0.15, 0.5, 0.85)
IDET_SECONDS = 20
REPEAT_SECONDS = 12
REPEAT_FRACTIONS = (0.3, 0.7)

VFR_STABLE_SHARE = 0.9         # intervals within tolerance of the mode
INTERVAL_TOLERANCE = 0.03
# MKV/WebM timestamps are rounded to 1 ms: two rounded intervals differ by up to
# 2 ms even at a perfectly constant rate (30/60 fps are not multiples of 1 ms)
TIMESTAMP_QUANTUM = 0.0025
GAP_FACTOR = 1.8               # an interval this many times the mode is a gap
TELECINE_REPEAT_SHARE = 0.3    # 3:2 pulldown repeats ~40% of the fields
REPEATED_FRAMES_NOTABLE = 0.12


@dataclass
class Cadence:
    interval_fps: float | None = None      # rate implied by the timestamps
    vfr: bool = False
    duplicate_timestamps: int = 0
    non_monotonic_dts: int = 0
    gaps: int = 0                          # dropped-frame gaps
    telecine: bool = False
    interlaced: bool = False
    repeated_frame_share: float | None = None
    notes: list[str] = field(default_factory=list)


def interval_stats(pts: list[float], dts: list[float]) -> dict:
    """Cadence figures from the packet timestamps of one window (pure)."""
    ordered = sorted(pts)
    # Packets come in decode order, so the newest frames of the window are
    # still missing their B-frame neighbours: do not read that as a gap.
    if len(ordered) > 4 * REORDER_DEPTH:
        ordered = ordered[:-REORDER_DEPTH]
    deltas = [round(b - a, 4) for a, b in zip(ordered, ordered[1:])]
    dup = sum(1 for d in deltas if d <= 0.0005)
    positive = [d for d in deltas if d > 0.0005]
    out = {"duplicates": dup, "non_monotonic_dts": 0, "gaps": 0,
           "mode": None, "stable_share": None}
    clean_dts = [d for d in dts if d is not None]
    out["non_monotonic_dts"] = sum(1 for a, b in zip(clean_dts, clean_dts[1:]) if b < a - 0.0005)
    if len(positive) < 5:
        return out
    # the typical frame interval: median is robust against gaps and doubles
    mode = statistics.median(positive)
    tol = max(mode * INTERVAL_TOLERANCE, TIMESTAMP_QUANTUM)
    near = sum(1 for d in positive if abs(d - mode) <= tol)
    out["mode"] = mode
    out["stable_share"] = near / len(positive)
    out["gaps"] = sum(1 for d in positive if d > mode * GAP_FACTOR + TIMESTAMP_QUANTUM)
    return out


def _read_packets(path: Path, start: float) -> tuple[list[float], list[float | None]]:
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "packet=pts_time,dts_time", "-of", "csv=p=0",
           "-read_intervals", f"{start:.2f}%+#{PACKET_WINDOW}", str(path)]
    try:
        res = run_tool(cmd, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return [], []
    pts, dts = [], []
    for line in res.stdout.splitlines():
        parts = line.strip().split(",")
        try:
            p = float(parts[0])
        except (ValueError, IndexError):
            continue
        pts.append(p)
        try:
            dts.append(float(parts[1]))
        except (ValueError, IndexError):
            dts.append(None)
    return pts, dts


def idet_summary(stderr: str) -> dict | None:
    """Field-analysis counts from ffmpeg's idet filter (last instance)."""
    reps = re.findall(r"Repeated Fields: Neither:\s*(\d+) Top:\s*(\d+) Bottom:\s*(\d+)", stderr)
    multi = re.findall(r"Multi frame detection: TFF:\s*(\d+) BFF:\s*(\d+) Progressive:\s*(\d+) "
                       r"Undetermined:\s*(\d+)", stderr)
    if not reps or not multi:
        return None
    rn, rt, rb = map(int, reps[-1])
    tff, bff, prog, und = map(int, multi[-1])
    total_rep = rn + rt + rb
    total = tff + bff + prog + und
    if total_rep == 0 or total == 0:
        return None
    return {"repeat_share": (rt + rb) / total_rep,
            "interlaced_share": (tff + bff) / total, "frames": total}


def _idet(path: Path, start: float) -> dict | None:
    cmd = ["ffmpeg", "-v", "info", "-nostdin", "-ss", f"{start:.2f}", "-t", str(IDET_SECONDS),
           "-i", str(path), "-map", "0:v:0", "-vf", "idet", "-an", "-sn", "-f", "null", "-"]
    try:
        res = run_tool(cmd, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return idet_summary(res.stderr)


def _count_frames(path: Path, start: float, vf_chain: str) -> int | None:
    cmd = ["ffmpeg", "-v", "info", "-nostdin", "-ss", f"{start:.2f}", "-t", str(REPEAT_SECONDS),
           "-i", str(path), "-map", "0:v:0", "-vf", vf_chain + ",showinfo", "-an", "-sn",
           "-f", "null", "-"]
    try:
        res = run_tool(cmd, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return len(re.findall(r"\bn:\s*\d+", res.stderr))


def repeated_share(path: Path, start: float) -> float | None:
    """Share of pictures that merely repeat the previous one."""
    total = _count_frames(path, start, "null")
    kept = _count_frames(path, start, "mpdecimate")
    if not total or kept is None or total < 30:
        return None
    return max(0.0, 1.0 - kept / total)


def repeated_share_over(path: Path, duration: float) -> float | None:
    """Repeated-picture share that holds in several places: a static title
    card or credit roll in one window must not pass for a padded rate."""
    shares = [s for s in (repeated_share(path, duration * f) for f in REPEAT_FRACTIONS)
              if s is not None]
    return min(shares) if len(shares) == len(REPEAT_FRACTIONS) else None


def analyze_cadence(path: Path, duration: float, nominal_fps: float = 0.0,
                    effective_fps: float = 0.0) -> Cadence:
    """Measure the cadence of the first video stream."""
    c = Cadence()
    if duration <= 0:
        return c
    modes, shares = [], []
    for frac in WINDOW_FRACTIONS:
        pts, dts = _read_packets(path, duration * frac)
        st = interval_stats(pts, dts)
        c.duplicate_timestamps += st["duplicates"]
        c.non_monotonic_dts += st["non_monotonic_dts"]
        c.gaps += st["gaps"]
        if st["mode"]:
            modes.append(st["mode"])
            shares.append(st["stable_share"])
    if modes:
        c.interval_fps = round(1.0 / statistics.median(modes), 3)
        c.vfr = statistics.mean(shares) < VFR_STABLE_SHARE
    mid = duration * 0.5
    fields = _idet(path, mid)
    if fields:
        # idet's TFF/BFF verdict is unreliable on detailed progressive
        # pictures, so interlacing is taken from the stream's field_order
        # (probe.measure_cadence); only the field-repeat pattern is used here.
        c.telecine = fields["repeat_share"] >= TELECINE_REPEAT_SHARE
    c.repeated_frame_share = repeated_share_over(path, duration)

    if c.duplicate_timestamps:
        c.notes.append(f"{c.duplicate_timestamps} duplicate timestamps")
    if c.non_monotonic_dts:
        c.notes.append(f"{c.non_monotonic_dts} decreasing DTS values")
    if c.gaps:
        c.notes.append(f"{c.gaps} timestamp gaps (dropped frames)")
    if c.vfr:
        c.notes.append("variable frame rate")
    if c.interval_fps and effective_fps and abs(c.interval_fps - effective_fps) > effective_fps * 0.05 \
            and not c.vfr:
        c.notes.append(f"timestamps imply {c.interval_fps:g} fps, not {effective_fps:g}")
    if c.telecine:
        c.notes.append("telecined (film pulled down to video rate)")
    if c.repeated_frame_share is not None and c.repeated_frame_share >= REPEATED_FRAMES_NOTABLE \
            and not c.telecine:
        c.notes.append(f"{c.repeated_frame_share * 100:.0f}% repeated pictures "
                       "(lower-rate source padded to the container rate)")
    return c
