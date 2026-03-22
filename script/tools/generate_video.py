from __future__ import annotations

from ._shared import *
from .export_video import export_video as _export_video_tool


@tool
def generate_video(
    input_path: str,
    output_name: str = "output_final",
    resolution: str = "1080p",
) -> str:
    """兼容导出工具别名（generate_video -> export_video）。

    Args:
        input_path: 输入视频路径。
        output_name: 输出文件名（不含扩展名）。
        resolution: 输出分辨率（720p/1080p/4k）。
    """
    try:
        result = _export_video_tool.invoke(
            {
                "input_path": input_path,
                "output_name": output_name,
                "resolution": resolution,
            }
        )
        return str(result)
    except Exception as e:
        return f"生成视频出错: {e}"
