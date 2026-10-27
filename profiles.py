"""Priority profiles for scoring.

Allow configuring component weights when comparing video files.
A profile can be selected via --profile or custom weights set via --weights.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScoringWeights:
    """Scoring component weights. All values are multipliers (1.0 = default)."""
    resolution: float = 1.0       # Resolution
    video_quality: float = 1.0    # Video quality (bitrate/codec)
    hdr: float = 1.0              # HDR
    audio: float = 1.0            # Audio (codec, channels, Atmos)
    subtitles: float = 1.0        # Subtitles
    compatibility: float = 1.0    # Device compatibility
    file_size: float = 1.0        # Size efficiency (penalizes bloated encodes)
    stereo3d: float = 1.0         # 3D format fidelity bonus
    remaster: float = 1.0         # Remastered/restored release bonus

    def as_dict(self) -> dict[str, float]:
        return {
            "resolution": self.resolution,
            "video_quality": self.video_quality,
            "hdr": self.hdr,
            "audio": self.audio,
            "subtitles": self.subtitles,
            "compatibility": self.compatibility,
            "file_size": self.file_size,
            "stereo3d": self.stereo3d,
            "remaster": self.remaster,
        }


# Preset profiles
PROFILES: dict[str, tuple[str, ScoringWeights]] = {
    "default": (
        "Balanced (default)",
        ScoringWeights(),
    ),
    "quality": (
        "Maximum quality — resolution, bitrate and HDR prioritized",
        ScoringWeights(
            resolution=1.5,
            video_quality=1.5,
            hdr=1.5,
            audio=1.2,
            compatibility=0.3,
            file_size=0.0,
            remaster=1.5,
        ),
    ),
    "atmos": (
        "Audio priority — Dolby Atmos, DTS:X, lossless, multichannel",
        ScoringWeights(
            resolution=0.8,
            video_quality=0.8,
            hdr=0.8,
            audio=2.5,
            subtitles=0.5,
            compatibility=0.5,
            file_size=0.0,
        ),
    ),
    "compact": (
        "Minimum size with acceptable quality",
        ScoringWeights(
            resolution=0.7,
            video_quality=0.7,
            hdr=0.5,
            audio=0.5,
            subtitles=0.5,
            compatibility=1.0,
            file_size=1.5,
        ),
    ),
    "androidtv": (
        "Android TV / Homatics / Kodi — compatibility prioritized",
        ScoringWeights(
            resolution=1.0,
            video_quality=1.0,
            hdr=0.8,
            audio=1.0,
            subtitles=0.8,
            compatibility=2.0,
            file_size=0.0,
        ),
    ),
    "universal": (
        "Universal — maximum compatibility with all devices",
        ScoringWeights(
            resolution=0.8,
            video_quality=0.8,
            hdr=0.5,
            audio=0.8,
            subtitles=1.0,
            compatibility=2.5,
            file_size=0.0,
        ),
    ),
    "videophile": (
        "Videophile — everything maxed: 4K HDR Atmos lossless, size irrelevant",
        ScoringWeights(
            resolution=1.5,
            video_quality=1.5,
            hdr=2.0,
            audio=2.0,
            subtitles=1.0,
            compatibility=0.2,
            file_size=0.0,
            remaster=1.5,
        ),
    ),
}


def get_profile(name: str) -> ScoringWeights:
    """Get profile by name."""
    if name not in PROFILES:
        available = ", ".join(PROFILES.keys())
        raise ValueError(f"Unknown profile '{name}'. Available: {available}")
    return PROFILES[name][1]


def parse_custom_weights(weights_str: str) -> ScoringWeights:
    """Parse custom weights string like 'resolution=1.5,audio=2.0,...'."""
    w = ScoringWeights()
    for pair in weights_str.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"Invalid weight format: '{pair}'. Expected: key=value")
        key, val = pair.split("=", 1)
        key = key.strip()
        val = val.strip()
        if not hasattr(w, key):
            valid = ", ".join(w.as_dict().keys())
            raise ValueError(f"Unknown weight '{key}'. Available: {valid}")
        try:
            setattr(w, key, float(val))
        except ValueError:
            raise ValueError(f"Invalid value for '{key}': '{val}'")
    return w


def list_profiles() -> str:
    """List available profiles for reference."""
    lines = ["Available priority profiles:", ""]
    for name, (desc, weights) in PROFILES.items():
        lines.append(f"  {name:15s} — {desc}")
        w = weights.as_dict()
        non_default = {k: v for k, v in w.items() if v != 1.0}
        if non_default:
            parts = [f"{k}={v}" for k, v in non_default.items()]
            lines.append(f"  {'':15s}   weights: {', '.join(parts)}")
    lines.append("")
    lines.append("Custom weights: --weights 'resolution=1.5,audio=2.0,file_size=1.0'")
    lines.append("Available keys: resolution, video_quality, hdr, audio, subtitles, "
                  "compatibility, file_size, stereo3d, remaster")
    return "\n".join(lines)
