"""Fast read-only discovery of series / release directories.

Walks directories only (never opens media files, never runs ffprobe),
stops descending into a directory as soon as it matches, so a series
folder with nested season folders is reported once, as a unit.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MAX_DEPTH = 4


def normalize_name(text: str) -> str:
    """Case-folded, NFKC-normalized words (ё = е, punctuation = space)."""
    text = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return re.sub(r"[\W_]+", " ", text).strip()


def _matches(dir_name: str, needles: list[str]) -> bool:
    """A needle matches as whole words of the directory name."""
    padded = f" {normalize_name(dir_name)} "
    return any(f" {n} " in padded for n in needles)


@dataclass
class DiscoveryResult:
    matches: list[Path] = field(default_factory=list)
    missing_roots: list[Path] = field(default_factory=list)
    unreadable: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def searched_ok(self) -> bool:
        return not self.missing_roots and not self.unreadable

    def to_json(self) -> dict:
        return {
            "matches": [str(p) for p in self.matches],
            "missing_roots": [str(p) for p in self.missing_roots],
            "unreadable": [{"path": str(p), "error": e} for p, e in self.unreadable],
        }


def find_series(name: str, aliases: list[str], roots: list[Path],
                max_depth: int = DEFAULT_MAX_DEPTH) -> DiscoveryResult:
    """Directories whose name matches the series name or an alias."""
    needles = [n for n in (normalize_name(x) for x in [name, *aliases]) if n]
    result = DiscoveryResult()
    seen: set[str] = set()
    for root in roots:
        root = Path(root)
        if not root.exists():
            # try the other Unicode form (macOS stores NFD)
            alt = Path(unicodedata.normalize("NFC", str(root)))
            if alt.exists():
                root = alt
            else:
                result.missing_roots.append(root)
                continue
        if not root.is_dir():
            result.unreadable.append((root, "not a directory"))
            continue
        _walk(root, needles, 0, max_depth, result, seen)
    result.matches.sort(key=lambda p: normalize_name(str(p)))
    return result


def _walk(directory: Path, needles: list[str], depth: int, max_depth: int,
          result: DiscoveryResult, seen: set[str]) -> None:
    if depth >= max_depth:
        return
    subdirs = []
    try:
        with os.scandir(directory) as it:
            for e in it:
                try:
                    if e.is_dir():          # follows symlinks (common on NAS layouts)
                        subdirs.append(e)
                except OSError as err:
                    result.unreadable.append((Path(e.path), err.strerror or str(err)))
    except OSError as e:
        result.unreadable.append((directory, e.strerror or str(e)))
        return
    for entry in sorted(subdirs, key=lambda e: e.name):
        path = Path(entry.path)
        real = os.path.realpath(entry.path)
        if _matches(entry.name, needles):
            if real not in seen:
                seen.add(real)
                result.matches.append(path)
            continue          # a matched series is one unit: do not descend
        if real in seen:      # symlink loop / already visited
            continue
        seen.add(real)
        _walk(path, needles, depth + 1, max_depth, result, seen)
