from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


PROFILE_VERSION = "media-profile-v1"
_RESOLUTION_HEIGHTS = {"720p": 720, "1080p": 1080, "4k": 2160}


def _even(value: float) -> int:
    rounded = max(2, int(round(value)))
    return rounded if rounded % 2 == 0 else rounded + 1


def _parse_aspect(value: str | tuple[int, int] | None) -> tuple[int, int]:
    if value is None:
        return 16, 9
    if isinstance(value, tuple) and len(value) == 2:
        left, right = int(value[0]), int(value[1])
    else:
        normalized = str(value).strip().lower()
        aliases = {
            "landscape": (16, 9),
            "horizontal": (16, 9),
            "portrait": (9, 16),
            "vertical": (9, 16),
            "square": (1, 1),
        }
        if normalized in aliases:
            return aliases[normalized]
        match = re.fullmatch(r"(\d+)\s*[:/]\s*(\d+)", normalized)
        if not match:
            raise ValueError(f"Unsupported aspect ratio: {value!r}")
        left, right = int(match.group(1)), int(match.group(2))
    if left <= 0 or right <= 0:
        raise ValueError("Aspect ratio values must be positive")
    return left, right


def _auto_resolution(duration_seconds: float) -> str:
    if duration_seconds <= 30:
        return "720p"
    if duration_seconds <= 90:
        return "1080p"
    return "720p"


def _resolve_dimensions(
    resolution: str | tuple[int, int] | None,
    aspect: tuple[int, int],
    duration_seconds: float,
) -> tuple[int, int, str]:
    if isinstance(resolution, tuple) and len(resolution) == 2:
        width, height = _even(resolution[0]), _even(resolution[1])
        return width, height, f"{width}x{height}"

    label = str(resolution or "auto").strip().lower()
    explicit = re.fullmatch(r"(\d+)x(\d+)", label)
    if explicit:
        width, height = _even(int(explicit.group(1))), _even(int(explicit.group(2)))
        return width, height, f"{width}x{height}"
    if label == "auto":
        label = _auto_resolution(duration_seconds)
    if label not in _RESOLUTION_HEIGHTS:
        raise ValueError(f"Unsupported output resolution: {resolution!r}")

    base_height = _RESOLUTION_HEIGHTS[label]
    aspect_width, aspect_height = aspect
    if aspect_width >= aspect_height:
        height = base_height
        width = _even(height * aspect_width / aspect_height)
    else:
        width = base_height
        height = _even(width * aspect_height / aspect_width)
    return _even(width), _even(height), label


@dataclass(frozen=True)
class MediaProfile:
    version: str
    target_duration_seconds: float
    width: int
    height: int
    resolution: str
    aspect_ratio: str
    fit_mode: str = "blur_fill"
    fps: int = 30
    video_codec: str = "libx264"
    video_codec_name: str = "h264"
    pixel_format: str = "yuv420p"
    color_primaries: str = "bt709"
    color_transfer: str = "bt709"
    color_space: str = "bt709"
    sample_aspect_ratio: str = "1:1"
    audio_codec: str = "aac"
    audio_sample_rate: int = 48_000
    audio_channels: int = 2
    clip_loudness_lufs: float = -18.0
    final_loudness_lufs: float = -16.0
    true_peak_dbfs: float = -1.5
    loudness_range: float = 11.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_media_profile(
    target_duration_seconds: float,
    *,
    user_resolution: str | tuple[int, int] | None = "auto",
    target_aspect: str | tuple[int, int] | None = "16:9",
    fit_mode: str = "blur_fill",
) -> MediaProfile:
    """Resolve the versioned canonical output contract for one task.

    An explicit resolution always wins over the duration-based default.  Explicit
    dimensions are preserved (rounded up to encoder-safe even values).
    """
    duration = float(target_duration_seconds)
    if duration <= 0:
        raise ValueError("target_duration_seconds must be positive")
    normalized_fit = str(fit_mode).strip().lower()
    if normalized_fit not in {"blur_fill", "cover"}:
        raise ValueError("fit_mode must be 'blur_fill' or 'cover'")
    aspect = _parse_aspect(target_aspect)
    width, height, resolution = _resolve_dimensions(
        user_resolution, aspect, duration
    )
    return MediaProfile(
        version=PROFILE_VERSION,
        target_duration_seconds=duration,
        width=width,
        height=height,
        resolution=resolution,
        aspect_ratio=f"{aspect[0]}:{aspect[1]}",
        fit_mode=normalized_fit,
    )
