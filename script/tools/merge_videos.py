from __future__ import annotations

from ._shared import *


@tool
def merge_videos(
    video_paths: list[str],
    output_name: str = "merged",
    target_duration: float | None = None,
    tolerance: float = 0.15,
) -> str:
    """将多个视频片段按顺序合并为一个视频。
    若指定了 target_duration，末尾超出目标时长的部分会被自动截断。

    Args:
        video_paths: 要合并的视频文件路径列表，**按播放顺序排列**。
            例如 ["/workspace/clip_1.mp4", "/workspace/clip_2.mp4"]。
            所有路径必须是工作目录内存在的真实文件。
        output_name: 输出文件名（不含扩展名），默认 "merged"。
            输出文件将保存为 /workspace/{output_name}.mp4。
        target_duration: 合并后的目标总时长（秒）。若指定，当累积时长达到
            target_duration × (1 + tolerance) 时停止添加后续片段。
            不指定（默认 None）则合并所有片段不截断。
        tolerance: 时长超出容忍比例，默认 0.15（即容忍超出 15%）。
            仅在 target_duration 不为 None 时生效，一般无需修改。
    """
    try:
        from moviepy import concatenate_videoclips
        from moviepy.video.io.VideoFileClip import VideoFileClip

        output_path = _safe_output_video_path(output_name, default_stem="merged")
        if not video_paths:
            return "合并出错: 没有可用的视频片段"

        clips: list[Any] = []
        remaining = target_duration if target_duration and target_duration > 0 else None

        for raw_path in video_paths:
            if remaining is not None and remaining <= 0:
                break
            resolved_input = _resolve_workspace_input_path(raw_path, must_exist=True)
            if resolved_input is None:
                return f"合并出错: 文件不存在或不在WORKSPACE: {raw_path}"
            p = str(resolved_input)
            clip = VideoFileClip(p)
            if remaining is not None and clip.duration > remaining * (1 + tolerance):
                logger.info(f"⏱️ 合并阶段裁剪片段以满足时长限制: {p}, 原时长={clip.duration:.1f}s, 目标裁剪={remaining:.1f}s")
                clip = clip.subclipped(0, remaining)
            clips.append(clip)
            if remaining is not None:
                remaining -= clip.duration

        if not clips:
            return "合并出错: 没有可合并的有效片段"

        target_w, target_h = clips[0].size
        resized: list[Any] = []
        for c in clips:
            if c.size != (target_w, target_h):
                c = c.resized((target_w, target_h))
            resized.append(c)

        final = concatenate_videoclips(resized, method="compose")
        final.write_videofile(
            str(output_path), codec="libx264", audio_codec="aac", logger=None
        )
        total_dur = sum(c.duration for c in clips)
        for c in clips:
            c.close()
        final.close()

        return json.dumps({
            "status": "success",
            "path": str(output_path),
            "total_duration": round(total_dur, 1),
            "num_clips": len(clips),
            "target_duration": target_duration,
        }, ensure_ascii=False)
    except Exception as e:
        return f"合并出错: {e}"
