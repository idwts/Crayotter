"""Pure helpers for keeping model-produced timelines inside source media bounds."""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def sample_timeline_segments(
    segments: list[dict[str, Any]],
    limit: int,
    duration_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Select chronologically distributed segments instead of a prefix."""
    valid: list[dict[str, Any]] = []
    for item in segments:
        if not isinstance(item, dict):
            continue
        start = _finite_number(item.get("start"))
        end = _finite_number(item.get("end"))
        if start is None or end is None or end <= start:
            continue
        valid.append(dict(item))
    valid.sort(key=lambda item: (float(item["start"]), float(item["end"])))

    count = max(0, int(limit))
    if count == 0 or not valid:
        return []
    if len(valid) <= count:
        return valid

    duration = _finite_number(duration_seconds)
    if duration is None or duration <= 0:
        duration = max(float(item["end"]) for item in valid)
    if count == 1:
        target = duration / 2
        return [
            min(
                valid,
                key=lambda item: abs(
                    (float(item["start"]) + float(item["end"])) / 2 - target
                ),
            )
        ]

    remaining = list(valid)
    selected: list[dict[str, Any]] = []
    for index in range(count):
        target = duration * index / (count - 1)
        closest = min(
            remaining,
            key=lambda item: abs(
                (float(item["start"]) + float(item["end"])) / 2 - target
            ),
        )
        selected.append(closest)
        remaining.remove(closest)
    selected.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    return selected


def timeline_bucket_coverage_ratio(
    segments: list[dict[str, Any]],
    duration_seconds: float,
    bucket_count: int = 8,
) -> float:
    """Measure whether analyzed intervals are distributed across the source."""
    duration = _finite_number(duration_seconds)
    buckets = max(1, int(bucket_count))
    if duration is None or duration <= 0:
        return 0.0

    covered = [False] * buckets
    for item in segments:
        if not isinstance(item, dict):
            continue
        start = _finite_number(item.get("start"))
        end = _finite_number(item.get("end"))
        if start is None or end is None or end <= start:
            continue
        start = max(0.0, min(duration, start))
        end = max(0.0, min(duration, end))
        for index in range(buckets):
            bucket_start = duration * index / buckets
            bucket_end = duration * (index + 1) / buckets
            if start < bucket_end and end > bucket_start:
                covered[index] = True
    return round(sum(covered) / buckets, 3)


def timeline_covered_duration_seconds(
    segments: list[dict[str, Any]],
    duration_seconds: float | None = None,
) -> float:
    """Return the union duration of timeline segments without double-counting overlaps."""
    duration = _finite_number(duration_seconds)
    intervals: list[tuple[float, float]] = []
    for item in segments:
        if not isinstance(item, dict):
            continue
        start = _finite_number(item.get("start"))
        end = _finite_number(item.get("end"))
        if start is None or end is None or end <= start:
            continue
        start = max(0.0, start)
        if duration is not None and duration > 0:
            start = min(duration, start)
            end = min(duration, end)
        if end > start:
            intervals.append((start, end))
    if not intervals:
        return 0.0

    intervals.sort()
    covered = 0.0
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start > current_end:
            covered += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    return round(covered + current_end - current_start, 3)


def clamp_analysis_segments(
    segments: list[dict[str, Any]],
    duration_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Drop segments outside EOF and clamp partially overlapping segments."""
    duration = _finite_number(duration_seconds)
    if duration is None or duration <= 0:
        raise ValueError("source duration must be positive")

    bounded: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()
    dropped = 0
    clamped = 0
    for item in segments:
        if not isinstance(item, dict):
            dropped += 1
            continue
        start = _finite_number(item.get("start"))
        end = _finite_number(item.get("end"))
        if start is None or end is None or end <= start:
            dropped += 1
            continue

        bounded_start = max(0.0, start)
        bounded_end = min(duration, end)
        if bounded_start >= duration or bounded_end <= bounded_start:
            dropped += 1
            continue
        if bounded_start != start or bounded_end != end:
            clamped += 1

        normalized = dict(item)
        normalized["start"] = round(bounded_start, 3)
        normalized["end"] = round(bounded_end, 3)
        if "duration" in normalized:
            normalized["duration"] = round(bounded_end - bounded_start, 3)
        key = (normalized["start"], normalized["end"])
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        bounded.append(normalized)

    return bounded, {
        "input_count": len(segments),
        "output_count": len(bounded),
        "clamped_count": clamped,
        "dropped_count": dropped,
    }


def normalize_analysis_payload(
    payload: Mapping[str, Any],
    duration_seconds: float,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Return a copy whose structured timestamps cannot exceed the real source EOF."""
    normalized = deepcopy(dict(payload))
    aggregate = {
        "input_count": 0,
        "output_count": 0,
        "clamped_count": 0,
        "dropped_count": 0,
    }
    for field in ("segments", "semantic_segments"):
        raw = normalized.get(field, [])
        if not isinstance(raw, list):
            raw = []
        bounded, report = clamp_analysis_segments(raw, duration_seconds)
        normalized[field] = bounded
        for key in aggregate:
            aggregate[key] += report[key]

    normalized["source_duration_seconds"] = round(float(duration_seconds), 3)
    normalized["timeline_validation"] = {
        "status": "corrected"
        if aggregate["clamped_count"] or aggregate["dropped_count"]
        else "valid",
        "bucket_coverage_ratio": timeline_bucket_coverage_ratio(
            normalized.get("semantic_segments") or normalized.get("segments") or [],
            float(duration_seconds),
        ),
        **aggregate,
    }
    semantic_segments = normalized.get("semantic_segments", [])
    semantic_index = normalized.get("semantic_index")
    if isinstance(semantic_index, dict):
        semantic_index["segment_count"] = len(semantic_segments)
    return normalized, aggregate


def bounded_analysis_detail(payload: Mapping[str, Any]) -> str:
    """Render only validated timeline content for downstream LLM prompts."""
    segments = payload.get("semantic_segments") or payload.get("segments") or []
    if not isinstance(segments, list):
        return ""
    lines: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        start = segment.get("start")
        end = segment.get("end")
        description = str(
            segment.get("semantic_text")
            or segment.get("description")
            or segment.get("content")
            or ""
        ).strip()
        suffix = f"：{description}" if description else ""
        lines.append(f"t={start}s-t={end}s{suffix}")
    return "\n".join(lines)


__all__ = [
    "bounded_analysis_detail",
    "clamp_analysis_segments",
    "normalize_analysis_payload",
    "sample_timeline_segments",
    "timeline_bucket_coverage_ratio",
    "timeline_covered_duration_seconds",
]
