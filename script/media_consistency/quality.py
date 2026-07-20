from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .validation import MediaQualityMetrics


@dataclass(frozen=True)
class QualityAnalysisCommands:
    loudness: tuple[str, ...]
    black_frames: tuple[str, ...]
    freeze_frames: tuple[str, ...]
    decode: tuple[str, ...]


def build_quality_analysis_commands(
    path: str | Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
) -> QualityAnalysisCommands:
    """Return read-only analysis commands used to populate final quality metrics."""
    source = str(path)
    common = (ffmpeg_bin, "-hide_banner", "-nostats", "-v", "info", "-i", source)
    return QualityAnalysisCommands(
        loudness=common
        + ("-map", "0:a:0", "-af", "loudnorm=print_format=json", "-f", "null", "-"),
        black_frames=common
        + ("-map", "0:v:0", "-vf", "blackdetect=d=0.10:pic_th=0.98", "-an", "-f", "null", "-"),
        freeze_frames=common
        + ("-map", "0:v:0", "-vf", "freezedetect=n=-60dB:d=0.5", "-an", "-f", "null", "-"),
        decode=(
            ffmpeg_bin,
            "-hide_banner",
            "-v",
            "error",
            "-xerror",
            "-i",
            source,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-f",
            "null",
            "-",
        ),
    )


def _last_loudnorm_json(output: str) -> dict:
    candidates = re.findall(r"\{[^{}]*\}", output, flags=re.DOTALL)
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if "input_i" in parsed or "input_tp" in parsed:
            return parsed
    return {}


def _optional_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def parse_quality_analysis_outputs(
    *,
    duration_seconds: float,
    loudness_output: str,
    black_frames_output: str,
    freeze_frames_output: str,
    decode_returncode: int,
) -> MediaQualityMetrics:
    """Parse stderr/stdout captured from :func:`build_quality_analysis_commands`."""
    duration = max(0.0, float(duration_seconds))
    loudness = _last_loudnorm_json(loudness_output)
    black_seconds = sum(
        float(value)
        for value in re.findall(r"black_duration:([0-9]+(?:\.[0-9]+)?)", black_frames_output)
    )
    freeze_seconds = sum(
        float(value)
        for value in re.findall(r"freeze_duration:\s*([0-9]+(?:\.[0-9]+)?)", freeze_frames_output)
    )
    return MediaQualityMetrics(
        integrated_lufs=_optional_float(loudness.get("input_i")),
        true_peak_dbfs=_optional_float(loudness.get("input_tp")),
        black_frame_ratio=min(1.0, black_seconds / duration) if duration else None,
        freeze_frame_ratio=min(1.0, freeze_seconds / duration) if duration else None,
        decode_errors=0 if int(decode_returncode) == 0 else 1,
    )
