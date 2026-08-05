from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from .judge import _parse_json_object, _sample_video_frames, load_judge_config
from .segment_credit import VIDEO_SUFFIXES, build_contiguous_segments


SEMANTIC_DIMENSIONS = (
    "request_fulfillment_delta",
    "coverage_delta",
    "narrative_delta",
    "pacing_delta",
    "preservation_delta",
    "visual_quality_delta",
)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _existing_video(paths: list[Any]) -> Path | None:
    for value in reversed(paths):
        path = Path(str(value))
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
            return path
    return None


def _select_artifact_segments(tool_events: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    segments = build_contiguous_segments(tool_events)
    candidates: list[dict[str, Any]] = []
    for segment in segments:
        path = _existing_video(list(segment.get("artifact_paths") or []))
        if path is None:
            continue
        candidates.append({"segment": segment, "path": path})
    if len(candidates) <= limit:
        return candidates

    priority = {"rough_cut": 3, "timeline_ordering": 4, "transition_pacing": 5, "subtitle_narration": 5,
                "audio_mixing": 5, "export_repair": 6}
    ranked = sorted(
        candidates,
        key=lambda item: (
            priority.get(str(item["segment"].get("stage")), 0),
            int(item["segment"].get("end_event_index", 0)),
        ),
        reverse=True,
    )[:limit]
    return sorted(ranked, key=lambda item: int(item["segment"].get("end_event_index", 0)))


def _baseline_path(episode_root: str, metadata: dict[str, Any]) -> Path | None:
    target = str(metadata.get("previous_final_target") or "").strip()
    if not target:
        return None
    path = Path(target)
    if not path.is_absolute():
        path = Path(episode_root) / path
    return path if path.is_file() else None


def _normalize_result(parsed: dict[str, Any], selected_ids: set[str], model: str, frame_count: int) -> dict[str, Any]:
    normalized: dict[str, dict[str, Any]] = {}
    raw_segments = parsed.get("segments")
    if isinstance(raw_segments, list):
        for item in raw_segments:
            if not isinstance(item, dict):
                continue
            segment_id = str(item.get("segment_id") or "")
            if segment_id not in selected_ids:
                continue
            values = {
                name: max(-1.0, min(1.0, _finite(item.get(name))))
                for name in SEMANTIC_DIMENSIONS
            }
            values["confidence"] = max(0.0, min(1.0, _finite(item.get("confidence"), 0.5)))
            values["evidence"] = str(item.get("evidence") or "")[:240]
            normalized[segment_id] = values
    return {
        "enabled": True,
        "eligible": bool(normalized),
        "model": model,
        "sampled_frames": frame_count,
        "dimensions": list(SEMANTIC_DIMENSIONS),
        "segments": normalized,
        "request_conditioned": True,
        "direct_policy_reward": False,
    }


async def evaluate_semantic_artifact_deltas(
    *,
    user_request: str,
    tool_events: list[dict[str, Any]],
    episode_root: str,
    episode_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Score semantic changes between a shared prefix and key editing artifacts.

    The returned values are allocator features only. They never become a direct
    process reward, which keeps subjective judgments behind the lagged credit
    allocator bottleneck.
    """

    if not _env_bool("CRAYOTTER_RL_SEMANTIC_ARTIFACT_ENABLED", False):
        return {"enabled": False, "reason": "semantic_artifact_disabled"}
    config = load_judge_config()
    if not config.enabled or not config.api_key:
        return {"enabled": False, "reason": "judge_unavailable"}
    try:
        limit = max(1, min(6, int(os.environ.get("CRAYOTTER_RL_SEMANTIC_MAX_SEGMENTS", "3"))))
    except (TypeError, ValueError):
        limit = 3
    try:
        frames_per_artifact = max(
            1,
            min(2, int(os.environ.get("CRAYOTTER_RL_SEMANTIC_FRAMES_PER_ARTIFACT", "2"))),
        )
    except (TypeError, ValueError):
        frames_per_artifact = 2
    selected = _select_artifact_segments(tool_events, limit)
    baseline = _baseline_path(episode_root, episode_metadata)
    if len(selected) < (1 if baseline else 2):
        return {"enabled": True, "eligible": False, "reason": "insufficient_artifact_chain"}

    visual_items: list[tuple[str, str, float, str]] = []
    if baseline:
        frames = _sample_video_frames(str(baseline), 1)
        if frames:
            visual_items.append(("shared_prefix", "prefix", frames[0][0], frames[0][1]))
    for item in selected:
        frames = _sample_video_frames(str(item["path"]), frames_per_artifact)
        if not frames:
            continue
        segment = item["segment"]
        visual_items.extend(
            [
                (
                    str(segment["segment_id"]),
                    str(segment["stage"]),
                    timestamp,
                    frame,
                )
                for timestamp, frame in frames
            ]
        )
    selected_ids = {item[0] for item in visual_items if item[0] != "shared_prefix"}
    if len(selected_ids) < (1 if baseline else 2):
        return {"enabled": True, "eligible": False, "reason": "insufficient_decodable_artifacts"}

    schema = {
        "segments": [
            {
                "segment_id": "segment_001",
                **{name: 0.0 for name in SEMANTIC_DIMENSIONS},
                "confidence": 0.0,
                "evidence": "short visible evidence",
            }
        ]
    }
    prompt = (
        "你是视频剪辑过程变化评审。画面按照共享前缀到后续剪辑 artifact 的顺序给出。"
        "针对用户需求，判断每个 artifact 相比前一个可见 artifact 带来的增量，而不是评价工具调用。"
        "每个 delta 取值 -1 到 1：正数表示改善，负数表示退化，无法观察则为 0 并降低 confidence。"
        "不要把成功导出本身当作语义改善。只返回 JSON。"
        f"\n用户需求：{user_request[:5000]}"
        f"\n输出格式：{json.dumps(schema, ensure_ascii=False)}"
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for segment_id, stage, timestamp, frame in visual_items:
        content.append(
            {
                "type": "text",
                "text": f"artifact={segment_id}, stage={stage}, sampled_timestamp={timestamp:.2f}s",
            }
        )
        content.append({"type": "image_url", "image_url": {"url": frame}})

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=2,
        )
        response = await client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": "只依据用户需求和可见 artifact 差异输出严格 JSON。"},
                {"role": "user", "content": content},
            ],
            temperature=0.0,
            max_tokens=1800,
            extra_body={"enable_thinking": False},
        )
        parsed = _parse_json_object(response.choices[0].message.content or "")
        result = _normalize_result(parsed, selected_ids, config.model, len(visual_items))
        result["artifact_sequence"] = [
            {"segment_id": segment_id, "stage": stage}
            for segment_id, stage, _, _ in visual_items
        ]
        return result
    except Exception as exc:
        return {
            "enabled": True,
            "eligible": False,
            "model": config.model,
            "error": str(exc)[:500],
        }
