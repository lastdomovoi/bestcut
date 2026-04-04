"""Filename parsing and TV series episode grouping."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from models import VideoFile

# Patterns for extracting season/episode (in priority order)
EPISODE_PATTERNS = [
    # S01E01, S1E1, s01e01
    re.compile(r'[Ss](\d{1,3})[Ee](\d{1,3})'),
    # 1x01, 01x01
    re.compile(r'(\d{1,2})[xX](\d{1,3})'),
    # Season 1 Episode 01
    re.compile(r'[Ss]eason\s*(\d{1,3})\s*[Ee]pisode\s*(\d{1,3})', re.IGNORECASE),
    # E01, Ep01, Ep.01 (no season)
    re.compile(r'[Ee](?:p\.?\s*)?(\d{1,3})(?!\d)'),
    # " - 01", " - 001" (anime style)
    re.compile(r'\s-\s(\d{2,3})(?:\s|\.|\[|$)'),
    # Trailing part number: name-1, name_1, name.1 (before year/quality tags or end)
    # Matches: "Myunhgauzen-1.1979", "ylnian_1", "Movie_2", "Film-3"
    re.compile(r'[-_](\d{1,2})(?:\.|$)'),
]

# Video file extensions
VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv",
    ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".webm",
}


def parse_episode(filename: str) -> tuple[int | None, int | None]:
    """Extract (season, episode) from filename.

    Returns (None, None) if not found.
    """
    name = Path(filename).stem

    for i, pattern in enumerate(EPISODE_PATTERNS):
        m = pattern.search(name)
        if m:
            groups = m.groups()
            if len(groups) >= 2:
                return int(groups[0]), int(groups[1])
            # Patterns without season (E01, anime)
            return None, int(groups[0])

    return None, None


def normalize_title(filename: str) -> str:
    """Extract and normalize series title from filename."""
    name = Path(filename).stem

    # Cut everything after season/episode marker
    cut_patterns = [
        r'[Ss]\d{1,3}[Ee]',
        r'\d{1,2}[xX]\d{1,3}',
        r'[Ss]eason\s*\d',
        r'\s-\s\d{2,3}(?:\s|\.|\[|$)',
        r'[Ee](?:p\.?\s*)?\d{1,3}(?!\d)',
        r'[-_]\d{1,2}(?:\.|$)',  # trailing part number: name-1, name_1
    ]
    for pattern in cut_patterns:
        m = re.search(pattern, name)
        if m:
            name = name[:m.start()]
            break

    # Remove separators and extra whitespace
    name = re.sub(r'[._\-\[\](){}]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip().lower()

    # Remove typical quality/release tags
    noise = [
        r'\b(19|20)\d{2}\b',  # year (1900-2099)
        r'\b\d{3,4}[pi]\b',   # 1080p, 1080i, 720p, etc.
        r'\bbluray\b', r'\bbdrip\b', r'\bwebrip\b',
        r'\bweb-?dl\b', r'\bhdtv\b', r'\bx26[45]\b', r'\bh\.?26[45]\b',
        r'\baac\b', r'\bflac\b', r'\bdts\b', r'\b10bit\b',
        r'\bhdr\b', r'\bremux\b', r'\bdvdrip\b',
    ]
    for n in noise:
        name = re.sub(n, '', name, flags=re.IGNORECASE)

    return re.sub(r'\s+', ' ', name).strip()


def scan_directory(path: Path) -> list[Path]:
    """Recursively find all video files in a directory."""
    path = Path(path)
    if path.is_file():
        return [path] if path.suffix.lower() in VIDEO_EXTENSIONS else []

    files = []
    for f in sorted(path.rglob("*")):
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
            files.append(f)
    return files


def group_episodes(files: list[VideoFile]) -> dict[str, dict[tuple[int | None, int | None], list[VideoFile]]]:
    """Group video files by series and episode.

    Returns: {title: {(season, episode): [VideoFile, ...], ...}, ...}
    Files without season/episode are grouped under key (None, None).

    When files come from different directories and have the same episode
    number but different titles (e.g. transliteration vs original), they
    are merged into a single group using the first encountered title.
    """
    groups: dict[str, dict[tuple[int | None, int | None], list[VideoFile]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for vf in files:
        fname = vf.path.name
        season, episode = parse_episode(fname)
        vf.season = season
        vf.episode = episode

        if episode is not None:
            title = normalize_title(fname)
            vf.series_title = title
        else:
            # For standalone movies — use filename
            title = normalize_title(fname) or vf.path.stem.lower()
            vf.series_title = title

        groups[title][(season, episode)].append(vf)

    # Merge groups that have different titles but same episode keys
    # (common when comparing releases with transliterated vs original names)
    return _merge_by_episode(dict(groups))


def _merge_by_episode(
    groups: dict[str, dict[tuple[int | None, int | None], list[VideoFile]]],
) -> dict[str, dict[tuple[int | None, int | None], list[VideoFile]]]:
    """Merge groups with different titles but identical episode sets.

    If two groups have the exact same set of (season, episode) keys and
    all episodes are singletons (one file per episode per group), they
    likely represent different releases of the same content.
    """
    titles = list(groups.keys())
    if len(titles) < 2:
        return groups

    merged: dict[str, dict[tuple[int | None, int | None], list[VideoFile]]] = {}
    used: set[str] = set()

    for i, t1 in enumerate(titles):
        if t1 in used:
            continue

        ep_keys_1 = set(groups[t1].keys())
        # Skip groups without episode numbers (standalone movies)
        if ep_keys_1 == {(None, None)}:
            merged[t1] = groups[t1]
            used.add(t1)
            continue

        # Look for a matching group with same episode keys
        best_match = None
        for j in range(i + 1, len(titles)):
            t2 = titles[j]
            if t2 in used:
                continue
            ep_keys_2 = set(groups[t2].keys())
            if ep_keys_2 == {(None, None)}:
                continue
            # Same episode key set — likely same content
            if ep_keys_1 == ep_keys_2:
                best_match = t2
                break
            # Overlapping episode keys — also merge
            overlap = ep_keys_1 & ep_keys_2
            if overlap and len(overlap) >= min(len(ep_keys_1), len(ep_keys_2)) * 0.5:
                best_match = t2
                break

        if best_match:
            # Merge into t1
            combined = defaultdict(list)
            for ep_key, vf_list in groups[t1].items():
                combined[ep_key].extend(vf_list)
            for ep_key, vf_list in groups[best_match].items():
                combined[ep_key].extend(vf_list)
            merged[t1] = dict(combined)
            used.add(t1)
            used.add(best_match)
        else:
            merged[t1] = groups[t1]
            used.add(t1)

    return merged
