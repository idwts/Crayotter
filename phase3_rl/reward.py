from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .tool_runtime import ToolExecutionResult


@dataclass(slots=True)
class StepReward:
    total: float
    components: dict[str, float]


def _canonical_args(arguments: dict[str, Any]) -> str:
    try:
        return json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(arguments)


def compute_step_reward(
    *,
    tool_name: str,
    execution: ToolExecutionResult,
    prior_events: list[dict[str, Any]],
) -> StepReward:
    components: dict[str, float] = {
        "tool_success": 0.6 if execution.success else -0.8,
        "artifact_bonus": 0.1 if execution.output_paths else 0.0,
        "returncode_penalty": -0.1 if execution.returncode not in (0, 2) else 0.0,
        "repeat_penalty": 0.0,
        "order_bonus": 0.0,
    }

    signature = f"{tool_name}:{_canonical_args(execution.arguments)}"
    if prior_events:
        last = prior_events[-1]
        if signature == last.get("signature"):
            components["repeat_penalty"] = -0.2

    seen_tools = {str(item.get("tool_name")) for item in prior_events}
    if tool_name == "export_video":
        if "inspect_video_duration" in seen_tools:
            components["order_bonus"] += 0.15
        else:
            components["order_bonus"] -= 0.15
    if tool_name == "add_narration_segments":
        if "validate_narration_timeline" in seen_tools:
            components["order_bonus"] += 0.2
        else:
            components["order_bonus"] -= 0.4
    if tool_name == "merge_videos":
        cut_count = sum(1 for item in prior_events if item.get("tool_name") in {"cut_video", "batch_cut_video"})
        if cut_count >= 2:
            components["order_bonus"] += 0.1
        elif cut_count == 0:
            components["order_bonus"] -= 0.1

    total = round(sum(components.values()), 4)
    return StepReward(total=total, components=components)


def compute_episode_reward(
    *,
    tool_events: list[dict[str, Any]],
    target_duration_seconds: float,
    final_output: str,
) -> dict[str, Any]:
    step_total = round(sum(float(item.get("step_reward", 0.0)) for item in tool_events), 4)

    export_events = [item for item in tool_events if item.get("tool_name") == "export_video" and item.get("success")]
    export_bonus = 1.0 if export_events else 0.0

    final_duration = None
    for item in reversed(tool_events):
        duration = item.get("duration_seconds")
        if isinstance(duration, (int, float)) and duration > 0:
            final_duration = float(duration)
            break

    duration_reward = 0.0
    duration_error_ratio = None
    if final_duration is not None and target_duration_seconds > 0:
        duration_error_ratio = abs(final_duration - target_duration_seconds) / max(target_duration_seconds, 1.0)
        duration_reward = max(-0.5, round(0.8 - duration_error_ratio, 4))

    completion_bonus = 0.3 if final_output.strip() else 0.0
    efficiency_penalty = -0.02 * max(0, len(tool_events) - 6)

    total = round(step_total + export_bonus + duration_reward + completion_bonus + efficiency_penalty, 4)
    return {
        "total_reward": total,
        "step_total": step_total,
        "export_bonus": export_bonus,
        "duration_reward": duration_reward,
        "completion_bonus": completion_bonus,
        "efficiency_penalty": round(efficiency_penalty, 4),
        "final_duration_seconds": final_duration,
        "duration_error_ratio": duration_error_ratio,
    }
