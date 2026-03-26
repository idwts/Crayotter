from __future__ import annotations

import sys
import time

from ._shared import *


@tool
def download_bilibili_video(
    url: str,
    filename: str = "bilibili_video",
    prefer_h264: bool = True,
) -> str:
    """从 Bilibili 下载视频到工作目录。"""
    output_path = _safe_output_video_path(filename, default_stem="bilibili_video")

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
            sys.executable,
            "-m",
            "yt_dlp",
            "-f",
            format_selector,
            "--merge-output-format",
            "mp4",
            "--prefer-free-formats",
            "-o",
            str(output_path),
            url,
        ]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        heartbeat_interval = 15
        timeout_seconds = 300
        started = time.monotonic()
        stderr_text = ""
        while True:
            try:
                _, stderr_text = process.communicate(timeout=heartbeat_interval)
                break
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - started
                logger.info(
                    "⏳ Bilibili 下载进行中: filename='%s', elapsed=%.0fs, prefer_h264=%s",
                    filename,
                    elapsed,
                    prefer_h264,
                )
                if elapsed >= timeout_seconds:
                    process.kill()
                    _, stderr_text = process.communicate()
                    return f"下载失败: 单个视频下载超时（>{timeout_seconds}s）"

        if process.returncode != 0:
            return f"下载失败: {stderr_text[:500]}"

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
        match = re.search(r"(BV[0-9A-Za-z]{10})", url)
        if match:
            bvid = match.group(1)

        alias_path = None
        if bvid:
            alias_path = WORKSPACE / f"{bvid}.mp4"
            if not alias_path.exists() and output_path.exists():
                try:
                    shutil.copy2(output_path, alias_path)
                except Exception as exc:
                    logger.warning("⚠️ 生成BV别名文件失败: %s", exc)

        logger.info(
            "✅ Bilibili 下载完成: filename='%s', duration=%.1fs, resolution=%sx%s",
            filename,
            duration,
            width,
            height,
        )
        return json.dumps(
            {
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
            },
            ensure_ascii=False,
        )
    except OSError as exc:
        error_msg = f"下载B站视频出错: 系统策略或权限阻止启动下载程序: {exc}"
        logger.error("❌ Bilibili下载异常: %s", exc, exc_info=True)
        return error_msg
    except Exception as exc:
        error_msg = f"下载B站视频出错: {exc}"
        logger.error("❌ Bilibili下载异常: %s", exc, exc_info=True)
        return error_msg
