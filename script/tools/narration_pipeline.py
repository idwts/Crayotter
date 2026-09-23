from __future__ import annotations

from pathlib import Path
from typing import Any

from ._shared import (
    WORKSPACE,
    _resolve_workspace_input_path,
    _safe_output_video_path,
    tool_error,
    tool_success,
)


def compose_prepared_narration(
    video_path: str,
    prepared_segments: list[dict[str, Any]],
    output_name: str = "narrated_prepared",
) -> str:
    """Mix already generated narration audio artifacts onto a video."""
    try:
        resolved_video = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved_video is None:
            return tool_error("预生成旁白混合", f"视频不存在或不在WORKSPACE: {video_path}")
        if not prepared_segments:
            return tool_success(path=str(resolved_video), segment_count=0)

        from ._native_ffmpeg import (
            binaries_available,
            mix_narration_native,
            probe_media_duration,
            probe_video_optional,
        )

        if binaries_available():
            probe = probe_video_optional(resolved_video)
            if probe is not None:
                parts: list[tuple[Path, float, float, float | None]] = []
                native_warnings: list[str] = []
                for index, segment in enumerate(prepared_segments, start=1):
                    audio_path = _resolve_workspace_input_path(
                        str(segment.get("audio_path") or ""),
                        must_exist=True,
                    )
                    if audio_path is None:
                        native_warnings.append(f"段 {index}: 音频文件不存在")
                        continue
                    start = max(0.0, float(segment.get("start", 0.0)))
                    end = min(float(probe.duration), float(segment.get("end", 0.0)))
                    if end <= start:
                        native_warnings.append(f"段 {index}: 时间范围无效")
                        continue
                    slot = end - start
                    try:
                        audio_duration = probe_media_duration(audio_path)
                    except Exception:
                        audio_duration = slot
                    if audio_duration > slot:
                        native_warnings.append(f"段 {index}: TTS 超出时段，已截断")
                    parts.append((audio_path, start, 1.0, slot))
                if not parts:
                    return tool_error("预生成旁白混合", "没有可用旁白音频")
                output_path = _safe_output_video_path(output_name, default_stem="narrated_prepared")
                mix_narration_native(resolved_video, parts, output_path)
                return tool_success(
                    path=str(output_path),
                    segment_count=len(parts),
                    duration_seconds=round(float(probe.duration), 3),
                    warnings=native_warnings,
                )

        from moviepy.audio.AudioClip import CompositeAudioClip
        from moviepy.audio.io.AudioFileClip import AudioFileClip
        from moviepy.video.io.VideoFileClip import VideoFileClip

        video = VideoFileClip(str(resolved_video))
        audio_clips: list[Any] = []
        warnings: list[str] = []
        for index, segment in enumerate(prepared_segments, start=1):
            audio_path = _resolve_workspace_input_path(
                str(segment.get("audio_path") or ""),
                must_exist=True,
            )
            if audio_path is None:
                warnings.append(f"段 {index}: 音频文件不存在")
                continue
            start = max(0.0, float(segment.get("start", 0.0)))
            end = min(float(video.duration), float(segment.get("end", 0.0)))
            if end <= start:
                warnings.append(f"段 {index}: 时间范围无效")
                continue
            clip = AudioFileClip(str(audio_path))
            slot = end - start
            if clip.duration > slot:
                clip = clip.subclipped(0, slot)
                warnings.append(f"段 {index}: TTS 超出时段，已截断")
            audio_clips.append(clip.with_start(start))

        if not audio_clips:
            video.close()
            return tool_error("预生成旁白混合", "没有可用旁白音频")

        mixed_parts: list[Any] = []
        if video.audio is not None:
            mixed_parts.append(video.audio.with_volume_scaled(0.2))
        mixed_parts.extend(audio_clips)
        mixed = CompositeAudioClip(mixed_parts)
        final = video.with_audio(mixed)
        output_path = _safe_output_video_path(output_name, default_stem="narrated_prepared")
        final.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac",
            logger=None,
        )
        duration = float(final.duration)
        for clip in audio_clips:
            clip.close()
        final.close()
        video.close()
        return tool_success(
            path=str(output_path),
            segment_count=len(audio_clips),
            duration_seconds=round(duration, 3),
            warnings=warnings,
        )
    except Exception as exc:
        return tool_error("预生成旁白混合", exc)


def narration_audio_path(output_name: str, index: int) -> Path:
    safe_name = _safe_output_video_path(output_name, default_stem="narration").stem
    return WORKSPACE / f"{safe_name}_{index:03d}.mp3"
