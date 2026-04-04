"""Профили приоритетов для скоринга.

Позволяют настроить веса компонентов при сравнении видеофайлов.
Профиль можно выбрать через --profile или задать свои веса через --weights.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScoringWeights:
    """Веса компонентов скоринга. Все значения — множители (1.0 = норма)."""
    resolution: float = 1.0       # Разрешение
    video_quality: float = 1.0    # Качество видео (битрейт/кодек)
    hdr: float = 1.0              # HDR
    audio: float = 1.0            # Аудио (кодек, каналы, Atmos)
    subtitles: float = 1.0        # Субтитры
    compatibility: float = 1.0    # Совместимость с устройствами
    file_size: float = 0.0        # Штраф за размер (0 = не учитывать, >0 = меньше лучше)

    def as_dict(self) -> dict[str, float]:
        return {
            "resolution": self.resolution,
            "video_quality": self.video_quality,
            "hdr": self.hdr,
            "audio": self.audio,
            "subtitles": self.subtitles,
            "compatibility": self.compatibility,
            "file_size": self.file_size,
        }


# Предустановленные профили
PROFILES: dict[str, tuple[str, ScoringWeights]] = {
    "default": (
        "Сбалансированный (по умолчанию)",
        ScoringWeights(),
    ),
    "quality": (
        "Максимальное качество — разрешение, битрейт и HDR в приоритете",
        ScoringWeights(
            resolution=1.5,
            video_quality=1.5,
            hdr=1.5,
            audio=1.2,
            compatibility=0.3,
            file_size=0.0,
        ),
    ),
    "atmos": (
        "Приоритет аудио — Dolby Atmos, DTS:X, lossless, многоканальность",
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
        "Минимальный размер при приемлемом качестве",
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
        "Android TV / Homatics / Kodi — совместимость в приоритете",
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
        "Универсальный — максимальная совместимость со всеми устройствами",
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
        "Видеофил — максимум всего: 4K HDR Atmos lossless, размер не важен",
        ScoringWeights(
            resolution=1.5,
            video_quality=1.5,
            hdr=2.0,
            audio=2.0,
            subtitles=1.0,
            compatibility=0.2,
            file_size=0.0,
        ),
    ),
}


def get_profile(name: str) -> ScoringWeights:
    """Получить профиль по имени."""
    if name not in PROFILES:
        available = ", ".join(PROFILES.keys())
        raise ValueError(f"Неизвестный профиль '{name}'. Доступные: {available}")
    return PROFILES[name][1]


def parse_custom_weights(weights_str: str) -> ScoringWeights:
    """Разобрать строку кастомных весов вида 'resolution=1.5,audio=2.0,...'."""
    w = ScoringWeights()
    for pair in weights_str.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"Неверный формат веса: '{pair}'. Ожидается: ключ=значение")
        key, val = pair.split("=", 1)
        key = key.strip()
        val = val.strip()
        if not hasattr(w, key):
            valid = ", ".join(w.as_dict().keys())
            raise ValueError(f"Неизвестный вес '{key}'. Доступные: {valid}")
        try:
            setattr(w, key, float(val))
        except ValueError:
            raise ValueError(f"Неверное значение для '{key}': '{val}'")
    return w


def list_profiles() -> str:
    """Список доступных профилей для справки."""
    lines = ["Доступные профили приоритетов:", ""]
    for name, (desc, weights) in PROFILES.items():
        lines.append(f"  {name:15s} — {desc}")
        w = weights.as_dict()
        non_default = {k: v for k, v in w.items() if v != 1.0}
        if non_default:
            parts = [f"{k}={v}" for k, v in non_default.items()]
            lines.append(f"  {'':15s}   веса: {', '.join(parts)}")
    lines.append("")
    lines.append("Кастомные веса: --weights 'resolution=1.5,audio=2.0,file_size=1.0'")
    lines.append("Доступные ключи: resolution, video_quality, hdr, audio, subtitles, "
                  "compatibility, file_size")
    return "\n".join(lines)
