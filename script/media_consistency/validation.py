from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .probe import MediaProbe
from .profile import MediaProfile


@dataclass(frozen=True)
class MediaQualityMetrics:
    integrated_lufs: float | None = None
    true_peak_dbfs: float | None = None
    black_frame_ratio: float | None = None
    freeze_frame_ratio: float | None = None
    decode_errors: int | None = None


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class MediaValidationResult:
    passed: bool
    issues: tuple[ValidationIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": [asdict(issue) for issue in self.issues],
        }


def _issue(issues: list[ValidationIssue], code: str, message: str) -> None:
    issues.append(ValidationIssue(code=code, message=message))


def validate_technical_media(
    probe: MediaProbe,
    profile: MediaProfile,
    *,
    duration_tolerance_seconds: float | None = None,
) -> MediaValidationResult:
    issues: list[ValidationIssue] = []
    if (probe.width, probe.height) != (profile.width, profile.height):
        _issue(
            issues,
            "resolution_mismatch",
            f"expected {profile.width}x{profile.height}, got {probe.width}x{probe.height}",
        )
    if abs(probe.average_fps - profile.fps) > 0.05 or probe.is_variable_frame_rate:
        _issue(issues, "frame_rate_mismatch", f"expected CFR {profile.fps} fps")
    if probe.sample_aspect_ratio not in {"1:1", "1/1"}:
        _issue(issues, "sample_aspect_ratio_mismatch", "expected SAR 1:1")
    if probe.video_codec != profile.video_codec_name:
        _issue(issues, "video_codec_mismatch", f"expected {profile.video_codec_name}")
    if probe.pixel_format != profile.pixel_format:
        _issue(issues, "pixel_format_mismatch", f"expected {profile.pixel_format}")
    expected_colors = {
        "color_primaries": (probe.color_primaries, profile.color_primaries),
        "color_transfer": (probe.color_transfer, profile.color_transfer),
        "color_space": (probe.color_space, profile.color_space),
    }
    for field, (actual, expected) in expected_colors.items():
        if actual != expected:
            _issue(issues, f"{field}_mismatch", f"expected {expected}, got {actual}")
    if not probe.has_audio:
        _issue(issues, "audio_missing", "canonical output requires an audio stream")
    else:
        if probe.audio_codec != profile.audio_codec:
            _issue(issues, "audio_codec_mismatch", f"expected {profile.audio_codec}")
        if probe.audio_sample_rate != profile.audio_sample_rate:
            _issue(issues, "audio_sample_rate_mismatch", f"expected {profile.audio_sample_rate} Hz")
        if probe.audio_channels != profile.audio_channels:
            _issue(issues, "audio_channels_mismatch", f"expected {profile.audio_channels} channels")
    tolerance = (
        float(duration_tolerance_seconds)
        if duration_tolerance_seconds is not None
        else max(0.75, profile.target_duration_seconds * 0.03)
    )
    if abs(probe.duration_seconds - profile.target_duration_seconds) > tolerance:
        _issue(
            issues,
            "duration_mismatch",
            f"expected {profile.target_duration_seconds:.3f}s ± {tolerance:.3f}s, got {probe.duration_seconds:.3f}s",
        )
    return MediaValidationResult(passed=not issues, issues=tuple(issues))


def validate_final_media(
    probe: MediaProbe,
    profile: MediaProfile,
    quality: MediaQualityMetrics | None,
    *,
    loudness_tolerance_lu: float = 1.0,
    max_black_frame_ratio: float = 0.02,
    max_freeze_frame_ratio: float = 0.05,
    duration_tolerance_seconds: float | None = None,
) -> MediaValidationResult:
    technical = validate_technical_media(
        probe,
        profile,
        duration_tolerance_seconds=duration_tolerance_seconds,
    )
    issues = list(technical.issues)
    if quality is None:
        _issue(issues, "quality_metrics_missing", "final validation requires measured quality metrics")
        return MediaValidationResult(False, tuple(issues))

    required = {
        "integrated_lufs": quality.integrated_lufs,
        "true_peak_dbfs": quality.true_peak_dbfs,
        "black_frame_ratio": quality.black_frame_ratio,
        "freeze_frame_ratio": quality.freeze_frame_ratio,
        "decode_errors": quality.decode_errors,
    }
    for name, value in required.items():
        if value is None:
            _issue(issues, f"{name}_missing", f"quality metric {name} was not measured")
    if quality.integrated_lufs is not None and abs(
        quality.integrated_lufs - profile.final_loudness_lufs
    ) > loudness_tolerance_lu:
        _issue(issues, "loudness_out_of_range", "integrated loudness is outside tolerance")
    if quality.true_peak_dbfs is not None and quality.true_peak_dbfs > profile.true_peak_dbfs:
        _issue(issues, "true_peak_too_high", "true peak exceeds profile maximum")
    if quality.black_frame_ratio is not None and quality.black_frame_ratio > max_black_frame_ratio:
        _issue(issues, "excessive_black_frames", "black-frame ratio exceeds threshold")
    if quality.freeze_frame_ratio is not None and quality.freeze_frame_ratio > max_freeze_frame_ratio:
        _issue(issues, "excessive_freeze_frames", "freeze-frame ratio exceeds threshold")
    if quality.decode_errors is not None and quality.decode_errors > 0:
        _issue(issues, "decode_errors", "media contains decode errors")
    return MediaValidationResult(passed=not issues, issues=tuple(issues))
