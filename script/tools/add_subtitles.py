from __future__ import annotations

from ._shared import *
from .subtitle_layout import burn_subtitles, resolve_subtitle_font


@tool
def add_subtitles(
    video_path: str,
    subtitles: list[dict],
    output_name: str = "subtitled",
) -> str:
    """为视频添加字幕（不含配音），字幕按时间段显示在画面底部。

    适合已有配音的视频补加字幕，或为保留原声的片段添加解说字幕。

    Args:
        video_path: 输入视频文件路径，例如 "/workspace/final.mp4"。
        subtitles: 字幕列表，每个元素是一个 dict：
            - "text": str — 字幕文字（必填）
            - "start": float — 显示开始时间（秒）（必填）
            - "end": float — 显示结束时间（秒）（必填）
            示例：
            [
                {"text": "新疆大学始建于1924年", "start": 10.0, "end": 15.0},
                {"text": "是国家双一流建设高校", "start": 15.0, "end": 20.0}
            ]
        output_name: 输出文件名（不含扩展名），默认 "subtitled"。
    """
    try:
        resolved_video = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved_video is None:
            return f"字幕添加出错: 输入视频不存在或不在WORKSPACE: {video_path}"

        if not subtitles or not isinstance(subtitles, list):
            return "字幕添加出错: subtitles 参数必须是非空列表"

        font_path = resolve_subtitle_font()
        if not font_path:
            return "字幕添加出错: 未找到可用字幕字体"
        output_path = _safe_output_video_path(output_name, default_stem="subtitled")
        count = burn_subtitles(resolved_video, output_path, subtitles, font_path)

        return json.dumps({
            "status": "success",
            "path": str(output_path),
            "subtitle_count": count,
        }, ensure_ascii=False)
    except Exception as e:
        return f"字幕添加出错: {e}"
