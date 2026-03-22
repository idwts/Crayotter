from __future__ import annotations

from ._shared import *


@tool
def inspect_video_duration(video_path: str) -> str:
    """检测视频时长、分辨率、帧率等基本信息。
    在剪辑后、合并后、导出前应主动调用此工具进行时长校验。

    Args:
        video_path: 要检测的视频文件路径，例如 "/workspace/merged.mp4"。
            支持工作目录内的任意 .mp4 文件（源视频或中间产物均可）。
    """
    try:
        resolved_input = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved_input is None:
            return f"检测失败: 文件不存在或不在WORKSPACE: {video_path}"
        meta = _get_video_meta(str(resolved_input))
        logger.info(
            "📏 时长检测: %s -> %.2fs (%s)",
            str(resolved_input),
            meta["duration_seconds"],
            meta["resolution"],
        )
        return json.dumps({"status": "success", "path": str(resolved_input), **meta}, ensure_ascii=False)
    except Exception as e:
        return f"时长检测出错: {e}"
