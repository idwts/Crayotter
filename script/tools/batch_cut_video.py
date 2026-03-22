from __future__ import annotations

from ._shared import *


@tool
def batch_cut_video(
    video_path: str,
    target_duration: float = 120.0,
    analysis_json_path: str = "",
) -> str:
    """根据 analyze_video 生成的分析JSON，从源视频批量裁剪所有推荐时间段。
    一次性产出多个片段，总时长尽量接近 target_duration。
    比多次调用 cut_video 更高效、更准确。首选此工具完成批量剪辑。

    Args:
        video_path: 源视频文件路径，必须是原始下载的视频文件。
            例如 "/workspace/scut_intro_video.mp4"。
            会自动匹配同名的 _analysis.json 文件读取剪辑时间段。
        target_duration: 期望裁剪出的片段总时长（秒），默认 120.0。
            工具会按时间顺序累积剪辑片段，直到总时长 ≥ target_duration 为止。
            例如目标两分钟就传 120.0，目标五分钟传 300.0。
        analysis_json_path: 分析JSON文件的路径（analyze_video 产出的 *_analysis.json）。
            留空（默认""）时会自动查找与 video_path 同名的 _analysis.json。
            仅当自动查找失败时才需要手动指定，例如 "/workspace/scut_intro_video_analysis.json"。
    """
    try:
        from moviepy.video.io.VideoFileClip import VideoFileClip

        resolved_input = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved_input is None:
            return f"批量剪辑出错: 输入视频不存在或不在WORKSPACE: {video_path}"

        # ---------- 查找分析JSON ----------
        analysis_data = None
        if analysis_json_path:
            json_resolved = _resolve_workspace_input_path(analysis_json_path, must_exist=True)
            if json_resolved:
                try:
                    with json_resolved.open("r", encoding="utf-8") as f:
                        analysis_data = json.load(f)
                except Exception:
                    pass

        if analysis_data is None:
            # 自动查找：先精确匹配源视频 stem，再取最新
            for fp in sorted(
                WORKSPACE.glob("*_analysis.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            ):
                try:
                    with fp.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                    src = str(data.get("source_video", ""))
                    if resolved_input.name in src or resolved_input.stem in src:
                        analysis_data = data
                        break
                    if analysis_data is None:
                        analysis_data = data  # fallback: 最新文件
                except Exception:
                    continue

        if analysis_data is None:
            return (
                f"批量剪辑出错: 未找到视频 {video_path} 的分析JSON。"
                "请先调用 analyze_video 分析视频内容。"
            )

        # ---------- 提取时间段 ----------
        segments = analysis_data.get("segments", [])
        if not segments:
            analysis_text = analysis_data.get("analysis_text", "")
            segments = _extract_time_segments_from_analysis(analysis_text)
        if not segments:
            return "批量剪辑出错: 分析结果中没有可用的时间段。请检查 analyze_video 的分析是否正常。"

        # 获取源视频时长以做边界裁剪
        meta = _get_video_meta(str(resolved_input))
        video_dur = meta["duration_seconds"]

        # ---------- 合并相邻短片段: 仅在上一段仍过短时合并，避免链式膨胀 ----------
        MIN_CLIP_SECONDS = 8.0
        merged_segments: list[dict[str, float]] = []
        for seg in segments:
            s, e = float(seg.get("start", 0)), float(seg.get("end", 0))
            if e <= s or s >= video_dur:
                continue
            e = min(e, video_dur)
            dur = e - s
            # 仅当前一段仍过短时，才允许把当前短片段并入上一段
            if merged_segments and dur < MIN_CLIP_SECONDS:
                prev = merged_segments[-1]
                prev_dur = prev["end"] - prev["start"]
                gap = s - prev["end"]
                if prev_dur < MIN_CLIP_SECONDS and gap <= 2.0:  # 间隔不超过2秒视为相邻
                    prev["end"] = max(prev["end"], e)
                    continue
            # 如果前一个片段合并后仍太短，也尝试扩展
            if merged_segments and (merged_segments[-1]["end"] - merged_segments[-1]["start"]) < MIN_CLIP_SECONDS:
                prev = merged_segments[-1]
                gap = s - prev["end"]
                if gap <= 5.0:
                    prev["end"] = max(prev["end"], e)
                    continue
            merged_segments.append({"start": round(s, 2), "end": round(min(e, video_dur), 2)})

        if len(merged_segments) < len(segments):
            logger.info(
                "🔗 片段合并: %d 个原始片段 → %d 个合并片段 (最小片段时长=%.0fs)",
                len(segments), len(merged_segments), MIN_CLIP_SECONDS,
            )

        # 按顺序选取片段，严格控制总时长不超过 target_duration
        selected: list[dict[str, float]] = []
        accum = 0.0
        safe_target = float(target_duration) if float(target_duration) > 0 else 0.0
        for seg in merged_segments:
            if safe_target and accum >= safe_target:
                break
            s, e = float(seg.get("start", 0)), float(seg.get("end", 0))
            if e <= s or s >= video_dur:
                continue
            e = min(e, video_dur)
            seg_dur = e - s
            if safe_target:
                remaining = safe_target - accum
                if remaining <= 0:
                    break
                if seg_dur > remaining:
                    e = s + remaining
                    seg_dur = remaining
            if seg_dur <= 0:
                continue
            selected.append({"start": round(s, 2), "end": round(e, 2), "duration": round(seg_dur, 2)})
            accum += seg_dur

        if not selected:
            return "批量剪辑出错: 分析中没有有效可裁剪的时间段。"

        # ---------- 执行批量裁剪 ----------
        clip_results: list[dict[str, Any]] = []
        for i, seg in enumerate(selected, 1):
            out_name = f"{resolved_input.stem}_clip_{i}"
            out_path = _safe_output_video_path(out_name, default_stem=f"clip_{i}")
            try:
                with VideoFileClip(str(resolved_input)) as clip:
                    sub = clip.subclipped(seg["start"], seg["end"])
                    sub.write_videofile(str(out_path), codec="libx264", audio_codec="aac", logger=None)
                clip_results.append({
                    "index": i,
                    "path": str(out_path),
                    "start": seg["start"],
                    "end": seg["end"],
                    "duration": seg["duration"],
                })
            except Exception as e:
                logger.warning("⚠️ 批量裁剪片段 %d 失败: %s", i, e)
                continue

        if not clip_results:
            return "批量剪辑出错: 所有片段裁剪均失败。"

        total_dur = sum(c["duration"] for c in clip_results)
        logger.info(
            "✂️ 批量剪辑完成: 源=%s, 片段=%d, 总时长=%.1fs, 目标=%.1fs",
            resolved_input.name, len(clip_results), total_dur, target_duration,
        )
        return json.dumps({
            "status": "success",
            "source_video": str(resolved_input),
            "clips": clip_results,
            "clip_count": len(clip_results),
            "total_duration": round(total_dur, 1),
            "target_duration": target_duration,
            "clip_paths": [c["path"] for c in clip_results],
        }, ensure_ascii=False)
    except Exception as e:
        return f"批量剪辑出错: {e}"
