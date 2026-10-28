"""Click / crackle detection.

A click is an isolated, sharply impulsive defect (a pop from a bad splice,
a dropout, vinyl or tape crackle).  It is told apart from legitimate
transients by shape: a click is one to a few samples wide and the signal is
ordinary on both sides of it, while drum hits, door slams and plosives
build over many samples and are followed by a decay.  Regular spikes
(pitch pulses, a metronome) are periodic and therefore not defects.

Method: the second difference of the signal removes smooth content and
leaves impulses; each sample is compared with a robust (median-based) local
scale; a spike counts only if its neighbourhood is quiet.
"""

from __future__ import annotations

import statistics
import subprocess
from array import array
from dataclasses import dataclass
from pathlib import Path

from procutil import run_tool_bytes

SAMPLE_RATE = 16000
WINDOW_SEC = 15
BLOCK = 1024               # samples per local-scale block
SPIKE_SIGMA = 12.0         # spike must exceed this many robust sigmas
QUIET_SIGMA = 4.0          # ...and its neighbours must stay below this
QUIET_RADIUS = 6           # samples checked on each side
MIN_ABS = 0.01             # ignore spikes below this amplitude (noise floor)
PERIODIC_MIN = 6           # this many spikes in a window can be "periodic"
PERIODIC_CV = 0.15         # intervals this regular = not random defects
CRACKLE_PER_MIN = 30.0     # at least this dense = crackle
CLICK_PER_MIN = 6.0        # below this the odd pop is not worth a penalty


@dataclass
class ClickStats:
    clicks: int
    seconds: float

    @property
    def per_minute(self) -> float:
        return self.clicks / self.seconds * 60.0 if self.seconds > 0 else 0.0


def find_clicks(x) -> list[int]:
    """Indices of isolated impulsive spikes in a mono signal."""
    n = len(x)
    if n < BLOCK:
        return []
    # second difference: r[i] = x[i] - (x[i-1] + x[i+1]) / 2
    r = [0.0] * n
    for i in range(1, n - 1):
        r[i] = x[i] - 0.5 * (x[i - 1] + x[i + 1])
    absr = [abs(v) for v in r]
    scale = [0.0] * n
    for b in range(0, n, BLOCK):
        block = absr[b:b + BLOCK]
        sigma = 1.4826 * statistics.median(block)
        sigma = max(sigma, 1e-6)
        for i in range(b, min(n, b + BLOCK)):
            scale[i] = sigma
    spikes = []
    i = 2
    while i < n - 2:
        if absr[i] > MIN_ABS and absr[i] > SPIKE_SIGMA * scale[i]:
            # an impulse also shows as -A/2 on both neighbours: skip those,
            # look further out for anything else that is loud
            lo, hi = max(0, i - QUIET_RADIUS), min(n, i + QUIET_RADIUS + 1)
            loud_neighbour = any(
                absr[j] > QUIET_SIGMA * scale[i]
                for j in range(lo, hi) if abs(j - i) > 1)
            if not loud_neighbour:
                spikes.append(i)
                i += QUIET_RADIUS
                continue
        i += 1
    return spikes


def _drop_periodic(spikes: list[int]) -> list[int]:
    """Regular spikes (pitch pulses, a ticking clock) are not random defects."""
    if len(spikes) < PERIODIC_MIN:
        return spikes
    gaps = [b - a for a, b in zip(spikes, spikes[1:])]
    mean = statistics.mean(gaps)
    if mean > 0 and statistics.pstdev(gaps) / mean < PERIODIC_CV:
        return []
    return spikes


def count_clicks(x) -> int:
    return len(_drop_periodic(find_clicks(x)))


def _windows(duration: float) -> list[tuple[float, float]]:
    if duration >= 120:
        fractions = (0.15, 0.35, 0.55, 0.75)
    elif duration >= 30:
        fractions = (0.25, 0.65)
    else:
        fractions = (0.0,)
    length = max(1.0, min(WINDOW_SEC, duration / (len(fractions) + 1))) if duration > 0 else WINDOW_SEC
    return [(max(0.0, duration * f), length) for f in fractions]


def detect_clicks(path: Path, stream_index: int, duration: float) -> ClickStats | None:
    """Click statistics of one audio track over several scenes; None if the
    audio cannot be decoded."""
    clicks, seconds = 0, 0.0
    for start, length in _windows(duration):
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.2f}",
               "-t", f"{length:.2f}", "-i", str(path), "-map", f"0:a:{stream_index}",
               "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
        try:
            res = run_tool_bytes(cmd, timeout=120)
        except (subprocess.TimeoutExpired, OSError):
            continue
        usable = len(res.stdout) // 4 * 4
        if usable < BLOCK * 4:
            continue
        pcm = array("f")
        pcm.frombytes(res.stdout[:usable])
        clicks += count_clicks(pcm)
        seconds += len(pcm) / SAMPLE_RATE
    if seconds <= 0:
        return None
    return ClickStats(clicks, seconds)
