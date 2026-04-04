"""Video file compatibility check with popular players/devices."""

from __future__ import annotations

from models import VideoFile

# Device profiles: supported formats
DEVICE_PROFILES = {
    "plex": {
        "name": "Plex (Direct Play)",
        "containers": {"matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2", "mpegts", "avi"},
        "video_codecs": {"h264", "hevc", "vp9", "mpeg4", "mpeg2video"},
        "audio_codecs": {"aac", "ac3", "eac3", "flac", "mp3", "opus", "vorbis"},
        "subtitle_codecs": {"subrip", "ass", "mov_text", "webvtt"},
        "max_video_level": {"h264": 5.2, "hevc": 5.1},
        "hdr": True,
        "notes": {
            "dts": "DTS requires transcoding on most clients",
            "truehd": "TrueHD/Atmos — passthrough via HDMI only",
            "pgs": "PGS subtitles — burn-in (video transcoding required)",
        },
    },
    "apple_tv": {
        "name": "Apple TV 4K",
        "containers": {"mov,mp4,m4a,3gp,3g2,mj2"},
        "video_codecs": {"h264", "hevc", "vp9"},
        "audio_codecs": {"aac", "ac3", "eac3", "mp3"},
        "subtitle_codecs": {"subrip", "mov_text", "webvtt"},
        "max_video_level": {"h264": 4.2, "hevc": 5.1},
        "hdr": True,
        "notes": {
            "mkv": "MKV not natively supported (use Infuse/VLC)",
            "dts": "DTS not natively supported",
            "flac": "FLAC — third-party apps only",
            "ass": "ASS subtitles — no styling support",
        },
    },
    "samsung_tv": {
        "name": "Samsung Smart TV (2020+)",
        "containers": {"matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2", "avi", "mpegts"},
        "video_codecs": {"h264", "hevc", "vp9", "av1"},
        "audio_codecs": {"aac", "ac3", "eac3", "dts", "mp3"},
        "subtitle_codecs": {"subrip", "mov_text"},
        "max_video_level": {"h264": 4.1, "hevc": 5.1},
        "hdr": True,
        "notes": {
            "ass": "ASS subtitles not supported",
            "pgs": "PGS — limited support, depends on model",
            "flac": "FLAC — not on all models",
        },
    },
    "lg_tv": {
        "name": "LG Smart TV (2020+)",
        "containers": {"matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2", "avi", "mpegts"},
        "video_codecs": {"h264", "hevc", "vp9", "av1"},
        "audio_codecs": {"aac", "ac3", "eac3", "dts", "mp3"},
        "subtitle_codecs": {"subrip", "mov_text"},
        "max_video_level": {"h264": 4.1, "hevc": 5.1},
        "hdr": True,
        "notes": {
            "ass": "ASS subtitles not supported",
            "truehd": "TrueHD — passthrough via HDMI eARC only",
        },
    },
    "browser": {
        "name": "Browser (Chrome/Firefox/Safari)",
        "containers": {"mov,mp4,m4a,3gp,3g2,mj2"},
        "video_codecs": {"h264", "vp9", "av1"},
        "audio_codecs": {"aac", "opus", "mp3", "vorbis"},
        "subtitle_codecs": {"webvtt"},
        "max_video_level": {"h264": 4.2},
        "hdr": False,
        "notes": {
            "mkv": "MKV not supported by browsers",
            "hevc": "H.265 — Safari only (macOS/iOS)",
            "ac3": "AC3/EAC3 — Safari only",
            "dts": "DTS not supported by any browser",
            "srt": "SRT not natively supported (WebVTT required)",
        },
    },
    "vlc": {
        "name": "VLC",
        "containers": {"*"},  # everything
        "video_codecs": {"*"},
        "audio_codecs": {"*"},
        "subtitle_codecs": {"*"},
        "max_video_level": {},
        "hdr": True,
        "notes": {},
    },
    "kodi_desktop": {
        "name": "Kodi (PC/macOS)",
        "containers": {"*"},
        "video_codecs": {"*"},
        "audio_codecs": {"*"},
        "subtitle_codecs": {"*"},
        "max_video_level": {},
        "hdr": True,
        "notes": {},
    },
    "kodi_androidtv": {
        "name": "Kodi on Android TV (Homatics/Shield/AMLogic)",
        "containers": {"matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2", "mpegts", "avi"},
        "video_codecs": {"h264", "hevc", "vp9", "av1", "mpeg4", "mpeg2video"},
        # Audio: Kodi software-decodes everything, but passthrough depends on HDMI.
        # Listing codecs that actually play without issues.
        "audio_codecs": {"aac", "ac3", "eac3", "dts", "dca", "flac", "mp3",
                         "opus", "vorbis", "truehd", "pcm_s16le", "pcm_s24le"},
        "subtitle_codecs": {"subrip", "ass", "hdmv_pgs_subtitle", "dvd_subtitle",
                            "mov_text", "webvtt"},
        "max_video_level": {"h264": 5.1, "hevc": 5.1},
        "max_resolution": (3840, 2160),  # 4K max for most SoCs
        "hdr": True,  # HDR10 on most devices; DV depends on device
        "notes": {
            "h264_10bit": "H.264 10-bit (Hi10P) — no HW decoding, "
                          "software decoding may stutter",
            "truehd": "TrueHD/Atmos — passthrough via HDMI; "
                      "software decoding is CPU-intensive",
            "dts_hdma": "DTS-HD MA/DTS:X — passthrough via HDMI; "
                        "software decoding may not work on low-end SoCs",
            "av1": "AV1 — only on newer SoCs (S905X4/S928X, Shield 2019+); "
                   "not supported on older ones",
            "hevc_4k60": "HEVC 4K@60fps — not all budget SoCs can handle it; "
                         "S905X3 and below — HEVC limited to 4K@30fps",
            "dolby_vision": "Dolby Vision — certified devices only "
                            "(Homatics Box R 4K Plus, Shield Pro); profile 5/8 via MEL",
            "pgs_4k": "PGS subtitles in 4K — rendering may cause frame drops "
                      "on low-end devices",
        },
    },
}

# Additional checks for Android TV (called from check_compatibility)
def _check_androidtv_extras(vf: VideoFile, profile: dict) -> list[str]:
    """Android TV / Homatics / Shield specific checks."""
    issues = []
    notes = profile.get("notes", {})

    # H.264 10-bit
    if vf.video.codec == "h264" and vf.video.bit_depth == 10:
        if "h264_10bit" in notes:
            issues.append(notes["h264_10bit"])

    # HEVC 4K@60fps on low-end SoCs
    if (vf.video.codec == "hevc" and vf.video.width >= 3840
            and vf.video.fps > 30):
        if "hevc_4k60" in notes:
            issues.append(notes["hevc_4k60"])

    # Resolution above 4K
    max_res = profile.get("max_resolution")
    if max_res and (vf.video.width > max_res[0] or vf.video.height > max_res[1]):
        issues.append(
            f"Resolution {vf.video.width}x{vf.video.height} exceeds "
            f"device maximum ({max_res[0]}x{max_res[1]})"
        )

    # TrueHD/Atmos — passthrough warning
    for at in vf.audio_tracks:
        if at.codec == "truehd" and "truehd" in notes:
            issues.append(notes["truehd"])
            break

    # DTS-HD MA / DTS:X — warning
    for at in vf.audio_tracks:
        if at.codec in ("dts", "dca") and at.is_lossless and "dts_hdma" in notes:
            issues.append(notes["dts_hdma"])
            break

    # Dolby Vision
    if vf.video.hdr_type == "dolby_vision" and "dolby_vision" in notes:
        issues.append(notes["dolby_vision"])

    # PGS в 4K
    if vf.video.width >= 3840 and "pgs_4k" in notes:
        has_pgs = any(st.codec == "hdmv_pgs_subtitle" for st in vf.subtitle_tracks)
        if has_pgs:
            issues.append(notes["pgs_4k"])

    return issues


def _container_matches(file_container: str, supported: set[str]) -> bool:
    """Check if the file container is supported."""
    if "*" in supported:
        return True
    return any(
        fc in s or s in fc
        for fc in file_container.split(",")
        for s in supported
    )


def check_compatibility(vf: VideoFile, devices: list[str] | None = None) -> dict[str, list[str]]:
    """Check file compatibility with devices.

    Returns dict: device_id -> list of issues (empty = compatible).
    """
    if devices is None:
        devices = list(DEVICE_PROFILES.keys())

    results: dict[str, list[str]] = {}

    for dev_id in devices:
        profile = DEVICE_PROFILES.get(dev_id)
        if not profile:
            continue

        issues: list[str] = []
        name = profile["name"]

        # Container
        if not _container_matches(vf.container, profile["containers"]):
            issues.append(f"Container {vf.container} not supported")

        # Video codec
        if "*" not in profile["video_codecs"]:
            if vf.video.codec not in profile["video_codecs"]:
                issues.append(f"Video codec {vf.video.codec} not supported")

        # H.264 10-bit (Hi10P) — problematic for all hardware decoders
        if vf.video.codec == "h264" and vf.video.bit_depth == 10:
            issues.append("H.264 10-bit (Hi10P) — no hardware decoding")

        # Codec level
        max_levels = profile.get("max_video_level", {})
        if vf.video.codec in max_levels and vf.video.level:
            try:
                file_level = float(vf.video.level)
                max_level = max_levels[vf.video.codec]
                if file_level > max_level:
                    issues.append(
                        f"{vf.video.codec} level {vf.video.level} > "
                        f"max {max_level} (decoding issues possible)"
                    )
            except ValueError:
                pass

        # HDR
        if vf.video.hdr_type and not profile.get("hdr"):
            issues.append(f"HDR ({vf.video.hdr_type}) not supported — colors will be incorrect")

        # Audio codecs
        if "*" not in profile["audio_codecs"]:
            for at in vf.audio_tracks:
                if at.codec not in profile["audio_codecs"]:
                    note = profile.get("notes", {}).get(at.codec, "")
                    msg = f"Audio codec {at.codec}"
                    if at.language:
                        msg += f" [{at.language}]"
                    msg += " not supported"
                    if note:
                        msg += f" ({note})"
                    issues.append(msg)

        # Subtitles
        if "*" not in profile["subtitle_codecs"]:
            for st in vf.subtitle_tracks:
                if st.codec not in profile["subtitle_codecs"]:
                    note = profile.get("notes", {}).get(
                        "pgs" if st.is_bitmap else st.codec, ""
                    )
                    msg = f"Subtitles {st.codec}"
                    if st.language:
                        msg += f" [{st.language}]"
                    msg += " not supported"
                    if note:
                        msg += f" ({note})"
                    issues.append(msg)

        # Additional notes
        notes = profile.get("notes", {})
        if "matroska" in vf.container and "mkv" in notes:
            issues.append(notes["mkv"])

        # Additional checks for Android TV
        if dev_id == "kodi_androidtv":
            issues.extend(_check_androidtv_extras(vf, profile))

        results[dev_id] = issues

    return results


def compatibility_score(vf: VideoFile) -> float:
    """Calculate compatibility score (0-10)."""
    # Check against key devices (excluding VLC/Kodi — they play everything)
    key_devices = ["kodi_androidtv", "plex", "apple_tv", "samsung_tv", "lg_tv", "browser"]
    issues = check_compatibility(vf, key_devices)

    score = 10.0
    for dev_id, dev_issues in issues.items():
        if dev_issues:
            # Each device with issues lowers the score
            score -= min(2.0, len(dev_issues) * 0.5)

    return max(0.0, score)
