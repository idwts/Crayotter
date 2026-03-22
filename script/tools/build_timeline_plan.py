from __future__ import annotations

from ._shared import *


@tool
def build_timeline_plan(
    clip_paths: list[str],
    target_duration: float | None = None,
    transition_duration: float = 0.8,
    overlap_for_crossfade: bool = True,
    output_name: str = "timeline_plan",
) -> str:
    """根据片段列表生成专业时间线规划（参考 CapCut timelines/audio_timelines 思路）。

    输出内容包含：
    - 每个片段在合并后时间轴中的 start/end（秒与微秒）
    - 相邻片段边界信息（可用于转场规划）
    - 目标时长偏差
    - 本地保存的 timeline JSON 路径

    Args:
        clip_paths: 片段路径列表，按播放顺序。
        target_duration: 目标总时长（秒），可选。
        transition_duration: 规划中的默认转场时长（秒）。
        overlap_for_crossfade: 是否按交叉转场重叠时间轴（默认 True）。
        output_name: 输出 JSON 名称（不含扩展名）。
    """
    try:
        if not clip_paths or not isinstance(clip_paths, list):
            return "时间线规划出错: clip_paths 必须是非空列表"

        resolved_paths: list[Path] = []
        for p in clip_paths:
            resolved = _resolve_workspace_input_path(p, must_exist=True)
            if resolved is None:
                return f"时间线规划出错: 文件不存在或不在WORKSPACE: {p}"
            resolved_paths.append(resolved)

        safe_transition = max(0.0, min(float(transition_duration), 2.5))

        timeline_items: list[dict[str, Any]] = []
        cursor = 0.0
        for idx, p in enumerate(resolved_paths):
            meta = _get_video_meta(str(p))
            clip_dur = float(meta.get("duration_seconds", 0.0) or 0.0)
            if clip_dur <= 0:
                return f"时间线规划出错: 无法读取片段时长: {p.name}"

            start_s = round(cursor, 3)
            end_s = round(start_s + clip_dur, 3)
            timeline_items.append(
                {
                    "index": idx,
                    "path": str(p),
                    "name": p.name,
                    "clip_duration_seconds": round(clip_dur, 3),
                    "start_seconds": start_s,
                    "end_seconds": end_s,
                    "start_us": int(start_s * 1_000_000),
                    "end_us": int(end_s * 1_000_000),
                }
            )

            cursor = end_s
            if overlap_for_crossfade and idx < len(resolved_paths) - 1:
                # 交叉转场会在时间轴上重叠一段，提前推进下一个片段起点
                cursor = max(0.0, round(cursor - safe_transition, 3))

        boundaries: list[dict[str, Any]] = []
        for i in range(len(timeline_items) - 1):
            left = timeline_items[i]
            right = timeline_items[i + 1]
            boundaries.append(
                {
                    "boundary_index": i,
                    "from_clip": left["name"],
                    "to_clip": right["name"],
                    "boundary_time_seconds": right["start_seconds"],
                    "suggested_transition_duration": safe_transition,
                }
            )

        total_duration = round(timeline_items[-1]["end_seconds"], 3) if timeline_items else 0.0
        duration_delta = (
            round(total_duration - float(target_duration), 3)
            if target_duration is not None and float(target_duration) > 0
            else None
        )

        output_path = _safe_output_data_path(output_name, suffix=".json", default_stem="timeline_plan")
        payload = {
            "status": "success",
            "overlap_for_crossfade": bool(overlap_for_crossfade),
            "default_transition_duration": safe_transition,
            "target_duration_seconds": target_duration,
            "total_duration_seconds": total_duration,
            "duration_delta_seconds": duration_delta,
            "clip_count": len(timeline_items),
            "timeline_items": timeline_items,
            "boundaries": boundaries,
            "timelines": [{"start": x["start_us"], "end": x["end_us"]} for x in timeline_items],
            "all_timelines": [{"start": 0, "end": int(total_duration * 1_000_000)}],
        }
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        return json.dumps(
            {
                "status": "success",
                "path": str(output_path),
                "clip_count": len(timeline_items),
                "total_duration_seconds": total_duration,
                "duration_delta_seconds": duration_delta,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return f"时间线规划出错: {e}"
