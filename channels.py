"""Real audio channel analysis: energy plus inter-channel correlation.

Samples several scenes across the file (a quiet or dialogue-only scene
cannot decide the verdict), measures per-channel energy and the pairwise
correlation of channels, and reports duplicated, phase-inverted and
matrixed channels separately from genuinely independent ones.
"""

from __future__ import annotations

import math
import subprocess
from array import array
from dataclasses import dataclass, field
from operator import mul
from pathlib import Path

from procutil import run_tool_bytes

SAMPLE_RATE = 8000          # plenty for correlation, cheap to decode
WINDOW_SEC = 15
SILENT_DB = -70.0           # channel energy below this is silence
CORR_MIN_DB = -60.0         # channels must be at least this loud to correlate
DUPLICATE_CORR = 0.995      # exact copy (mono packed into more channels)
NEAR_DUPLICATE_CORR = 0.98
MATRIX_CORR = 0.9           # surround vs (L-R) for a matrixed upmix
MIN_SIGNAL_WINDOWS = 2      # scenes with sound needed for a verdict


@dataclass
class ChannelAnalysis:
    real_channels: int | None = None   # None = no reliable verdict
    confidence: str = "low"            # "low" | "medium" | "high"
    notes: list[str] = field(default_factory=list)
    windows: int = 0
    signal_windows: int = 0


def _window_starts(duration: float) -> tuple[list[float], float]:
    if duration >= 120:
        fractions = (0.15, 0.35, 0.55, 0.75)
    elif duration >= 30:
        fractions = (0.25, 0.65)
    else:
        fractions = (0.0,)
    length = min(WINDOW_SEC, duration / (len(fractions) + 1)) if duration > 0 else WINDOW_SEC
    length = max(1.0, length)
    return [max(0.0, duration * f) for f in fractions], length


def _read_window(path: Path, stream_index: int, channels: int,
                 start: float, length: float) -> list[array] | None:
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.2f}",
           "-t", f"{length:.2f}", "-i", str(path), "-map", f"0:a:{stream_index}",
           "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
    try:
        res = run_tool_bytes(cmd, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return None
    usable = len(res.stdout) // (4 * channels) * (4 * channels)
    if usable == 0:
        return None
    pcm = array("f")
    pcm.frombytes(res.stdout[:usable])
    return [pcm[c::channels] for c in range(channels)]


def _rms_db(x) -> float:
    if not len(x):
        return -200.0
    power = sum(map(mul, x, x)) / len(x)
    return 10 * math.log10(power) if power > 1e-20 else -200.0


def _unit(x):
    """Mean-removed signal scaled to unit length, or None if (near) flat.

    Correlation of two channels is then just a dot product, and each
    channel is normalized once per window instead of once per pair.
    """
    n = len(x)
    if not n:
        return None
    mean = sum(x) / n
    c = [v - mean for v in x]
    norm = math.sqrt(sum(map(mul, c, c)))
    if norm < 1e-9:
        return None
    return [v / norm for v in c]


def _dot(u, v) -> float:
    if u is None or v is None:
        return 0.0
    return sum(map(mul, u, v))


def analyze_channels(path: Path, stream_index: int, channels: int,
                     duration: float) -> ChannelAnalysis:
    """Analyze one audio track.  Never claims a result it cannot support."""
    result = ChannelAnalysis()
    if channels <= 1:
        return result

    starts, length = _window_starts(duration)
    windows = []
    for start in starts:
        data = _read_window(path, stream_index, channels, start, length)
        if data is not None:
            windows.append(data)
    result.windows = len(windows)
    if not windows:
        result.notes.append("audio could not be decoded for analysis")
        return result

    # Per-window energies; the scene counts only if something is audible.
    energies = [[_rms_db(ch) for ch in w] for w in windows]
    signal = [i for i, e in enumerate(energies) if max(e) > SILENT_DB]
    result.signal_windows = len(signal)
    if len(signal) < MIN_SIGNAL_WINDOWS and len(windows) >= MIN_SIGNAL_WINDOWS:
        result.notes.append("too little sound in the sampled scenes; review by ear")
        return result

    # A channel is real if it carries sound in ANY sampled scene.
    active = [c for c in range(channels)
              if any(energies[i][c] > SILENT_DB for i in signal)]
    silent = [c for c in range(channels) if c not in active]
    if silent:
        result.notes.append(
            f"channel(s) {', '.join(str(c + 1) for c in silent)} silent in all "
            f"{len(signal)} sampled scene(s)")
    if not active:
        return result

    # Normalized signals, computed once per (scene, channel)
    units: dict[tuple[int, int], list[float] | None] = {}

    def unit(i, c):
        if (i, c) not in units:
            units[(i, c)] = _unit(windows[i][c])
        return units[(i, c)]

    # Pairwise relations, judged over every scene where either channel sounds.
    parent = {c: c for c in active}

    def find(c):
        while parent[c] != c:
            c = parent[c]
        return c

    duplicates = []
    for ai, a in enumerate(active):
        for b in active[ai + 1:]:
            either = [i for i in signal
                      if energies[i][a] > CORR_MIN_DB or energies[i][b] > CORR_MIN_DB]
            both = [i for i in either
                    if energies[i][a] > CORR_MIN_DB and energies[i][b] > CORR_MIN_DB]
            # A scene where only one of them sounds proves they differ
            if len(both) < len(either):
                continue
            if len(both) < MIN_SIGNAL_WINDOWS and len(signal) >= MIN_SIGNAL_WINDOWS:
                continue
            corrs = [_dot(unit(i, a), unit(i, b)) for i in both]
            if not corrs:
                continue
            if min(corrs) >= DUPLICATE_CORR:
                duplicates.append((a, b, "identical"))
                parent[find(b)] = find(a)
            elif max(corrs) <= -DUPLICATE_CORR:
                duplicates.append((a, b, "phase-inverted copy"))
                parent[find(b)] = find(a)
            elif min(corrs) >= NEAR_DUPLICATE_CORR:
                result.notes.append(
                    f"channels {a + 1} and {b + 1} are near-duplicates "
                    f"(corr >= {min(corrs):.3f}); review by ear")
    for a, b, kind in duplicates:
        result.notes.append(f"channels {a + 1} and {b + 1}: {kind}")

    # Matrixed upmix: both surrounds are just the front difference (L-R).
    matrixed = False
    if channels in (6, 8) and {0, 1, 4, 5} <= set(active):
        ok = True
        for s in (4, 5):
            both = [i for i in signal if energies[i][s] > CORR_MIN_DB
                    and energies[i][0] > CORR_MIN_DB and energies[i][1] > CORR_MIN_DB]
            if len(both) < MIN_SIGNAL_WINDOWS:
                ok = False
                break
            for i in both:
                diff = _unit([x - y for x, y in zip(windows[i][0], windows[i][1])])
                if abs(_dot(unit(i, s), diff)) < MATRIX_CORR:
                    ok = False
                    break
            if not ok:
                break
        matrixed = ok
        if matrixed:
            result.notes.append("surround channels are derived from L-R "
                                "(matrixed pseudo-surround)")

    # Matrixed surrounds are derived, so they add no independent channel
    counted = [c for c in active if not (matrixed and c in (4, 5))]
    result.real_channels = max(1, len({find(c) for c in counted}))

    result.confidence = "high" if len(signal) >= 3 else "medium"
    if result.real_channels >= channels and not result.notes:
        result.notes.append(f"{channels} independent channels")
    return result
