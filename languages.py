"""Audio language tags: normalization and present/absent/unknown status."""

from __future__ import annotations

from models import VideoFile

# ISO 639-1 / common alias -> ISO 639-2/B code used in configs ("rus", "eng")
_ALIASES = {
    "ru": "rus", "russian": "rus", "rus": "rus",
    "en": "eng", "english": "eng", "eng": "eng",
    "uk": "ukr", "ukrainian": "ukr", "ukr": "ukr",
    "de": "ger", "deu": "ger", "german": "ger", "ger": "ger",
    "fr": "fre", "fra": "fre", "french": "fre", "fre": "fre",
    "es": "spa", "spanish": "spa", "spa": "spa",
    "it": "ita", "italian": "ita", "ita": "ita",
    "ja": "jpn", "japanese": "jpn", "jpn": "jpn",
}

# Tags that carry no information about the spoken language.
UNKNOWN_TAGS = {"", "und", "unk", "unknown", "zxx", "mis", "mul", "?"}


def normalize_language(tag: str) -> str:
    """Lowercase, drop region ("ru-RU" -> "ru") and map to a 3-letter code."""
    key = (tag or "").strip().lower().replace("_", "-").split("-")[0]
    return _ALIASES.get(key, key)


def is_unknown_language(tag: str) -> bool:
    return normalize_language(tag) in UNKNOWN_TAGS


def language_matches(tag: str, wanted: str) -> bool:
    return bool(wanted) and normalize_language(tag) == normalize_language(wanted)


def primary_language_status(vf: VideoFile, wanted: str) -> str:
    """"present" (a track is tagged with it), "absent" (every track is
    tagged with some other language) or "unknown" (untagged tracks exist
    and none is confirmed).  Empty string if no language is configured.

    Absence is never inferred from missing tags.
    """
    if not wanted:
        return ""
    if any(language_matches(at.language, wanted) for at in vf.audio_tracks):
        return "present"
    if any(is_unknown_language(at.language) for at in vf.audio_tracks):
        return "unknown"
    return "absent"
