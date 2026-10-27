"""Filename parsing and TV series episode grouping."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from models import VideoFile

# Patterns for extracting season/episode (in priority order)
EPISODE_PATTERNS = [
    # S01E01, S1E1, s01e01, S01.E01, S01_E01, S2_E05, S1 E1
    re.compile(r'[Ss](\d{1,3})[._\-\s]*[Ee](\d{1,3})'),
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
        r'[Ss]\d{1,3}[._\-\s]*[Ee]',
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
        r'\bbluray\b', r'\bbdrip\b', r'\bwebrip\b', r'\bbdremux\b',
        r'\bweb-?dl\b', r'\bhdtv\b', r'\bx26[45]\b', r'\bh\.?26[45]\b',
        r'\baac\b', r'\bflac\b', r'\bdts\b', r'\b10bit\b',
        r'\bhdr\b', r'\bremux\b', r'\bdvdrip\b', r'\bhdrip\b',
        r'\bсуб\b', r'\bсерия\b', r'\bсерии\b', r'\bчасть\b',
        r'\b\d+\s*серия\b', r'\b\d+\s*часть\b',
        r'\bили\b',  # "или" in extended titles
    ]
    for n in noise:
        name = re.sub(n, '', name, flags=re.IGNORECASE)

    # Remove punctuation leftovers
    name = re.sub(r'[,!?;:]+', ' ', name)
    # Remove trailing standalone numbers (episode/part leftovers)
    name = re.sub(r'\s+\d{1,2}\s*$', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def _normalize_path(path: Path) -> Path:
    """Normalize Unicode in path (macOS uses NFD, Python strings NFC)."""
    import unicodedata
    return Path(unicodedata.normalize("NFC", str(path)))


def scan_directory(path: Path) -> list[Path]:
    """Recursively find all video files in a directory."""
    path = Path(path)

    # macOS NFD vs NFC: if path doesn't exist, try NFC-normalized version
    if not path.exists():
        nfc = _normalize_path(path)
        if nfc.exists():
            path = nfc
        else:
            # Try finding via parent directory listing
            parent = path.parent
            if parent.exists():
                target = path.name
                import unicodedata
                target_nfc = unicodedata.normalize("NFC", target)
                for f in parent.iterdir():
                    f_nfc = unicodedata.normalize("NFC", f.name)
                    if f_nfc == target_nfc:
                        path = f
                        break

    if path.is_file():
        return [path] if path.suffix.lower() in VIDEO_EXTENSIONS else []

    files = []
    if path.is_dir():
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
        title_source = fname

        # If file itself has no episode info (e.g. BDMV/STREAM/00000.m2ts),
        # inspect parent directory names up the tree
        if episode is None:
            for parent in vf.path.parents:
                if parent.name in ("", "/", "Volumes", "Torrents", "video", "Projects"):
                    break
                p_season, p_episode = parse_episode(parent.name)
                if p_episode is not None:
                    season, episode = p_season, p_episode
                    title_source = parent.name
                    break

        vf.season = season
        vf.episode = episode

        if episode is not None:
            title = normalize_title(title_source)
            vf.series_title = title
        else:
            # For standalone movies — use filename
            title = normalize_title(fname) or vf.path.stem.lower()
            vf.series_title = title

        groups[title][(season, episode)].append(vf)

    # Merge groups that have different titles but same episode keys
    # (common when comparing releases with transliterated vs original names)
    return _merge_by_episode(dict(groups))


def _titles_similar(t1: str, t2: str) -> bool:
    """Check if two normalized titles refer to the same content.

    Uses prefix/substring matching and word overlap.
    """
    if not t1 or not t2:
        return False
    # Exact match
    if t1 == t2:
        return True
    # One is a prefix of the other (with at least 4 chars)
    shorter, longer = (t1, t2) if len(t1) <= len(t2) else (t2, t1)
    if len(shorter) >= 4 and longer.startswith(shorter):
        return True
    # Word overlap: if the shorter title's words are all in the longer
    w1 = set(shorter.split())
    w2 = set(longer.split())
    if len(w1) >= 2 and w1.issubset(w2):
        return True
    # High word overlap ratio (>= 60% of the shorter set)
    if w1 and w2:
        overlap = w1 & w2
        if len(overlap) >= max(2, len(min(w1, w2, key=len)) * 0.6):
            return True
    return False


def _merge_by_episode(
    groups: dict[str, dict[tuple[int | None, int | None], list[VideoFile]]],
) -> dict[str, dict[tuple[int | None, int | None], list[VideoFile]]]:
    """Merge groups with different titles but same content.

    Merges when:
    1. Episode key sets match or overlap significantly, OR
    2. Titles are similar (prefix/substring/word overlap) — for standalone movies
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
        is_standalone_1 = ep_keys_1 == {(None, None)}

        # Look for matching groups
        matches: list[str] = []
        for j in range(i + 1, len(titles)):
            t2 = titles[j]
            if t2 in used:
                continue
            ep_keys_2 = set(groups[t2].keys())
            is_standalone_2 = ep_keys_2 == {(None, None)}

            should_merge = False

            # Both have episodes — check episode overlap
            if not is_standalone_1 and not is_standalone_2:
                if ep_keys_1 == ep_keys_2:
                    should_merge = True
                else:
                    overlap = ep_keys_1 & ep_keys_2
                    if overlap and len(overlap) >= min(len(ep_keys_1), len(ep_keys_2)) * 0.5:
                        should_merge = True

            # Title similarity — works for both standalone and episodic
            if not should_merge and _titles_similar(t1, t2):
                should_merge = True

            if should_merge:
                matches.append(t2)

        if matches:
            # Merge all matches into t1
            combined = defaultdict(list)
            for ep_key, vf_list in groups[t1].items():
                combined[ep_key].extend(vf_list)
            for t2 in matches:
                for ep_key, vf_list in groups[t2].items():
                    combined[ep_key].extend(vf_list)
                used.add(t2)
            merged[t1] = dict(combined)
        else:
            merged[t1] = groups[t1]
        used.add(t1)

    return merged
