"""Dataclasses for storing video file information."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VideoTrack:
    codec: str = ""
    profile: str = ""
    level: str = ""
    width: int = 0
    height: int = 0
    bitrate: int = 0  # bps
    fps: float = 0.0
    bit_depth: int = 8
    pix_fmt: str = ""
    color_space: str = ""
    color_transfer: str = ""
    color_primaries: str = ""
    hdr_type: str = ""  # "", "hdr10", "hdr10+", "dolby_vision", "hlg"
    duration: float = 0.0

    @property
    def pixels(self) -> int:
        return self.width * self.height

    @property
    def resolution_label(self) -> str:
        h = self.height
        if h >= 2160:
            return "4K"
        if h >= 1440:
            return "1440p"
        if h >= 1080:
            return "1080p"
        if h >= 720:
            return "720p"
        if h >= 480:
            return "480p"
        return f"{h}p"


@dataclass
class AudioTrack:
    codec: str = ""
    profile: str = ""
    bitrate: int = 0
    channels: int = 0
    channel_layout: str = ""
    sample_rate: int = 0
    language: str = ""
    title: str = ""
    default: bool = False
    duration: float = 0.0
    has_atmos: bool = False  # Dolby Atmos (TrueHD Atmos / EAC3 JOC)
    has_dtsx: bool = False   # DTS:X
    is_lossless: bool = False  # TrueHD, DTS-HD MA, FLAC, PCM

    @property
    def spatial_format(self) -> str:
        """Spatial audio format."""
        if self.has_atmos:
            return "Dolby Atmos"
        if self.has_dtsx:
            return "DTS:X"
        return ""

    @property
    def format_label(self) -> str:
        """Full audio format description."""
        base = self.codec.upper()
        if self.codec == "truehd" and self.has_atmos:
            return "TrueHD Atmos"
        if self.codec == "eac3" and self.has_atmos:
            return "EAC3 Atmos (DD+ JOC)"
        if self.codec == "dts" and self.has_dtsx:
            return "DTS:X"
        if self.codec == "truehd":
            return "TrueHD"
        if self.codec in ("dts", "dca"):
            profile = self.profile.lower()
            if "hd ma" in profile or "ma" in profile:
                return "DTS-HD MA"
            if "hd hra" in profile or "hra" in profile:
                return "DTS-HD HRA"
            if "express" in profile:
                return "DTS Express"
            return "DTS"
        if self.codec == "eac3":
            return "EAC3 (DD+)"
        if self.codec == "ac3":
            return "AC3 (DD)"
        return base

    @property
    def channel_label(self) -> str:
        if self.channels >= 8:
            return "7.1"
        if self.channels >= 6:
            return "5.1"
        if self.channels >= 2:
            return "Stereo"
        return "Mono"


@dataclass
class SubtitleTrack:
    codec: str = ""
    language: str = ""
    title: str = ""
    forced: bool = False
    default: bool = False

    @property
    def is_bitmap(self) -> bool:
        return self.codec in ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle")


@dataclass
class IntegrityResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    is_truncated: bool = False
    error_count: int = 0
    checked: bool = False

    @property
    def is_ok(self) -> bool:
        return self.checked and self.error_count == 0 and not self.is_truncated


@dataclass
class VideoFile:
    path: Path = field(default_factory=Path)
    container: str = ""
    file_size: int = 0  # bytes
    duration: float = 0.0  # seconds
    overall_bitrate: int = 0  # bps
    video: VideoTrack = field(default_factory=VideoTrack)
    audio_tracks: list[AudioTrack] = field(default_factory=list)
    subtitle_tracks: list[SubtitleTrack] = field(default_factory=list)
    integrity: IntegrityResult = field(default_factory=IntegrityResult)
    score: float = 0.0
    compatibility_score: float = 0.0
    compatibility_issues: list[str] = field(default_factory=list)
    # For episode matching
    series_title: str = ""
    season: int | None = None
    episode: int | None = None

    @property
    def file_size_gb(self) -> float:
        return self.file_size / (1024 ** 3)

    @property
    def file_size_human(self) -> str:
        size = self.file_size
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    @property
    def duration_human(self) -> str:
        m, s = divmod(int(self.duration), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"
