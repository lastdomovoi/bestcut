"""Synchronized comparison of two video files.

Matching titles and metadata never prove that two files hold the same
video.  This module decodes both files, aligns their timelines, and then
classifies the pair from the pictures themselves:

  byte-identical / payload-identical (remux) / decoded-equivalent /
  same-cut re-encode / different cut / different content

along the way it reports timeline shifts (inserted or missing segments),
replaced blocks, persistent overlays, colour/contrast differences and
whether the larger file adds anything over the smaller one.

How it works
  1. One decode per file produces a small RGB thumbnail every second
     (geometry-normalized: black bars cropped, scaled to 24x14).  They are
     cached on disk, so each file is decoded once for any number of pairs.
  2. Thumbnails are compared after brightness/contrast normalization, so a
     colour-graded copy still aligns.  A global offset is found first, then
     it is re-estimated piecewise, which exposes inserted/removed material.
  3. At checkpoints inside matched stretches, the exact frame offset is
     refined and SSIM/PSNR plus a detail (Laplacian) measure are taken on
     full-size frames.
  4. Stream payload hashes (stream copy, no decoding) tell remuxes and
     unchanged audio/subtitle tracks from changed ones.
"""

from __future__ import annotations

import hashlib
import math
import re
import statistics
import subprocess
from dataclasses import dataclass, field
from operator import sub
from pathlib import Path

import cache
from models import VideoFile
from procutil import run_tool, run_tool_bytes

# --- thumbnails -----------------------------------------------------------
THUMB_W, THUMB_H = 24, 14
THUMB_PIXELS = THUMB_W * THUMB_H
THUMB_BYTES = THUMB_PIXELS * 3
THUMB_VERSION = 1

# A thumbnail flatter than this (luma std) carries no alignment information
FLAT_STD = 3.0

# --- alignment ------------------------------------------------------------
MATCH_DIST = 0.45          # mean |difference| in std units: below = same picture
COARSE_PROBES = 24
FINE_PROBES = 60
SEGMENT_SEC = 10
SEGMENT_MIN_SHARE = 0.6    # share of seconds that must match to accept an offset
SHIFT_MAX = 180            # largest inserted/removed stretch looked for (s)
UNPAIRED_SEC = 2           # head/tail longer than this is a different cut
GLITCH_SEC = 3             # unmatched stretches this short: glitch / damage
MAX_GLOBAL_OFFSET = 1800

# --- verdicts -------------------------------------------------------------
SSIM_EQUIVALENT = 0.9995
SSIM_REENCODE = 0.85
SSIM_PROCESSED = 0.6
CHECKPOINTS = 10
MIN_COVERAGE_SAME_CUT = 0.9

# Colour/contrast differences that matter (0-255 scale)
COLOR_CAST = 4.0
LUMA_SHIFT = 8.0
CONTRAST_BAND = (0.9, 1.1)
COLOR_STABLE = 0.8         # share of seconds that must agree in sign

SAFE_RELATIONS = {"byte_identical", "payload_identical", "decoded_equivalent",
                  "same_cut_reencode"}


@dataclass
class Divergence:
    kind: str        # extra_in_a, extra_in_b, replaced_block, glitch, overlay
    a_start: float   # seconds in file A's timeline
    a_end: float
    note: str = ""


@dataclass
class ColorDiff:
    d_luma: float = 0.0          # A minus B, median over aligned seconds
    cast: float = 0.0            # largest chroma-ish channel shift
    contrast_ratio: float = 1.0  # A / B
    stable: bool = False
    material: bool = False
    metadata_issues: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return (f"luma {self.d_luma:+.1f}, colour cast {self.cast:.1f}, "
                f"contrast x{self.contrast_ratio:.2f}"
                + (", stable across scenes" if self.stable else ""))


@dataclass
class Redundancy:
    verdict: str = ""            # "", "redundant_larger", "better_larger", "degraded_smaller", "equivalent"
    redundant_path: Path | None = None
    size_ratio: float = 1.0      # larger / smaller
    detail_ratio: float | None = None   # larger / smaller (Laplacian energy)
    unique_tracks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class SequenceComparison:
    path_a: Path
    path_b: Path
    relation: str = "unknown"
    confidence: str = "low"
    evidence: list[str] = field(default_factory=list)
    offset: int | None = None            # B index = A index + offset (seconds)
    coverage: float = 0.0                # matched share of informative seconds
    ssim_mean: float | None = None
    ssim_min: float | None = None
    psnr_mean: float | None = None
    checkpoints: int = 0
    frame_counts_equal: bool | None = None
    divergences: list[Divergence] = field(default_factory=list)
    overlay_owner: Path | None = None    # file that carries the overlay, if known
    overlay_note: str = ""
    color: ColorDiff = field(default_factory=ColorDiff)
    identical_streams: list[str] = field(default_factory=list)
    changed_streams: list[str] = field(default_factory=list)
    redundancy: Redundancy = field(default_factory=Redundancy)
    review_required: bool = True
    review_reasons: list[str] = field(default_factory=list)

    def unique_in(self, path: Path) -> list[str]:
        """Tracks the file at `path` has and the other file lacks."""
        tag = "A has" if path == self.path_a else "B has"
        return [u for u in self.redundancy.unique_tracks if u.startswith(tag)]

    @property
    def safe_to_dedupe(self) -> bool:
        return self.relation in SAFE_RELATIONS and not self.review_required


# ===========================================================================
# Thumbnails
# ===========================================================================

class ThumbSeq:
    """One RGB thumbnail per second plus derived luma / normalized forms."""

    def __init__(self, frames: list[bytes]):
        self.frames = frames
        self.luma = [self._luma(f) for f in frames]
        self.norm = [self._normalize(y) for y in self.luma]

    def __len__(self) -> int:
        return len(self.frames)

    @staticmethod
    def _luma(frame: bytes) -> bytes:
        r, g, b = frame[0::3], frame[1::3], frame[2::3]
        return bytes((77 * x + 150 * y + 29 * z) >> 8 for x, y, z in zip(r, g, b))

    @staticmethod
    def _normalize(luma: bytes) -> list[float] | None:
        n = len(luma)
        mean = sum(luma) / n
        var = sum((v - mean) ** 2 for v in luma) / n
        std = math.sqrt(var)
        if std < FLAT_STD:
            return None
        return [(v - mean) / std for v in luma]

    def informative(self, i: int) -> bool:
        return 0 <= i < len(self.norm) and self.norm[i] is not None


def _dist(a: list[float], b: list[float]) -> float:
    return sum(map(abs, map(sub, a, b))) / len(a)


def _crop_filter(vf: VideoFile) -> str:
    v = vf.video
    if v.has_black_bars and v.crop_width and v.crop_height:
        return f"crop={v.crop_width}:{v.crop_height},"
    return ""


def _thumb_cache_path(path: Path) -> Path:
    return cache.CACHE_DIR / f"thumbs{THUMB_VERSION}-{cache._cache_key(path)}.bin"


def extract_thumbs(vf: VideoFile, use_cache: bool = True) -> ThumbSeq | None:
    """Decode a file once into per-second thumbnails (None on failure)."""
    path = vf.path
    cp = None
    if use_cache:
        try:
            cp = _thumb_cache_path(path)
            if cp.exists():
                raw = cp.read_bytes()
                if raw and len(raw) % THUMB_BYTES == 0:
                    return _split(raw)
        except OSError:
            cp = None
    vf_chain = (f"{_crop_filter(vf)}fps=1,scale={THUMB_W}:{THUMB_H}:flags=area,"
                "setsar=1,format=rgb24")
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:v:0",
           "-an", "-sn", "-vf", vf_chain, "-f", "rawvideo", "-"]
    try:
        res = run_tool_bytes(cmd, timeout=max(600, vf.duration * 4))
    except (subprocess.TimeoutExpired, OSError):
        return None
    raw = res.stdout[: len(res.stdout) // THUMB_BYTES * THUMB_BYTES]
    # A failed or cut-off decode must neither be used nor cached
    if res.returncode != 0 or not raw:
        return None
    if vf.duration > 20 and len(raw) // THUMB_BYTES < vf.duration * 0.95 - 3:
        return None
    if use_cache and cp is not None:
        try:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_bytes(raw)
        except OSError:
            pass
    return _split(raw)


def _split(raw: bytes) -> ThumbSeq:
    return ThumbSeq([raw[i:i + THUMB_BYTES] for i in range(0, len(raw), THUMB_BYTES)])


# ===========================================================================
# Alignment
# ===========================================================================

def _probe_seconds(seq: ThumbSeq, count: int) -> list[int]:
    """Evenly spread informative seconds of a sequence."""
    informative = [i for i in range(len(seq)) if seq.informative(i)]
    if len(informative) <= count:
        return informative
    step = len(informative) / count
    return [informative[int(k * step)] for k in range(count)]


def _mean_dist(a: ThumbSeq, b: ThumbSeq, seconds: list[int], offset: int,
               min_valid: int = 1) -> float | None:
    vals = []
    for i in seconds:
        j = i + offset
        if a.informative(i) and b.informative(j):
            vals.append(_dist(a.norm[i], b.norm[j]))
    if len(vals) < min_valid:
        return None
    return sum(vals) / len(vals)


def _match_share(a: ThumbSeq, b: ThumbSeq, seconds: list[int], offset: int,
                 quick: bool = False) -> float:
    """Share of the seconds that look the same at this offset.

    quick: look at a few seconds first and give up (0.0) when none match,
    which rejects almost every wrong offset at a fraction of the cost.
    """
    if not seconds:
        return 0.0
    if quick and len(seconds) > 4:
        probe = seconds[:: max(1, len(seconds) // 4)][:4]
        if not any(_second_state(a, b, i, offset) == "match" for i in probe):
            return 0.0
    ok = sum(1 for i in seconds if _second_state(a, b, i, offset) == "match")
    return ok / len(seconds)


def global_offset(a: ThumbSeq, b: ThumbSeq) -> tuple[int, float] | None:
    """Best single offset (B index = A index + offset) and its distance."""
    span = min(MAX_GLOBAL_OFFSET, abs(len(a) - len(b)) + 90)
    probes = _probe_seconds(a, COARSE_PROBES)
    if len(probes) < 4:
        return None
    scored = []
    for off in range(-span, span + 1):
        d = _mean_dist(a, b, probes, off, min_valid=4)
        if d is not None:
            scored.append((d, off))
    if not scored:
        return None
    scored.sort()
    fine = _probe_seconds(a, FINE_PROBES)
    best = None
    for _, off in scored[:5]:
        d = _mean_dist(a, b, fine, off, min_valid=4)
        if d is not None and (best is None or d < best[1]):
            best = (off, d)
    return best


@dataclass
class Alignment:
    # per A-second: offset used (None if unmatched) and verdict
    offsets: list[int | None]
    state: list[str]            # "match" | "mismatch" | "flat"
    events: list[tuple[int, int, int]]   # (a_second, old_offset, new_offset)
    coverage: float


def _second_state(a: ThumbSeq, b: ThumbSeq, i: int, off: int) -> str:
    """"match", "mismatch", "flat" (nothing to judge) or "outside" (B has no
    such second) for A second i."""
    if not a.informative(i):
        return "flat"
    j = i + off
    if not 0 <= j < len(b):
        return "outside"
    if not b.informative(j):      # B is blank where A is not
        return "mismatch"
    return "match" if _dist(a.norm[i], b.norm[j]) <= MATCH_DIST * 1.4 else "mismatch"


def align(a: ThumbSeq, b: ThumbSeq) -> Alignment | None:
    """Piecewise alignment of two timelines."""
    g = global_offset(a, b)
    if g is None or g[1] > MATCH_DIST * 1.5:
        # no stretch of the files looks alike at any offset
        return None
    cur = g[0]
    n = len(a)
    seg_offsets: list[tuple[int, int, int | None]] = []   # (start, end, offset)
    for start in range(0, n, SEGMENT_SEC):
        end = min(n, start + SEGMENT_SEC)
        secs = [i for i in range(start, end) if a.informative(i)]
        if not secs:
            seg_offsets.append((start, end, cur))     # nothing to judge: keep
            continue
        if _match_share(a, b, secs, cur) >= SEGMENT_MIN_SHARE:
            seg_offsets.append((start, end, cur))
            continue
        best = None
        for delta in range(-SHIFT_MAX, SHIFT_MAX + 1):
            share = _match_share(a, b, secs, cur + delta, quick=True)
            if best is None or share > best[1] or (
                    share == best[1] and abs(delta) < abs(best[0] - cur)):
                best = (cur + delta, share)
        if best is not None and best[1] >= SEGMENT_MIN_SHARE:
            cur = best[0]
            seg_offsets.append((start, end, cur))
        else:
            # nothing matches: keep the running offset; the per-second
            # states below show exactly which seconds differ
            seg_offsets.append((start, end, cur))

    offsets: list[int | None] = [None] * n
    for start, end, off in seg_offsets:
        for i in range(start, end):
            offsets[i] = off

    # Move each shift boundary to where the picture actually changes
    events: list[tuple[int, int, int]] = []
    last_off: int | None = None
    last_end = 0
    for k, (start, end, off) in enumerate(seg_offsets):
        if off is None:
            continue
        if last_off is not None and off != last_off:
            lo = max(0, start - 2 * SEGMENT_SEC)
            hi = min(n, end)
            best_t, best_cost = start, None
            for t in range(lo, hi + 1):
                cost = 0
                for i in range(lo, hi):
                    if not a.informative(i):
                        continue
                    o = last_off if i < t else off
                    if _second_state(a, b, i, o) == "mismatch":
                        cost += 1
                if best_cost is None or cost < best_cost:
                    best_t, best_cost = t, cost
            events.append((best_t, last_off, off))
            for i in range(lo, hi):
                offsets[i] = last_off if i < best_t else off
        last_off = off
        last_end = end

    state = []
    for i in range(n):
        off = offsets[i]
        if not a.informative(i):
            state.append("flat")
        elif off is None:
            state.append("mismatch")
        else:
            state.append(_second_state(a, b, i, off))
    informative = [s for s in state if s in ("match", "mismatch", "outside")]
    coverage = (sum(1 for s in informative if s == "match") / len(informative)
                if informative else 0.0)
    return Alignment(offsets, state, events, coverage)


def find_divergences(al: Alignment, n_b: int) -> list[Divergence]:
    """Timeline shifts and unmatched stretches, in A's timeline."""
    out: list[Divergence] = []
    for t, old, new in al.events:
        delta = new - old
        if delta > 0:
            out.append(Divergence("extra_in_b", t, t, f"B has {delta}s more material here"))
        else:
            out.append(Divergence("extra_in_a", t, t - delta, f"A has {-delta}s more material here"))
    # Unpaired head / tail: one file starts earlier or ends later
    n = len(al.state)
    mapped = [i for i in range(n) if al.state[i] == "match" and al.offsets[i] is not None]
    if mapped:
        head = al.offsets[mapped[0]]      # B index of A's second 0
        if head > UNPAIRED_SEC:
            out.append(Divergence("extra_in_b", 0, 0, f"B has {head}s more material at the start"))
        elif -head > UNPAIRED_SEC:
            out.append(Divergence("extra_in_a", 0, -head, f"A has {-head}s more material at the start"))
        tail = n_b - 1 - (n - 1 + al.offsets[mapped[-1]])
        if tail > UNPAIRED_SEC:
            out.append(Divergence("extra_in_b", n, n, f"B has {tail}s more material at the end"))
        elif -tail > UNPAIRED_SEC:
            out.append(Divergence("extra_in_a", n + tail, n, f"A has {-tail}s more material at the end"))
    # unmatched runs (ignore tolerance of isolated single mismatched seconds)
    i = 0
    while i < n:
        if al.state[i] == "mismatch":
            j = i
            while j < n and al.state[j] in ("mismatch", "flat"):
                j += 1
            while j > i and al.state[j - 1] == "flat":
                j -= 1
            length = j - i
            if length >= 1:
                kind = "glitch" if length <= GLITCH_SEC else "replaced_block"
                out.append(Divergence(kind, i, j,
                                      f"{length}s differ"
                                      + (" (possible damage)" if kind == "glitch" else "")))
            i = j
        else:
            i += 1
    # A mismatching stretch inside material that only A has is that same
    # material, not an extra "replaced block"
    extra_a = [d for d in out if d.kind == "extra_in_a" and d.a_end > d.a_start]
    out = [d for d in out
           if not (d.kind in ("replaced_block", "glitch")
                   and any(e.a_start <= d.a_start and d.a_end <= e.a_end for e in extra_a))]
    out.sort(key=lambda d: d.a_start)
    return out


# ===========================================================================
# Overlay and colour from aligned thumbnails
# ===========================================================================

def _matched_pairs(a: ThumbSeq, b: ThumbSeq, al: Alignment, limit: int = 300):
    pairs = [(i, i + al.offsets[i]) for i in range(len(al.state))
             if al.state[i] == "match" and al.offsets[i] is not None
             and 0 <= i + al.offsets[i] < len(b)]
    if len(pairs) > limit:
        step = len(pairs) / limit
        pairs = [pairs[int(k * step)] for k in range(limit)]
    return pairs


def detect_overlay(a: ThumbSeq, b: ThumbSeq, al: Alignment,
                   path_a: Path, path_b: Path) -> tuple[Path | None, str]:
    """Persistent localized difference (logo, sponsor bug, burned text).

    Returns (owner, note): owner is the file that carries it when that can
    be told (static region = lower temporal variation), else None.
    """
    pairs = _matched_pairs(a, b, al)
    if len(pairs) < 8:
        return None, ""
    cells = THUMB_PIXELS
    hot = [0] * cells
    sums = [0.0] * cells
    valid = 0
    for i, j in pairs:
        na, nb = a.norm[i], b.norm[j]
        if na is None or nb is None:
            continue
        valid += 1
        for c in range(cells):
            d = abs(na[c] - nb[c])
            sums[c] += d
            if d > 0.9:
                hot[c] += 1
    if valid < 8:
        return None, ""
    persistent = [c for c in range(cells) if hot[c] / valid >= 0.7]
    if not persistent or len(persistent) > cells * 0.2:
        return None, ""
    # Others must be quiet, otherwise it is just a different picture
    quiet = statistics.median(sums) / valid
    if quiet > 0.35:
        return None, ""
    cols = [c % THUMB_W for c in persistent]
    rows = [c // THUMB_W for c in persistent]
    region = (f"{'top' if statistics.mean(rows) < THUMB_H / 2 else 'bottom'}-"
              f"{'left' if statistics.mean(cols) < THUMB_W / 2 else 'right'}")
    # Which side is static there?  An overlay is a fixed patch.
    def tstd(seq, idxs):
        vals = []
        for c in persistent:
            series = [seq.luma[k][c] for k in idxs]
            vals.append(statistics.pstdev(series))
        return statistics.mean(vals)
    sa = tstd(a, [i for i, _ in pairs])
    sb = tstd(b, [j for _, j in pairs])
    note = f"persistent overlay in the {region} area ({len(persistent)} of {cells} cells)"
    if sb > 0 and sa / sb < 0.8:
        return path_a, note + "; A shows the static patch"
    if sa > 0 and sb / sa < 0.8:
        return path_b, note + "; B shows the static patch"
    return None, note + "; cannot tell which file carries it"


def _chan_means(frame: bytes) -> tuple[float, float, float]:
    n = THUMB_PIXELS
    return (sum(frame[0::3]) / n, sum(frame[1::3]) / n, sum(frame[2::3]) / n)


def compare_color(a: ThumbSeq, b: ThumbSeq, al: Alignment,
                  va: VideoFile, vb: VideoFile) -> ColorDiff:
    """Stable colour / contrast difference over aligned matched scenes."""
    cd = ColorDiff()
    pairs = _matched_pairs(a, b, al)
    if len(pairs) >= 8:
        dl, dr, dg, db, cr = [], [], [], [], []
        for i, j in pairs:
            ra, ga, ba = _chan_means(a.frames[i])
            rb, gb, bb = _chan_means(b.frames[j])
            ya, yb = a.luma[i], b.luma[j]
            la, lb = sum(ya) / THUMB_PIXELS, sum(yb) / THUMB_PIXELS
            dl.append(la - lb)
            dr.append((ra - la) - (rb - lb))   # chroma-like: channel minus luma
            dg.append((ga - la) - (gb - lb))
            db.append((ba - la) - (bb - lb))
            sa_, sb_ = statistics.pstdev(ya), statistics.pstdev(yb)
            if sa_ > FLAT_STD and sb_ > FLAT_STD:
                cr.append(sa_ / sb_)
        cd.d_luma = statistics.median(dl)
        med = {"r": statistics.median(dr), "g": statistics.median(dg),
               "b": statistics.median(db)}
        cd.cast = max(abs(v) for v in med.values())
        cd.contrast_ratio = statistics.median(cr) if cr else 1.0

        def consistent(vals, threshold):
            m = statistics.median(vals)
            if abs(m) < threshold:
                return False
            same = sum(1 for v in vals if (v > 0) == (m > 0) and abs(v) > threshold / 2)
            return same / len(vals) >= COLOR_STABLE

        cd.stable = (consistent(dl, LUMA_SHIFT) or consistent(dr, COLOR_CAST)
                     or consistent(dg, COLOR_CAST) or consistent(db, COLOR_CAST)
                     or (cr and (cd.contrast_ratio < CONTRAST_BAND[0]
                                 or cd.contrast_ratio > CONTRAST_BAND[1])
                         and sum(1 for v in cr
                                 if (v > 1) == (cd.contrast_ratio > 1)) / len(cr) >= COLOR_STABLE))
        cd.material = cd.stable and (
            cd.cast >= COLOR_CAST or abs(cd.d_luma) >= LUMA_SHIFT
            or not (CONTRAST_BAND[0] <= cd.contrast_ratio <= CONTRAST_BAND[1]))
    cd.metadata_issues = color_metadata_issues(va, vb)
    return cd


def color_metadata_issues(va: VideoFile, vb: VideoFile) -> list[str]:
    """Missing or contradictory colour tags between two files."""
    issues = []
    for label, attr in (("range", "color_range"), ("matrix", "color_space"),
                        ("transfer", "color_transfer"), ("primaries", "color_primaries")):
        x, y = getattr(va.video, attr), getattr(vb.video, attr)
        if x and y and x != y:
            issues.append(f"color {label} differs: {x} vs {y}")
        elif bool(x) != bool(y):
            missing = va if not x else vb
            issues.append(f"color {label} missing in {missing.path.name}")
    return issues


# ===========================================================================
# Checkpoints: exact frame offset, SSIM / PSNR, detail
# ===========================================================================

def _active_size(vf: VideoFile) -> tuple[int, int]:
    return vf.video.active_width or vf.video.width, vf.video.active_height or vf.video.height


def _raw_gray(vf: VideoFile, t: float, length: float, w: int, h: int,
              fps: float | None = None, frames: int | None = None) -> list[bytes]:
    chain = f"{_crop_filter(vf)}scale={w}:{h}:flags=bicubic,setsar=1"
    if fps:
        chain += f",fps={fps}"
    chain += ",format=gray"
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}",
           "-t", f"{length:.3f}", "-i", str(vf.path), "-map", "0:v:0", "-an", "-sn",
           "-vf", chain]
    if frames:
        cmd += ["-frames:v", str(frames)]
    cmd += ["-f", "rawvideo", "-"]
    try:
        res = run_tool_bytes(cmd, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return []
    size = w * h
    return [res.stdout[i:i + size] for i in range(0, len(res.stdout) - size + 1, size)]


def _hf_energy(gray: bytes, w: int, h: int) -> float:
    """Mean absolute Laplacian: fine-detail energy of a frame."""
    total = 0
    for y in range(1, h - 1):
        r0, r1, r2 = (gray[(y - 1) * w:y * w], gray[y * w:(y + 1) * w],
                      gray[(y + 1) * w:(y + 2) * w])
        total += sum(abs(4 * c - u - d - l - r)
                     for c, u, d, l, r in zip(r1[1:-1], r0[1:-1], r2[1:-1], r1[:-2], r1[2:]))
    return total / ((w - 2) * (h - 2))


def _fps_of(vf: VideoFile) -> float:
    return vf.video.fps if vf.video.fps and vf.video.fps > 0 else 25.0


def refine_checkpoint(va: VideoFile, vb: VideoFile, ta: float, tb: float,
                      w: int, h: int) -> tuple[float, bytes, bytes] | None:
    """Find the frame of B matching A's frame at ta (±0.6 s window).

    Returns (b_time, a_gray, b_gray) with full-size gray frames, or None.
    """
    a_frames = _raw_gray(va, ta, 1.0, w, h, frames=1)
    if not a_frames:
        return None
    fb = _fps_of(vb)
    start = max(0.0, tb - 0.6)
    b_frames = _raw_gray(vb, start, 1.4, w, h, fps=fb)
    if not b_frames:
        return None
    a = a_frames[0]
    stride = max(1, w // 64)

    def sample(g):
        return [g[y * w + x] for y in range(0, h, stride) for x in range(0, w, stride)]

    sa = sample(a)
    ma = sum(sa) / len(sa)
    best, best_d = 0, None
    for k, g in enumerate(b_frames):
        sg = sample(g)
        mg = sum(sg) / len(sg)
        d = sum(abs((x - ma) - (y - mg)) for x, y in zip(sa, sg))
        if best_d is None or d < best_d:
            best, best_d = k, d
    return start + best / fb, a, b_frames[best]


_SSIM_RE = re.compile(r"SSIM Y:\S+ \(\S+\) U:\S+ \(\S+\) V:\S+ \(\S+\) All:([\d.]+)")
_PSNR_RE = re.compile(r"PSNR y:\S+ u:\S+ v:\S+ average:(inf|[\d.]+)")


def ssim_psnr(va: VideoFile, vb: VideoFile, ta: float, tb: float,
              w: int, h: int) -> tuple[float, float] | None:
    """SSIM / PSNR over a short aligned stretch, both sides at WxH."""
    fps = _fps_of(va)
    chain_a = (f"{_crop_filter(va)}scale={w}:{h}:flags=bicubic,setsar=1,"
               f"fps={fps},format=yuv420p")
    chain_b = (f"{_crop_filter(vb)}scale={w}:{h}:flags=bicubic,setsar=1,"
               f"fps={fps},format=yuv420p")
    graph = (f"[0:v:0]{chain_a},split[a1][a2];[1:v:0]{chain_b},split[b1][b2];"
             "[a1][b1]ssim[s];[a2][b2]psnr[p]")
    cmd = ["ffmpeg", "-v", "info", "-nostdin", "-an", "-sn",
           "-ss", f"{ta:.3f}", "-t", "1.2", "-i", str(va.path),
           "-ss", f"{tb:.3f}", "-t", "1.2", "-i", str(vb.path),
           "-filter_complex", graph,
           "-map", "[s]", "-f", "null", "-", "-map", "[p]", "-f", "null", "-"]
    try:
        res = run_tool(cmd, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return None
    ms, mp = _SSIM_RE.search(res.stderr), _PSNR_RE.search(res.stderr)
    if not ms or not mp:
        return None
    psnr = 100.0 if mp.group(1) == "inf" else float(mp.group(1))
    return float(ms.group(1)), psnr


@dataclass
class CheckpointStats:
    ssim: list[float] = field(default_factory=list)
    psnr: list[float] = field(default_factory=list)
    hf_a: list[float] = field(default_factory=list)
    hf_b: list[float] = field(default_factory=list)


def run_checkpoints(va: VideoFile, vb: VideoFile, a: ThumbSeq, al: Alignment,
                    count: int = CHECKPOINTS) -> CheckpointStats:
    stats = CheckpointStats()
    good = [i for i in range(2, len(al.state) - 2)
            if al.state[i] == "match" and al.offsets[i] is not None
            and a.informative(i)]
    if not good:
        return stats
    step = len(good) / count
    picks = good if len(good) <= count else [good[int(k * step)] for k in range(count)]
    aw, ah = _active_size(va)
    w = min(480, aw - aw % 2)
    h = max(2, int(round(w * ah / aw)) // 2 * 2)
    for i in picks:
        ta, tb = float(i), float(i + al.offsets[i])
        ref = refine_checkpoint(va, vb, ta, tb, w, h)
        if ref is None:
            continue
        tb_exact, ga, gb = ref
        res = ssim_psnr(va, vb, ta, tb_exact, w, h)
        if res is None:
            continue
        stats.ssim.append(res[0])
        stats.psnr.append(res[1])
        stats.hf_a.append(_hf_energy(ga, w, h))
        stats.hf_b.append(_hf_energy(gb, w, h))
    return stats


# ===========================================================================
# Payload hashes and streams
# ===========================================================================

_HASH_MEMO: dict = {}


def _memo_key(path: Path, kind: str):
    try:
        st = path.stat()
        return (kind, str(path), st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def stream_hashes(vf: VideoFile) -> dict[str, str] | None:
    """MD5 of every stream's compressed payload, in one pass (no decoding).

    Memoized per file: a file taking part in several pairs is read once.
    """
    key = _memo_key(vf.path, "streams")
    if key is not None and key in _HASH_MEMO:
        return _HASH_MEMO[key]
    result = _stream_hashes(vf)
    if key is not None and result is not None:
        _HASH_MEMO[key] = result
    return result


def _stream_hashes(vf: VideoFile) -> dict[str, str] | None:
    specs = ["v:0"] + [f"a:{i}" for i in range(len(vf.audio_tracks))] \
        + [f"s:{i}" for i in range(len(vf.subtitle_tracks))]
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(vf.path)]
    for spec in specs:
        cmd += ["-map", f"0:{spec}", "-c", "copy", "-f", "md5", "-"]
    try:
        res = run_tool(cmd, timeout=max(600, vf.duration * 2))
    except (subprocess.TimeoutExpired, OSError):
        return None
    hashes = re.findall(r"MD5=([0-9a-f]{32})", res.stdout)
    if len(hashes) != len(specs):
        return None
    return dict(zip(specs, hashes))


def file_sha256(path: Path) -> str | None:
    key = _memo_key(path, "sha256")
    if key is not None and key in _HASH_MEMO:
        return _HASH_MEMO[key]
    value = _file_sha256(path)
    if key is not None and value is not None:
        _HASH_MEMO[key] = value
    return value


def _file_sha256(path: Path) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def compare_streams(va: VideoFile, vb: VideoFile, ha: dict | None,
                    hb: dict | None) -> tuple[list[str], list[str], list[str]]:
    """(identical, changed, unique) streams.

    identical / changed: by payload hash.  unique: tracks one file has and
    the other lacks, judged by kind and language (a re-encoded audio track
    of the same language is "changed", not unique).
    """
    identical, changed, unique = [], [], []
    if ha is None or hb is None:
        return identical, changed, unique
    if ha.get("v:0") == hb.get("v:0"):
        identical.append("video")
    else:
        changed.append("video")
    from collections import Counter
    from languages import is_unknown_language, normalize_language

    def lang_of(t):
        return "?" if is_unknown_language(t.language) else normalize_language(t.language)

    for kind, label in (("a", "audio"), ("s", "subtitle")):
        tracks_a = va.audio_tracks if kind == "a" else va.subtitle_tracks
        tracks_b = vb.audio_tracks if kind == "a" else vb.subtitle_tracks
        a_h = {k: v for k, v in ha.items() if k.startswith(kind + ":")}
        b_hashes = {v for k, v in hb.items() if k.startswith(kind + ":")}
        for k, hsh in a_h.items():
            idx = int(k.split(":")[1])
            lang = lang_of(tracks_a[idx])
            (identical if hsh in b_hashes else changed).append(f"{label} #{idx + 1} [{lang}]")
        ca = Counter(lang_of(t) for t in tracks_a)
        cb = Counter(lang_of(t) for t in tracks_b)
        for lang in sorted(set(ca) | set(cb)):
            if ca[lang] > cb[lang]:
                unique.append(f"A has {ca[lang] - cb[lang]} more {label} track(s) [{lang}]")
            elif cb[lang] > ca[lang]:
                unique.append(f"B has {cb[lang] - ca[lang]} more {label} track(s) [{lang}]")
    return identical, changed, unique


# ===========================================================================
# Redundancy (issue 14)
# ===========================================================================

def assess_redundancy(va: VideoFile, vb: VideoFile, comp: SequenceComparison,
                      stats: CheckpointStats, unique_tracks: list[str]) -> Redundancy:
    """Does the larger file add anything over the smaller one?"""
    red = Redundancy(unique_tracks=unique_tracks)
    if comp.relation not in ("same_cut_reencode", "decoded_equivalent", "payload_identical"):
        return red
    big, small = (va, vb) if va.file_size >= vb.file_size else (vb, va)
    if small.file_size <= 0:
        return red
    red.size_ratio = big.file_size / small.file_size
    if stats.hf_a and stats.hf_b:
        hb_, hs_ = (stats.hf_a, stats.hf_b) if big is va else (stats.hf_b, stats.hf_a)
        if statistics.median(hs_) > 0:
            red.detail_ratio = statistics.median(hb_) / statistics.median(hs_)
    from scoring import select_best_audio
    ab, as_ = select_best_audio(big), select_best_audio(small)
    if ab and as_ and any(c.startswith("audio") for c in comp.changed_streams):
        from scoring import _audio_track_rank
        if _audio_track_rank(as_)[:4] > _audio_track_rank(ab)[:4]:
            red.notes.append("the smaller file has the better audio track")
            red.verdict = "unique_content"
            return red
    geom_big = (big.video.active_width, big.video.active_height)
    geom_small = (small.video.active_width, small.video.active_height)
    if geom_big != geom_small:
        red.notes.append(f"active picture {geom_big[0]}x{geom_big[1]} vs "
                         f"{geom_small[0]}x{geom_small[1]}")
    if unique_tracks:
        red.notes.append("has tracks the other lacks: " + ", ".join(unique_tracks))
        red.verdict = "unique_content"
        return red
    if red.size_ratio < 1.25:
        red.verdict = "equivalent"
        return red
    if comp.color.material:
        red.notes.append("colour differs (" + comp.color.describe() + ")")
        red.verdict = ""
        return red
    bv, sv = big.video, small.video
    if (bv.active_pixels >= sv.active_pixels * 1.15 or bv.bit_depth > sv.bit_depth
            or (bv.hdr_type and bv.hdr_type != sv.hdr_type)):
        # the comparison frames are downscaled, so these cannot show up as detail
        red.verdict = "better_larger"
        red.notes.append("larger file has a higher resolution, bit depth or HDR")
        return red
    dr = red.detail_ratio
    if dr is None:
        red.notes.append("detail could not be measured")
        return red
    if dr > 1.12:
        red.verdict = "better_larger"
        red.notes.append(f"larger file keeps {dr:.2f}x the fine detail")
    elif dr < 0.8:
        red.verdict = "degraded_smaller"
        red.notes.append(f"smaller file lost detail (larger has {dr:.2f}x)")
    else:
        red.verdict = "redundant_larger"
        red.redundant_path = big.path
        red.notes.append(f"{red.size_ratio:.1f}x the size, no visible detail advantage "
                         f"(detail x{dr:.2f})")
    return red


# ===========================================================================
# Orchestration
# ===========================================================================

def _classify(comp: SequenceComparison, al: Alignment | None, stats: CheckpointStats):
    ev = comp.evidence
    if al is None:
        comp.relation = "different_content"
        comp.confidence = "medium"
        ev.append("no stretch of the two files shows the same picture at any offset")
        return
    comp.coverage = al.coverage
    ev.append(f"{al.coverage * 100:.0f}% of the informative seconds match at "
              f"offset {comp.offset:+d}s")
    shifts = [d for d in comp.divergences if d.kind in ("extra_in_a", "extra_in_b")]
    blocks = [d for d in comp.divergences if d.kind == "replaced_block"]
    glitches = [d for d in comp.divergences if d.kind == "glitch"]
    if stats.ssim:
        comp.ssim_mean = statistics.mean(stats.ssim)
        comp.ssim_min = min(stats.ssim)
        comp.psnr_mean = statistics.mean(stats.psnr)
        ev.append(f"{len(stats.ssim)} checkpoints: SSIM mean {comp.ssim_mean:.4f} "
                  f"(min {comp.ssim_min:.4f}), PSNR mean {comp.psnr_mean:.1f} dB")
    if al.coverage < 0.3:
        comp.relation = "different_content"
        comp.confidence = "high" if al.coverage < 0.1 else "medium"
        return
    if shifts or blocks or al.coverage < MIN_COVERAGE_SAME_CUT:
        comp.relation = "different_cut"
        comp.confidence = "medium"
        return
    if comp.ssim_mean is None:
        comp.relation = "unknown"
        return
    comp.confidence = "high" if len(stats.ssim) >= 6 else "medium"
    if comp.ssim_mean >= SSIM_EQUIVALENT and comp.ssim_min >= 0.999:
        comp.relation = "decoded_equivalent"
    elif comp.ssim_mean >= SSIM_REENCODE:
        comp.relation = "same_cut_reencode"
    elif comp.ssim_mean >= SSIM_PROCESSED:
        comp.relation = "same_cut_processed"
        ev.append("same cut but heavily processed (filters, rescale or strong re-encode)")
    else:
        comp.relation = "different_content"
    if glitches:
        ev.append(f"{len(glitches)} short mismatching stretch(es): possible damage")


def _review_reasons(comp: SequenceComparison) -> list[str]:
    reasons = []
    if comp.relation not in SAFE_RELATIONS:
        reasons.append(f"relation '{comp.relation}' is not a proven duplicate")
    if comp.relation in ("same_cut_reencode", "decoded_equivalent") and comp.checkpoints < 5:
        reasons.append("too few measured checkpoints")
    if comp.color.material:
        reasons.append("different master / colour grade: " + comp.color.describe())
    if comp.color.metadata_issues and comp.relation not in ("byte_identical", "payload_identical"):
        reasons.append("colour metadata ambiguity: " + "; ".join(comp.color.metadata_issues))
    if comp.overlay_note:
        reasons.append(comp.overlay_note)
    for d in comp.divergences:
        if d.kind in ("extra_in_a", "extra_in_b", "replaced_block", "glitch"):
            reasons.append(f"{d.kind.replace('_', ' ')} at {d.a_start:.0f}s: {d.note}")
    for u in comp.redundancy.unique_tracks:
        reasons.append("tracks would be lost: " + u)
    if comp.confidence == "low":
        reasons.append("low confidence")
    return reasons


def compare_files(a: VideoFile, b: VideoFile, use_cache: bool = True,
                  checkpoints: int = CHECKPOINTS) -> SequenceComparison:
    """Full synchronized comparison of two probed files."""
    comp = SequenceComparison(a.path, b.path)
    comp.frame_counts_equal = (a.video.nb_frames == b.video.nb_frames
                               if a.video.nb_frames and b.video.nb_frames else None)

    # 1. Byte / payload identity
    if a.file_size and a.file_size == b.file_size:
        ha_, hb_ = file_sha256(a.path), file_sha256(b.path)
        if ha_ and ha_ == hb_:
            comp.relation, comp.confidence = "byte_identical", "high"
            comp.evidence.append("files are byte-identical (SHA-256)")
            comp.review_required = False
            return comp
    ha, hb = stream_hashes(a), stream_hashes(b)
    identical, changed, unique = compare_streams(a, b, ha, hb)
    comp.identical_streams, comp.changed_streams = identical, changed
    if ha and hb and "video" in identical and not [c for c in changed if c.startswith("audio")]:
        comp.relation, comp.confidence = "payload_identical", "high"
        comp.evidence.append("video and audio payloads are identical (remux / container change only)")
        if changed:
            comp.evidence.append("differing streams: " + ", ".join(changed))
        comp.redundancy = assess_redundancy(a, b, comp, CheckpointStats(), unique)
        comp.review_reasons = _review_reasons(comp)
        comp.review_required = bool(comp.review_reasons)
        return comp

    # 2. Decode and align
    sa, sb = extract_thumbs(a, use_cache), extract_thumbs(b, use_cache)
    if sa is None or sb is None:
        comp.evidence.append("a file could not be decoded for comparison")
        comp.review_reasons = ["comparison failed"]
        return comp
    al = align(sa, sb)
    stats = CheckpointStats()
    if al is not None:
        comp.offset = next((o for o in al.offsets if o is not None), None)
        comp.divergences = find_divergences(al, len(sb))
        owner, note = detect_overlay(sa, sb, al, a.path, b.path)
        comp.overlay_owner, comp.overlay_note = owner, note
        if note:
            comp.divergences.append(Divergence("overlay", 0, len(sa), note))
        comp.color = compare_color(sa, sb, al, a, b)
        stats = run_checkpoints(a, b, sa, al, checkpoints)
        comp.checkpoints = len(stats.ssim)
    _classify(comp, al, stats)
    comp.redundancy = assess_redundancy(a, b, comp, stats, unique)
    comp.review_reasons = _review_reasons(comp)
    comp.review_required = bool(comp.review_reasons)
    return comp
