from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import torch

from .preference_credit import annotate_group_relative_preference_credit

try:
    from verl import DataProto
    from verl.experimental.reward_loop.reward_manager.base import RewardManagerBase
except Exception as exc:  # pragma: no cover - optional dependency
    DataProto = Any
    RewardManagerBase = object
    _VERL_IMPORT_ERROR = exc
else:
    _VERL_IMPORT_ERROR = None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if hasattr(value, "tolist"):
        converted = value.tolist()
        return converted if isinstance(converted, list) else [converted]
    return [value]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _get_item(batch: dict[str, Any], key: str, index: int, default: Any = None) -> Any:
    if key not in batch:
        return default
    value = batch[key]
    try:
        return value[index]
    except Exception:
        return default


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _required_finite(value: Any, field: str) -> float:
    result = _finite_float(value, math.nan)
    if not math.isfinite(result):
        raise ValueError(f"Missing or non-finite process reward field: {field}")
    return result


def _valid_response_length(data: DataProto, index: int) -> int:
    response_width = int(data.batch["responses"].shape[-1])
    attention = data.batch.get("attention_mask")
    prompts = data.batch.get("prompts")
    if attention is None or prompts is None:
        return response_width
    prompt_width = int(prompts.shape[-1])
    valid = int(attention[index, prompt_width:].sum().item())
    return max(0, min(response_width, valid))


def _assistant_spans(response_mask: torch.Tensor, valid_length: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for pos in range(valid_length):
        if int(response_mask[pos].item()) == 1:
            if start is None:
                start = pos
        elif start is not None:
            spans.append((start, pos - 1))
            start = None
    if start is not None:
        spans.append((start, valid_length - 1))
    return spans


def _event_credits(tool_events: list[Any], reward_summary: dict[str, Any]) -> list[float]:
    events = [_as_dict(item) for item in tool_events]
    credits = [_finite_float(event.get("step_reward")) for event in events]
    if not credits:
        return []

    stage_credit = _as_dict(reward_summary.get("stage_credit"))
    stages = _as_dict(stage_credit.get("stages"))
    if stages and events:
        stage_counts: dict[str, int] = {}
        for event in events:
            stage = str(event.get("stage") or "other")
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
        for idx, event in enumerate(events):
            stage = str(event.get("stage") or "other")
            stage_info = _as_dict(stages.get(stage))
            allocation = _finite_float(stage_info.get("allocated_outcome_residual"))
            count = max(1, stage_counts.get(stage, 1))
            credits[idx] += allocation / count
    return credits


def _segment_end_credits(reward_summary: dict[str, Any]) -> list[tuple[int, float, float]]:
    segment_credit = _as_dict(reward_summary.get("segment_credit"))
    result: list[tuple[int, float, float]] = []
    for raw_segment in _as_list(segment_credit.get("segments")):
        segment = _as_dict(raw_segment)
        if not segment:
            continue
        try:
            end_event_index = max(0, int(segment.get("end_event_index", 0)))
        except (TypeError, ValueError):
            continue
        preference = _finite_float(segment.get("allocated_preference_credit"))
        total = _finite_float(segment.get("segment_reward_total"))
        result.append((end_event_index, total - preference, preference))
    return result


def _has_no_tool_terminal_segment(reward_summary: dict[str, Any]) -> bool:
    segments = _as_list(_as_dict(reward_summary.get("segment_credit")).get("segments"))
    return any(
        _as_dict(segment).get("terminal_behavior") == "no_tool_call"
        for segment in segments
    )


def _event_position_index(event_index: int, event_count: int, position_count: int) -> int:
    if position_count <= 1 or event_count <= 1:
        return 0
    ratio = max(0.0, min(1.0, event_index / max(1, event_count - 1)))
    return min(position_count - 1, int(round(ratio * (position_count - 1))))


def _bounded_segment_position_rewards(
    segment_credits: list[tuple[int, float, float]],
    *,
    event_count: int,
    position_count: int,
    total_reward: float,
    clip_value: float,
) -> tuple[dict[int, float], float]:
    """Place base return without relocating segment preference credit.

    Preference credit reserves capacity at its own segment endpoint. Any base
    return clipped at one endpoint is spread over the remaining segment
    endpoints instead of being dumped onto the final action.
    """

    if not segment_credits or position_count <= 0:
        return {}, total_reward
    aggregated: dict[int, list[float]] = {}
    for event_index, base_reward, preference_credit in segment_credits:
        position_index = _event_position_index(event_index, event_count, position_count)
        item = aggregated.setdefault(position_index, [0.0, 0.0])
        item[0] += _finite_float(base_reward)
        item[1] += _finite_float(preference_credit)

    if clip_value <= 0:
        base_total = sum(item[0] for item in aggregated.values())
        residual = total_reward - base_total
        if abs(residual) > 1e-12:
            anchor = max(aggregated, key=lambda index: abs(aggregated[index][0]))
            aggregated[anchor][0] += residual
        return {
            position_index: base_reward + preference_credit
            for position_index, (base_reward, preference_credit) in aggregated.items()
        }, 0.0

    clip_value = abs(clip_value)

    base_values: dict[int, float] = {}
    preferences: dict[int, float] = {}
    requested_base: dict[int, float] = {}
    for position_index, (base_reward, preference_credit) in aggregated.items():
        preference = max(-clip_value, min(clip_value, preference_credit))
        base_limit = max(0.0, clip_value - abs(preference))
        requested_base[position_index] = base_reward
        base_values[position_index] = max(-base_limit, min(base_limit, base_reward))
        preferences[position_index] = preference

    residual = total_reward - sum(base_values.values())
    for _ in range(max(1, len(base_values) * 2)):
        if abs(residual) <= 1e-7:
            break
        direction = 1.0 if residual > 0 else -1.0
        capacities: dict[int, float] = {}
        for position_index, current in base_values.items():
            base_limit = max(0.0, clip_value - abs(preferences[position_index]))
            capacity = base_limit - current if direction > 0 else base_limit + current
            if capacity > 1e-9:
                capacities[position_index] = capacity
        if not capacities:
            break
        weights = {
            position_index: max(0.1, abs(requested_base[position_index]))
            for position_index in capacities
        }
        weight_sum = sum(weights.values())
        moved = 0.0
        for position_index, capacity in capacities.items():
            share = abs(residual) * weights[position_index] / weight_sum
            delta = direction * min(capacity, share)
            base_values[position_index] += delta
            moved += delta
        if abs(moved) <= 1e-9:
            break
        residual -= moved

    rewards = {
        position_index: base_values[position_index] + preferences[position_index]
        for position_index in base_values
    }
    return rewards, residual


def _persist_annotated_reward(
    episode_root: str,
    reward_summary: dict[str, Any],
    *,
    persist_manifest: bool = True,
) -> None:
    if not episode_root:
        raise ValueError("Missing phase3_episode_root for annotated reward persistence")
    if not reward_summary:
        raise ValueError("Missing phase3_episode_reward for annotated reward persistence")
    root = Path(episode_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    reward_path = root / "phase3_episode_reward.json"
    temporary = reward_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(reward_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, reward_path)

    manifest_dir_raw = os.environ.get("CRAYOTTER_RL_TRAJECTORY_MANIFEST_DIR", "").strip()
    if persist_manifest and manifest_dir_raw:
        manifest_dir = Path(manifest_dir_raw).expanduser().resolve()
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{root.name}.reward.json"
        manifest_tmp = manifest_path.with_suffix(".json.tmp")
        manifest_tmp.write_text(
            json.dumps(reward_summary, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(manifest_tmp, manifest_path)


def _group_key_for_item(non_tensor: dict[str, Any], index: int, reward_summary: dict[str, Any], extras: dict[str, Any]) -> str:
    fixture_path = str(
        _get_item(non_tensor, "phase3_fixture_path", index, "")
        or extras.get("phase3_fixture_path")
        or reward_summary.get("phase3_fixture_path")
        or ""
    ).strip()
    if fixture_path:
        return fixture_path
    metadata = _as_dict(reward_summary.get("episode_metadata"))
    case_id = str(metadata.get("case_id") or "").strip()
    revision_round = str(metadata.get("revision_round") or "").strip()
    if case_id:
        return f"case:{case_id}:rev:{revision_round}"
    return f"batch_item:{index}"


def _is_validation_batch(data: DataProto, reward_summaries: list[Any]) -> bool:
    meta_info = getattr(data, "meta_info", {}) or {}
    if bool(meta_info.get("validate")) or bool(meta_info.get("validation")):
        return True

    splits: list[str] = []
    for summary in reward_summaries:
        metadata = _as_dict(_as_dict(summary).get("episode_metadata"))
        split = str(
            metadata.get("horizon_suite_split")
            or metadata.get("benchmark_split")
            or ""
        ).strip().lower()
        if split:
            splits.append(split)
    if splits and all(split in {"eval", "validation", "val", "test"} for split in splits):
        return True

    non_tensor = data.non_tensor_batch
    sources = [
        str(_get_item(non_tensor, "data_source", index, "")).strip().lower()
        for index in range(len(data))
    ]
    sources = [source for source in sources if source]
    return bool(sources) and all(
        any(marker in source for marker in ("_eval_", "_validation_", "_test_"))
        for source in sources
    )


def _annotate_batch_preference_credit(data: DataProto) -> None:
    non_tensor = data.non_tensor_batch
    reward_summaries: list[Any] = []
    group_keys: list[str] = []
    for idx in range(len(data)):
        extras = _as_dict(_get_item(non_tensor, "tool_extra_fields", idx, {}))
        reward_summary = _as_dict(
            _get_item(non_tensor, "phase3_episode_reward", idx, {})
            or extras.get("phase3_episode_reward")
        )
        reward_summaries.append(reward_summary)
        group_keys.append(_group_key_for_item(non_tensor, idx, reward_summary, extras))
    validate = _is_validation_batch(data, reward_summaries)
    allocator_updates_enabled = os.environ.get(
        "CRAYOTTER_RL_ALLOCATOR_UPDATE_ENABLED",
        "1",
    ).strip().lower() not in {"0", "false", "no", "off"}
    annotate_group_relative_preference_credit(
        reward_summaries,
        group_keys=group_keys,
        update_allocator=allocator_updates_enabled and not validate,
        apply_policy_credit=not validate,
    )
    for idx, reward_summary in enumerate(reward_summaries):
        extras = _as_dict(_get_item(non_tensor, "tool_extra_fields", idx, {}))
        episode_root = str(
            _get_item(non_tensor, "phase3_episode_root", idx, "")
            or extras.get("phase3_episode_root")
            or ""
        ).strip()
        _persist_annotated_reward(
            episode_root,
            _as_dict(reward_summary),
            persist_manifest=True,
        )


def build_process_trainer_metrics(data: DataProto, *, global_step: int | None) -> dict[str, Any]:
    """Summarize the process-reward batch after preference attribution."""

    non_tensor = data.non_tensor_batch
    summaries: list[dict[str, Any]] = []
    group_keys: list[str] = []
    for idx in range(len(data)):
        extras = _as_dict(_get_item(non_tensor, "tool_extra_fields", idx, {}))
        summary = _as_dict(
            _get_item(non_tensor, "phase3_episode_reward", idx, {})
            or extras.get("phase3_episode_reward")
        )
        if not summary:
            raise ValueError(f"Sample {idx} is missing phase3_episode_reward for trainer metrics")
        summaries.append(summary)
        group_keys.append(_group_key_for_item(non_tensor, idx, summary, extras))

    totals = [_required_finite(item.get("total_reward"), "total_reward") for item in summaries]
    judge_scores: list[float] = []
    eligible_judge_scores: dict[str, list[float]] = {}
    rank_advantages: list[float] = []
    preference_credits: list[float] = []
    segment_counts: list[float] = []
    for group_key, summary in zip(group_keys, summaries):
        judge = _as_dict(summary.get("judge"))
        score = _finite_float(judge.get("score"), math.nan)
        eligible = bool(judge.get("eligible_for_preference", True)) and bool(summary.get("judge_applied"))
        if math.isfinite(score):
            judge_scores.append(score)
            if eligible:
                eligible_judge_scores.setdefault(group_key, []).append(score)

        relative = _as_dict(summary.get("group_relative_preference"))
        if "rank_advantage" in relative:
            rank_advantages.append(_finite_float(relative.get("rank_advantage")))

        segment_credit = _as_dict(summary.get("segment_credit"))
        preference_credits.append(_finite_float(segment_credit.get("preference_credit_sum")))
        segment_counts.append(float(len(_as_list(segment_credit.get("segments")))))

    group_spreads = [
        max(scores) - min(scores)
        for scores in eligible_judge_scores.values()
        if len(scores) >= 2
    ]
    allocator_update = next(
        (_as_dict(summary.get("allocator_update")) for summary in summaries if summary.get("allocator_update")),
        {},
    )
    preference_backprops = [_as_dict(summary.get("preference_backprop")) for summary in summaries]
    raw_judge_policy_flags = [
        bool(_as_dict(summary.get("group_relative_preference")).get("raw_judge_score_used_as_policy_reward"))
        for summary in summaries
    ]

    return {
        "event": "trainer_step_metrics",
        "global_step": global_step,
        "crayotter/reward/total_mean": round(_mean(totals), 6),
        "crayotter/reward/total_std": round(_std(totals), 6),
        "crayotter/reward/total_min": round(min(totals), 6),
        "crayotter/reward/total_max": round(max(totals), 6),
        "crayotter/execution/export_success_rate": round(
            _mean([float(bool(summary.get("export_success"))) for summary in summaries]), 6
        ),
        "crayotter/judge/applied_rate": round(
            _mean([float(bool(summary.get("judge_applied"))) for summary in summaries]), 6
        ),
        "crayotter/judge/eligible_rate": round(
            _mean([
                float(
                    bool(summary.get("judge_applied"))
                    and bool(_as_dict(summary.get("judge")).get("eligible_for_preference", True))
                )
                for summary in summaries
            ]),
            6,
        ),
        "crayotter/judge/score_mean": round(_mean(judge_scores), 6),
        "crayotter/judge/score_std": round(_std(judge_scores), 6),
        "crayotter/judge/within_group_spread_mean": round(_mean(group_spreads), 6),
        "crayotter/preference/comparable_group_count": len(group_spreads),
        "crayotter/preference/rank_advantage_abs_mean": round(
            _mean([abs(value) for value in rank_advantages]), 6
        ),
        "crayotter/preference/rank_advantage_nonzero_rate": round(
            _mean([float(abs(value) > 1e-12) for value in rank_advantages]), 6
        ),
        "crayotter/preference/credit_abs_mean": round(
            _mean([abs(value) for value in preference_credits]), 6
        ),
        "crayotter/preference/credit_applied_episode_rate": round(
            _mean([float(bool(item.get("applied"))) for item in preference_backprops]), 6
        ),
        "crayotter/preference/raw_judge_policy_rate": round(
            _mean([float(value) for value in raw_judge_policy_flags]), 6
        ),
        "crayotter/segments/count_mean": round(_mean(segment_counts), 6),
        "crayotter/allocator/eligible_group_count": int(allocator_update.get("eligible_group_count") or 0),
        "crayotter/allocator/pair_count": int(allocator_update.get("pair_count") or 0),
        "crayotter/allocator/pair_examples": int(allocator_update.get("post_update_pair_examples") or 0),
        "crayotter/allocator/update_steps": int(allocator_update.get("post_update_steps") or 0),
        "crayotter/allocator/reliability": round(
            _finite_float(allocator_update.get("allocator_reliability")), 6
        ),
        "crayotter/allocator/calibration_accuracy_ema": round(
            _finite_float(allocator_update.get("calibration_accuracy_ema")), 6
        ),
        "crayotter/allocator/calibration_log_loss_ema": round(
            _finite_float(allocator_update.get("calibration_log_loss_ema")), 6
        ),
        "crayotter/allocator/rank_advantage_applied_count": int(
            allocator_update.get("rank_advantage_applied_count") or 0
        ),
        "crayotter/allocator/group_credit_drift": round(
            _finite_float(allocator_update.get("rank_advantage_group_credit_drift")), 6
        ),
    }


def emit_process_trainer_metrics(data: DataProto, *, global_step: int | None) -> dict[str, Any]:
    metrics = build_process_trainer_metrics(data, global_step=global_step)
    configured = os.environ.get("CRAYOTTER_RL_TRAINING_METRICS_JSONL", "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(metrics, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"[crayotter/trainer] {json.dumps(metrics, ensure_ascii=False, sort_keys=True)}", flush=True)
    return metrics


class CrayotterProcessRewardManager(RewardManagerBase):
    """Assign Crayotter editing process rewards to trainable action tokens."""

    def __init__(self, config, tokenizer, compute_score, reward_router_address=None, reward_model_tokenizer=None):
        if _VERL_IMPORT_ERROR is not None:  # pragma: no cover
            raise ImportError(f"verl is required: {_VERL_IMPORT_ERROR}")
        super().__init__(config, tokenizer, compute_score)

    async def run_single(self, data: DataProto) -> dict[str, Any]:
        item = data[0]
        extras = _as_dict(item.non_tensor_batch.get("tool_extra_fields"))
        reward_summary = _as_dict(
            item.non_tensor_batch.get("phase3_episode_reward")
            or extras.get("phase3_episode_reward")
        )
        tool_events = _as_list(
            item.non_tensor_batch.get("phase3_tool_trace")
            or extras.get("phase3_tool_trace")
        )
        if not reward_summary:
            raise ValueError("Process reward sample is missing phase3_episode_reward")
        if not tool_events and not _has_no_tool_terminal_segment(reward_summary):
            raise ValueError("Process reward sample is missing phase3_tool_trace")
        total = _required_finite(reward_summary.get("total_reward"), "total_reward")
        credits = _event_credits(tool_events, reward_summary)
        return {
            "reward_score": total,
            "reward_extra_info": {
                "process_reward_total": total,
                "process_reward_steps": len(credits),
                "process_reward_step_sum": round(sum(credits), 4),
            },
        }

    @classmethod
    def assemble_rm_scores(cls, data: DataProto, scores: list[float]) -> torch.Tensor:
        _annotate_batch_preference_credit(data)
        response_mask = data.batch["response_mask"]
        rm_scores = torch.zeros_like(response_mask, dtype=torch.float32)
        non_tensor = data.non_tensor_batch
        clip_value = _finite_float(os.environ.get("CRAYOTTER_RL_PROCESS_REWARD_CLIP"), 2.0)

        for idx in range(len(data)):
            valid_length = _valid_response_length(data, idx)
            if valid_length <= 0:
                raise ValueError(f"Sample {idx} has no valid response tokens")
            spans = _assistant_spans(response_mask[idx], valid_length)
            trainable_positions = [end for _, end in spans]
            if not trainable_positions:
                trainable_positions = [
                    pos for pos in range(valid_length) if int(response_mask[idx, pos].item()) == 1
                ]
            if not trainable_positions:
                raise ValueError(f"Sample {idx} has no trainable assistant token span")

            extras = _as_dict(_get_item(non_tensor, "tool_extra_fields", idx, {}))
            reward_summary = _as_dict(
                _get_item(non_tensor, "phase3_episode_reward", idx, {})
                or extras.get("phase3_episode_reward")
            )
            tool_events = _as_list(
                _get_item(non_tensor, "phase3_tool_trace", idx, [])
                or extras.get("phase3_tool_trace")
            )
            if not reward_summary:
                raise ValueError(f"Sample {idx} is missing phase3_episode_reward")
            if not tool_events and not _has_no_tool_terminal_segment(reward_summary):
                raise ValueError(f"Sample {idx} is missing phase3_tool_trace")
            total = _required_finite(reward_summary.get("total_reward"), "total_reward")

            segment_credits = _segment_end_credits(reward_summary)
            if not segment_credits:
                raise ValueError(f"Sample {idx} has no segment-level process credit")
            position_rewards, residual = _bounded_segment_position_rewards(
                segment_credits,
                event_count=max(1, len(tool_events)),
                position_count=len(trainable_positions),
                total_reward=total,
                clip_value=clip_value,
            )
            if abs(residual) > 1e-6:
                raise ValueError(
                    f"Sample {idx} process reward cannot fit token clip budget; residual={residual}"
                )
            for position_index, reward in position_rewards.items():
                rm_scores[idx, trainable_positions[position_index]] += reward

        return rm_scores
