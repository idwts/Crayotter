from __future__ import annotations

from ._shared import *


def _load_transition_style(style: str) -> dict[str, Any]:
    preset_path = DATASETS_DIR / "transition_presets.json"
    if not preset_path.exists():
        return {
            "default": {"transition_type": "crossfade", "duration": 0.8, "audio_crossfade": 0.35},
            "rules": [],
        }
    try:
        with preset_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {
            "default": {"transition_type": "crossfade", "duration": 0.8, "audio_crossfade": 0.35},
            "rules": [],
        }

    styles = data.get("styles", {}) if isinstance(data, dict) else {}
    picked = styles.get(style) or styles.get("promo_campus")
    if not isinstance(picked, dict):
        return {
            "default": {"transition_type": "crossfade", "duration": 0.8, "audio_crossfade": 0.35},
            "rules": [],
        }
    return picked


def _load_clip_semantic_map(analysis_json_paths: list[str]) -> dict[str, str]:
    semantic_by_name: dict[str, str] = {}
    for raw in analysis_json_paths:
        resolved = _resolve_workspace_input_path(raw, must_exist=True)
        if resolved is None or resolved.suffix.lower() != ".json":
            continue
        try:
            with resolved.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        source_video = str(payload.get("source_video", ""))
        source_name = Path(source_video).name if source_video else ""
        semantic_chunks: list[str] = []
        if isinstance(payload.get("semantic_segments"), list):
            for seg in payload.get("semantic_segments", [])[:30]:
                if isinstance(seg, dict):
                    text = str(seg.get("semantic_text", "")).strip()
                    if text:
                        semantic_chunks.append(text)
        if not semantic_chunks:
            analysis_text = str(payload.get("analysis_text", "")).strip()
            if analysis_text:
                semantic_chunks.append(analysis_text[:1200])
        semantic_text = " ".join(semantic_chunks).lower()

        if source_name:
            semantic_by_name[source_name.lower()] = semantic_text
            semantic_by_name[Path(source_name).stem.lower()] = semantic_text
    return semantic_by_name


def _pick_transition_from_rules(
    joined_text: str,
    style_data: dict[str, Any],
) -> tuple[str, float, float, str]:
    default_cfg = style_data.get("default", {}) if isinstance(style_data, dict) else {}
    default_type = str(default_cfg.get("transition_type", "crossfade")).strip() or "crossfade"
    default_duration = float(default_cfg.get("duration", 0.8) or 0.8)
    default_audio_crossfade = float(default_cfg.get("audio_crossfade", 0.35) or 0.35)

    rules = style_data.get("rules", []) if isinstance(style_data, dict) else []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        keywords = [str(x).strip().lower() for x in rule.get("keywords", []) if str(x).strip()]
        if keywords and not any(k in joined_text for k in keywords):
            continue
        return (
            str(rule.get("transition_type", default_type) or default_type),
            float(rule.get("duration", default_duration) or default_duration),
            float(rule.get("audio_crossfade", default_audio_crossfade) or default_audio_crossfade),
            f"rule_match:{','.join(keywords[:4])}" if keywords else "rule_match",
        )

    return default_type, default_duration, default_audio_crossfade, "default"


@tool
def plan_transition_for_clips(
    clip_paths: list[str],
    style: str = "promo_campus",
    analysis_json_paths: list[str] | None = None,
    output_name: str = "transition_plan",
) -> str:
    """为片段序列生成逐边界转场方案（专业化转场规划）。

    参考了 pyJianYingDraft 的“转场作为边界元数据”与 capcut-mate 的“先规划再执行”思路。

    Args:
        clip_paths: 片段路径列表（按播放顺序）。
        style: 转场风格预设名（promo_campus/documentary/energetic）。
        analysis_json_paths: 可选分析JSON列表，用于语义匹配转场规则。
        output_name: 输出 JSON 名称（不含扩展名）。
    """
    try:
        if not clip_paths or not isinstance(clip_paths, list):
            return "转场规划出错: clip_paths 必须是非空列表"

        resolved_paths: list[Path] = []
        clip_durations: list[float] = []
        for p in clip_paths:
            resolved = _resolve_workspace_input_path(p, must_exist=True)
            if resolved is None:
                return f"转场规划出错: 文件不存在或不在WORKSPACE: {p}"
            resolved_paths.append(resolved)
            clip_durations.append(float(_get_video_meta(str(resolved)).get("duration_seconds", 0.0) or 0.0))

        if len(resolved_paths) < 2:
            return json.dumps(
                {
                    "status": "success",
                    "message": "片段数不足2，无需转场规划",
                    "transition_plan": [],
                },
                ensure_ascii=False,
            )

        style_data = _load_transition_style(style)
        semantic_map = _load_clip_semantic_map(analysis_json_paths or [])

        transition_plan: list[dict[str, Any]] = []
        for i in range(len(resolved_paths) - 1):
            left = resolved_paths[i]
            right = resolved_paths[i + 1]
            left_key = left.name.lower()
            right_key = right.name.lower()
            left_stem = left.stem.lower()
            right_stem = right.stem.lower()

            joined_text = " ".join(
                [
                    semantic_map.get(left_key, ""),
                    semantic_map.get(left_stem, ""),
                    semantic_map.get(right_key, ""),
                    semantic_map.get(right_stem, ""),
                    left_stem,
                    right_stem,
                ]
            ).lower()

            t_type, t_duration, audio_crossfade, source = _pick_transition_from_rules(joined_text, style_data)
            left_dur = clip_durations[i]
            right_dur = clip_durations[i + 1]
            safe_limit = max(0.15, min(left_dur, right_dur) * 0.45) if left_dur > 0 and right_dur > 0 else 1.0
            safe_duration = round(max(0.1, min(float(t_duration), safe_limit, 2.5)), 3)
            safe_audio_crossfade = round(max(0.0, min(float(audio_crossfade), safe_duration)), 3)

            transition_plan.append(
                {
                    "index": i,
                    "from_clip": left.name,
                    "to_clip": right.name,
                    "transition_type": t_type,
                    "duration": safe_duration,
                    "audio_crossfade": safe_audio_crossfade,
                    "selection_source": source,
                }
            )

        output_path = _safe_output_data_path(output_name, suffix=".json", default_stem="transition_plan")
        payload = {
            "status": "success",
            "style": style,
            "clip_count": len(resolved_paths),
            "transition_count": len(transition_plan),
            "transition_plan": transition_plan,
            "clip_paths": [str(p) for p in resolved_paths],
        }
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        return json.dumps(
            {
                "status": "success",
                "path": str(output_path),
                "transition_count": len(transition_plan),
                "style": style,
                "transition_plan": transition_plan,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return f"转场规划出错: {e}"
