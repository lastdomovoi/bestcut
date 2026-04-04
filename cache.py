"""Caching probe results for faster repeated runs.

Cache is stored in $XDG_CACHE_HOME/video-compare/ (default ~/.cache/video-compare/).
Cache key: sha256(path + file size + mtime).
Cache is automatically invalidated when the file changes.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from models import AudioTrack, IntegrityResult, SubtitleTrack, VideoFile, VideoTrack

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "video-compare"


def _cache_key(path: Path) -> str:
    """Create cache key based on path, size and mtime."""
    stat = path.stat()
    raw = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _serialize(vf: VideoFile) -> dict:
    """Serialize VideoFile to dict for JSON."""
    return {
        "path": str(vf.path),
        "container": vf.container,
        "file_size": vf.file_size,
        "duration": vf.duration,
        "overall_bitrate": vf.overall_bitrate,
        "video": asdict(vf.video),
        "audio_tracks": [asdict(a) for a in vf.audio_tracks],
        "subtitle_tracks": [asdict(s) for s in vf.subtitle_tracks],
    }


def _deserialize(data: dict) -> VideoFile:
    """Restore VideoFile from dict."""
    vf = VideoFile(
        path=Path(data["path"]),
        container=data["container"],
        file_size=data["file_size"],
        duration=data["duration"],
        overall_bitrate=data["overall_bitrate"],
        video=VideoTrack(**data["video"]),
        audio_tracks=[AudioTrack(**a) for a in data["audio_tracks"]],
        subtitle_tracks=[SubtitleTrack(**s) for s in data["subtitle_tracks"]],
    )
    return vf


def get_cached(path: Path) -> VideoFile | None:
    """Get cached probe result or None."""
    try:
        key = _cache_key(path)
        cp = _cache_path(key)
        if not cp.exists():
            return None
        data = json.loads(cp.read_text(encoding="utf-8"))
        return _deserialize(data)
    except Exception:
        return None


def set_cached(path: Path, vf: VideoFile) -> None:
    """Save probe result to cache."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        key = _cache_key(path)
        cp = _cache_path(key)
        data = _serialize(vf)
        cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # cache is not critical


def clear_cache() -> int:
    """Clear entire cache. Returns number of deleted files."""
    count = 0
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.json"):
            f.unlink()
            count += 1
    return count
