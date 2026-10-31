from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from ._shared import (
    WORKSPACE,
    _resolve_workspace_input_path,
    _safe_output_video_path,
)


def align_prepared_narration(
    prepared_segments: list[dict[str, Any]], video_duration: float,
) -> list[dict[str, Any]]:
    """Keep starts fixed and fit complete measured speech before the next start."""
    from ._native_ffmpeg import probe_audio_duration

    if not math.isfinite(video_duration) or video_duration <= 0:
        raise ValueError("视频时长无效")
    ordered = sorted(prepared_segments, key=lambda segment: float(segment["start"]))
    aligned = []
    for index, segment in enumerate(ordered):
        start, planned_end = float(segment["start"]), float(segment["end"])
        if not all(math.isfinite(value) for value in (start, planned_end)) or start < 0 or planned_end <= start or start >= video_duration:
            raise ValueError(f"段 {index + 1}: 旁白时间范围无效")
        audio_path = _resolve_workspace_input_path(str(segment.get("audio_path") or ""), must_exist=True)
        if audio_path is None:
            raise ValueError(f"段 {index + 1}: 旁白音频不存在")
        measured = probe_audio_duration(audio_path)
        available_end = min(video_duration, float(ordered[index + 1]["start"])) if index + 1 < len(ordered) else video_duration
        end = start + measured
        if end > available_end + 0.001:
            raise ValueError(
                f"段 {index + 1}: 完整配音需要 {measured:.3f}s，"
                f"可用时间仅 {max(0.0, available_end - start):.3f}s；不能截断台词"
            )
        aligned.append({**segment, "audio_path": str(audio_path), "start": start,
                        "end": end, "planned_end": segment.get("planned_end", planned_end),
                        "audio_duration_seconds": measured})
    return aligned


def compose_prepared_narration(
    video_path: str,
    prepared_segments: list[dict[str, Any]],
    output_name: str = "narrated_prepared",
) -> str:
    """Mix already generated narration audio artifacts onto a video."""
    try:
        from ._native_ffmpeg import mix_narration_native, probe_video

        resolved_video = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved_video is None:
            return f"预生成旁白混合出错: 视频不存在或不在WORKSPACE: {video_path}"
        if not prepared_segments:
            return json.dumps(
                {"status": "success", "path": str(resolved_video), "segment_count": 0},
                ensure_ascii=False,
            )

        video_duration = probe_video(resolved_video).duration
        aligned = align_prepared_narration(prepared_segments, video_duration)
        audio_clips = [(Path(item["audio_path"]), item["start"], item["end"]) for item in aligned]

        output_path = _safe_output_video_path(output_name, default_stem="narrated_prepared")
        duration = mix_narration_native(resolved_video, output_path, audio_clips)
        return json.dumps(
            {
                "status": "success",
                "path": str(output_path),
                "segment_count": len(audio_clips),
                "duration_seconds": round(duration, 3),
                "segments": aligned,
                "warnings": [],
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return f"预生成旁白混合出错: {exc}"


def narration_audio_path(output_name: str, index: int) -> Path:
    safe_name = _safe_output_video_path(output_name, default_stem="narration").stem
    return WORKSPACE / f"{safe_name}_{index:03d}.mp3"
