from __future__ import annotations

from ._shared import Any, Path, json, tool, _resolve_workspace_input_path, _get_video_meta


def _resolve_many_video_paths(video_paths: list[str]) -> tuple[list[Path], str | None]:
    resolved: list[Path] = []
    for raw in video_paths:
        rp = _resolve_workspace_input_path(raw, must_exist=True)
        if rp is None:
            return [], f"文件不存在或不在WORKSPACE: {raw}"
        resolved.append(rp)
    return resolved, None


@tool
def build_edit_timeline_from_segments(
    video_paths: list[str],
    target_duration: float | None = None,
    min_clip_duration: float = 1.0,
) -> str:
    """根据片段列表构建可训练的结构化时间线。

    该工具输出统一 timeline schema，便于 RLVR 固化：
    - 每个片段在成片时间轴上的 start/end
    - 每段有效时长
    - 全片累计时长与裁剪信息

    Args:
        video_paths: 片段路径列表（按顺序）。
        target_duration: 目标总时长（秒），若指定会在尾部自动截断。
        min_clip_duration: 最小片段时长，小于该值会被标记 warning。
    """
    try:
        if not video_paths:
            return "构建时间线出错: video_paths 为空"

        resolved, err = _resolve_many_video_paths(video_paths)
        if err:
            return f"构建时间线出错: {err}"

        timeline: list[dict[str, Any]] = []
        warnings: list[str] = []

        cursor = 0.0
        remain = float(target_duration) if target_duration and target_duration > 0 else None

        for idx, p in enumerate(resolved):
            meta = _get_video_meta(str(p))
            dur = float(meta.get("duration_seconds", 0.0))
            if dur <= 0.0:
                warnings.append(f"片段 {idx} 时长异常，已跳过: {p.name}")
                continue

            use_dur = dur
            if remain is not None:
                if remain <= 0.0:
                    break
                use_dur = min(use_dur, remain)
                remain -= use_dur

            if use_dur < min_clip_duration:
                warnings.append(
                    f"片段 {idx} 有效时长 {use_dur:.2f}s 小于最小建议 {min_clip_duration:.2f}s"
                )

            start_t = cursor
            end_t = cursor + use_dur
            timeline.append(
                {
                    "clip_index": idx,
                    "path": str(p),
                    "source_duration": round(dur, 3),
                    "source_in": 0.0,
                    "source_out": round(use_dur, 3),
                    "timeline_start": round(start_t, 3),
                    "timeline_end": round(end_t, 3),
                    "timeline_duration": round(use_dur, 3),
                    "trimmed": bool(use_dur < dur),
                }
            )
            cursor = end_t

        status = "success" if timeline else "failed"
        return json.dumps(
            {
                "status": status,
                "timeline": timeline,
                "total_duration": round(cursor, 3),
                "target_duration": target_duration,
                "warnings": warnings,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return f"构建时间线出错: {e}"


@tool
def align_narration_to_timeline(
    timeline: list[dict],
    narration_blocks: list[dict],
    max_chars_per_sec: float = 6.5,
    min_block_duration: float = 1.2,
) -> str:
    """将解说块自动对齐到结构化时间线，输出可直接用于 add_narration_segments 的 segments。

    narration_blocks 支持三种输入方式：
    1) 指定 clip_index
    2) 指定 clip_indices（跨多个片段）
    3) 指定 start/end（已在时间轴上）

    若都未提供，则按顺序均匀分配到 timeline。
    """
    try:
        if not isinstance(timeline, list) or not timeline:
            return "对齐出错: timeline 必须是非空列表"
        if not isinstance(narration_blocks, list) or not narration_blocks:
            return "对齐出错: narration_blocks 必须是非空列表"

        clips = sorted(
            [c for c in timeline if isinstance(c, dict)],
            key=lambda x: float(x.get("timeline_start", 0.0)),
        )
        if not clips:
            return "对齐出错: timeline 中没有有效片段"

        total_start = float(clips[0].get("timeline_start", 0.0))
        total_end = float(clips[-1].get("timeline_end", 0.0))
        total_dur = max(0.0, total_end - total_start)

        issues: list[str] = []
        aligned: list[dict[str, Any]] = []

        fallback_slot = total_dur / max(1, len(narration_blocks))

        for i, block in enumerate(narration_blocks):
            if not isinstance(block, dict):
                issues.append(f"块 {i}: 不是对象，已跳过")
                continue
            text = str(block.get("text", "")).strip()
            if not text:
                issues.append(f"块 {i}: text 为空，已跳过")
                continue

            start = None
            end = None

            if "start" in block and "end" in block:
                start = float(block.get("start", 0.0))
                end = float(block.get("end", 0.0))
            elif "clip_index" in block:
                ci = int(block.get("clip_index", -1))
                matched = [c for c in clips if int(c.get("clip_index", -9999)) == ci]
                if matched:
                    start = float(matched[0].get("timeline_start", 0.0))
                    end = float(matched[0].get("timeline_end", 0.0))
            elif "clip_indices" in block and isinstance(block.get("clip_indices"), list):
                cis = [int(v) for v in block.get("clip_indices", [])]
                matched = [c for c in clips if int(c.get("clip_index", -9999)) in cis]
                if matched:
                    start = min(float(c.get("timeline_start", 0.0)) for c in matched)
                    end = max(float(c.get("timeline_end", 0.0)) for c in matched)

            if start is None or end is None:
                start = total_start + i * fallback_slot
                end = min(total_end, start + fallback_slot)
                issues.append(f"块 {i}: 未提供可定位信息，已按顺序自动分配")

            start = max(total_start, float(start))
            end = min(total_end, float(end))
            if end <= start:
                end = min(total_end, start + max(min_block_duration, 0.8))

            dur = max(0.0, end - start)
            if dur < min_block_duration:
                expand = min_block_duration - dur
                end = min(total_end, end + expand)
                dur = max(0.0, end - start)

            cps = len(text) / max(dur, 0.01)
            if cps > max_chars_per_sec:
                issues.append(
                    f"块 {i}: 语速偏快 {cps:.1f} 字/秒，建议缩短文案或拉长时段"
                )

            aligned.append(
                {
                    "text": text,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(dur, 3),
                    "chars_per_sec": round(cps, 2),
                }
            )

        aligned.sort(key=lambda x: (x["start"], x["end"]))

        return json.dumps(
            {
                "status": "success",
                "segments": aligned,
                "issues": issues,
                "usage": "将 segments 直接传给 add_narration_segments；建议先经 validate_narration_timeline 复核",
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return f"对齐出错: {e}"


@tool
def validate_timeline_constraints(
    timeline: list[dict],
    target_duration: float | None = None,
    min_clip_duration: float = 0.8,
    max_clip_duration: float = 20.0,
    max_gap: float = 0.5,
) -> str:
    """校验编辑时间线约束，输出 fail/pass_with_warnings/pass。

    主要检查：
    - 时间段排序
    - 重叠与断层
    - 片段时长上下限
    - 总时长与目标时长偏差
    """
    try:
        if not isinstance(timeline, list) or not timeline:
            return "校验出错: timeline 必须是非空列表"

        rows = [r for r in timeline if isinstance(r, dict)]
        rows.sort(key=lambda x: float(x.get("timeline_start", 0.0)))

        issues: list[dict[str, Any]] = []

        total_duration = 0.0
        prev_end = None
        for i, r in enumerate(rows):
            s = float(r.get("timeline_start", 0.0))
            e = float(r.get("timeline_end", s))
            d = max(0.0, e - s)

            if e <= s:
                issues.append({"severity": "error", "index": i, "message": "timeline_end 必须大于 timeline_start"})
            if d < min_clip_duration:
                issues.append(
                    {
                        "severity": "warning",
                        "index": i,
                        "message": f"片段时长 {d:.2f}s 小于建议下限 {min_clip_duration:.2f}s",
                    }
                )
            if d > max_clip_duration:
                issues.append(
                    {
                        "severity": "warning",
                        "index": i,
                        "message": f"片段时长 {d:.2f}s 超过建议上限 {max_clip_duration:.2f}s",
                    }
                )

            if prev_end is not None:
                if s < prev_end:
                    issues.append(
                        {
                            "severity": "error",
                            "index": i,
                            "message": f"与上一个片段重叠 {prev_end - s:.2f}s",
                        }
                    )
                else:
                    gap = s - prev_end
                    if gap > max_gap:
                        issues.append(
                            {
                                "severity": "warning",
                                "index": i,
                                "message": f"与上一个片段间隔 {gap:.2f}s 超过建议 {max_gap:.2f}s",
                            }
                        )
            prev_end = max(prev_end or 0.0, e)
            total_duration = max(total_duration, e)

        if target_duration and target_duration > 0:
            delta = abs(total_duration - target_duration)
            ratio = delta / target_duration
            if ratio > 0.1:
                issues.append(
                    {
                        "severity": "warning",
                        "message": (
                            f"总时长偏差 {delta:.2f}s ({ratio*100:.1f}%)，"
                            "建议重新分配片段时长"
                        ),
                    }
                )

        status = "pass"
        if any(i.get("severity") == "error" for i in issues):
            status = "fail"
        elif issues:
            status = "pass_with_warnings"

        return json.dumps(
            {
                "status": status,
                "total_duration": round(total_duration, 3),
                "issues": issues,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return f"校验出错: {e}"
