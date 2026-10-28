"""Audio language tags: normalization and present/absent/unknown status."""

from __future__ import annotations

import json
import re
from pathlib import Path

from models import AudioTrack, VideoFile

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
    "zh": "chi", "chinese": "chi", "chi": "chi", "zho": "chi",
    "ko": "kor", "korean": "kor", "kor": "kor",
    "pt": "por", "portuguese": "por", "por": "por",
    "pl": "pol", "polish": "pol", "pol": "pol",
    "tr": "tur", "turkish": "tur", "tur": "tur",
    "cs": "cze", "ces": "cze", "czech": "cze", "cze": "cze",
    "nl": "dut", "nld": "dut", "dutch": "dut", "dut": "dut",
    "sv": "swe", "swedish": "swe", "swe": "swe",
    "hi": "hin", "hindi": "hin", "hin": "hin",
    "ar": "ara", "arabic": "ara", "ara": "ara",
}

KNOWN_LANGUAGES = set(_ALIASES.values())

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


# Explicit language names in a track title ("Russian 5.1", "Русский").  A
# title like "DUB" names no language and proves nothing.
_TITLE_LANGUAGES = {
    "rus": re.compile(r"русск|russian|(?<![a-z])rus(?![a-z])", re.I),
    "eng": re.compile(r"англ|english|(?<![a-z])eng(?![a-z])", re.I),
    "ukr": re.compile(r"украин|ukrainian|(?<![a-z])ukr(?![a-z])", re.I),
    "ger": re.compile(r"немец|german|deutsch|(?<![a-z])ger(?![a-z])", re.I),
    "fre": re.compile(r"француз|french|(?<![a-z])fre(?![a-z])", re.I),
    "spa": re.compile(r"испан|spanish|(?<![a-z])spa(?![a-z])", re.I),
    "jpn": re.compile(r"япон|japanese|(?<![a-z])jpn(?![a-z])", re.I),
}


def resolve_language(at: AudioTrack) -> tuple[str, str]:
    """(language code, source) of an audio track; ("", "") if unknown.

    Proof, in order: user confirmation ("override"), the stream's language
    tag ("tag").  An explicit language name in the track title is only a
    hint ("title"): titles also describe commentaries and dubs of other
    languages, so a hint never counts as the track's language.  File and
    directory names are never consulted.  Spoken-language identification
    is not implemented.
    """
    if at.language_override:
        return normalize_language(at.language_override), "override"
    if not is_unknown_language(at.language):
        return normalize_language(at.language), "tag"
    return "", ""


def title_hint(at: AudioTrack) -> str:
    """Language named in the track title, if exactly one; "" otherwise."""
    found = [code for code, rx in _TITLE_LANGUAGES.items() if rx.search(at.title or "")]
    return found[0] if len(found) == 1 else ""


def track_language(at: AudioTrack) -> str:
    return resolve_language(at)[0]


def language_label(at: AudioTrack) -> str:
    """Language with its evidence: "rus (tag)", "unknown (untagged)",
    "unknown (title suggests rus)"."""
    code, source = resolve_language(at)
    if code:
        return f"{code} ({source})"
    hint = title_hint(at)
    return f"unknown (title suggests {hint})" if hint else "unknown (untagged)"


# --- user confirmations ----------------------------------------------------

class OverrideStoreError(Exception):
    """The confirmations file cannot be read or written safely."""


def overrides_path() -> Path:
    from config import CONFIG_DIR
    return CONFIG_DIR / "language_overrides.json"


def _file_key(path: Path) -> str:
    try:
        return f"{Path(path).resolve()}|{Path(path).stat().st_size}"
    except OSError:
        return str(path)


_STORE_MEMO: dict = {}


def load_overrides(path: Path | None = None, strict: bool = False) -> dict:
    """Saved confirmations.  A missing file is empty; a damaged one is empty
    too, unless strict (used before writing, so nothing is overwritten)."""
    target = path or overrides_path()
    try:
        stamp = target.stat().st_mtime_ns
    except OSError:
        return {}
    memo = _STORE_MEMO.get(str(target))
    if memo and memo[0] == stamp:
        return memo[1]
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as e:
        if strict:
            raise OverrideStoreError(f"{target} is damaged ({e}); fix or remove it") from e
        return {}
    _STORE_MEMO[str(target)] = (stamp, data)
    return data


def save_override(media: Path, track: int, language: str,
                  store: Path | None = None) -> None:
    """Remember that audio track `track` (0-based) of `media` is `language`.

    Raises ValueError for an unknown language code and OverrideStoreError if
    the store is damaged or cannot be written.
    """
    code = ""
    if language:
        code = normalize_language(language)
        if code not in KNOWN_LANGUAGES:
            raise ValueError(f"unknown language '{language}' "
                             f"(known: {', '.join(sorted(KNOWN_LANGUAGES))})")
    target = store or overrides_path()
    data = dict(load_overrides(target, strict=True))
    key = _file_key(media)
    entry = dict(data.get(key, {}))
    if code:
        entry[str(track)] = code
    else:
        entry.pop(str(track), None)
    if entry:
        data[key] = entry
    else:
        data.pop(key, None)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)                  # atomic: never a half-written store
    except OSError as e:
        raise OverrideStoreError(f"cannot write {target}: {e.strerror or e}") from e
    _STORE_MEMO.pop(str(target), None)


def apply_overrides(vf: VideoFile, store: Path | None = None) -> None:
    """Set (or clear) every track's override from the saved confirmations.

    Always resets first: a cached copy must not carry a stale confirmation.
    """
    entry = load_overrides(store).get(_file_key(vf.path), {})
    for i, at in enumerate(vf.audio_tracks):
        at.language_override = entry.get(str(i), "")


def primary_language_status(vf: VideoFile, wanted: str) -> str:
    """"present" (a track is confirmed to be it), "absent" (every track is
    identified as some other language) or "unknown" (tracks without
    confirmed language exist and none is the wanted one).  Empty string if
    no language is configured.

    Proof is a language tag or a user confirmation; a title hint, a file
    name or a missing tag never proves presence or absence.
    """
    if not wanted:
        return ""
    if any(language_matches(track_language(at), wanted) for at in vf.audio_tracks):
        return "present"
    if any(is_unknown_language(track_language(at)) for at in vf.audio_tracks):
        return "unknown"
    return "absent"
