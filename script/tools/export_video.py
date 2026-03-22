from __future__ import annotations

from ._shared import *


@tool
def export_video(
    input_path: str,
    output_name: str = "output_final",
    resolution: str = "1080p",
) -> str:
    """导出最终成品视频，可指定输出分辨率并重新编码为高质量 H.264。
    这是视频制作流程的最后一步，在 add_narration 完成后调用。

    Args:
        input_path: 输入视频文件路径（已完成剪辑/合并/旁白的视频）。
            例如 "/workspace/narrated.mp4"
        output_name: 输出文件名（不含扩展名），默认 "output_final"。
            输出文件将保存为 /workspace/{output_name}.mp4。
        resolution: 输出分辨率，仅支持以下三个值（大小写敏感）：
            - "720p"：1280×720，文件较小，适合预览
            - "1080p"（默认）：1920×1080，标准高清
            - "4k"：3840×2160，超高清（编码耗时较长）
    """
    try:
        from moviepy.video.io.VideoFileClip import VideoFileClip

        resolved_input = _resolve_workspace_input_path(input_path, must_exist=True)
        if resolved_input is None:
            return f"导出出错: 输入视频不存在或不在WORKSPACE: {input_path}"

        res_map = {"720p": (1280, 720), "1080p": (1920, 1080), "4k": (3840, 2160)}
        target = res_map.get(resolution, (1920, 1080))
        output_path = _safe_output_video_path(output_name, default_stem="output_final")

        clip = VideoFileClip(str(resolved_input))
        if clip.size != target:
            clip = clip.resized(target)
        clip.write_videofile(
            str(output_path), codec="libx264", audio_codec="aac",
            bitrate="8000k", logger=None
        )
        dur = clip.duration
        clip.close()

        return json.dumps({
            "status": "success",
            "path": str(output_path),
            "resolution": resolution,
            "duration": round(dur, 1),
        }, ensure_ascii=False)
    except Exception as e:
        return f"导出出错: {e}"
