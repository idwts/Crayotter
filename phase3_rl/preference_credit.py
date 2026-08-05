from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path
from typing import Any

from .semantic_artifact import SEMANTIC_DIMENSIONS

ALLOCATOR_VERSION = 4
_STATE_LOCK = threading.Lock()
_CREDIT_DECIMALS = 6
_CREDIT_SCALE = 10**_CREDIT_DECIMALS

PREFERENCE_VARIANTS = {
    "process_ppo",
    "terminal_rank",
    "uniform",
    "grpb",
    "no_lag",
    "no_reliability",
    "no_cap_projection",
    "no_safeguards",
    "no_group_centering",
}

REQUEST_STAGE_CUES: dict[str, set[str]] = {
    "material_selection": {"素材", "复用", "保留", "material", "reuse", "preserve"},
    "rough_cut": {"粗剪", "删", "裁剪", "cut", "trim", "remove"},
    "timeline_ordering": {"叙事", "故事", "顺序", "开场", "story", "order", "opening"},
    "transition_pacing": {"节奏", "转场", "拖沓", "pacing", "rhythm", "transition"},
    "subtitle_narration": {"字幕", "旁白", "解说", "subtitle", "narration", "caption"},
    "audio_mixing": {"音乐", "音频", "配乐", "music", "audio", "loudness"},
    "export_repair": {"导出", "修复", "重剪", "export", "repair", "revision"},
    "validation": {"检查", "验证", "反馈", "时长", "validate", "feedback", "duration"},
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_float(name: str, default: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, _finite_float(os.environ.get(name), default)))


def _env_int(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


def _state_path() -> Path:
    configured = os.environ.get("CRAYOTTER_RL_SEGMENT_ALLOCATOR_STATE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parent / "state" / "segment_allocator.json").resolve()


def _preference_variant() -> str:
    variant = os.environ.get("CRAYOTTER_RL_PREFERENCE_VARIANT", "grpb").strip().lower()
    if variant not in PREFERENCE_VARIANTS:
        choices = ", ".join(sorted(PREFERENCE_VARIANTS))
        raise ValueError(f"Unknown CRAYOTTER_RL_PREFERENCE_VARIANT={variant!r}; expected one of {choices}")
    return variant


def _judge_score(summary: dict[str, Any]) -> float | None:
    if not bool(summary.get("export_success")) or not bool(summary.get("judge_applied")):
        return None
    if not bool(_as_dict(summary.get("judge")).get("eligible_for_preference", True)):
        return None
    score = _as_dict(summary.get("judge")).get("score", summary.get("judge_score"))
    value = _finite_float(score, math.nan)
    if not math.isfinite(value):
        return None
    return max(0.0, min(100.0, value))


def _request_text(summary: dict[str, Any]) -> str:
    metadata = _as_dict(summary.get("episode_metadata"))
    pieces = [
        str(summary.get("user_request") or ""),
        str(metadata.get("feedback") or ""),
        " ".join(str(item) for item in _as_list(metadata.get("preserve_requirements"))),
        " ".join(str(item) for item in _as_list(metadata.get("change_requirements"))),
    ]
    return " ".join(pieces).lower()


def _request_match(stage: str, summary: dict[str, Any]) -> float:
    text = _request_text(summary)
    cues = REQUEST_STAGE_CUES.get(stage, set())
    if not cues or not text:
        return 0.0
    return min(1.0, sum(1 for cue in cues if cue in text) / 2.0)


def _segment_features(
    segment: dict[str, Any],
    summary: dict[str, Any],
    segment_count: int,
) -> dict[str, float]:
    stage = str(segment.get("stage") or "other")
    calls = max(1.0, _finite_float(segment.get("call_count"), 1.0))
    success_ratio = _finite_float(segment.get("success_count")) / calls
    failure_ratio = _finite_float(segment.get("failure_count")) / calls
    metadata = _as_dict(summary.get("episode_metadata"))
    segment_index = max(0.0, _finite_float(segment.get("segment_index")))
    position = (segment_index + 1.0) / max(1.0, float(segment_count))
    prefix = f"stage::{stage}"
    structural_scale = _env_float("CRAYOTTER_RL_ALLOCATOR_STRUCTURAL_SCALE", 0.35, 0.0, 1.0)
    features = {
        f"{prefix}::present": 1.0,
        f"{prefix}::success_ratio": max(0.0, min(1.0, success_ratio)),
        f"{prefix}::failure_ratio": max(0.0, min(1.0, failure_ratio)),
        f"{prefix}::call_log": min(2.0, math.log1p(calls) / 2.0),
        f"{prefix}::artifact_log": min(
            2.0,
            math.log1p(max(0.0, _finite_float(segment.get("artifact_count")))) / 2.0,
        ),
        f"{prefix}::video_artifact": min(
            1.5,
            math.log1p(max(0.0, _finite_float(segment.get("video_artifact_count")))),
        ),
        f"{prefix}::duration_observed": min(
            1.0,
            _finite_float(segment.get("duration_observation_count")),
        ),
        f"{prefix}::repair_success": min(
            1.0,
            _finite_float(segment.get("repair_success_count")),
        ),
        f"{prefix}::local_rule": math.tanh(_finite_float(segment.get("local_step_total"))),
        f"{prefix}::request_match": _request_match(stage, summary),
        f"{prefix}::position": position,
        f"{prefix}::revision": 1.0 if metadata.get("long_horizon_task") else 0.0,
        f"{prefix}::previous_version": 1.0 if metadata.get("previous_version_available") else 0.0,
    }
    features = {key: value * structural_scale for key, value in features.items()}

    semantic = _as_dict(summary.get("semantic_artifact_delta"))
    semantic_segments = _as_dict(semantic.get("segments"))
    semantic_item = _as_dict(semantic_segments.get(str(segment.get("segment_id") or "")))
    if semantic_item:
        confidence = max(0.0, min(1.0, _finite_float(semantic_item.get("confidence"), 0.5)))
        features[f"{prefix}::semantic::evaluated"] = 1.0
        features[f"{prefix}::semantic::confidence"] = confidence
        for dimension in SEMANTIC_DIMENSIONS:
            value = max(-1.0, min(1.0, _finite_float(semantic_item.get(dimension))))
            features[f"{prefix}::semantic::{dimension}"] = value * confidence
    return features


def _mean_feature_maps(feature_maps: list[dict[str, float]]) -> dict[str, float]:
    if not feature_maps:
        return {}
    result: dict[str, float] = {}
    for features in feature_maps:
        for key, value in features.items():
            result[key] = result.get(key, 0.0) + value
    scale = 1.0 / len(feature_maps)
    return {key: value * scale for key, value in result.items()}


class OnlinePairwiseSegmentAllocator:
    """Small CPU Bradley-Terry model with interpretable sparse features."""

    def __init__(self, state: dict[str, Any] | None = None):
        payload = state or {}
        self.weights = {
            str(key): _finite_float(value)
            for key, value in _as_dict(payload.get("weights")).items()
        }
        self.grad_sq = {
            str(key): max(0.0, _finite_float(value))
            for key, value in _as_dict(payload.get("grad_sq")).items()
        }
        self.update_steps = max(0, int(_finite_float(payload.get("update_steps"))))
        self.pair_examples = max(0, int(_finite_float(payload.get("pair_examples"))))
        self.calibration_pairs = max(0, int(_finite_float(payload.get("calibration_pairs"))))
        self.calibration_accuracy_ema = max(
            0.0,
            min(1.0, _finite_float(payload.get("calibration_accuracy_ema"), 0.5)),
        )
        self.calibration_log_loss_ema = max(
            0.0,
            _finite_float(payload.get("calibration_log_loss_ema"), math.log(2.0)),
        )

    @classmethod
    def load(cls, path: Path) -> "OnlinePairwiseSegmentAllocator":
        if not path.exists():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid segment allocator state: {path}") from exc
        if _finite_float(_as_dict(payload).get("version")) != ALLOCATOR_VERSION:
            raise RuntimeError(
                f"Incompatible segment allocator state version at {path}; "
                f"expected {ALLOCATOR_VERSION}"
            )
        return cls(_as_dict(payload))

    def score(self, features: dict[str, float]) -> float:
        return sum(self.weights.get(key, 0.0) * value for key, value in features.items())

    def observe_pair(
        self,
        preferred: dict[str, float],
        rejected: dict[str, float],
        *,
        ema_decay: float,
    ) -> tuple[float, float]:
        """Measure pre-update ranking quality on the next rollout group."""

        keys = set(preferred) | set(rejected)
        delta = {key: preferred.get(key, 0.0) - rejected.get(key, 0.0) for key in keys}
        logit = max(-20.0, min(20.0, self.score(delta)))
        probability = 1.0 / (1.0 + math.exp(-logit))
        correct = 1.0 if logit > 0 else 0.0 if logit < 0 else 0.5
        log_loss = -math.log(max(1e-8, probability))
        if self.calibration_pairs == 0:
            self.calibration_accuracy_ema = correct
            self.calibration_log_loss_ema = log_loss
        else:
            self.calibration_accuracy_ema = (
                ema_decay * self.calibration_accuracy_ema + (1.0 - ema_decay) * correct
            )
            self.calibration_log_loss_ema = (
                ema_decay * self.calibration_log_loss_ema + (1.0 - ema_decay) * log_loss
            )
        self.calibration_pairs += 1
        return probability, correct

    def reliability(self, *, warmup_pairs: int, target_accuracy: float) -> float:
        """Return a conservative 0-1 gate from pre-update pairwise accuracy."""

        if self.calibration_pairs <= 0:
            return 0.0
        sample_confidence = min(
            1.0,
            math.sqrt(self.calibration_pairs / max(1.0, float(warmup_pairs * 8))),
        )
        accuracy_edge = max(
            0.0,
            min(
                1.0,
                (self.calibration_accuracy_ema - 0.5) / max(1e-6, target_accuracy - 0.5),
            ),
        )
        return sample_confidence * accuracy_edge

    def update_pair(
        self,
        preferred: dict[str, float],
        rejected: dict[str, float],
        *,
        learning_rate: float,
        l2: float,
        sample_weight: float = 1.0,
    ) -> float:
        keys = set(preferred) | set(rejected)
        delta = {key: preferred.get(key, 0.0) - rejected.get(key, 0.0) for key in keys}
        logit = max(-20.0, min(20.0, self.score(delta)))
        probability = 1.0 / (1.0 + math.exp(-logit))
        error = probability - 1.0
        for key, value in delta.items():
            if abs(value) <= 1e-12:
                continue
            weight = self.weights.get(key, 0.0)
            gradient = sample_weight * error * value + l2 * weight
            accumulator = self.grad_sq.get(key, 1e-3) + gradient * gradient
            updated = weight - learning_rate * gradient / math.sqrt(accumulator)
            self.weights[key] = max(-6.0, min(6.0, updated))
            self.grad_sq[key] = accumulator
        self.pair_examples += 1
        return -math.log(max(1e-8, probability))

    def save(self, path: Path, metrics: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": ALLOCATOR_VERSION,
            "update_steps": self.update_steps,
            "pair_examples": self.pair_examples,
            "calibration_pairs": self.calibration_pairs,
            "calibration_accuracy_ema": round(self.calibration_accuracy_ema, 8),
            "calibration_log_loss_ema": round(self.calibration_log_loss_ema, 8),
            "weights": dict(sorted(self.weights.items())),
            "grad_sq": dict(sorted(self.grad_sq.items())),
            "last_update": metrics,
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)


def _segment_feature_maps(summary: dict[str, Any]) -> list[dict[str, float]]:
    segments = _as_list(_as_dict(summary.get("segment_credit")).get("segments"))
    return [
        _segment_features(_as_dict(segment), summary, len(segments))
        for segment in segments
        if _as_dict(segment)
    ]


def _preference_segment_records(
    summary: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, float]]]:
    """Return attributable segments after the shared branch point."""

    segments = [
        _as_dict(item)
        for item in _as_list(_as_dict(summary.get("segment_credit")).get("segments"))
        if _as_dict(item)
    ]
    feature_maps = [
        _segment_features(segment, summary, len(segments)) for segment in segments
    ]
    metadata = _as_dict(summary.get("episode_metadata"))
    prefix = _as_dict(metadata.get("counterfactual_prefix"))
    branch_stage = str(prefix.get("branch_point_stage") or "").strip()
    if not prefix or not branch_stage:
        return list(zip(segments, feature_maps))
    start_index = next(
        (index for index, segment in enumerate(segments) if str(segment.get("stage")) == branch_stage),
        None,
    )
    if start_index is None:
        branch_event = int(_finite_float(prefix.get("branch_point_event_index"), 0.0))
        start_index = next(
            (index for index, segment in enumerate(segments) if int(_finite_float(segment.get("start_event_index"))) > branch_event),
            0,
        )
    records = list(zip(segments[start_index:], feature_maps[start_index:]))
    return records or list(zip(segments, feature_maps))


def _preference_training_feature_maps(summary: dict[str, Any]) -> list[dict[str, float]]:
    """Use only the suffix after a shared counterfactual branch point when available."""

    return [features for _, features in _preference_segment_records(summary)]


def _eligible_groups(
    reward_summaries: list[dict[str, Any]],
    group_keys: list[str],
    min_group_size: int,
    min_score_gap: float,
) -> list[list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for index, summary in enumerate(reward_summaries):
        score = _judge_score(summary)
        training_maps = _preference_training_feature_maps(summary)
        if score is None or not training_maps:
            continue
        key = group_keys[index] if index < len(group_keys) else f"item:{index}"
        groups.setdefault(key, []).append(
            {
                "index": index,
                "summary": summary,
                "score": score,
                "features": _mean_feature_maps(training_maps),
                "counterfactual_prefix": _as_dict(
                    _as_dict(summary.get("episode_metadata")).get("counterfactual_prefix")
                ),
            }
        )
    result = []
    for group in groups.values():
        scores = [item["score"] for item in group]
        counterfactual_items = [item for item in group if item["counterfactual_prefix"]]
        if counterfactual_items:
            prefix_ids = {str(item["counterfactual_prefix"].get("prefix_id")) for item in counterfactual_items}
            branch_ids = {str(item["counterfactual_prefix"].get("id")) for item in counterfactual_items}
            if len(prefix_ids) != 1 or len(branch_ids) < min_group_size:
                continue
        if len(group) >= min_group_size and max(scores) - min(scores) >= min_score_gap:
            result.append(group)
    return result


def _relative_rank_advantages(
    scores: list[float],
    tie_epsilon: float,
    *,
    center: bool = True,
) -> list[float]:
    """Return tie-aware pairwise rank advantages.

    The full method uses centered win-minus-loss values. The no-centering
    ablation intentionally keeps only the non-negative pairwise win rate.
    """

    if len(scores) < 2:
        return [0.0 for _ in scores]
    denominator = float(len(scores) - 1)
    advantages: list[float] = []
    for score in scores:
        wins = sum(score - other > tie_epsilon for other in scores)
        if center:
            losses = sum(other - score > tie_epsilon for other in scores)
            advantages.append((wins - losses) / denominator)
        else:
            advantages.append(wins / denominator)
    if center:
        drift = sum(advantages)
        if abs(drift) > 1e-12:
            raise RuntimeError(f"Relative rank advantage is not zero-sum: drift={drift}")
    return advantages


def _bounded_score_allocation(
    scores: list[float],
    target_return: float,
    *,
    max_segment_abs: float,
    temperature: float,
) -> list[float] | None:
    """Allocate a signed return to contrastive segments without temporal broadcasting."""

    if not scores:
        return None
    if abs(target_return) <= 1e-12:
        return [0.0 for _ in scores]
    if max(scores) - min(scores) <= 1e-8:
        return None
    if abs(target_return) > len(scores) * max_segment_abs + 1e-9:
        raise ValueError("Segment cap cannot represent the requested trajectory return")

    signed_scores = scores if target_return > 0 else [-score for score in scores]
    peak = max(signed_scores)
    weights = [math.exp((score - peak) / temperature) for score in signed_scores]
    remaining = abs(target_return)
    allocations = [0.0 for _ in scores]
    active = set(range(len(scores)))
    while active and remaining > 1e-12:
        weight_sum = sum(weights[index] for index in active)
        capped: list[int] = []
        for index in active:
            share = remaining * weights[index] / max(1e-12, weight_sum)
            capacity = max_segment_abs - allocations[index]
            if share >= capacity - 1e-12:
                capped.append(index)
        if not capped:
            for index in active:
                allocations[index] += remaining * weights[index] / max(1e-12, weight_sum)
            remaining = 0.0
            break
        for index in capped:
            capacity = max(0.0, max_segment_abs - allocations[index])
            allocations[index] += capacity
            remaining -= capacity
            active.remove(index)
    if remaining > 1e-8:
        raise RuntimeError("Failed to allocate rank advantage within segment caps")
    sign = 1.0 if target_return > 0 else -1.0
    result = [sign * value for value in allocations]
    result[-1] += target_return - sum(result)
    return result


def _uncapped_score_allocation(
    scores: list[float],
    target_return: float,
    *,
    temperature: float,
) -> list[float] | None:
    """Softmax allocation without per-segment caps or water filling."""

    if not scores:
        return None
    if abs(target_return) <= 1e-12:
        return [0.0 for _ in scores]
    if max(scores) - min(scores) <= 1e-8:
        return None
    signed_scores = scores if target_return > 0 else [-score for score in scores]
    peak = max(signed_scores)
    weights = [math.exp((score - peak) / temperature) for score in signed_scores]
    weight_sum = max(1e-12, sum(weights))
    return [target_return * weight / weight_sum for weight in weights]


def _uniform_allocation(segment_count: int, target_return: float) -> list[float]:
    if segment_count <= 0:
        return []
    share = target_return / segment_count
    allocations = [share for _ in range(segment_count)]
    allocations[-1] += target_return - sum(allocations)
    return allocations


def _quantize_conserved_allocations(
    allocations: list[float],
    target_return: float,
    *,
    max_segment_abs: float,
) -> list[float]:
    """Round stored credits without changing their trajectory-level return."""

    def to_units(value: float) -> int:
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("Preference credit must be finite")
        return int(round(numeric * _CREDIT_SCALE))

    target_units = to_units(target_return)
    cap_units = to_units(max_segment_abs)
    if cap_units <= 0:
        raise ValueError("max_segment_abs must be positive")

    credit_units = [to_units(value) for value in allocations]
    if any(abs(value) > cap_units for value in credit_units):
        raise RuntimeError("Rounded segment credit exceeds the configured cap")

    residual_units = target_units - sum(credit_units)
    if residual_units:
        candidates = sorted(
            range(len(credit_units)),
            key=lambda index: (cap_units - abs(credit_units[index]), -index),
            reverse=True,
        )
        for index in candidates:
            adjusted = credit_units[index] + residual_units
            if abs(adjusted) <= cap_units:
                credit_units[index] = adjusted
                break
        else:
            raise RuntimeError(
                "Rounded segment credits cannot conserve the target return within caps"
            )

    if sum(credit_units) != target_units:
        raise RuntimeError("Quantized segment credits do not conserve the target return")
    return [value / _CREDIT_SCALE for value in credit_units]


def _apply_group_rank_advantage(
    group: list[dict[str, Any]],
    allocator: OnlinePairwiseSegmentAllocator,
    *,
    advantages: list[float],
    max_group_return: float,
    max_segment_abs: float,
    warmup_pairs: int,
    target_accuracy: float,
    min_reliability: float,
    score_temperature: float,
    allocation_mode: str = "learned",
    use_reliability_gate: bool = True,
    policy_uses_pre_update_allocator: bool = True,
    require_group_zero_sum: bool = True,
) -> dict[str, Any]:
    """Map group rank advantages to editing-segment endpoint rewards."""

    measured_reliability = allocator.reliability(
        warmup_pairs=warmup_pairs,
        target_accuracy=target_accuracy,
    )
    if use_reliability_gate and allocator.pair_examples < warmup_pairs:
        return {
            "applied": False,
            "reason": "allocator_warmup",
            "allocator_pair_examples": allocator.pair_examples,
            "required_warmup_pairs": warmup_pairs,
        }
    if use_reliability_gate and measured_reliability < min_reliability:
        return {
            "applied": False,
            "reason": "allocator_reliability_below_threshold",
            "allocator_pair_examples": allocator.pair_examples,
            "reliability": round(measured_reliability, 6),
        }
    reliability_scale = measured_reliability if use_reliability_gate else 1.0

    proposals: list[dict[str, Any]] = []
    min_segment_count = min(
        (len(_preference_segment_records(item["summary"])) for item in group),
        default=0,
    )
    if min_segment_count <= 0:
        return {"applied": False, "reason": "missing_attributable_segments"}
    if allocation_mode in {"terminal", "uncapped"}:
        group_budget = max_group_return * reliability_scale
    else:
        group_budget = min(max_group_return, min_segment_count * max_segment_abs) * reliability_scale
    for item, advantage in zip(group, advantages):
        records = _preference_segment_records(item["summary"])
        scores = [allocator.score(features) for _, features in records]
        target_return = advantage * group_budget
        quantization_cap = max_segment_abs
        if allocation_mode == "terminal":
            allocations = [0.0 for _ in records]
            allocations[-1] = target_return
            quantization_cap = max(max_group_return, max_segment_abs)
        elif allocation_mode == "uniform":
            allocations = _uniform_allocation(len(records), target_return)
        elif allocation_mode == "uncapped":
            allocations = _uncapped_score_allocation(
                scores,
                target_return,
                temperature=score_temperature,
            )
            quantization_cap = max(max_group_return, max_segment_abs)
        else:
            allocations = _bounded_score_allocation(
                scores,
                target_return,
                max_segment_abs=max_segment_abs,
                temperature=score_temperature,
            )
        if allocations is None:
            return {
                "applied": False,
                "reason": "allocator_has_no_segment_contrast",
                "failed_rollout_index": item["index"],
            }
        allocations = _quantize_conserved_allocations(
            allocations,
            target_return,
            max_segment_abs=quantization_cap,
        )
        proposals.append(
            {
                "item": item,
                "advantage": advantage,
                "target_return": target_return,
                "records": records,
                "scores": scores,
                "allocations": allocations,
            }
        )

    rollout_details: list[dict[str, Any]] = []
    group_credit_sum = 0.0
    for proposal in proposals:
        item = proposal["item"]
        summary = item["summary"]
        segment_credit = _as_dict(summary.get("segment_credit"))
        segments = [_as_dict(value) for value in _as_list(segment_credit.get("segments"))]
        for segment in segments:
            previous = _finite_float(segment.get("allocated_preference_credit"))
            segment["allocated_preference_credit"] = 0.0
            segment["segment_reward_total"] = round(
                _finite_float(segment.get("segment_reward_total")) - previous,
                6,
            )

        stage_allocations: dict[str, float] = {}
        explanations: list[dict[str, Any]] = []
        for (segment, features), score, allocation in zip(
            proposal["records"], proposal["scores"], proposal["allocations"]
        ):
            rounded = allocation
            segment["allocator_score"] = round(score, 6)
            segment["allocated_preference_credit"] = rounded
            segment["segment_reward_total"] = round(
                _finite_float(segment.get("segment_reward_total")) + rounded,
                6,
            )
            stage = str(segment.get("stage") or "other")
            stage_allocations[stage] = stage_allocations.get(stage, 0.0) + rounded
            contributions = sorted(
                (
                    (key, allocator.weights.get(key, 0.0) * value)
                    for key, value in features.items()
                    if abs(allocator.weights.get(key, 0.0) * value) > 1e-8
                ),
                key=lambda pair: abs(pair[1]),
                reverse=True,
            )[:5]
            explanations.append(
                {
                    "segment_id": segment.get("segment_id"),
                    "stage": stage,
                    "allocator_score": round(score, 6),
                    "credit": rounded,
                    "top_feature_contributions": [
                        {"feature": key, "contribution": round(value, 6)}
                        for key, value in contributions
                    ],
                }
            )

        actual_units = sum(
            int(round(_finite_float(segment.get("allocated_preference_credit")) * _CREDIT_SCALE))
            for segment in segments
        )
        expected_units = int(round(proposal["target_return"] * _CREDIT_SCALE))
        if actual_units != expected_units:
            raise RuntimeError(
                "Segment credit conservation failed: "
                f"{actual_units / _CREDIT_SCALE} != {expected_units / _CREDIT_SCALE}"
            )
        actual_return = actual_units / _CREDIT_SCALE
        stages = _as_dict(_as_dict(summary.get("stage_credit")).get("stages"))
        for stage, stage_raw in stages.items():
            stage_info = _as_dict(stage_raw)
            previous = _finite_float(stage_info.get("allocated_preference_backprop"))
            preference = round(stage_allocations.get(stage, 0.0), 6)
            stage_info["allocated_preference_backprop"] = preference
            stage_info["allocated_outcome_residual"] = round(
                _finite_float(stage_info.get("allocated_outcome_residual")) - previous + preference,
                6,
            )
            stage_info["stage_reward_total"] = round(
                _finite_float(stage_info.get("stage_reward_total")) - previous + preference,
                6,
            )
        segment_credit["preference_credit_sum"] = actual_return
        segment_credit["rank_advantage"] = round(proposal["advantage"], 6)
        segment_credit["allocator_reliability"] = round(measured_reliability, 6)
        detail = {
            "rollout_index": item["index"],
            "judge_score_audit_only": item["score"],
            "rank_advantage": round(proposal["advantage"], 6),
            "trajectory_preference_return": actual_return,
            "segment_explanations": explanations,
        }
        strategy = {
            "terminal": "terminal_rank_advantage",
            "uniform": "uniform_segment_rank_advantage",
            "uncapped": "uncapped_allocator_rank_advantage",
        }.get(allocation_mode, "frozen_allocator_rank_advantage")
        summary["preference_backprop"] = {
            "enabled": True,
            "applied": True,
            "strategy": strategy,
            "raw_judge_score_used_as_policy_reward": False,
            "current_group_rank_used_for_policy": True,
            "allocator_updated_after_policy_credit": policy_uses_pre_update_allocator,
            "policy_used_pre_update_allocator": policy_uses_pre_update_allocator,
            "reliability_gate_enabled": use_reliability_gate,
            "reliability": round(measured_reliability, 6),
            **detail,
        }
        rollout_details.append(detail)
        group_credit_sum += actual_return

    if require_group_zero_sum and abs(group_credit_sum) > 2e-5:
        raise RuntimeError(f"Group preference credit is not zero-sum: {group_credit_sum}")
    return {
        "applied": True,
        "strategy": {
            "terminal": "terminal_rank_advantage",
            "uniform": "uniform_segment_rank_advantage",
            "uncapped": "uncapped_allocator_rank_advantage",
        }.get(allocation_mode, "frozen_allocator_rank_advantage"),
        "group_size": len(group),
        "group_budget": round(group_budget, 6),
        "group_credit_sum": round(group_credit_sum, 6),
        "reliability": round(measured_reliability, 6),
        "reliability_gate_enabled": use_reliability_gate,
        "policy_used_pre_update_allocator": policy_uses_pre_update_allocator,
        "rollouts": rollout_details,
    }


def _append_metrics(metrics: dict[str, Any]) -> None:
    configured = os.environ.get("CRAYOTTER_RL_TRAINING_METRICS_JSONL", "").strip()
    if not configured:
        return
    path = Path(configured).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(metrics, ensure_ascii=False, separators=(",", ":")) + "\n")


def _build_training_pairs(
    groups: list[list[dict[str, Any]]],
    *,
    tie_epsilon: float,
    max_pairs_per_group: int,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    pair_budget = max_pairs_per_group * len(groups)
    for group in groups:
        ordered = sorted(group, key=lambda item: item["score"], reverse=True)
        for high_index, preferred in enumerate(ordered):
            for rejected in ordered[high_index + 1 :]:
                if preferred["score"] - rejected["score"] <= tie_epsilon:
                    continue
                pairs.append((preferred, rejected))
                if len(pairs) >= pair_budget:
                    return pairs
    return pairs


def _update_allocator_from_pairs(
    allocator: OnlinePairwiseSegmentAllocator,
    training_pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    *,
    calibration_ema_decay: float,
    learning_rate: float,
    l2: float,
) -> list[float]:
    # Every calibration observation uses the same pre-update snapshot.
    for preferred, rejected in training_pairs:
        allocator.observe_pair(
            preferred["features"],
            rejected["features"],
            ema_decay=calibration_ema_decay,
        )
    losses = [
        allocator.update_pair(
            preferred["features"],
            rejected["features"],
            learning_rate=learning_rate,
            l2=l2,
            sample_weight=1.0,
        )
        for preferred, rejected in training_pairs
    ]
    if losses:
        allocator.update_steps += 1
    return losses


def annotate_group_relative_preference_credit(
    reward_summaries: list[Any],
    *,
    group_keys: list[str],
    update_allocator: bool = True,
    apply_policy_credit: bool | None = None,
) -> None:
    """Apply the configured same-task preference-credit training variant."""

    variant = _preference_variant()
    if variant == "process_ppo" or not _env_bool(
        "CRAYOTTER_RL_SEGMENT_ALLOCATOR_ENABLED", False
    ):
        return

    summaries = [_as_dict(item) for item in reward_summaries]
    path = _state_path()
    if apply_policy_credit is None:
        apply_policy_credit = update_allocator
    warmup_pairs = _env_int("CRAYOTTER_RL_ALLOCATOR_WARMUP_PAIRS", 4, 0, 1000)
    min_group_size = _env_int("CRAYOTTER_RL_PREFERENCE_GROUP_MIN_SIZE", 2, 2, 32)
    tie_epsilon = _env_float("CRAYOTTER_RL_RANK_TIE_EPSILON", 3.0, 0.0, 50.0)
    learning_rate = _env_float("CRAYOTTER_RL_ALLOCATOR_LR", 0.08, 1e-4, 1.0)
    l2 = _env_float("CRAYOTTER_RL_ALLOCATOR_L2", 1e-3, 0.0, 0.1)
    max_pairs = _env_int("CRAYOTTER_RL_ALLOCATOR_MAX_PAIRS_PER_GROUP", 16, 1, 256)
    target_accuracy = _env_float("CRAYOTTER_RL_ALLOCATOR_TARGET_ACCURACY", 0.7, 0.51, 0.95)
    min_reliability = _env_float("CRAYOTTER_RL_ALLOCATOR_MIN_RELIABILITY", 0.1, 0.0, 1.0)
    score_temperature = _env_float("CRAYOTTER_RL_ALLOCATOR_SCORE_TEMPERATURE", 1.0, 0.05, 10.0)
    calibration_ema_decay = _env_float("CRAYOTTER_RL_ALLOCATOR_CALIBRATION_EMA", 0.9, 0.0, 0.999)
    max_group_return = _env_float("CRAYOTTER_RL_RANK_CREDIT_MAX_RETURN", 0.35, 0.0, 1.0)
    max_segment_abs = _env_float("CRAYOTTER_RL_RANK_CREDIT_MAX_SEGMENT", 0.25, 0.01, 1.0)

    allocation_mode = {
        "terminal_rank": "terminal",
        "uniform": "uniform",
        "no_cap_projection": "uncapped",
        "no_safeguards": "uncapped",
    }.get(variant, "learned")
    use_reliability_gate = variant not in {
        "terminal_rank",
        "no_reliability",
        "no_safeguards",
    }
    use_current_group_allocator = variant == "no_lag"
    center_advantages = variant != "no_group_centering"
    effective_allocator_update = update_allocator and variant != "terminal_rank"

    with _STATE_LOCK:
        allocator = OnlinePairwiseSegmentAllocator.load(path)
        frozen_examples = allocator.pair_examples
        frozen_steps = allocator.update_steps

        groups = _eligible_groups(summaries, group_keys, min_group_size, tie_epsilon)
        training_pairs = _build_training_pairs(
            groups,
            tie_epsilon=tie_epsilon,
            max_pairs_per_group=max_pairs,
        ) if effective_allocator_update else []
        losses: list[float] = []
        if use_current_group_allocator:
            losses = _update_allocator_from_pairs(
                allocator,
                training_pairs,
                calibration_ema_decay=calibration_ema_decay,
                learning_rate=learning_rate,
                l2=l2,
            )

        group_results: list[dict[str, Any]] = []
        for group in groups:
            advantages = _relative_rank_advantages(
                [float(item["score"]) for item in group],
                tie_epsilon,
                center=center_advantages,
            )
            for item, advantage in zip(group, advantages):
                item["rank_advantage"] = advantage
                item["summary"]["group_relative_preference"] = {
                    "judge_score_audit_only": item["score"],
                    "rank_advantage": round(advantage, 6),
                    "tie_epsilon": tie_epsilon,
                    "raw_judge_score_used_as_policy_reward": False,
                }
            if apply_policy_credit and any(abs(value) > 1e-12 for value in advantages):
                result = _apply_group_rank_advantage(
                    group,
                    allocator,
                    advantages=advantages,
                    max_group_return=max_group_return,
                    max_segment_abs=max_segment_abs,
                    warmup_pairs=warmup_pairs,
                    target_accuracy=target_accuracy,
                    min_reliability=min_reliability,
                    score_temperature=score_temperature,
                    allocation_mode=allocation_mode,
                    use_reliability_gate=use_reliability_gate,
                    policy_uses_pre_update_allocator=not use_current_group_allocator,
                    require_group_zero_sum=center_advantages,
                )
            else:
                result = {
                    "applied": False,
                    "reason": (
                        "policy_credit_disabled" if not apply_policy_credit else "all_rank_ties"
                    ),
                }
            compact = {key: value for key, value in result.items() if key != "rollouts"}
            for item in group:
                if not result.get("applied"):
                    item["summary"]["preference_backprop"] = {
                        "enabled": True,
                        "applied": False,
                        "allocator": (
                            "current_group_pairwise_segment_allocator"
                            if use_current_group_allocator
                            else "lagged_online_pairwise_segment_allocator"
                        ),
                        "allocator_state": str(path),
                        "frozen_pair_examples": frozen_examples,
                        "frozen_update_steps": frozen_steps,
                        **compact,
                    }
                item["summary"]["group_preference_backprop"] = compact
            group_results.append(result)
        if not use_current_group_allocator:
            losses = _update_allocator_from_pairs(
                allocator,
                training_pairs,
                calibration_ema_decay=calibration_ema_decay,
                learning_rate=learning_rate,
                l2=l2,
            )
        pair_count = len(losses)
        metrics = {
            "preference_variant": variant,
            "eligible_group_count": len(groups),
            "counterfactual_group_count": sum(
                bool(item.get("counterfactual_prefix"))
                for group in groups
                for item in group[:1]
            ),
            "pair_count": pair_count,
            "mean_pairwise_loss": round(sum(losses) / len(losses), 6) if losses else None,
            "weights_count": len(allocator.weights),
            "policy_used_pre_update_allocator": bool(
                apply_policy_credit
                and not use_current_group_allocator
                and variant != "terminal_rank"
            ),
            "allocator_update_enabled": effective_allocator_update,
            "policy_credit_enabled": bool(apply_policy_credit),
            "reliability_gate_enabled": use_reliability_gate,
            "group_centering_enabled": center_advantages,
            "allocation_mode": allocation_mode,
            "calibration_pairs": allocator.calibration_pairs,
            "calibration_accuracy_ema": round(allocator.calibration_accuracy_ema, 6),
            "calibration_log_loss_ema": round(allocator.calibration_log_loss_ema, 6),
            "allocator_reliability": round(
                allocator.reliability(
                    warmup_pairs=warmup_pairs,
                    target_accuracy=target_accuracy,
                ),
                6,
            ),
            "rank_advantage_group_count": len(group_results),
            "rank_advantage_applied_count": sum(bool(item.get("applied")) for item in group_results),
            "rank_advantage_group_credit_drift": round(
                sum(abs(_finite_float(item.get("group_credit_sum"))) for item in group_results),
                6,
            ),
            "tie_epsilon": tie_epsilon,
        }
        if effective_allocator_update:
            allocator.save(path, metrics)
            _append_metrics(
                {
                    "event": "preference_allocator_update",
                    "allocator_state": str(path),
                    "frozen_pair_examples": frozen_examples,
                    "frozen_update_steps": frozen_steps,
                    "post_update_pair_examples": allocator.pair_examples,
                    "post_update_steps": allocator.update_steps,
                    **metrics,
                }
            )

        for summary in summaries:
            summary["allocator_update"] = {
                **metrics,
                "post_update_pair_examples": allocator.pair_examples,
                "post_update_steps": allocator.update_steps,
                "final_video_score_used_for_group_order_only": True,
                "allocator_updated_after_policy_credit": bool(
                    effective_allocator_update and not use_current_group_allocator
                ),
            }
