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
    ]
    for pattern in cut_patterns:
        m = re.search(pattern, name)
        if m:
            name = name[:m.start()]
            break

    # Remove separators and extra whitespace
    name = re.sub(r'[._\-\[\](){}]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip().lower()

    # Remove typical quality tags from start/end
    noise = [
        r'\b\d{3,4}p\b', r'\bbluray\b', r'\bbdrip\b', r'\bwebrip\b',
        r'\bweb-?dl\b', r'\bhdtv\b', r'\bx26[45]\b', r'\bh\.?26[45]\b',
        r'\baac\b', r'\bflac\b', r'\bdts\b', r'\b10bit\b',
        r'\bhdr\b', r'\bremux\b',
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

    return dict(groups)
