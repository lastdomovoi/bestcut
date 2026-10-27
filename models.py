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
    encoder: str = ""   # e.g. "x264", "x265", "rav1e", "libvpx"
    is_3d: bool = False
    stereo_mode: str = ""  # "side_by_side", "top_bottom", "frame_sequential", etc.
    is_upscaled: bool = False
    real_height: int | None = None  # Estimated real resolution if upscaled
    crop_width: int | None = None   # Actual content width (without black bars)
    crop_height: int | None = None  # Actual content height (without black bars)
    duration: float = 0.0
    # Picture measurements (None = not measured)
    detail_psnr: float | None = None  # PSNR vs. half-res round trip; high = no fine detail
    peak_luma: float | None = None    # Median frame peak luma, fraction of code range
    picture_measured: bool = False    # measurement attempted (also set on failure)

    @property
    def has_black_bars(self) -> bool:
        if self.crop_width is None or self.crop_height is None:
            return False
        return (self.crop_width < self.width * 0.97 or
                self.crop_height < self.height * 0.97)

    @property
    def real_aspect_ratio(self) -> str:
        """Real aspect ratio of content (without black bars)."""
        w = self.crop_width or self.width
        h = self.crop_height or self.height
        if h == 0:
            return "?"
        ratio = w / h
        # Match to common aspect ratios
        aspects = [
            (4 / 3, "4:3"), (16 / 9, "16:9"), (1.85, "1.85:1"),
            (2.35, "2.35:1"), (2.39, "2.39:1"), (2.40, "2.40:1"),
            (21 / 9, "21:9"), (1.33, "4:3"), (1.78, "16:9"),
        ]
        closest = min(aspects, key=lambda x: abs(x[0] - ratio))
        if abs(closest[0] - ratio) < 0.05:
            return closest[1]
        return f"{ratio:.2f}:1"

    @property
    def encoded_aspect_ratio(self) -> str:
        if self.height == 0:
            return "?"
        ratio = self.width / self.height
        aspects = [
            (4 / 3, "4:3"), (16 / 9, "16:9"), (1.85, "1.85:1"),
            (2.35, "2.35:1"), (2.39, "2.39:1"), (2.40, "2.40:1"),
        ]
        closest = min(aspects, key=lambda x: abs(x[0] - ratio))
        if abs(closest[0] - ratio) < 0.05:
            return closest[1]
        return f"{ratio:.2f}:1"

    @property
    def pixels(self) -> int:
        return self.width * self.height

    @property
    def stereo_divisor(self) -> int:
        """How many views are packed into one frame (2 for SBS/top-bottom)."""
        if self.is_3d and self.stereo_mode in ("side_by_side", "top_bottom"):
            return 2
        return 1

    @property
    def eye_width(self) -> int:
        if self.is_3d and self.stereo_mode == "side_by_side":
            return self.width // 2
        return self.width

    @property
    def eye_height(self) -> int:
        if self.is_3d and self.stereo_mode == "top_bottom":
            return self.height // 2
        return self.height

    @property
    def eye_pixels(self) -> int:
        """Effective pixels per eye (SBS/TAB halve the frame)."""
        return self.eye_width * self.eye_height

    @property
    def stereo_full_res(self) -> bool:
        """True if each eye keeps full resolution (MVC, full SBS/TAB).

        Half-SBS/half-TAB squeeze one axis, so the per-eye aspect ratio
        is far from the real picture aspect.
        """
        if not self.is_3d:
            return False
        if self.stereo_mode == "mvc":
            return True
        if self.eye_height == 0:
            return False
        eye_aspect = self.eye_width / self.eye_height
        if self.stereo_mode == "side_by_side":
            return eye_aspect >= 1.3
        if self.stereo_mode == "top_bottom":
            return eye_aspect <= 2.7
        return False

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
    real_channels: int | None = None  # Actual active channels (detected via RMS analysis)
    # Quality analysis (ffmpeg astats); None = not analyzed
    snr_db: float | None = None           # RMS level minus quietest-window RMS
    noise_floor_db: float | None = None   # RMS of the quietest window
    clip_ratio: float | None = None       # Share of samples stuck at peak level
    hf_level_db: float | None = None      # RMS above 16 kHz, relative to overall RMS

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
    def effective_channels(self) -> int:
        """Actual channel count for scoring (real_channels if detected)."""
        if self.real_channels is not None:
            return self.real_channels
        return self.channels

    @property
    def channel_label(self) -> str:
        ch = self.channels
        real = self.real_channels
        label = self._ch_label(ch)
        if real is not None and real < ch:
            real_label = self._ch_label(real)
            return f"{label} [FAKE → actually {real_label}]"
        return label

    @staticmethod
    def _ch_label(ch: int) -> str:
        if ch >= 8:
            return "7.1"
        if ch >= 6:
            return "5.1"
        if ch >= 2:
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
    source_type: str = ""  # "BDRip", "WEB-DL", "HDTV", "DVDRip", etc.
    # Master-grade indicators measured from the content (never names/tags)
    remaster_evidence: list[str] = field(default_factory=list)
    # For episode matching
    series_title: str = ""
    season: int | None = None
    episode: int | None = None

    @property
    def is_remaster(self) -> bool:
        """Measured native detail and verified HDR (see probe.detect_remaster)."""
        return len(self.remaster_evidence) >= 2

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
