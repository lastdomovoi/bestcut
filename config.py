"""video-compare configuration via TOML file.

Path: ~/.config/video-compare/config.toml
Example contents:

[general]
profile = "androidtv"
verbose = false
check_integrity = "quick"  # "none", "quick", "full"

[weights]
resolution = 1.0
video_quality = 1.0
hdr = 0.8
audio = 1.5
subtitles = 0.8
compatibility = 2.0
file_size = 0.0

[devices]
# Devices for compatibility check (empty list = all)
check = ["kodi_androidtv", "plex"]

[audio]
# Primary language — main audio language to look for.
# Files without this language get a penalty; extra tracks in this language
# earn a bonus (multiple dubs / commentary tracks).
primary_language = "rus"
# Additional desired languages (no penalty if missing, but bonus if present).
extra_languages = ["eng"]
# Penalty when primary language track is completely absent.
missing_primary_penalty = 10.0
# Bonus per additional primary-language track beyond the first (max 3.0).
extra_primary_track_bonus = 1.5
# Bonus per extra_languages track present (max 3.0).
extra_language_bonus = 1.5
"""

from __future__ import annotations

import os
from pathlib import Path

from profiles import ScoringWeights

CONFIG_DIR = Path(__file__).resolve().parent
CONFIG_PATH = CONFIG_DIR / "config.toml"


def _parse_toml(path: Path) -> dict:
    """Parse TOML file. Python 3.11+ has tomllib."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            # Minimal parser for basic cases
            return _parse_toml_simple(path)

    with open(path, "rb") as f:
        return tomllib.load(f)


def _parse_toml_simple(path: Path) -> dict:
    """Minimal TOML parser for key=value and [section]."""
    result: dict = {}
    current_section: dict | None = None

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section_name = line[1:-1].strip()
            result[section_name] = {}
            current_section = result[section_name]
            continue
        if "=" in line and current_section is not None:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            # Remove comments
            if "#" in val:
                val = val[:val.index("#")].strip()
            # Parse values
            if val.startswith('"') and val.endswith('"'):
                current_section[key] = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                current_section[key] = val[1:-1]
            elif val.lower() in ("true", "false"):
                current_section[key] = val.lower() == "true"
            elif val.startswith("[") and val.endswith("]"):
                # Simple string array
                items = val[1:-1].split(",")
                current_section[key] = [
                    i.strip().strip('"').strip("'")
                    for i in items if i.strip()
                ]
            else:
                try:
                    current_section[key] = float(val) if "." in val else int(val)
                except ValueError:
                    current_section[key] = val

    return result


class Config:
    """Application configuration."""

    def __init__(self):
        self.profile: str = "default"
        self.verbose: bool = False
        self.check_integrity: str = "none"
        self.weights: ScoringWeights | None = None
        self.devices: list[str] = []
        self.primary_language: str = ""
        self.extra_languages: list[str] = []
        self.missing_primary_penalty: float = 10.0
        self.extra_primary_track_bonus: float = 1.5
        self.extra_language_bonus: float = 1.5

    @classmethod
    def load(cls) -> "Config":
        """Load configuration from file."""
        cfg = cls()
        if not CONFIG_PATH.exists():
            return cfg

        try:
            data = _parse_toml(CONFIG_PATH)
        except Exception:
            return cfg

        general = data.get("general", {})
        cfg.profile = general.get("profile", cfg.profile)
        cfg.verbose = general.get("verbose", cfg.verbose)
        cfg.check_integrity = general.get("check_integrity", cfg.check_integrity)

        weights_data = data.get("weights", {})
        if weights_data:
            cfg.weights = ScoringWeights(**{
                k: float(v) for k, v in weights_data.items()
                if hasattr(ScoringWeights(), k)
            })

        devices = data.get("devices", {})
        cfg.devices = devices.get("check", [])

        audio = data.get("audio", {})
        cfg.primary_language = audio.get("primary_language", "")
        cfg.extra_languages = audio.get("extra_languages", [])
        cfg.missing_primary_penalty = float(
            audio.get("missing_primary_penalty", cfg.missing_primary_penalty)
        )
        cfg.extra_primary_track_bonus = float(
            audio.get("extra_primary_track_bonus", cfg.extra_primary_track_bonus)
        )
        cfg.extra_language_bonus = float(
            audio.get("extra_language_bonus", cfg.extra_language_bonus)
        )

        return cfg

    def create_default_config(self) -> Path:
        """Create config file with default settings."""
        CONFIG_PATH.write_text('''\
# video-compare — configuration
# Path: ~/.config/video-compare/config.toml

[general]
# Default profile: default, quality, atmos, compact, androidtv, universal, videophile
profile = "default"
# Verbose output
verbose = false
# Integrity check: "none", "quick", "full"
check_integrity = "none"

[weights]
# Custom weights (override profile). Uncomment as needed.
# resolution = 1.0
# video_quality = 1.0
# hdr = 1.0
# audio = 1.0
# subtitles = 1.0
# compatibility = 1.0
# file_size = 0.0

[devices]
# Devices for compatibility check (empty = all).
# Available: kodi_androidtv, kodi_desktop, plex, apple_tv, samsung_tv, lg_tv, browser, vlc
# check = ["kodi_androidtv", "plex"]

[audio]
# Primary language — main audio language to look for.
primary_language = "rus"
# Additional desired languages (bonus if present).
extra_languages = ["eng"]
# Penalty when primary language track is completely absent.
missing_primary_penalty = 10.0
# Bonus per additional primary-language track beyond the first (max 3.0).
extra_primary_track_bonus = 1.5
# Bonus per extra_languages track present (max 3.0).
extra_language_bonus = 1.5
''', encoding="utf-8")
        return CONFIG_PATH
