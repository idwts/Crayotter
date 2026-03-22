from __future__ import annotations

from ._shared import Any, json, tool, _get_video_meta, _resolve_workspace_input_path


@tool
def validate_narration_timeline(
    video_path: str,
    segments: list[dict],
    min_segment_duration: float = 1.2,
    max_silence_gap: float = 8.0,
) -> str:
    """校验分段旁白时间线，提前发现音画不同步风险。

    在调用 add_narration_segments 前先执行本工具，避免：
    - 时间段越界
    - 段落重叠
    - 旁白段过短导致语速异常
    - 长时间留白导致叙事断裂

    Args:
        video_path: 待配音的视频路径。
        segments: 旁白分段列表，格式与 add_narration_segments 一致。
        min_segment_duration: 单段旁白最短建议时长（秒）。
        max_silence_gap: 相邻两段之间建议最大留白（秒）。
    """
    try:
        resolved_video = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved_video is None:
            return f"校验失败: 输入视频不存在或不在WORKSPACE: {video_path}"

        if not isinstance(segments, list) or not segments:
            return "校验失败: segments 必须是非空列表"

        meta = _get_video_meta(str(resolved_video))
        video_dur = float(meta.get("duration_seconds", 0.0))
        if video_dur <= 0:
            return "校验失败: 无法读取视频时长"

        normalized: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []

        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                issues.append({"index": i, "severity": "error", "message": "段落不是对象"})
                continue

            text = str(seg.get("text", "")).strip()
            if not text:
                issues.append({"index": i, "severity": "error", "message": "text 为空"})
                continue

            try:
                start = float(seg.get("start", 0.0))
                end = float(seg.get("end", 0.0))
            except Exception:
                issues.append({"index": i, "severity": "error", "message": "start/end 不是数字"})
                continue

            if start < 0:
                issues.append({"index": i, "severity": "warning", "message": "start 小于 0，已自动修正为 0"})
                start = 0.0
            if end > video_dur:
                issues.append(
                    {
                        "index": i,
                        "severity": "warning",
                        "message": f"end 超出视频时长，已自动修正为 {video_dur:.2f}",
                    }
                )
                end = video_dur
            if end <= start:
                issues.append({"index": i, "severity": "error", "message": "end 必须大于 start"})
                continue

            dur = end - start
            if dur < min_segment_duration:
                issues.append(
                    {
                        "index": i,
                        "severity": "warning",
                        "message": f"段落时长 {dur:.2f}s 过短，建议 >= {min_segment_duration:.2f}s",
                    }
                )

            char_len = len(text)
            cps = char_len / max(dur, 0.01)
            if cps > 7.0:
                issues.append(
                    {
                        "index": i,
                        "severity": "warning",
                        "message": f"语速可能过快: {cps:.1f} 字/秒，建议减少文案或延长时长",
                    }
                )

            normalized.append({"index": i, "text": text, "start": round(start, 3), "end": round(end, 3)})

        normalized.sort(key=lambda x: (x["start"], x["end"]))

        overlaps: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        for i in range(1, len(normalized)):
            prev = normalized[i - 1]
            cur = normalized[i]
            if cur["start"] < prev["end"]:
                overlaps.append(
                    {
                        "left_index": prev["index"],
                        "right_index": cur["index"],
                        "overlap_seconds": round(prev["end"] - cur["start"], 3),
                    }
                )
            gap = cur["start"] - prev["end"]
            if gap > max_silence_gap:
                gaps.append(
                    {
                        "left_index": prev["index"],
                        "right_index": cur["index"],
                        "gap_seconds": round(gap, 3),
                    }
                )

        for ov in overlaps:
            issues.append(
                {
                    "severity": "error",
                    "message": (
                        f"段 {ov['left_index']} 与段 {ov['right_index']} 重叠 "
                        f"{ov['overlap_seconds']:.2f}s"
                    ),
                }
            )

        for gp in gaps:
            issues.append(
                {
                    "severity": "warning",
                    "message": (
                        f"段 {gp['left_index']} 到段 {gp['right_index']} 留白 "
                        f"{gp['gap_seconds']:.2f}s，建议检查叙事连续性"
                    ),
                }
            )

        coverage = 0.0
        for seg in normalized:
            coverage += max(0.0, seg["end"] - seg["start"])

        status = "pass"
        if any(i.get("severity") == "error" for i in issues):
            status = "fail"
        elif issues:
            status = "pass_with_warnings"

        return json.dumps(
            {
                "status": status,
                "video_path": str(resolved_video),
                "video_duration": round(video_dur, 3),
                "segment_count": len(normalized),
                "narration_coverage_seconds": round(coverage, 3),
                "narration_coverage_ratio": round(coverage / max(video_dur, 0.01), 4),
                "normalized_segments": normalized,
                "issues": issues,
                "recommendation": (
                    "若 status=fail，请先修复重叠/越界后再调用 add_narration_segments。"
                    "若 pass_with_warnings，可按 issues 逐项优化。"
                ),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return f"校验失败: {e}"
