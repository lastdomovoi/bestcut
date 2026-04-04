"""Проверка совместимости видеофайлов с популярными плеерами/устройствами."""

from __future__ import annotations

from models import VideoFile

# Профили устройств: что поддерживается
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
            "dts": "DTS требует транскодирования на большинстве клиентов",
            "truehd": "TrueHD/Atmos — только passthrough через HDMI",
            "pgs": "PGS субтитры — burn-in (транскодирование видео)",
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
            "mkv": "MKV не поддерживается нативно (через Infuse/VLC)",
            "dts": "DTS не поддерживается нативно",
            "flac": "FLAC — только через сторонние приложения",
            "ass": "ASS субтитры — без стилей",
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
            "ass": "ASS субтитры не поддерживаются",
            "pgs": "PGS — ограниченная поддержка, зависит от модели",
            "flac": "FLAC — не на всех моделях",
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
            "ass": "ASS субтитры не поддерживаются",
            "truehd": "TrueHD — только passthrough через HDMI eARC",
        },
    },
    "browser": {
        "name": "Браузер (Chrome/Firefox/Safari)",
        "containers": {"mov,mp4,m4a,3gp,3g2,mj2"},
        "video_codecs": {"h264", "vp9", "av1"},
        "audio_codecs": {"aac", "opus", "mp3", "vorbis"},
        "subtitle_codecs": {"webvtt"},
        "max_video_level": {"h264": 4.2},
        "hdr": False,
        "notes": {
            "mkv": "MKV не поддерживается браузерами",
            "hevc": "H.265 — Safari only (macOS/iOS)",
            "ac3": "AC3/EAC3 — Safari only",
            "dts": "DTS не поддерживается ни одним браузером",
            "srt": "SRT не поддерживается нативно (нужен WebVTT)",
        },
    },
    "vlc": {
        "name": "VLC",
        "containers": {"*"},  # всё
        "video_codecs": {"*"},
        "audio_codecs": {"*"},
        "subtitle_codecs": {"*"},
        "max_video_level": {},
        "hdr": True,
        "notes": {},
    },
    "kodi_desktop": {
        "name": "Kodi (ПК/macOS)",
        "containers": {"*"},
        "video_codecs": {"*"},
        "audio_codecs": {"*"},
        "subtitle_codecs": {"*"},
        "max_video_level": {},
        "hdr": True,
        "notes": {},
    },
    "kodi_androidtv": {
        "name": "Kodi на Android TV (Homatics/Shield/AMLogic)",
        "containers": {"matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2", "mpegts", "avi"},
        "video_codecs": {"h264", "hevc", "vp9", "av1", "mpeg4", "mpeg2video"},
        # Аудио: Kodi декодирует программно всё, но passthrough зависит от HDMI.
        # Перечисляем то, что реально проигрывается без проблем.
        "audio_codecs": {"aac", "ac3", "eac3", "dts", "dca", "flac", "mp3",
                         "opus", "vorbis", "truehd", "pcm_s16le", "pcm_s24le"},
        "subtitle_codecs": {"subrip", "ass", "hdmv_pgs_subtitle", "dvd_subtitle",
                            "mov_text", "webvtt"},
        "max_video_level": {"h264": 5.1, "hevc": 5.1},
        "max_resolution": (3840, 2160),  # 4K макс. для большинства SoC
        "hdr": True,  # HDR10 на большинстве; DV зависит от устройства
        "notes": {
            "h264_10bit": "H.264 10-bit (Hi10P) — нет HW-декодирования, "
                          "программное может тормозить",
            "truehd": "TrueHD/Atmos — passthrough через HDMI; "
                      "программное декодирование нагружает CPU",
            "dts_hdma": "DTS-HD MA/DTS:X — passthrough через HDMI; "
                        "программное декодирование может не работать на слабых SoC",
            "av1": "AV1 — только на свежих SoC (S905X4/S928X, Shield 2019+); "
                   "на старых — нет поддержки",
            "hevc_4k60": "HEVC 4K@60fps — не все бюджетные SoC тянут; "
                         "S905X3 и ниже — только 4K@30fps для HEVC",
            "dolby_vision": "Dolby Vision — только сертифицированные устройства "
                            "(Homatics Box R 4K Plus, Shield Pro); профиль 5/8 через MEL",
            "pgs_4k": "PGS субтитры в 4K — рендеринг может вызвать выпадение кадров "
                      "на слабых устройствах",
        },
    },
}

# Дополнительные проверки для Android TV (вызываются из check_compatibility)
def _check_androidtv_extras(vf: VideoFile, profile: dict) -> list[str]:
    """Проверки, специфичные для Android TV / Homatics / Shield."""
    issues = []
    notes = profile.get("notes", {})

    # H.264 10-bit
    if vf.video.codec == "h264" and vf.video.bit_depth == 10:
        issues.append(notes["h264_10bit"])

    # HEVC 4K@60fps на слабых SoC
    if (vf.video.codec == "hevc" and vf.video.width >= 3840
            and vf.video.fps > 30):
        issues.append(notes["hevc_4k60"])

    # Разрешение выше 4K
    max_res = profile.get("max_resolution")
    if max_res and (vf.video.width > max_res[0] or vf.video.height > max_res[1]):
        issues.append(
            f"Разрешение {vf.video.width}x{vf.video.height} превышает "
            f"максимум устройства ({max_res[0]}x{max_res[1]})"
        )

    # TrueHD/Atmos — предупреждение о passthrough
    for at in vf.audio_tracks:
        if at.codec == "truehd":
            issues.append(notes["truehd"])
            break

    # DTS-HD MA / DTS:X — предупреждение
    for at in vf.audio_tracks:
        if at.codec in ("dts", "dca") and at.is_lossless:
            issues.append(notes["dts_hdma"])
            break

    # Dolby Vision
    if vf.video.hdr_type == "dolby_vision":
        issues.append(notes["dolby_vision"])

    # PGS в 4K
    if vf.video.width >= 3840:
        has_pgs = any(st.codec == "hdmv_pgs_subtitle" for st in vf.subtitle_tracks)
        if has_pgs:
            issues.append(notes["pgs_4k"])

    return issues


def _container_matches(file_container: str, supported: set[str]) -> bool:
    """Проверить, подходит ли контейнер файла."""
    if "*" in supported:
        return True
    return any(
        fc in s or s in fc
        for fc in file_container.split(",")
        for s in supported
    )


def check_compatibility(vf: VideoFile, devices: list[str] | None = None) -> dict[str, list[str]]:
    """Проверить совместимость файла с устройствами.

    Возвращает dict: device_id -> список проблем (пустой = совместимо).
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

        # Контейнер
        if not _container_matches(vf.container, profile["containers"]):
            issues.append(f"Контейнер {vf.container} не поддерживается")

        # Видеокодек
        if "*" not in profile["video_codecs"]:
            if vf.video.codec not in profile["video_codecs"]:
                issues.append(f"Видеокодек {vf.video.codec} не поддерживается")

        # H.264 10-bit (Hi10P) — проблема для всех аппаратных декодеров
        if vf.video.codec == "h264" and vf.video.bit_depth == 10:
            issues.append("H.264 10-bit (Hi10P) — нет аппаратного декодирования")

        # Уровень кодека
        max_levels = profile.get("max_video_level", {})
        if vf.video.codec in max_levels and vf.video.level:
            try:
                file_level = float(vf.video.level)
                max_level = max_levels[vf.video.codec]
                if file_level > max_level:
                    issues.append(
                        f"{vf.video.codec} level {vf.video.level} > "
                        f"макс. {max_level} (возможны проблемы с декодированием)"
                    )
            except ValueError:
                pass

        # HDR
        if vf.video.hdr_type and not profile.get("hdr"):
            issues.append(f"HDR ({vf.video.hdr_type}) не поддерживается — цвета будут неверными")

        # Аудиокодеки
        if "*" not in profile["audio_codecs"]:
            for at in vf.audio_tracks:
                if at.codec not in profile["audio_codecs"]:
                    note = profile.get("notes", {}).get(at.codec, "")
                    msg = f"Аудиокодек {at.codec}"
                    if at.language:
                        msg += f" [{at.language}]"
                    msg += " не поддерживается"
                    if note:
                        msg += f" ({note})"
                    issues.append(msg)

        # Субтитры
        if "*" not in profile["subtitle_codecs"]:
            for st in vf.subtitle_tracks:
                if st.codec not in profile["subtitle_codecs"]:
                    note = profile.get("notes", {}).get(
                        "pgs" if st.is_bitmap else st.codec, ""
                    )
                    msg = f"Субтитры {st.codec}"
                    if st.language:
                        msg += f" [{st.language}]"
                    msg += " не поддерживаются"
                    if note:
                        msg += f" ({note})"
                    issues.append(msg)

        # Дополнительные примечания
        notes = profile.get("notes", {})
        if "matroska" in vf.container and "mkv" in notes:
            issues.append(notes["mkv"])

        # Дополнительные проверки для Android TV
        if dev_id == "kodi_androidtv":
            issues.extend(_check_androidtv_extras(vf, profile))

        results[dev_id] = issues

    return results


def compatibility_score(vf: VideoFile) -> float:
    """Рассчитать балл совместимости (0-10)."""
    # Проверяем по ключевым устройствам (исключаем VLC/Kodi — они всё играют)
    key_devices = ["kodi_androidtv", "plex", "apple_tv", "samsung_tv", "lg_tv", "browser"]
    issues = check_compatibility(vf, key_devices)

    score = 10.0
    for dev_id, dev_issues in issues.items():
        if dev_issues:
            # Каждое устройство с проблемами снижает балл
            score -= min(2.0, len(dev_issues) * 0.5)

    return max(0.0, score)
