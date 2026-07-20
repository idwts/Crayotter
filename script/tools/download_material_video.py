from __future__ import annotations

from ._shared import *
from .download_bilibili_video import _download_via_bilibili_api
from .material_sources import detect_platform_from_url
from .search_bilibili_video import search_bilibili_video
from .source_adapters import DownloadRequest
from .search_material_sources import _source_registry


@tool
def download_material_video(
    url: str,
    source: str = "",
    filename: str = "material_video",
    prefer_h264: bool = True,
    fallback_query: str = "",
    fallback_to_bilibili: bool = False,
) -> str:
    """下载任意支持平台素材，并清洗为剪辑兼容的 MP4/H.264/AAC。"""
    original_url = str(url or "").strip()
    platform = str(source or detect_platform_from_url(original_url) or "unknown").strip() or "unknown"
    original_platform = platform
    output_path = _safe_output_video_path(filename, default_stem="material_video")
    if not original_url:
        return _error(platform, original_url, "invalid_input", "missing url")
    if platform == "bilibili" and original_url.startswith("BV"):
        original_url = f"https://www.bilibili.com/video/{original_url}"

    logger.info("📥 开始下载素材: source='%s', url='%s', filename='%s'", platform, original_url, filename)
    started_at = time.perf_counter()
    max_height = _positive_int_env("CRAYOTTER_DOWNLOAD_MAX_HEIGHT", 720)
    target_fps = _positive_int_env("CRAYOTTER_STANDARDIZE_TARGET_FPS", 30)
    loudnorm_target = _float_env("CRAYOTTER_AUDIO_LOUDNORM_TARGET", 0.0)
    tmp_path = output_path.with_name(f"{output_path.stem}_raw{output_path.suffix}")
    try:
        if tmp_path.exists():
            tmp_path.unlink()
        if output_path.exists():
            output_path.unlink()

        adapter_payload: dict[str, Any] | None = None
        if platform in {"douyin", "xiaohongshu", "rednote"}:
            adapter_result = _source_registry().download(
                DownloadRequest(
                    url=original_url,
                    platform="xiaohongshu" if platform == "rednote" else platform,
                    destination=tmp_path,
                    timeout_seconds=60.0,
                    auth_profile=str(os.environ.get("CRAYOTTER_BROWSER_AUTH_PROFILE", "") or ""),
                )
            )
            adapter_payload = adapter_result.to_dict()
            adapter_succeeded = bool(adapter_payload.get("path")) and adapter_payload.get("status") in {
                "success",
                "partial",
            }
            result = subprocess.CompletedProcess(
                args=[f"{platform}_source_adapter"],
                returncode=0 if adapter_succeeded else 1,
                stdout=json.dumps(adapter_payload, ensure_ascii=False),
                stderr="" if adapter_succeeded else json.dumps(adapter_payload, ensure_ascii=False),
            )
        else:
            result = _run_yt_dlp_download(original_url, tmp_path, max_height=max_height, prefer_h264=prefer_h264)
        fallback_used = False
        fallback_url = ""
        fallback_reason = ""
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "").strip()
            if platform == "bilibili":
                bvid_match = re.search(r"(BV[0-9A-Za-z]{10})", original_url)
                try:
                    _download_via_bilibili_api(
                        original_url,
                        bvid_match.group(1) if bvid_match else "",
                        tmp_path,
                    )
                except Exception as exc:
                    return _error(
                        platform,
                        original_url,
                        "download_failed",
                        f"yt-dlp={error_text[:350]}; api_fallback={exc}",
                    )
            else:
                if fallback_to_bilibili and str(fallback_query or "").strip():
                    try:
                        fallback_candidate, fallback_url = _download_bilibili_fallback(
                            str(fallback_query).strip(),
                            tmp_path,
                            max_height=max_height,
                            prefer_h264=prefer_h264,
                        )
                        platform = "bilibili"
                        fallback_used = True
                        fallback_reason = error_text[:500]
                        logger.info(
                            "↩️ 非 B 站素材下载失败，已降级到 Bilibili 候选: %s",
                            fallback_candidate.get("title") or fallback_url,
                        )
                    except Exception as exc:
                        return _error(
                            platform,
                            original_url,
                            "download_failed",
                            f"{error_text[:350]}; bilibili_fallback={exc}",
                        )
                else:
                    return _error(platform, original_url, "download_failed", error_text[:500])

        if not tmp_path.exists() or tmp_path.stat().st_size == 0:
            return _error(platform, original_url, "download_failed", "downloaded file is missing or empty")

        probe = _probe_video(tmp_path)
        duration = float(probe.get("duration_seconds") or 0)
        if duration > MAX_DOWNLOAD_DURATION_SECONDS:
            tmp_path.unlink(missing_ok=True)
            return _error(
                platform,
                original_url,
                "duration_exceeded",
                f"duration {duration:.1f}s exceeds {MAX_DOWNLOAD_DURATION_SECONDS}s",
            )

        raw_ingest = str(os.environ.get("CRAYOTTER_PHASE1_RAW_INGEST", "true")).strip().lower() not in {
            "0", "false", "no", "off"
        }
        if raw_ingest and not _needs_normalization(probe):
            shutil.move(str(tmp_path), str(output_path))
            standardization = {
                "standardized": False,
                "target_fps": None,
                "pixel_format": str(probe.get("pixel_format") or ""),
                "loudnorm_applied": False,
                "loudnorm_target": 0.0,
                "standardization_steps": ["raw_ingest"],
            }
        else:
            standardization = normalize_downloaded_material(
                tmp_path,
                output_path,
                max_height=max_height,
                target_fps=target_fps,
                loudnorm_target=loudnorm_target,
            )
            tmp_path.unlink(missing_ok=True)
        final_probe = _probe_video(output_path)
        codec = "h264"
        audio_codec = "aac"

        elapsed = time.perf_counter() - started_at
        payload = {
                "status": "success",
                "path": str(output_path),
                "source": platform,
                "original_source": original_platform,
                "original_url": original_url,
                "fallback": fallback_used,
                "fallback_used": fallback_used,
                "fallback_query": str(fallback_query or ""),
                "fallback_url": fallback_url,
                "fallback_reason": fallback_reason,
                "duration_seconds": round(float(final_probe.get("duration_seconds") or duration), 1),
                "resolution": str(final_probe.get("resolution") or ""),
                "fps": round(float(final_probe.get("fps") or 0), 2),
                "codec": codec,
                "audio_codec": audio_codec,
                "normalized": bool(standardization.get("standardized")),
                "cache_hit": False,
                "download_elapsed_seconds": round(elapsed, 3),
                "adapter": adapter_payload or {},
            }
        payload.update(standardization)
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        logger.error("❌ 素材下载异常: %s", exc, exc_info=True)
        return _error(platform, original_url, "exception", str(exc))
    finally:
        if tmp_path.exists() and tmp_path != output_path:
            try:
                tmp_path.unlink()
            except Exception:
                pass


def _run_yt_dlp_download(url: str, output_path: Path, *, max_height: int, prefer_h264: bool) -> subprocess.CompletedProcess:
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
        "-f",
        format_selector,
        "--merge-output-format",
        "mp4",
        "--prefer-free-formats",
        "--no-playlist",
        "--concurrent-fragments",
        "4",
        "--retries",
        "2",
        "--fragment-retries",
        "2",
        "--socket-timeout",
        "20",
        "-o",
        str(output_path),
        url,
    ]
    return run_subprocess(cmd, capture_output=True, text=True, timeout=300)


def _download_bilibili_fallback(
    query: str,
    output_path: Path,
    *,
    max_height: int,
    prefer_h264: bool,
) -> tuple[dict[str, Any], str]:
    raw = search_bilibili_video.invoke(
        {
            "query": query,
            "max_results": 3,
            "pages": 1,
            "expand_variants": 1,
            "max_total_results": 3,
            "request_concurrency": 1,
        }
    )
    try:
        candidates = json.loads(str(raw))
    except Exception as exc:
        raise RuntimeError(f"Bilibili fallback search returned invalid JSON: {str(raw)[:200]}") from exc
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("Bilibili fallback search returned no candidates")

    errors: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        bvid = str(candidate.get("bvid") or "").strip()
        candidate_url = str(candidate.get("url") or "").strip()
        if bvid:
            candidate_url = f"https://www.bilibili.com/video/{bvid}"
        if not candidate_url:
            continue
        result = _run_yt_dlp_download(
            candidate_url,
            output_path,
            max_height=max_height,
            prefer_h264=prefer_h264,
        )
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            return candidate, candidate_url
        error_text = (result.stderr or result.stdout or "").strip()
        try:
            _download_via_bilibili_api(candidate_url, bvid, output_path)
            if output_path.exists() and output_path.stat().st_size > 0:
                return candidate, candidate_url
        except Exception as exc:
            errors.append(f"{candidate_url}: yt-dlp={error_text[:180]}; api={exc}")
            continue
        errors.append(f"{candidate_url}: yt-dlp={error_text[:220]}")
    raise RuntimeError("; ".join(errors)[:500] or "Bilibili fallback download failed")


def _probe_video(path: Path) -> dict[str, Any]:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps if fps > 0 else 0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    video_codec = _ffprobe_stream_codec(path, "v:0")
    audio_codec = _ffprobe_stream_codec(path, "a:0")
    return {
        "duration_seconds": duration,
        "fps": fps,
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}",
        "video_codec": video_codec,
        "audio_codec": audio_codec,
    }


def _ffprobe_stream_codec(path: Path, stream: str) -> str:
    try:
        result = run_subprocess(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                stream,
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return ""
    return (result.stdout or "").strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else ""


def _needs_normalization(probe: dict[str, Any]) -> bool:
    return str(probe.get("video_codec") or "").lower() not in {"h264", "avc1"} or str(
        probe.get("audio_codec") or ""
    ).lower() not in {"aac", ""}


def _normalize_video(input_path: Path, output_path: Path, *, max_height: int) -> None:
    normalize_downloaded_material(
        input_path,
        output_path,
        max_height=max_height,
        target_fps=_positive_int_env("CRAYOTTER_STANDARDIZE_TARGET_FPS", 30),
        loudnorm_target=_float_env("CRAYOTTER_AUDIO_LOUDNORM_TARGET", 0.0),
    )


def normalize_downloaded_material(
    input_path: Path,
    output_path: Path,
    *,
    max_height: int,
    target_fps: int = 30,
    loudnorm_target: float = 0.0,
) -> dict[str, Any]:
    """Standardize a downloaded source into the editing-compatible baseline."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    target_fps = max(1, int(target_fps or 30))
    loudnorm_target = float(loudnorm_target or 0.0)
    steps: list[str] = []
    pixel_format = "yuv420p"
    tmp_standardized = output_path.with_name(f"{output_path.stem}_standardized_tmp{output_path.suffix}")
    standard_output = tmp_standardized if _loudnorm_enabled(loudnorm_target) else output_path

    for path in (output_path, tmp_standardized):
        if path.exists():
            path.unlink()

    try:
        _run_standardization_ffmpeg(
            input_path,
            standard_output,
            max_height=max_height,
            target_fps=target_fps,
            pixel_format=pixel_format,
        )
        steps.append("video_audio_standardization")

        loudnorm_applied = False
        if _loudnorm_enabled(loudnorm_target):
            stats = _run_loudnorm_analysis(standard_output, target_lufs=loudnorm_target)
            steps.append("loudnorm_analysis")
            _run_loudnorm_apply(
                standard_output,
                output_path,
                stats,
                target_lufs=loudnorm_target,
            )
            steps.append("loudnorm_apply")
            loudnorm_applied = True

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("ffmpeg normalization did not produce a valid output file")

        return {
            "standardized": True,
            "target_fps": target_fps,
            "pixel_format": pixel_format,
            "loudnorm_applied": loudnorm_applied,
            "loudnorm_target": loudnorm_target if loudnorm_applied else 0.0,
            "standardization_steps": steps,
        }
    finally:
        if tmp_standardized.exists():
            try:
                tmp_standardized.unlink()
            except Exception:
                pass


def _run_standardization_ffmpeg(
    input_path: Path,
    output_path: Path,
    *,
    max_height: int,
    target_fps: int,
    pixel_format: str,
) -> None:
    vf = (
        f"scale='if(gt(ih,{max_height}),-2,iw)':'if(gt(ih,{max_height}),{max_height},ih)',"
        f"fps={target_fps},format={pixel_format}"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        pixel_format,
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    _run_ffmpeg_checked(cmd, timeout=300, error_label="ffmpeg normalization failed")


def _run_loudnorm_analysis(input_path: Path, *, target_lufs: float) -> dict[str, str]:
    audio_filter = f"loudnorm=I={target_lufs:.1f}:TP=-1.5:LRA=11.0:print_format=json"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-af",
        audio_filter,
        "-f",
        "null",
        "-",
    ]
    result = run_subprocess(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "ffmpeg loudnorm analysis failed")[:500])
    return _parse_loudnorm_json(result.stderr or result.stdout or "")


def _run_loudnorm_apply(
    input_path: Path,
    output_path: Path,
    stats: dict[str, str],
    *,
    target_lufs: float,
) -> None:
    audio_filter = (
        f"loudnorm=I={target_lufs:.1f}:TP=-1.5:LRA=11.0:"
        f"measured_I={stats['input_i']}:"
        f"measured_TP={stats['input_tp']}:"
        f"measured_LRA={stats['input_lra']}:"
        f"measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:linear=true:print_format=summary"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-af",
        audio_filter,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    _run_ffmpeg_checked(cmd, timeout=300, error_label="ffmpeg loudnorm apply failed")


def _parse_loudnorm_json(text: str) -> dict[str, str]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("ffmpeg loudnorm analysis did not return JSON")
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception as exc:
        raise RuntimeError("ffmpeg loudnorm analysis returned invalid JSON") from exc
    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    missing = [key for key in required if key not in parsed]
    if missing:
        raise RuntimeError(f"ffmpeg loudnorm analysis missing fields: {', '.join(missing)}")
    return {key: str(parsed[key]) for key in required}


def _run_ffmpeg_checked(cmd: list[str], *, timeout: int, error_label: str) -> None:
    result = run_subprocess(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or error_label)[:500])


def _loudnorm_enabled(target: float) -> bool:
    return float(target or 0.0) < 0.0


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    text = str(raw or "").strip().lower()
    if not text or text in {"0", "off", "false", "no", "none", "null"}:
        return default
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _error(source: str, original_url: str, error_type: str, error: str) -> str:
    return json.dumps(
        {
            "status": "error",
            "source": source,
            "original_url": original_url,
            "error_type": error_type,
            "error": error,
        },
        ensure_ascii=False,
    )
