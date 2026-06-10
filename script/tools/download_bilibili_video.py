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

    bvid = ""
    bvid_match = re.search(r"(BV[0-9A-Za-z]{10})", url)
    if bvid_match:
        bvid = bvid_match.group(1)

    cache_path = None
    if benchmark_mode() and bvid:
        cache_path = _media_cache_dir() / "downloads" / f"{bvid}.mp4"
        if cache_path.exists() and cache_path.stat().st_size > 0:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(cache_path, output_path)
            except Exception:
                shutil.copy2(cache_path, output_path)
            emit_benchmark_event(
                "cache_hit",
                {"kind": "download", "bvid": bvid, "path": cache_path.name},
            )
    
    logger.info(
        "📥 开始下载Bilibili视频: url='%s', filename='%s', prefer_h264=%s",
        url,
        filename,
        prefer_h264,
    )
    try:
        started_at = time.perf_counter()
        max_height = _positive_int_env("CRAYOTTER_DOWNLOAD_MAX_HEIGHT", 720)
        result: Any = True
        if cache_path is not None and output_path.exists() and output_path.stat().st_size > 0:
            result = None
        else:
            emit_benchmark_event(
                "download_started",
                {"bvid": bvid, "url": url, "max_height": max_height},
            )
        if prefer_h264:
            format_selector = (
                f"bv*[height<={max_height}][vcodec^=avc1][ext=mp4]+ba[ext=m4a]/"
                f"b[height<={max_height}][vcodec^=avc1][ext=mp4]/"
                f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
                f"best[height<={max_height}][ext=mp4]/best"
            )
        else:
            format_selector = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

        cmd = [
            "yt-dlp",
            "-f", format_selector,
            "--merge-output-format", "mp4",
            "--prefer-free-formats",
            "--no-playlist",
            "--concurrent-fragments", "4",
            "--retries", "2",
            "--fragment-retries", "2",
            "--socket-timeout", "20",
            "-o", str(output_path),
            url,
        ]
        if result is not None:
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

        if cache_path is not None and not cache_path.exists():
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(output_path, cache_path)
            except Exception:
                shutil.copy2(output_path, cache_path)
            emit_benchmark_event(
                "cache_stored",
                {"kind": "download", "bvid": bvid, "path": cache_path.name},
            )

        alias_path = None
        if bvid:
            alias_path = WORKSPACE / f"{bvid}.mp4"
            if not alias_path.exists() and output_path.exists():
                try:
                    os.link(output_path, alias_path)
                except Exception as e:
                    logger.warning("⚠️ 生成BV别名硬链接失败，使用索引路径: %s", e)
                    alias_path = output_path

        elapsed = time.perf_counter() - started_at
        emit_benchmark_event(
            "download_completed",
            {
                "bvid": bvid,
                "duration_seconds": round(elapsed, 3),
                "bytes": output_path.stat().st_size,
                "video_duration_seconds": round(duration, 3),
                "cache_hit": result is None,
            },
        )

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
            "download_elapsed_seconds": round(elapsed, 3),
            "cache_hit": result is None,
        }, ensure_ascii=False)
    except Exception as e:
        error_msg = f"下载B站视频出错: {e}"
        logger.error(f"❌ Bilibili下载异常: {e}", exc_info=True)
        return error_msg
