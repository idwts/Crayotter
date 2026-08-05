from __future__ import annotations

import math
from pathlib import Path
from typing import Any


VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
ARTIFACT_PRODUCING_TOOLS = {
    "cut_video",
    "batch_cut_video",
    "merge_videos",
    "build_edit_timeline_from_segments",
    "add_transition",
    "add_subtitles",
    "add_narration",
    "add_narration_segments",
    "duck_background_audio",
    "normalize_loudness",
    "export_video",
}


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _event_stage(event: dict[str, Any]) -> str:
    return str(event.get("stage") or "other")


def _output_paths(event: dict[str, Any]) -> list[str]:
    value = event.get("output_paths")
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def build_contiguous_segments(tool_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse consecutive tool events in the same editing stage.

    A segment is the smallest reward unit used by PPO. It intentionally stays
    above individual tool calls while preserving revisits to a stage as new
    segments, which is important for revision and repair trajectories.
    """

    segments: list[dict[str, Any]] = []
    failed_tools: set[str] = set()
    current: dict[str, Any] | None = None

    for event_index, raw_event in enumerate(tool_events):
        event = _as_dict(raw_event)
        stage = _event_stage(event)
        if current is None or current["stage"] != stage:
            current = {
                "segment_id": f"segment_{len(segments):03d}",
                "segment_index": len(segments),
                "stage": stage,
                "start_event_index": event_index,
                "end_event_index": event_index,
                "event_indices": [],
                "call_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "artifact_count": 0,
                "artifact_paths": [],
                "video_artifact_count": 0,
                "duration_observation_count": 0,
                "repair_success_count": 0,
                "unique_tools": [],
                "tools": {},
                "local_step_total": 0.0,
                "allocated_rule_residual": 0.0,
                "allocated_preference_credit": 0.0,
                "segment_reward_total": 0.0,
            }
            segments.append(current)

        tool_name = str(event.get("tool_name") or "")
        paths = _output_paths(event) if tool_name in ARTIFACT_PRODUCING_TOOLS else []
        current["end_event_index"] = event_index
        current["event_indices"].append(event_index)
        current["call_count"] += 1
        current["local_step_total"] += _finite_float(event.get("step_reward"))
        current["artifact_count"] += len(paths)
        current["artifact_paths"].extend(path for path in paths if path not in current["artifact_paths"])
        current["video_artifact_count"] += sum(
            1 for item in paths if Path(item).suffix.lower() in VIDEO_SUFFIXES
        )
        if _finite_float(event.get("duration_seconds")) > 0:
            current["duration_observation_count"] += 1

        if bool(event.get("success")):
            current["success_count"] += 1
            if tool_name and tool_name in failed_tools:
                current["repair_success_count"] += 1
        else:
            current["failure_count"] += 1
            if tool_name:
                failed_tools.add(tool_name)
        if tool_name:
            current["tools"][tool_name] = int(current["tools"].get(tool_name, 0)) + 1

    for segment in segments:
        segment["unique_tools"] = sorted(segment["tools"])
        segment["local_step_total"] = round(_finite_float(segment["local_step_total"]), 6)
    return segments


def compute_segment_credit(
    tool_events: list[dict[str, Any]],
    stage_credit: dict[str, Any],
    total_reward: float,
) -> dict[str, Any]:
    """Allocate rule return to contiguous semantic segments.

    Final-video preference credit is added later by the lagged learned allocator.
    The segment rule rewards always conserve the scalar episode return.
    """

    segments = build_contiguous_segments(tool_events)
    if not segments:
        terminal_segment = {
            "segment_id": "segment_terminal_no_action",
            "segment_index": 0,
            "stage": "policy_terminal",
            "start_event_index": 0,
            "end_event_index": 0,
            "event_indices": [],
            "call_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "artifact_count": 0,
            "artifact_paths": [],
            "video_artifact_count": 0,
            "duration_observation_count": 0,
            "repair_success_count": 0,
            "unique_tools": [],
            "tools": {},
            "local_step_total": 0.0,
            "allocated_rule_residual": round(_finite_float(total_reward), 6),
            "allocated_preference_credit": 0.0,
            "segment_reward_total": round(_finite_float(total_reward), 6),
            "terminal_behavior": "no_tool_call",
        }
        return {
            "strategy": "terminal_policy_segment",
            "segments": [terminal_segment],
            "segment_count": 1,
            "rule_reward_sum": round(_finite_float(total_reward), 6),
            "preference_credit_sum": 0.0,
        }

    stage_items = _as_dict(stage_credit.get("stages"))
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for segment in segments:
        by_stage.setdefault(str(segment["stage"]), []).append(segment)

    for stage, stage_segments in by_stage.items():
        stage_info = _as_dict(stage_items.get(stage))
        residual = _finite_float(stage_info.get("allocated_rule_residual"))
        positive_mass = sum(max(0.0, _finite_float(item["local_step_total"])) for item in stage_segments)
        count_mass = sum(max(1, int(item["call_count"])) for item in stage_segments)
        for segment in stage_segments:
            if positive_mass > 0:
                share = max(0.0, _finite_float(segment["local_step_total"])) / positive_mass
            else:
                share = max(1, int(segment["call_count"])) / max(1, count_mass)
            allocation = residual * share
            segment["allocated_rule_residual"] = round(allocation, 6)
            segment["segment_reward_total"] = round(
                _finite_float(segment["local_step_total"]) + allocation,
                6,
            )

    drift = _finite_float(total_reward) - sum(
        _finite_float(segment["segment_reward_total"]) for segment in segments
    )
    if abs(drift) > 1e-9:
        segments[-1]["allocated_rule_residual"] = round(
            _finite_float(segments[-1]["allocated_rule_residual"]) + drift,
            6,
        )
        segments[-1]["segment_reward_total"] = round(
            _finite_float(segments[-1]["segment_reward_total"]) + drift,
            6,
        )

    return {
        "strategy": "contiguous_stage_segment_end_reward",
        "note": (
            "Rule return is conserved across contiguous editing segments. "
            "A frozen learned allocator may later add zero-sum preference credit."
        ),
        "segments": segments,
        "segment_count": len(segments),
        "rule_reward_sum": round(
            sum(_finite_float(item["segment_reward_total"]) for item in segments),
            6,
        ),
        "preference_credit_sum": 0.0,
    }
