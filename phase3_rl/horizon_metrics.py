from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CORE_STAGES = {
    "diagnosis",
    "material_selection",
    "rough_cut",
    "timeline_ordering",
    "pacing_transition",
    "subtitle_narration",
    "audio",
    "export_validation",
    "repair",
}


def requires_semantic_material_grounding(metadata: dict[str, Any] | None) -> bool:
    """Return whether source selection needs semantic evidence before editing.

    Older fixtures lack the explicit grounding flag but retain their horizon
    classification. Supporting both formats keeps prompt and reward behavior
    stable when generated fixture directories are reused.
    """
    metadata = metadata or {}
    if metadata.get("semantic_material_grounding_required") or metadata.get(
        "multi_constraint_task"
    ):
        return True
    horizon_metrics = metadata.get("horizon_metrics")
    if not isinstance(horizon_metrics, dict):
        horizon_metrics = {}
    task_type = str(
        metadata.get("task_type") or horizon_metrics.get("task_type") or ""
    ).strip()
    return task_type == "medium_horizon_editing"


@dataclass(frozen=True)
class HorizonMetrics:
    task_horizon_score: float
    task_type: str
    feedback_rounds: int
    has_previous_final: bool
    material_count: int
    constraint_count: int
    required_stage_count: int
    artifact_dependency_depth_expected: int
    preserve_count: int
    change_count: int
    expected_stages: list[str]
    rationale: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_horizon_score": round(float(self.task_horizon_score), 3),
            "task_type": self.task_type,
            "feedback_rounds": self.feedback_rounds,
            "has_previous_final": self.has_previous_final,
            "material_count": self.material_count,
            "constraint_count": self.constraint_count,
            "required_stage_count": self.required_stage_count,
            "artifact_dependency_depth_expected": self.artifact_dependency_depth_expected,
            "preserve_count": self.preserve_count,
            "change_count": self.change_count,
            "expected_stages": self.expected_stages,
            "rationale": self.rationale,
        }


def annotate_fixture_payload(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = compute_task_horizon_metrics(payload)
    metadata = dict(payload.get("metadata") or {})
    metadata["horizon_metrics"] = metrics.to_dict()
    metadata["task_horizon_score"] = metrics.task_horizon_score
    metadata["task_type"] = metrics.task_type
    metadata["expected_stages"] = metrics.expected_stages
    payload["metadata"] = metadata
    return payload


def compute_task_horizon_metrics(payload: dict[str, Any]) -> HorizonMetrics:
    metadata = dict(payload.get("metadata") or {})
    user_request = str(payload.get("user_request") or "")
    blueprint = str(payload.get("editing_blueprint") or "")
    text = f"{user_request}\n{blueprint}"
    runtime_seed = payload.get("runtime_seed") or []
    material_count = _count_video_seeds(runtime_seed)
    feedback_rounds = _feedback_rounds(metadata, text)
    has_previous_final = bool(metadata.get("previous_version_available") or metadata.get("previous_final_target"))
    preserve_count = len(_as_list(metadata.get("preserve_requirements")))
    change_count = len(_as_list(metadata.get("change_requirements")))
    constraint_count = _constraint_count(text, preserve_count, change_count)
    expected_stages = _expected_stages(text, metadata, has_previous_final, feedback_rounds)
    required_stage_count = len(expected_stages)
    artifact_dependency_depth = _expected_dependency_depth(
        required_stage_count=required_stage_count,
        has_previous_final=has_previous_final,
        feedback_rounds=feedback_rounds,
        preserve_count=preserve_count,
        change_count=change_count,
    )

    score = (
        min(feedback_rounds, 3) * 10.0
        + (12.0 if has_previous_final else 0.0)
        + min(material_count, 15) * 1.0
        + min(constraint_count, 8) * 3.0
        + min(required_stage_count, 9) * 3.0
        + min(artifact_dependency_depth, 12) * 2.0
        + min(preserve_count + change_count, 6) * 4.0
    )
    task_type = _task_type(
        score=score,
        feedback_rounds=feedback_rounds,
        has_previous_final=has_previous_final,
        required_stage_count=required_stage_count,
        preserve_count=preserve_count,
        change_count=change_count,
        metadata=metadata,
    )
    rationale = [
        f"feedback_rounds={feedback_rounds}",
        f"has_previous_final={has_previous_final}",
        f"material_count={material_count}",
        f"constraint_count={constraint_count}",
        f"required_stage_count={required_stage_count}",
        f"artifact_dependency_depth_expected={artifact_dependency_depth}",
        f"revision_requirements={preserve_count + change_count}",
    ]
    return HorizonMetrics(
        task_horizon_score=score,
        task_type=task_type,
        feedback_rounds=feedback_rounds,
        has_previous_final=has_previous_final,
        material_count=material_count,
        constraint_count=constraint_count,
        required_stage_count=required_stage_count,
        artifact_dependency_depth_expected=artifact_dependency_depth,
        preserve_count=preserve_count,
        change_count=change_count,
        expected_stages=expected_stages,
        rationale=rationale,
    )


def rollout_horizon_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    steps = summary.get("steps") or summary.get("tool_steps") or []
    if not isinstance(steps, list):
        steps = []
    stage_credit = summary.get("stage_credit") or {}
    stages = stage_credit.get("stages") if isinstance(stage_credit, dict) else {}
    if not isinstance(stages, dict):
        stages = {}
    valid_steps = [step for step in steps if isinstance(step, dict) and step.get("tool_name")]
    successful_steps = [
        step for step in valid_steps
        if not step.get("error") and str(step.get("status", "")).lower() not in {"failed", "error"}
    ]
    covered_stages = [
        stage for stage, payload in stages.items()
        if isinstance(payload, dict) and float(payload.get("stage_reward_total") or payload.get("tool_reward") or 0.0) != 0.0
    ]
    final_artifacts = summary.get("final_artifacts") or summary.get("accepted_artifacts") or []
    repair_count = sum(1 for step in valid_steps if "repair" in str(step.get("tool_name", "")).lower())
    repeated_call_rate = _repeated_call_rate(valid_steps)
    return {
        "valid_tool_steps": len(valid_steps),
        "successful_tool_steps": len(successful_steps),
        "effective_tool_steps": max(0, len(successful_steps) - int(round(repeated_call_rate * len(successful_steps)))),
        "stage_coverage_count": len(set(covered_stages)),
        "stage_coverage": len(set(covered_stages)) / max(1, len(CORE_STAGES)),
        "artifact_lineage_depth_proxy": len(successful_steps) + len(final_artifacts),
        "repair_loop_count": repair_count,
        "repeated_call_rate": repeated_call_rate,
        "has_final_artifact": bool(final_artifacts or summary.get("final_video")),
    }


def write_manifest(path: str | Path, records: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    task_counts: dict[str, int] = {}
    scores: list[float] = []
    for record in records:
        metrics = record.get("horizon_metrics") or {}
        task_type = str(metrics.get("task_type") or record.get("task_type") or "unknown")
        task_counts[task_type] = task_counts.get(task_type, 0) + 1
        if "task_horizon_score" in metrics:
            scores.append(float(metrics["task_horizon_score"]))
    payload = {
        "record_count": len(records),
        "task_type_counts": task_counts,
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
        "score_mean": sum(scores) / len(scores) if scores else None,
        "records": records,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[;；,，\n]", value) if item.strip()]
    return [value]


def _count_video_seeds(runtime_seed: Any) -> int:
    if not isinstance(runtime_seed, list):
        return 0
    count = 0
    for item in runtime_seed:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target") or item.get("source") or "").lower()
        if target.endswith((".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v")):
            count += 1
    return count


def _feedback_rounds(metadata: dict[str, Any], text: str) -> int:
    raw = metadata.get("revision_round")
    try:
        if raw not in (None, ""):
            return max(0, int(raw))
    except Exception:
        pass
    if metadata.get("long_horizon_task") or metadata.get("feedback"):
        return 1
    matches = re.findall(r"(?:第\s*)?(\d+)\s*(?:次)?(?:反馈|重剪|修改|revision)", text, flags=re.IGNORECASE)
    if matches:
        return max(int(item) for item in matches)
    return 0


def _constraint_count(text: str, preserve_count: int, change_count: int) -> int:
    patterns = [
        r"\d+\s*(?:秒|分钟|min|s|sec)",
        r"字幕|旁白|解说|narration|subtitle",
        r"音乐|音频|配乐|audio|music",
        r"节奏|转场|pacing|transition",
        r"风格|style|清新|高级|科技|活泼",
        r"保留|复用|preserve|reuse",
        r"替换|修改|优化|change|replace|revise",
        r"开头|结尾|story|故事|结构",
    ]
    count = preserve_count + change_count
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            count += 1
    return count


def _expected_stages(
    text: str,
    metadata: dict[str, Any],
    has_previous_final: bool,
    feedback_rounds: int,
) -> list[str]:
    stages = ["material_selection", "rough_cut", "timeline_ordering", "export_validation"]
    if has_previous_final or feedback_rounds > 0 or metadata.get("long_horizon_task"):
        stages.insert(0, "diagnosis")
        stages.append("repair")
    if re.search(r"节奏|转场|pacing|transition|开头|拖", text, flags=re.IGNORECASE):
        stages.append("pacing_transition")
    if re.search(r"字幕|旁白|解说|narration|subtitle", text, flags=re.IGNORECASE):
        stages.append("subtitle_narration")
    if re.search(r"音乐|音频|配乐|audio|music|loudness", text, flags=re.IGNORECASE):
        stages.append("audio")
    seen: set[str] = set()
    ordered: list[str] = []
    for stage in stages:
        if stage not in seen:
            seen.add(stage)
            ordered.append(stage)
    return ordered


def _expected_dependency_depth(
    *,
    required_stage_count: int,
    has_previous_final: bool,
    feedback_rounds: int,
    preserve_count: int,
    change_count: int,
) -> int:
    return (
        2
        + required_stage_count
        + (2 if has_previous_final else 0)
        + min(feedback_rounds, 3)
        + min(preserve_count + change_count, 4)
    )


def _task_type(
    *,
    score: float,
    feedback_rounds: int,
    has_previous_final: bool,
    required_stage_count: int,
    preserve_count: int,
    change_count: int,
    metadata: dict[str, Any],
) -> str:
    if (
        feedback_rounds >= 1
        or has_previous_final
        or metadata.get("long_horizon_task")
        or required_stage_count >= 7
        or preserve_count + change_count >= 3
        or score >= 60
    ):
        return "long_horizon_revision"
    if score >= 35 or required_stage_count >= 6:
        return "medium_horizon_editing"
    return "normal_editing"


def _repeated_call_rate(steps: list[dict[str, Any]]) -> float:
    if not steps:
        return 0.0
    signatures: list[str] = []
    for step in steps:
        name = str(step.get("tool_name") or "")
        args = step.get("arguments") or step.get("args") or {}
        try:
            rendered = json.dumps(args, ensure_ascii=False, sort_keys=True)
        except Exception:
            rendered = str(args)
        signatures.append(f"{name}:{rendered}")
    repeated = len(signatures) - len(set(signatures))
    return repeated / max(1, len(signatures))
