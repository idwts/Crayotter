from __future__ import annotations

from ._shared import *


@tool
def download_bilibili_video(
    url: str,
    filename: str = "bilibili_video",
    prefer_h264: bool = True,
) -> str:
    """从 Bilibili 下载视频到工作目录。

    Args:
        url: B站视频的完整 URL 或 BV号。支持以下格式：
            - 完整 URL："https://www.bilibili.com/video/BV1xx411c7XZ"
            - 仅 BV 号："BV1xx411c7XZ"（会自动补全 URL）
        filename: 保存的文件名（不含扩展名），默认 "bilibili_video"。
            建议使用能体现视频内容的名称，如 "scut_intro_video"。
            文件将保存为 /workspace/{filename}.mp4，同时生成 /workspace/{BV号}.mp4 别名。
        prefer_h264: 是否优先下载 H.264 编码格式，默认 True（推荐保持默认）。
            True：优先选择 H.264/AVC 编码，兼容性最好；
            False：选择效果最佳（可能是 H.265/HEVC）但可能与 moviepy 不兼容。
    """
    output_path = _safe_output_video_path(filename, default_stem="bilibili_video")
    
    # 如果只提供了BV号，构建完整URL
    if url.startswith("BV"):
        url = f"https://www.bilibili.com/video/{url}"
    
    logger.info(
        "📥 开始下载Bilibili视频: url='%s', filename='%s', prefer_h264=%s",
        url,
        filename,
        prefer_h264,
    )
    try:
        if prefer_h264:
            format_selector = (
                "bv*[vcodec^=avc1][ext=mp4]+ba[ext=m4a]/"
                "b[vcodec^=avc1][ext=mp4]/"
                "bv*[ext=mp4]+ba[ext=m4a]/"
                "best[ext=mp4]/best"
            )
        else:
            format_selector = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

        cmd = [
            "yt-dlp",
            "-f", format_selector,
            "--merge-output-format", "mp4",
            "--prefer-free-formats",
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

        bvid = ""
        m = re.search(r"(BV[0-9A-Za-z]{10})", url)
        if m:
            bvid = m.group(1)

        alias_path = None
        if bvid:
            alias_path = WORKSPACE / f"{bvid}.mp4"
            if not alias_path.exists() and output_path.exists():
                try:
                    shutil.copy2(output_path, alias_path)
                except Exception as e:
                    logger.warning("⚠️ 生成BV别名文件失败: %s", e)

        return json.dumps({
            "status": "success",
            "path": str(output_path),
            "bvid": bvid,
            "alias_path": str(alias_path) if alias_path else "",
            "duration_seconds": round(duration, 1),
            "resolution": f"{width}x{height}",
            "fps": round(fps, 1),
            "source": "bilibili",
            "prefer_h264": prefer_h264,
            "format_selector": format_selector,
        }, ensure_ascii=False)
    except Exception as e:
        error_msg = f"下载B站视频出错: {e}"
        logger.error(f"❌ Bilibili下载异常: {e}", exc_info=True)
        return error_msg
