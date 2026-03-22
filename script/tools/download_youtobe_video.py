from __future__ import annotations

from ._shared import *


@tool
def download_youtobe_video(url: str, filename: str = "downloaded") -> str:
    """从 YouTube 下载视频到工作目录。

    Args:
        url: 视频的完整 YouTube URL，例如 "https://www.youtube.com/watch?v=xxxxx"
        filename: 保存的文件名（不含扩展名），默认 "downloaded"。
            建议使用能体现视频内容的名称，如 "avatar_battle_scene"。
            文件将保存为 /workspace/{filename}.mp4。
    """
    output_path = _safe_output_video_path(filename, default_stem="downloaded")
    logger.info(f"📥 开始下载YouTube视频: url='{url}', filename='{filename}'")    
    try:
        cmd = [
            "yt-dlp",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
            "--merge-output-format", "mp4",
            "-o", str(output_path),
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return f"下载失败: {result.stderr[:500]}"

        # 获取视频基本信息
        cap = cv2.VideoCapture(str(output_path))
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps if fps > 0 else 0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()

        if duration > MAX_DOWNLOAD_DURATION_SECONDS:
            try:
                if output_path.exists():
                    output_path.unlink()
            except Exception:
                pass
            return (
                f"下载失败: 下载后检测到时长 {duration:.1f} 秒，超过限制 "
                f"{MAX_DOWNLOAD_DURATION_SECONDS} 秒（10分钟），文件已删除"
            )

        return json.dumps({
            "status": "success",
            "path": str(output_path),
            "duration_seconds": round(duration, 1),
            "resolution": f"{width}x{height}",
            "fps": round(fps, 1),
        }, ensure_ascii=False)
    except Exception as e:
        error_msg = f"下载出错: {e}"
        logger.error(f"❌ YouTube下载异常: {e}", exc_info=True)
        return error_msg
