from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .horizon_metrics import requires_semantic_material_grounding
from .segment_credit import compute_segment_credit

from .tool_runtime import ToolExecutionResult

ARTIFACT_PRODUCING_TOOLS = {
    "add_background_music",
    "add_narration_segments",
    "add_subtitles",
    "add_transition",
    "batch_cut_video",
    "cut_video",
    "export_video",
    "merge_videos",
}
VIDEO_SUFFIXES = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}
TOOL_STAGE_MAP = {
    "analyze_video": "material_selection",
    "recall_semantic_segments": "material_selection",
    "inspect_video_duration": "validation",
    "batch_cut_video": "rough_cut",
    "cut_video": "rough_cut",
    "build_edit_timeline_from_segments": "timeline_ordering",
    "validate_timeline_constraints": "timeline_ordering",
    "plan_transition_timeline": "transition_pacing",
    "recommend_transition_for_cut": "transition_pacing",
    "score_cut_continuity": "transition_pacing",
    "add_transition": "transition_pacing",
    "align_narration_to_timeline": "subtitle_narration",
    "validate_narration_timeline": "subtitle_narration",
    "add_narration_segments": "subtitle_narration",
    "add_subtitles": "subtitle_narration",
    "duck_background_audio": "audio_mixing",
    "normalize_loudness": "audio_mixing",
    "merge_videos": "timeline_ordering",
    "export_video": "export_repair",
}
LONG_HORIZON_CORE_STAGES = {
    "rough_cut",
    "timeline_ordering",
    "export_repair",
    "validation",
}
QUALITY_CREDIT_STAGE_WEIGHTS = {
    "material_selection": 0.8,
    "rough_cut": 1.2,
    "timeline_ordering": 1.5,
    "transition_pacing": 1.2,
    "subtitle_narration": 1.0,
    "audio_mixing": 0.8,
    "export_repair": 0.4,
    "validation": 0.2,
}


@dataclass(slots=True)
class StepReward:
    total: float
    components: dict[str, float]


def classify_tool_stage(tool_name: str) -> str:
    return TOOL_STAGE_MAP.get(tool_name, "other")


def _canonical_args(arguments: dict[str, Any]) -> str:
    try:
        return json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(arguments)


def build_tool_signature(tool_name: str, arguments: dict[str, Any]) -> str:
    return f"{tool_name}:{_canonical_args(arguments)}"


def _existing_output_count(paths: list[str]) -> int:
    count = 0
    for raw_path in paths:
        try:
            path = Path(raw_path)
            if path.is_file() and path.stat().st_size > 0:
                count += 1
        except OSError:
            continue
    return count


def compute_step_reward(
    *,
    tool_name: str,
    execution: ToolExecutionResult,
    prior_events: list[dict[str, Any]],
) -> StepReward:
    signature = build_tool_signature(tool_name, execution.arguments)
    prior_signatures = [str(item.get("signature") or "") for item in prior_events]
    repeated_count = prior_signatures.count(signature)
    failed_same_tool = any(
        str(item.get("tool_name")) == tool_name and not bool(item.get("success"))
        for item in prior_events
    )
    valid_outputs = (
        _existing_output_count(execution.output_paths)
        if tool_name in ARTIFACT_PRODUCING_TOOLS
        else 0
    )

    components: dict[str, float] = {
        "tool_success": 0.08 if execution.success else -0.35,
        "artifact_bonus": min(0.08, 0.04 * valid_outputs),
        "returncode_penalty": -0.15 if execution.returncode != 0 else 0.0,
        "repeat_penalty": -min(0.45, 0.18 * repeated_count),
        "order_bonus": 0.0,
        "repair_bonus": 0.12 if execution.success and failed_same_tool else 0.0,
    }

    seen_tools = {str(item.get("tool_name")) for item in prior_events}
    if tool_name == "export_video":
        if "merge_videos" in seen_tools or "build_edit_timeline_from_segments" in seen_tools:
            components["order_bonus"] += 0.28
        elif "cut_video" in seen_tools or "batch_cut_video" in seen_tools:
            components["order_bonus"] += 0.16
        elif "inspect_video_duration" in seen_tools:
            components["order_bonus"] += 0.08
        else:
            components["order_bonus"] -= 0.12
    if tool_name == "add_narration_segments":
        if "validate_narration_timeline" in seen_tools:
            components["order_bonus"] += 0.08
        else:
            components["order_bonus"] -= 0.18
    if tool_name in {"merge_videos", "add_transition"}:
        cut_count = sum(
            1
            for item in prior_events
            if item.get("tool_name") in {"cut_video", "batch_cut_video"}
            and item.get("success")
        )
        if cut_count >= 2:
            components["order_bonus"] += 0.22
        elif cut_count == 1:
            components["order_bonus"] += 0.14
        elif cut_count == 0:
            components["order_bonus"] -= 0.08

    if tool_name == "inspect_video_duration":
        successful_inspects = sum(
            1
            for item in prior_events
            if item.get("tool_name") == "inspect_video_duration" and item.get("success")
        )
        has_cut = any(
            item.get("tool_name") in {"cut_video", "batch_cut_video"} and item.get("success")
            for item in prior_events
        )
        has_timeline = any(
            item.get("tool_name") in {"merge_videos", "build_edit_timeline_from_segments"} and item.get("success")
            for item in prior_events
        )
        if successful_inspects >= 4 and not has_cut:
            components["progress_stall_penalty"] = -0.12 * (successful_inspects - 3)
        elif successful_inspects >= 2 and has_cut and not has_timeline:
            components["progress_stall_penalty"] = -0.1

    upper = 0.55 if tool_name in {"merge_videos", "export_video"} else 0.3
    total = round(max(-0.75, min(upper, sum(components.values()))), 4)
    return StepReward(total=total, components=components)


def _event_paths(event: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    parsed = event.get("parsed_result")
    if isinstance(parsed, dict):
        for key in ("final_path", "output_path", "video_path", "path"):
            value = str(parsed.get(key) or "").strip()
            if value:
                paths.append(value)
    paths.extend(
        str(item)
        for item in event.get("output_paths", [])
        if str(item).strip()
    )
    return list(dict.fromkeys(paths))


def find_final_video_path(tool_events: list[dict[str, Any]]) -> str:
    for event in reversed(tool_events):
        if event.get("tool_name") != "export_video" or not event.get("success"):
            continue
        arguments = event.get("arguments") or {}
        input_paths = {
            str(arguments.get(key) or "").strip()
            for key in ("input_path", "video_path", "source_path")
            if str(arguments.get(key) or "").strip()
        }
        for raw_path in _event_paths(event):
            path = Path(raw_path)
            if path.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            if any(_same_path_reference(raw_path, input_path) for input_path in input_paths):
                continue
            try:
                if path.is_file() and path.stat().st_size > 0:
                    return str(path.resolve())
            except OSError:
                continue
    return ""


def _same_path_reference(candidate: str, target: str) -> bool:
    candidate_path = Path(candidate)
    target_path = Path(target)
    if candidate_path.is_absolute() != target_path.is_absolute():
        return False
    try:
        return candidate_path.resolve(strict=False) == target_path.resolve(strict=False)
    except OSError:
        return str(candidate_path) == str(target_path)


def _path_matches(candidate: str, target: str) -> bool:
    if not candidate or not target:
        return False
    candidate_path = Path(candidate)
    target_path = Path(target)
    if candidate_path.name == target_path.name and (
        not candidate_path.is_absolute() or not target_path.is_absolute()
    ):
        return True
    try:
        return candidate_path.resolve(strict=False) == target_path.resolve(strict=False)
    except OSError:
        return candidate_path.name == target_path.name


def _final_duration(
    tool_events: list[dict[str, Any]],
    final_video_path: str,
) -> float | None:
    export_index = -1
    for index, event in enumerate(tool_events):
        if event.get("tool_name") == "export_video" and event.get("success"):
            export_index = index

    for event in reversed(tool_events[export_index + 1 :]):
        if event.get("tool_name") != "inspect_video_duration" or not event.get("success"):
            continue
        inspected_path = str((event.get("arguments") or {}).get("video_path") or "")
        if final_video_path and not _path_matches(inspected_path, final_video_path):
            continue
        duration = event.get("duration_seconds")
        if isinstance(duration, (int, float)) and duration > 0:
            return float(duration)

    if final_video_path:
        try:
            import cv2

            capture = cv2.VideoCapture(final_video_path)
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
            capture.release()
            if fps > 0 and frames > 0:
                return frames / fps
        except Exception:
            pass

    if export_index >= 0:
        export_duration = tool_events[export_index].get("duration_seconds")
        if isinstance(export_duration, (int, float)) and export_duration > 0:
            return float(export_duration)
    return None


def _repair_count(tool_events: list[dict[str, Any]]) -> int:
    failed_tools: set[str] = set()
    repaired_tools: set[str] = set()
    for event in tool_events:
        tool_name = str(event.get("tool_name") or "")
        if not tool_name:
            continue
        if event.get("success") and tool_name in failed_tools:
            repaired_tools.add(tool_name)
        elif not event.get("success"):
            failed_tools.add(tool_name)
    return len(repaired_tools)


def _argument_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _event_references(event: dict[str, Any], marker: str) -> bool:
    haystack = " ".join(
        [
            str(event.get("tool_name") or ""),
            _argument_text(event.get("arguments") or {}),
            _argument_text(event.get("parsed_result") or {}),
            _argument_text(event.get("output_paths") or []),
        ]
    ).lower()
    normalized_haystack = haystack.replace("\\\\", "/").replace("\\", "/")
    normalized_marker = marker.lower().replace("\\", "/")
    return normalized_marker in normalized_haystack


def _long_horizon_revision_reward(
    tool_events: list[dict[str, Any]],
    metadata: dict[str, Any],
    final_video_path: str,
) -> tuple[float, dict[str, float]]:
    if not metadata.get("long_horizon_task"):
        return 0.0, {}

    previous_available = bool(metadata.get("previous_version_available"))
    previous_target = str(metadata.get("previous_final_target") or "previous_versions").strip()
    previous_marker = previous_target or "previous_versions"
    inspected_previous = any(
        event.get("tool_name") == "inspect_video_duration"
        and event.get("success")
        and _event_references(event, previous_marker)
        for event in tool_events
    )
    reused_source_material = any(
        event.get("success")
        and event.get("tool_name") in {"recall_semantic_segments", "cut_video", "batch_cut_video"}
        and (
            _event_references(event, "user_temp/materials")
            or _event_references(event, "materials/")
        )
        for event in tool_events
    )
    exported_new_final = bool(final_video_path) and not (
        previous_available and previous_marker and previous_marker in final_video_path
    )
    inspected_final_after_export = False
    export_seen = False
    for event in tool_events:
        if event.get("tool_name") == "export_video" and event.get("success"):
            export_seen = True
            continue
        if export_seen and event.get("tool_name") == "inspect_video_duration" and event.get("success"):
            inspected_final_after_export = True

    components = {
        "revision_diagnosis": 0.18 if (not previous_available or inspected_previous) else -0.18,
        "source_material_reuse": 0.16 if reused_source_material else -0.08,
        "new_export_not_previous": 0.16 if exported_new_final else -0.2,
        "post_export_validation": 0.12 if inspected_final_after_export else -0.06,
    }
    return round(sum(components.values()), 4), components


def _env_float(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _successful_stage_set(tool_events: list[dict[str, Any]]) -> set[str]:
    return {
        str(event.get("stage") or classify_tool_stage(str(event.get("tool_name") or "")))
        for event in tool_events
        if event.get("success")
    }


def _has_successful_tool(tool_events: list[dict[str, Any]], names: set[str]) -> bool:
    return any(event.get("success") and event.get("tool_name") in names for event in tool_events)


def _has_post_export_inspection(tool_events: list[dict[str, Any]], final_video_path: str) -> bool:
    export_seen = False
    for event in tool_events:
        if event.get("tool_name") == "export_video" and event.get("success"):
            export_seen = True
            continue
        if not export_seen:
            continue
        if event.get("tool_name") != "inspect_video_duration" or not event.get("success"):
            continue
        inspected_path = str((event.get("arguments") or {}).get("video_path") or "")
        if not final_video_path or _path_matches(inspected_path, final_video_path):
            return True
    return False


def _progress_stall_penalty(tool_events: list[dict[str, Any]]) -> tuple[float, dict[str, float]]:
    successful_inspects = sum(
        1
        for event in tool_events
        if event.get("tool_name") == "inspect_video_duration" and event.get("success")
    )
    successful_cuts = sum(
        1
        for event in tool_events
        if event.get("tool_name") in {"cut_video", "batch_cut_video"} and event.get("success")
    )
    has_timeline = _has_successful_tool(tool_events, {"merge_videos", "build_edit_timeline_from_segments"})
    has_export = _has_successful_tool(tool_events, {"export_video"})

    inspect_without_cut = 0.0
    cut_without_timeline = 0.0
    timeline_without_export = 0.0
    if successful_inspects >= 5 and successful_cuts == 0:
        inspect_without_cut = -min(0.8, 0.12 * (successful_inspects - 4))
    if successful_cuts >= 1 and not has_timeline:
        cut_without_timeline = -min(0.75, 0.18 * successful_cuts)
    if has_timeline and not has_export:
        timeline_without_export = -0.35

    components = {
        "inspect_without_cut": round(inspect_without_cut, 4),
        "cut_without_timeline": round(cut_without_timeline, 4),
        "timeline_without_export": round(timeline_without_export, 4),
    }
    return round(sum(components.values()), 4), components


def _milestone_progress_reward(
    tool_events: list[dict[str, Any]],
    metadata: dict[str, Any],
    final_video_path: str,
) -> tuple[float, dict[str, float]]:
    if not metadata.get("long_horizon_task"):
        return 0.0, {}

    success_count = sum(1 for event in tool_events if event.get("success"))
    stages = _successful_stage_set(tool_events)
    previous_available = bool(metadata.get("previous_version_available"))
    previous_marker = str(metadata.get("previous_final_target") or "previous_versions").strip()
    inspected_previous = (
        not previous_available
        or any(
            event.get("tool_name") == "inspect_video_duration"
            and event.get("success")
            and _event_references(event, previous_marker or "previous_versions")
            for event in tool_events
        )
    )
    material_context = (
        "material_selection" in stages
        or inspected_previous
        or _has_successful_tool(tool_events, {"cut_video", "batch_cut_video"})
    )
    timeline_progress = bool(stages.intersection({"rough_cut", "timeline_ordering"}))
    rough_cut_progress = "rough_cut" in stages
    timeline_ordering_progress = "timeline_ordering" in stages
    continuity_progress = "transition_pacing" in stages
    narration_progress = "subtitle_narration" in stages
    export_attempt = any(event.get("tool_name") == "export_video" for event in tool_events)
    export_success = bool(final_video_path)
    post_export_validation = _has_post_export_inspection(tool_events, final_video_path)
    repair_progress = _repair_count(tool_events) > 0
    failed_calls = sum(1 for event in tool_events if not event.get("success"))

    min_success = _env_int("CRAYOTTER_RL_MIN_SUCCESSFUL_TOOLS", 4, minimum=1)
    components = {
        "trajectory_depth": 0.45 if success_count >= min_success else -0.35 * (min_success - success_count),
        "previous_or_material_context": 0.35 if material_context else -0.55,
        "revision_diagnosis": 0.3 if inspected_previous else -0.45,
        "timeline_or_rough_cut_progress": 0.45 if timeline_progress else -0.7,
        "rough_cut_progress": 0.35 if rough_cut_progress else -0.35,
        "timeline_ordering_progress": 0.55 if timeline_ordering_progress else -0.55,
        "continuity_or_pacing_progress": 0.18 if continuity_progress else -0.08,
        "narration_or_subtitle_progress": 0.16 if narration_progress else 0.0,
        "export_attempt": 0.7 if export_attempt else -0.75,
        "valid_new_export": 1.1 if export_success else -1.0,
        "post_export_validation": 0.5 if post_export_validation else -0.4,
        "repair_after_failure": 0.18 if repair_progress else 0.0,
        "failed_tool_penalty": -min(0.9, 0.18 * failed_calls),
    }
    _stall_penalty, stall_components = _progress_stall_penalty(tool_events)
    components.update(stall_components)
    return round(sum(components.values()), 4), components


def _medium_semantic_grounding_reward(
    tool_events: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    if not requires_semantic_material_grounding(metadata):
        return 0.0, {}
    grounding_tools = {"analyze_video", "recall_semantic_segments"}
    grounding_indices = [
        index
        for index, event in enumerate(tool_events)
        if event.get("success") and event.get("tool_name") in grounding_tools
    ]
    cut_indices = [
        index
        for index, event in enumerate(tool_events)
        if event.get("success") and event.get("tool_name") in {"cut_video", "batch_cut_video"}
    ]
    grounding_before_cut = bool(grounding_indices) and (
        not cut_indices or min(grounding_indices) < min(cut_indices)
    )
    grounded_sources = set()
    for event in tool_events:
        arguments = event.get("arguments")
        if (
            event.get("success")
            and event.get("tool_name") == "analyze_video"
            and isinstance(arguments, dict)
        ):
            grounded_sources.add(str(arguments.get("video_path") or ""))
    grounded_sources.discard("")
    components = {
        "semantic_material_grounding": 0.55 if grounding_indices else -0.75,
        "grounding_before_cut": 0.25 if grounding_before_cut else -0.3,
        "multi_candidate_grounding": 0.12 if len(grounded_sources) >= 2 else 0.0,
    }
    return round(sum(components.values()), 4), components


def _long_horizon_failure_cap(
    tool_events: list[dict[str, Any]],
    metadata: dict[str, Any],
    final_video_path: str,
) -> tuple[float | None, dict[str, Any]]:
    if not metadata.get("long_horizon_task"):
        return None, {}

    tool_count = len(tool_events)
    success_count = sum(1 for event in tool_events if event.get("success"))
    stages = _successful_stage_set(tool_events)
    min_success = _env_int("CRAYOTTER_RL_MIN_SUCCESSFUL_TOOLS", 4, minimum=1)
    required_stages = set(
        str(item).strip()
        for item in os.environ.get(
            "CRAYOTTER_RL_REQUIRED_CORE_STAGES",
            ",".join(sorted(LONG_HORIZON_CORE_STAGES)),
        ).split(",")
        if str(item).strip()
    )
    missing_stages = sorted(required_stages - stages)

    if tool_count == 0:
        return _env_float("CRAYOTTER_RL_NO_TOOL_CAP", -5.0, maximum=-0.1), {
            "reason": "no_tool_call",
            "missing_stages": missing_stages,
        }
    if success_count < 2:
        return _env_float("CRAYOTTER_RL_SHALLOW_TRAJECTORY_CAP", -4.5, maximum=-0.1), {
            "reason": "too_few_successful_tools",
            "success_count": success_count,
            "missing_stages": missing_stages,
        }
    if success_count < min_success:
        return _env_float("CRAYOTTER_RL_MIN_TOOL_CAP", -3.6, maximum=-0.1), {
            "reason": "below_min_successful_tools",
            "success_count": success_count,
            "required_success_count": min_success,
            "missing_stages": missing_stages,
        }
    if not final_video_path and required_stages.intersection(missing_stages):
        return _env_float("CRAYOTTER_RL_MISSING_CORE_STAGE_CAP", -3.2, maximum=-0.1), {
            "reason": "missing_core_stages_without_export",
            "missing_stages": missing_stages,
        }
    if not final_video_path:
        return _env_float("CRAYOTTER_RL_NO_EXPORT_CAP", -2.8, maximum=-0.1), {
            "reason": "no_valid_export",
            "missing_stages": missing_stages,
        }
    return None, {"missing_stages": missing_stages}


def _tool_call_bootstrap_reward(
    tool_events: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    if not metadata.get("tool_call_bootstrap"):
        return None

    required_tool = str(metadata.get("bootstrap_tool_name") or "").strip()
    successful_events = [event for event in tool_events if event.get("success")]
    required_success = any(
        event.get("success") and (not required_tool or event.get("tool_name") == required_tool)
        for event in tool_events
    )
    failed_calls = sum(1 for event in tool_events if not event.get("success"))
    excess_calls = max(0, len(tool_events) - 1)
    components = {
        "any_tool_call": 0.6 if tool_events else -1.0,
        "required_tool_success": 1.0 if required_success else -0.6,
        "tool_success_count": min(0.6, 0.2 * len(successful_events)),
        "failed_call_penalty": -min(0.6, 0.2 * failed_calls),
        "single_call_bonus": 0.2 if len(tool_events) == 1 and required_success else 0.0,
        "excess_call_penalty": -min(0.4, 0.1 * excess_calls),
    }
    total = round(sum(components.values()), 4)
    return {
        "total_reward": total,
        "rule_reward": total,
        "step_total": round(sum(float(item.get("step_reward", 0.0)) for item in tool_events), 4),
        "raw_step_total": round(sum(float(item.get("step_reward", 0.0)) for item in tool_events), 4),
        "reported_export_success": False,
        "export_success": False,
        "export_reward": 0.0,
        "artifact_validity_reward": 0.0,
        "duration_reward": 0.0,
        "completion_bonus": 0.0,
        "repair_bonus": 0.0,
        "failure_penalty": components["failed_call_penalty"],
        "efficiency_penalty": components["excess_call_penalty"],
        "long_horizon_reward": 0.0,
        "long_horizon_components": {},
        "tool_call_bootstrap": True,
        "tool_call_bootstrap_components": components,
        "required_tool": required_tool,
        "tool_event_count": len(tool_events),
        "successful_tool_event_count": len(successful_events),
        "final_video_path": "",
        "final_duration_seconds": None,
        "duration_error_ratio": None,
        "judge_applied": False,
        "judge_reward": 0.0,
        "judge_weight": 0.0,
        "judge": {},
        "stage_credit": compute_stage_credit(tool_events, total),
    }


def _quality_stage_weight(stage: str, item: dict[str, Any]) -> float:
    call_count = float(item.get("call_count", 0) or 0)
    if call_count <= 0:
        return 0.0
    stage_weight = QUALITY_CREDIT_STAGE_WEIGHTS.get(stage, 0.15)
    success_ratio = float(item.get("success_count", 0) or 0) / max(1.0, call_count)
    failure_ratio = float(item.get("failure_count", 0) or 0) / max(1.0, call_count)
    artifact_count = float(item.get("artifact_count", 0) or 0)
    video_artifact_count = float(item.get("video_artifact_count", 0) or 0)
    duration_observations = float(item.get("duration_observation_count", 0) or 0)
    repair_success = float(item.get("repair_success_count", 0) or 0)
    unique_tools = float(item.get("unique_tool_count", 0) or 0)
    artifact_multiplier = 1.0
    artifact_multiplier += min(0.9, 0.1 * artifact_count + 0.18 * video_artifact_count)
    artifact_multiplier += min(0.35, 0.08 * duration_observations + 0.12 * repair_success)
    artifact_multiplier += min(0.25, 0.06 * unique_tools)
    artifact_multiplier *= max(0.25, 1.0 - 0.5 * failure_ratio)
    return max(0.0, stage_weight * (0.3 + 0.7 * success_ratio) * artifact_multiplier)


def _quality_credit_allocation(
    stages: dict[str, dict[str, Any]],
    quality_credit_reward: float,
) -> dict[str, float]:
    if not stages or abs(quality_credit_reward) <= 1e-8:
        return {stage: 0.0 for stage in stages}

    max_abs = _env_float("CRAYOTTER_RL_JUDGE_CREDIT_MAX_ABS", 1.0, minimum=0.0, maximum=3.0)
    signed_mass = max(-max_abs, min(max_abs, quality_credit_reward))
    target_weights = {
        stage: _quality_stage_weight(stage, item)
        for stage, item in stages.items()
        if item.get("call_count", 0) > 0
    }
    target_mass = sum(target_weights.values())
    call_mass = sum(float(item.get("call_count", 0)) for item in stages.values())
    if target_mass <= 0 or call_mass <= 0:
        return {stage: 0.0 for stage in stages}

    allocation: dict[str, float] = {}
    for stage, item in stages.items():
        target_share = target_weights.get(stage, 0.0) / target_mass
        baseline_share = float(item.get("call_count", 0)) / call_mass
        allocation[stage] = signed_mass * (target_share - baseline_share)
    drift = sum(allocation.values())
    if abs(drift) > 1e-10:
        anchor = max(allocation, key=lambda key: abs(allocation[key]))
        allocation[anchor] -= drift
    return allocation

def compute_stage_credit(
    tool_events: list[dict[str, Any]],
    total_reward: float,
    *,
    quality_credit_reward: float = 0.0,
    quality_credit_enabled: bool = False,
) -> dict[str, Any]:
    stages: dict[str, dict[str, Any]] = {}
    failed_tools: set[str] = set()
    for event_index, event in enumerate(tool_events):
        stage = str(event.get("stage") or classify_tool_stage(str(event.get("tool_name") or "")))
        item = stages.setdefault(stage, {
            "step_total": 0.0,
            "allocated_rule_residual": 0.0,
            "allocated_quality_credit": 0.0,
            "allocated_outcome_residual": 0.0,
            "stage_reward_total": 0.0,
            "call_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "artifact_count": 0,
            "video_artifact_count": 0,
            "duration_observation_count": 0,
            "repair_success_count": 0,
            "first_event_index": event_index,
            "last_event_index": event_index,
            "tools": {},
        })
        step_reward = float(event.get("step_reward", 0.0) or 0.0)
        item["step_total"] += step_reward
        item["call_count"] += 1
        item["last_event_index"] = event_index
        tool_name = str(event.get("tool_name") or "")
        output_paths = [str(path) for path in event.get("output_paths", []) if str(path).strip()]
        item["artifact_count"] += _existing_output_count(output_paths)
        item["video_artifact_count"] += sum(1 for raw_path in output_paths if Path(raw_path).suffix.lower() in VIDEO_SUFFIXES)
        if isinstance(event.get("duration_seconds"), (int, float)) and float(event.get("duration_seconds") or 0) > 0:
            item["duration_observation_count"] += 1
        if event.get("success"):
            item["success_count"] += 1
            if tool_name in failed_tools:
                item["repair_success_count"] += 1
        else:
            item["failure_count"] += 1
            if tool_name:
                failed_tools.add(tool_name)
        if tool_name:
            item["tools"][tool_name] = int(item["tools"].get(tool_name, 0)) + 1

    for item in stages.values():
        item["unique_tool_count"] = len(item["tools"])
        item["event_span"] = int(item["last_event_index"]) - int(item["first_event_index"]) + 1

    raw_step_total = sum(float(item["step_total"]) for item in stages.values())
    bounded_step_total = max(-1.5, min(1.5, raw_step_total))
    residual = float(total_reward) - bounded_step_total
    positive_mass = sum(max(0.0, float(item["step_total"])) for item in stages.values())
    count_mass = sum(float(item["call_count"]) for item in stages.values())
    quality_allocation = _quality_credit_allocation(stages, quality_credit_reward) if quality_credit_enabled else {stage: 0.0 for stage in stages}

    for stage, item in stages.items():
        if positive_mass > 0:
            weight = max(0.0, float(item["step_total"])) / positive_mass
        elif count_mass > 0:
            weight = float(item["call_count"]) / count_mass
        else:
            weight = 0.0
        rule_allocation = residual * weight
        quality_allocation_value = quality_allocation.get(stage, 0.0)
        item["step_total"] = round(float(item["step_total"]), 4)
        item["allocated_rule_residual"] = round(rule_allocation, 4)
        item["allocated_quality_credit"] = round(quality_allocation_value, 4)
        item["allocated_outcome_residual"] = round(rule_allocation + quality_allocation_value, 4)
        item["stage_reward_total"] = round(item["step_total"] + item["allocated_outcome_residual"], 4)
        item["dynamic_quality_weight"] = round(_quality_stage_weight(stage, item), 6)

    return {
        "strategy": "step_reward_plus_dynamic_artifact_quality_guided_outcome_residual" if quality_credit_enabled else "step_reward_plus_proportional_outcome_residual",
        "note": "This attribution is used by the process reward manager. Judge credit-only mode redistributes final-video quality across stages without changing total_reward. Dynamic fields expose artifact deltas for group-relative preference backpropagation.",
        "raw_step_total": round(raw_step_total, 4),
        "bounded_step_total": round(bounded_step_total, 4),
        "outcome_residual": round(residual, 4),
        "quality_credit_enabled": quality_credit_enabled,
        "quality_credit_reward": round(quality_credit_reward, 4),
        "quality_credit_allocated": round(sum(quality_allocation.values()), 4),
        "stages": dict(sorted(stages.items())),
    }

def _judge_weight() -> float:
    try:
        return max(0.0, min(3.0, float(os.environ.get("CRAYOTTER_RL_JUDGE_WEIGHT", "1.0"))))
    except (TypeError, ValueError):
        return 1.0


def compute_episode_reward(
    *,
    tool_events: list[dict[str, Any]],
    target_duration_seconds: float,
    final_output: str,
    judge_result: dict[str, Any] | None = None,
    episode_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(episode_metadata or {})
    bootstrap_reward = _tool_call_bootstrap_reward(tool_events, metadata)
    if bootstrap_reward is not None:
        return bootstrap_reward

    raw_step_total = sum(float(item.get("step_reward", 0.0)) for item in tool_events)
    step_total = round(max(-1.5, min(1.5, raw_step_total)), 4)

    reported_export_events = [
        item
        for item in tool_events
        if item.get("tool_name") == "export_video" and item.get("success")
    ]
    final_video_path = find_final_video_path(tool_events)
    export_success = bool(final_video_path)
    export_reward = 1.0 if export_success else -1.25
    artifact_validity_reward = 0.4 if export_success else -0.4

    final_duration = _final_duration(tool_events, final_video_path)
    duration_error_ratio = None
    if final_duration is not None and target_duration_seconds > 0:
        duration_error_ratio = abs(final_duration - target_duration_seconds) / max(
            target_duration_seconds,
            1.0,
        )
        duration_reward = max(-1.0, 1.0 - 2.0 * duration_error_ratio)
    else:
        duration_reward = -0.5 if export_success else -1.0
    duration_reward = round(duration_reward, 4)

    completion_bonus = 0.15 if final_output.strip() else -0.1
    repairs = _repair_count(tool_events)
    repair_bonus = min(0.3, repairs * 0.12)
    failed_calls = sum(1 for item in tool_events if not item.get("success"))
    failure_penalty = -min(0.6, failed_calls * 0.12)
    if metadata.get("long_horizon_task"):
        efficiency_penalty = 0.0
    else:
        efficient_call_budget = 14 if requires_semantic_material_grounding(metadata) else 10
        excess_calls = max(0, len(tool_events) - efficient_call_budget)
        efficiency_penalty = -min(0.6, excess_calls * 0.06)
    long_horizon_reward, long_horizon_components = _long_horizon_revision_reward(
        tool_events,
        metadata,
        final_video_path,
    )
    milestone_reward, milestone_components = _milestone_progress_reward(
        tool_events,
        metadata,
        final_video_path,
    )
    grounding_reward, grounding_components = _medium_semantic_grounding_reward(
        tool_events,
        metadata,
    )

    rule_reward = round(
        step_total
        + export_reward
        + artifact_validity_reward
        + duration_reward
        + completion_bonus
        + repair_bonus
        + failure_penalty
        + efficiency_penalty
        + long_horizon_reward
        + milestone_reward
        + grounding_reward,
        4,
    )
    if not export_success:
        rule_reward = min(rule_reward, -0.25)

    judge_payload = dict(judge_result or {})
    judge_score = judge_payload.get("score")
    judge_reward = 0.0
    judge_applied = export_success and (
        isinstance(judge_score, (int, float))
        and math.isfinite(float(judge_score))
    )
    if judge_applied:
        normalized_judge = max(0.0, min(100.0, float(judge_score)))
        judge_reward = round((normalized_judge / 50.0) - 1.0, 4)
    judge_weight = _judge_weight() if judge_applied else 0.0
    judge_credit_only = _env_bool("CRAYOTTER_RL_JUDGE_CREDIT_ONLY", False)
    judge_scalar_reward = 0.0 if judge_credit_only else judge_weight * judge_reward
    quality_credit_reward = judge_weight * judge_reward if judge_credit_only else 0.0

    total = round(rule_reward + judge_scalar_reward, 4)
    if not export_success:
        total = min(total, -0.25)
    failure_cap, failure_cap_info = _long_horizon_failure_cap(
        tool_events,
        metadata,
        final_video_path,
    )
    if failure_cap is not None:
        total = min(total, failure_cap)
        rule_reward = min(rule_reward, failure_cap)

    reward_payload = {
        "total_reward": total,
        "rule_reward": rule_reward,
        "step_total": step_total,
        "raw_step_total": round(raw_step_total, 4),
        "reported_export_success": bool(reported_export_events),
        "export_success": export_success,
        "export_reward": export_reward,
        "artifact_validity_reward": round(artifact_validity_reward, 4),
        "duration_reward": duration_reward,
        "completion_bonus": completion_bonus,
        "repair_bonus": round(repair_bonus, 4),
        "failure_penalty": round(failure_penalty, 4),
        "efficiency_penalty": round(efficiency_penalty, 4),
        "long_horizon_reward": round(long_horizon_reward, 4),
        "long_horizon_components": long_horizon_components,
        "milestone_progress_reward": round(milestone_reward, 4),
        "milestone_progress_components": milestone_components,
        "semantic_grounding_reward": round(grounding_reward, 4),
        "semantic_grounding_components": grounding_components,
        "failure_cap": failure_cap,
        "failure_cap_info": failure_cap_info,
        "episode_metadata": metadata,
        "final_video_path": final_video_path,
        "final_duration_seconds": final_duration,
        "duration_error_ratio": duration_error_ratio,
        "judge_applied": judge_applied,
        "judge_reward": judge_reward,
        "judge_weight": judge_weight,
        "judge_credit_only": judge_credit_only,
        "judge_scalar_reward": round(judge_scalar_reward, 4),
        "quality_credit_reward": round(quality_credit_reward, 4),
        "judge": judge_payload,
    }
    learned_segment_allocator = _env_bool("CRAYOTTER_RL_SEGMENT_ALLOCATOR_ENABLED", False)
    reward_payload["stage_credit"] = compute_stage_credit(
        tool_events,
        total,
        quality_credit_reward=quality_credit_reward,
        quality_credit_enabled=(
            judge_credit_only
            and judge_applied
            and not learned_segment_allocator
        ),
    )
    reward_payload["segment_credit"] = compute_segment_credit(
        tool_events,
        reward_payload["stage_credit"],
        total,
    )
    reward_payload["segment_allocator_enabled"] = learned_segment_allocator
    return reward_payload
