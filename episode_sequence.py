"""Episode-order anomalies across releases of the same series.

Same-numbered files are not necessarily the same episode: a release can
have swapped neighbours, a one-episode shift, a missing or duplicated
episode, or a special that moved the numbering.  Durations only *suspect*
such problems; every suspicion is checked against the pictures (the
matcher decodes both files and aligns them) before anything is reported
as a content mapping, and nothing here renames or deletes files.

Findings per episode of the other release:
  ok         same-numbered base episode has the same picture
  shifted    the picture belongs to another base episode (remap candidate)
  duplicate  two files carry the same picture
  partial    same episode but cut short / different cut
  unmatched  matches no base episode (special, extra, unrelated)
  suspected  duration looks wrong but the content check was not run
Gaps in the base release are reported as `missing_in_other`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from models import VideoFile

# Same-number duration difference that is plainly a different edit / episode
LARGE_DELTA = 0.03
# Smaller than this is container / trim noise
NORMAL_DELTA = 0.01
# A neighbour must fit this closely, and be this many times closer, to
# make a same-length-looking file suspicious
NEIGHBOUR_FIT = 0.003
NEIGHBOUR_RATIO = 4.0
# Neighbouring episodes tried when verifying
NEIGHBOUR_REACH = 3
# Content match thresholds (share of informative seconds that match)
STRONG_MATCH = 0.8
PARTIAL_MATCH = 0.4
# Equal durations (s) that make two files look like copies of one episode
DUPLICATE_DURATION_TOL = 1.0
PERSISTENT_RUN = 3

Key = tuple


@dataclass
class ContentMatch:
    coverage: float = 0.0
    offset: int | None = None
    note: str = ""


Matcher = Callable[[VideoFile, VideoFile], ContentMatch]


@dataclass
class Finding:
    kind: str
    other: VideoFile | None = None
    base: VideoFile | None = None           # same-numbered base episode
    other_key: Key | None = None
    mapped_key: Key | None = None           # base episode the picture belongs to
    confidence: str = "medium"
    evidence: list[str] = field(default_factory=list)

    def describe(self) -> str:
        who = self.other.path.name if self.other else "-"
        k = _fmt(self.other_key)
        if self.kind == "shifted":
            return (f"{who} is labelled {k} but shows episode {_fmt(self.mapped_key)} "
                    f"of the base release — remap candidate")
        if self.kind == "duplicate":
            return f"{who} ({k}) shows the same picture as another file (episode {_fmt(self.mapped_key)})"
        if self.kind == "partial":
            return f"{who} ({k}) is a shortened or different cut of the base episode"
        if self.kind == "unmatched":
            return f"{who} ({k}) matches no episode of the base release (special / extra / unrelated)"
        if self.kind == "unverified":
            return f"{who} ({k}) could not be decoded for the content check"
        if self.kind == "suspected":
            return f"{who} ({k}) has an odd duration (not verified by content)"
        if self.kind == "missing_in_other":
            return f"episode {k} exists in the base release but not in this one"
        return f"{who} ({k}): {self.kind}"


def _fmt(key: Key | None) -> str:
    if key is None:
        return "?"
    season, ep = key
    if season is None:
        return f"#{ep}"
    return f"S{season:02d}E{ep:02d}"


@dataclass
class SequenceReport:
    title: str
    base_release: int
    other_release: int
    findings: list[Finding] = field(default_factory=list)
    persistent: list[str] = field(default_factory=list)   # "from #101 every episode is shifted by +1"
    verified: bool = False

    BLOCKING = ("shifted", "duplicate", "partial", "unmatched", "suspected", "unverified")

    def anomalies(self) -> list[Finding]:
        return [f for f in self.findings if f.kind in self.BLOCKING]

    def blocked(self) -> dict[Key, str]:
        """Episode keys that must not get deletion advice, with reasons."""
        out: dict[Key, str] = {}
        for f in self.anomalies():
            reason = f"episode order is ambiguous: {f.describe()}"
            for key in (f.other_key, f.mapped_key):
                if key is not None:
                    out.setdefault(key, reason)
        return out


# ===========================================================================
# Duration screening
# ===========================================================================

def _rel_delta(a: float, b: float) -> float:
    m = max(a, b)
    return abs(a - b) / m if m > 0 else 0.0


def _form(key: Key) -> bool:
    """True for season-less absolute keys (None, n)."""
    return key[0] is None


def _ordered(keys) -> list[Key]:
    """Season keys, then absolute keys, each in episode order (the two
    forms are never treated as neighbours of each other)."""
    return sorted(keys, key=lambda k: (_form(k), k[0] if k[0] is not None else 0,
                                       k[1] if k[1] is not None else 0))


def screen(base: dict[Key, VideoFile], other: dict[Key, VideoFile]) -> dict[Key, str]:
    """Other-release keys whose duration pattern is suspicious, with reasons.

    Durations are only candidate evidence: a flag means "check the pictures".
    """
    suspects: dict[Key, str] = {}
    base_keys = _ordered(base)
    pos = {k: i for i, k in enumerate(base_keys)}
    for key, o in other.items():
        if o.duration <= 0:
            suspects[key] = "unknown duration"
            continue
        b = base.get(key)
        if b is None or b.duration <= 0:
            suspects[key] = "no same-numbered episode in the base release"
            continue
        delta = _rel_delta(o.duration, b.duration)
        if delta > LARGE_DELTA:
            suspects[key] = f"duration differs by {delta * 100:.1f}% from the same-numbered episode"
            continue
        if delta >= NORMAL_DELTA:
            i = pos[key]
            for j in range(max(0, i - NEIGHBOUR_REACH), min(len(base_keys), i + NEIGHBOUR_REACH + 1)):
                n = base[base_keys[j]]
                if j == i or n.duration <= 0 or _form(base_keys[j]) != _form(key):
                    continue
                dn = _rel_delta(o.duration, n.duration)
                if dn <= NEIGHBOUR_FIT and dn * NEIGHBOUR_RATIO <= delta:
                    suspects[key] = (f"duration fits {_fmt(base_keys[j])} "
                                     f"({dn * 100:.2f}%) better than its own number "
                                     f"({delta * 100:.1f}%)")
                    break
    # Several files with one duration while their counterparts differ
    by_duration: dict[int, list[Key]] = defaultdict(list)
    for key, o in other.items():
        if o.duration > 0:
            by_duration[round(o.duration / DUPLICATE_DURATION_TOL)].append(key)
    for keys in by_duration.values():
        if len(keys) < 2:
            continue
        counterparts = [base[k].duration for k in keys if k in base]
        if len(counterparts) >= 2 and max(counterparts) - min(counterparts) > 2 * DUPLICATE_DURATION_TOL:
            for k in keys:
                suspects.setdefault(k, "same duration as another file whose counterpart differs")
    return suspects


# ===========================================================================
# Verification by content
# ===========================================================================

def _candidates(key: Key, base: dict[Key, VideoFile], base_keys: list[Key],
                o: VideoFile) -> list[Key]:
    """Base episodes worth testing: own number first, then close neighbours
    (nearest duration first)."""
    pos = {k: i for i, k in enumerate(base_keys)}
    i = pos[key] if key in pos else _ordered(base_keys + [key]).index(key)
    near = [base_keys[j] for j in range(max(0, i - NEIGHBOUR_REACH),
                                        min(len(base_keys), i + NEIGHBOUR_REACH + 1))
            if base_keys[j] != key and _form(base_keys[j]) == _form(key)]
    near.sort(key=lambda k: _rel_delta(base[k].duration, o.duration))
    return ([key] if key in base else []) + near


def analyze_pair(title: str, base: dict[Key, VideoFile], other: dict[Key, VideoFile],
                 base_release: int, other_release: int,
                 matcher: Matcher | None = None, verify_all: bool = False) -> SequenceReport:
    """Compare the episode sequences of two releases."""
    report = SequenceReport(title, base_release, other_release, verified=matcher is not None)
    suspects = screen(base, other)
    base_keys = _ordered(base)
    claimed: dict[Key, list[Key]] | None = defaultdict(list)   # base key -> other keys showing it

    order = _ordered(other)
    if matcher is None:
        for key in order:
            if key in suspects:
                report.findings.append(Finding("suspected", other[key], base.get(key), key,
                                               None, "low", [suspects[key]]))
        claimed = None
    else:
        # Verify the screened files, then keep walking outwards from every
        # anomaly: a shift or a special spreads over neighbours whose own
        # durations happen to look fine.
        pending = [k for k in order if k in suspects or verify_all]
        reasons = {k: suspects.get(k, "verification requested") for k in pending}
        checked: dict[Key, Finding] = {}
        while pending:
            key = pending.pop(0)
            if key in checked:
                continue
            finding = _verify(key, other[key], base, base_keys, matcher, reasons[key])
            checked[key] = finding
            if finding.kind != "ok":
                i = order.index(key)
                for j in (i - 1, i + 1):
                    if 0 <= j < len(order) and order[j] not in checked and order[j] not in pending:
                        pending.append(order[j])
                        reasons[order[j]] = f"neighbour of {_fmt(key)}, which is out of place"
        for key in order:
            if key in checked:
                finding = checked[key]
                report.findings.append(finding)
                if finding.kind == "ok":
                    claimed[key].append(key)
                elif finding.kind == "shifted":      # a partial cut claims nothing
                    claimed[finding.mapped_key].append(key)
            elif key in base:        # duration-consistent: assumed to be that episode
                claimed[key].append(key)

    if matcher is not None:
        # Two files with one picture
        for mapped, keys in claimed.items():
            if len(keys) > 1:
                for f in report.findings:
                    if f.other_key in keys and f.kind in ("ok", "shifted"):
                        f.kind = "duplicate"
                        f.mapped_key = mapped
                        f.evidence.append(f"{len(keys)} files show {_fmt(mapped)}")
                # un-screened members of a duplicate pair
                for k in keys:
                    if not any(f.other_key == k for f in report.findings):
                        report.findings.append(Finding(
                            "duplicate", other[k], base.get(k), k, mapped, "medium",
                            [f"{len(keys)} files show {_fmt(mapped)}"]))

    # Gaps: base episodes nobody shows
    shown = set(claimed) if claimed is not None else set(other)
    for key in base_keys:
        if key not in shown:
            report.findings.append(Finding("missing_in_other", None, base[key], None, key, "medium",
                                           ["no file shows this episode"]))
    report.persistent = _persistent_runs(report, base_keys)
    return report


def _verify(key: Key, o: VideoFile, base: dict[Key, VideoFile], base_keys: list[Key],
            matcher: Matcher, why: str) -> Finding:
    best: tuple[float, Key, ContentMatch] | None = None
    failures = 0
    tried = 0
    for cand in _candidates(key, base, base_keys, o):
        tried += 1
        m = matcher(base[cand], o)
        if m is None:
            failures += 1
            continue
        if best is None or m.coverage > best[0]:
            best = (m.coverage, cand, m)
        if m.coverage >= STRONG_MATCH:
            break
    ev = [why]
    if best is None and tried and failures == tried:
        ev.append("decoding failed; nothing was checked")
        return Finding("unverified", o, base.get(key), key, None, "low", ev)
    if best is None or best[0] < PARTIAL_MATCH:
        ev.append("no candidate episode shows the same picture")
        return Finding("unmatched", o, base.get(key), key, None, "medium", ev)
    coverage, cand, m = best
    ev.append(f"{coverage * 100:.0f}% of the seconds match {_fmt(cand)}"
              + (f" (offset {m.offset:+d}s)" if m.offset is not None else ""))
    if m.note:
        ev.append(m.note)
    conf = "high" if coverage >= 0.9 else "medium"
    if coverage >= STRONG_MATCH:
        if cand == key:
            return Finding("ok", o, base.get(key), key, cand, conf, ev)
        return Finding("shifted", o, base.get(key), key, cand, conf, ev)
    return Finding("partial", o, base.get(cand), key, cand, "medium", ev)


def _persistent_runs(report: SequenceReport, base_keys: list[Key]) -> list[str]:
    """Runs of consecutive episodes all shifted by the same number of positions."""
    pos = {k: i for i, k in enumerate(base_keys)}
    shifted = sorted(((pos[f.other_key], pos[f.mapped_key]) for f in report.findings
                      if f.kind == "shifted" and f.other_key in pos and f.mapped_key in pos))
    out, run = [], []
    for i, j in shifted:
        d = j - i
        if run and (i != run[-1][0] + 1 or d != run[-1][2]):
            if len(run) >= PERSISTENT_RUN:
                out.append(_run_text(base_keys, run))
            run = []
        run.append((i, j, d))
    if len(run) >= PERSISTENT_RUN:
        out.append(_run_text(base_keys, run))
    return out


def _run_text(base_keys: list[Key], run: list[tuple[int, int, int]]) -> str:
    d = run[0][2]
    return (f"from {_fmt(base_keys[run[0][0]])} to {_fmt(base_keys[run[-1][0]])} every file "
            f"shows the episode {abs(d)} position(s) {'later' if d > 0 else 'earlier'} "
            f"than its number")


# ===========================================================================
# Whole workset
# ===========================================================================

def assign_releases(files: list[VideoFile], roots: list[Path]) -> None:
    """Tag each file with the index of the command-line path it came from."""
    resolved = [Path(r).resolve() for r in roots]
    for vf in files:
        vf.release = -1
        p = vf.path.resolve()
        for i, root in enumerate(resolved):
            if p == root or root in p.parents:
                vf.release = i
                break


def analyze_groups(groups: dict, matcher: Matcher | None = None,
                   verify_all: bool = False) -> list[SequenceReport]:
    """Sequence reports for every title that appears in 2+ releases."""
    reports: list[SequenceReport] = []
    for title, episodes in groups.items():
        per_release: dict[int, dict[Key, VideoFile]] = defaultdict(dict)
        for key, files in episodes.items():
            if key == (None, None):
                continue
            for vf in files:
                if vf.release >= 0:
                    per_release[vf.release].setdefault(key, vf)
        if len(per_release) < 2:
            continue
        base_id = max(per_release, key=lambda r: len(per_release[r]))
        for rid in sorted(per_release):
            if rid == base_id:
                continue
            reports.append(analyze_pair(title, per_release[base_id], per_release[rid],
                                        base_id, rid, matcher, verify_all))
    return reports


def blocked_keys(reports: list[SequenceReport]) -> dict[tuple[str, Key], str]:
    out: dict[tuple[str, Key], str] = {}
    for r in reports:
        for key, reason in r.blocked().items():
            out.setdefault((r.title, key), reason)
    return out


def format_reports(reports: list[SequenceReport]) -> str:
    lines = []
    for r in reports:
        anomalies = [f for f in r.findings if f.kind in r.BLOCKING or f.kind == "missing_in_other"]
        if not anomalies and not r.persistent:
            continue
        mode = "verified by content" if r.verified else "duration screening only"
        lines.append(f"  {r.title}: release #{r.other_release + 1} vs #{r.base_release + 1} ({mode})")
        for text in r.persistent:
            lines.append(f"    ~ {text}")
        for f in anomalies:
            lines.append(f"    ! {f.describe()}")
            for e in f.evidence:
                lines.append(f"        - {e}")
    if not lines:
        return ""
    head = ["\n" + "=" * 70, " EPISODE SEQUENCE CHECK (no files are renamed or deleted)", "=" * 70]
    return "\n".join(head + lines)
