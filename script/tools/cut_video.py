from __future__ import annotations

from ._shared import *


@tool
def cut_video(
    input_path: str,
    start_time: float,
    end_time: float,
    output_name: str = "",
) -> str:
    """从视频中裁剪指定时间段的片段。
    如果需要从同一视频裁剪多个片段，推荐使用 batch_cut_video 替代多次调用此工具。

    Args:
        input_path: 输入视频文件的完整路径，例如 "/workspace/source.mp4"
        start_time: 裁剪开始时间（秒），例如 10.5（表示第 10.5 秒）
        end_time: 裁剪结束时间（秒），例如 45.0（表示第 45 秒）。
            必须大于 start_time，且不超过视频总时长。
        output_name: 输出文件名（不含扩展名），为空则自动生成 clip_{start}_{end}.mp4。
            例如 "intro_highlight"
    """
    try:
        from moviepy.video.io.VideoFileClip import VideoFileClip

        resolved_input = _resolve_workspace_input_path(input_path, must_exist=True)
        if resolved_input is None:
            return f"剪辑出错: 输入视频不在WORKSPACE或不存在: {input_path}"

        if not output_name:
            output_name = f"clip_{start_time:.0f}_{end_time:.0f}"
        output_path = _safe_output_video_path(output_name, default_stem="clip")

        with VideoFileClip(str(resolved_input)) as clip:
            sub_clip = clip.subclipped(start_time, end_time)
            sub_clip.write_videofile(
                str(output_path), codec="libx264", audio_codec="aac", logger=None
            )
        return json.dumps({
            "status": "success",
            "path": str(output_path),
            "duration": round(end_time - start_time, 1),
        }, ensure_ascii=False)
    except Exception as e:
        return f"剪辑出错: {e}"
