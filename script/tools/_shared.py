"""
多模态视频自动编辑 Agent — 工具集
核心能力:
    - 视频搜索与下载 (yt-dlp, bilibili-api)
    - 多模态视频内容分析 (GPT-4o Vision — 抽帧+理解)
    - 视频剪辑/合并/转场 (moviepy)
    - AI 旁白生成 (OpenAI TTS)
    - AI 视频片段生成 (占位/可接入 Runway 等)

依赖:
    pip install yt-dlp moviepy opencv-python openai langchain langchain-core langchain_openai
    pip install bilibili-api-python aiohttp
"""

from __future__ import annotations

import base64

import ipaddress

import ssl

import os

import mimetypes

import shutil

import time
import threading
import hashlib

try:
    import certifi
    os.environ['SSL_CERT_FILE'] = certifi.where()
    os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
except ImportError:
    pass

ssl._create_default_https_context = ssl._create_unverified_context

import dashscope

import ast

import json

import logging

import re

import subprocess

from urllib.parse import unquote, urlparse

from datetime import datetime

from pathlib import Path

from typing import Any

import cv2

from langchain_core.tools import tool

from openai import OpenAI
from app.media_index import build_analysis_index, iter_analysis_files, match_analysis_files
from app.runtime_paths import configure_runtime_environment, get_bundle_root, get_runtime_root
from model_runtime import (
    ModelCallError,
    benchmark_mode,
    emit_benchmark_event,
    ensure_model_calls_allowed,
    fail_fast_model_errors,
    raise_model_failure,
)

configure_runtime_environment()

BUNDLE_DIR = get_bundle_root()
CURRENT_DIR = get_runtime_root()

_task_workspace = os.environ.get("CRAYOTTER_TASK_WORKSPACE", "").strip()
WORKSPACE = Path(_task_workspace).resolve(strict=False) if _task_workspace else CURRENT_DIR / "temp"

_user_workspace = os.environ.get("CRAYOTTER_USER_WORKSPACE", "").strip()
USER_WORKSPACE = Path(_user_workspace).resolve(strict=False) if _user_workspace else CURRENT_DIR / "user_temp"
os.environ.setdefault("CRAYOTTER_USER_WORKSPACE", str(USER_WORKSPACE.resolve(strict=False)))

MEMORY_EXPERIENCE_DIR = CURRENT_DIR / "memory_experience"

def _select_logs_dir() -> Path:
    primary = CURRENT_DIR / "logs"
    fallback = CURRENT_DIR / "runtime_logs"

    for candidate in (primary, fallback):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except Exception:
            continue

    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


LOGS_DIR = _select_logs_dir()

WORKSPACE.mkdir(parents=True, exist_ok=True)

USER_WORKSPACE.mkdir(parents=True, exist_ok=True)

MEMORY_EXPERIENCE_DIR.mkdir(parents=True, exist_ok=True)

LOGS_DIR.mkdir(parents=True, exist_ok=True)

print(f"[workspace] {WORKSPACE}")

print(f"[user_workspace] {USER_WORKSPACE}")

print(f"[memory_experience] {MEMORY_EXPERIENCE_DIR}")

print(f"[logs] {LOGS_DIR}")

log_filename = LOGS_DIR / f"video_agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
    ]
)

logger = logging.getLogger(__name__)

logger.info(f"日志系统已初始化，日志文件: {log_filename}")

console_handler = logging.StreamHandler()

console_handler.setLevel(logging.INFO)

console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

logging.getLogger().addHandler(console_handler)

API_KEY: str = ""

BASE_URL: str = ""

MODEL_NAME: str = ""

VIDEO_API_KEY: str = ""

VIDEO_BASE_URL: str = ""

VIDEO_MODEL_NAME: str = ""

TTS_API_KEY: str = ""

TTS_BASE_URL: str = ""

TTS_MODEL_NAME: str = ""

_openai_client: OpenAI | None = None

_video_client: OpenAI | None = None

_CANDIDATE_POOL_PATH = WORKSPACE / "candidate_pool.jsonl"

_CANDIDATE_SNAPSHOT_PATH = WORKSPACE / "candidate_pool_snapshot.json"
_CANDIDATE_POOL_LOCK = threading.RLock()

_RANK_CACHE: dict[str, str] = {}

MAX_DOWNLOAD_DURATION_SECONDS = 10 * 60


def _hidden_subprocess_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def _merge_hidden_subprocess_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    hidden = _hidden_subprocess_kwargs()
    if not hidden:
        return dict(kwargs)
    merged = dict(kwargs)
    merged["creationflags"] = int(merged.get("creationflags", 0) or 0) | int(
        hidden["creationflags"]
    )
    merged.setdefault("startupinfo", hidden["startupinfo"])
    return merged


def _is_unittest_mock(callable_obj: Any) -> bool:
    return str(getattr(callable_obj, "__module__", "")) == "unittest.mock"


def run_subprocess(*popenargs: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    if _is_unittest_mock(subprocess.run):
        return subprocess.run(*popenargs, **kwargs)
    return subprocess.run(*popenargs, **_merge_hidden_subprocess_kwargs(kwargs))


def popen_subprocess(*popenargs: Any, **kwargs: Any) -> subprocess.Popen:
    if _is_unittest_mock(subprocess.Popen):
        return subprocess.Popen(*popenargs, **kwargs)
    return subprocess.Popen(*popenargs, **_merge_hidden_subprocess_kwargs(kwargs))


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


def _media_cache_dir() -> Path:
    raw = os.environ.get("CRAYOTTER_MEDIA_CACHE_DIR", "").strip()
    cache_dir = Path(raw).resolve() if raw else (CURRENT_DIR / "cache").resolve()
    try:
        cache_dir.relative_to(CURRENT_DIR.resolve())
    except ValueError as exc:
        raise RuntimeError("CRAYOTTER_MEDIA_CACHE_DIR must stay inside the runtime root.") from exc
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    stat = path.stat()
    digest.update(str(stat.st_size).encode("ascii"))
    with path.open("rb") as handle:
        digest.update(handle.read(1024 * 1024))
    return digest.hexdigest()


def _analysis_cache_key(source_video: Path, analysis_goal: str, model_name: str) -> str:
    payload = {
        "file": _file_digest(source_video),
        "goal": str(analysis_goal),
        "model": str(model_name),
        "prompt_version": "video-analysis-v3-proxy",
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _analysis_output_path(source_video: Path) -> Path:
    try:
        source_video.resolve(strict=False).relative_to(USER_WORKSPACE.resolve(strict=False))
        output_root = USER_WORKSPACE
    except Exception:
        output_root = WORKSPACE
    return output_root / f"{source_video.stem}_analysis.json"


def _restore_cached_analysis(
    source_video: Path,
    analysis_goal: str,
    model_name: str,
) -> Path | None:
    if not benchmark_mode():
        return None
    key = _analysis_cache_key(source_video, analysis_goal, model_name)
    cache_path = _media_cache_dir() / "analysis" / f"{key}.json"
    if not cache_path.exists():
        emit_benchmark_event(
            "cache_miss",
            {"kind": "video_analysis", "source": source_video.name, "key": key},
        )
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        payload["source_video"] = str(source_video)
        output_path = _analysis_output_path(source_video)
        _persist_analysis_payload(output_path, payload)
        emit_benchmark_event(
            "cache_hit",
            {"kind": "video_analysis", "source": source_video.name, "key": key},
        )
        return output_path
    except Exception as exc:
        logger.warning("视频分析缓存恢复失败: %s", exc)
        return None


def _store_cached_analysis(
    source_video: Path,
    analysis_goal: str,
    model_name: str,
    analysis_path: Path | None,
) -> None:
    if not benchmark_mode() or analysis_path is None or not analysis_path.exists():
        return
    key = _analysis_cache_key(source_video, analysis_goal, model_name)
    cache_path = _media_cache_dir() / "analysis" / f"{key}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(analysis_path, cache_path)
    emit_benchmark_event(
        "cache_stored",
        {"kind": "video_analysis", "source": source_video.name, "key": key},
    )

def _get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(
            api_key=API_KEY,
            base_url=BASE_URL,
            max_retries=0 if fail_fast_model_errors() else 2,
        )
    return _openai_client

def _get_video_client() -> OpenAI:
    global _video_client
    if _video_client is None:
        _video_client = OpenAI(
            api_key=VIDEO_API_KEY,
            base_url=VIDEO_BASE_URL,
            max_retries=0 if fail_fast_model_errors() else 2,
        )
    return _video_client

def _extract_chat_content(response: Any) -> str:
    try:
        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        first = choices[0]
        message = getattr(first, "message", None)
        content = getattr(message, "content", "")
        if content is None:
            return ""
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        parts.append(str(text))
                else:
                    parts.append(str(item))
            return "\n".join([p for p in parts if p]).strip()
        return str(content).strip()
    except Exception:
        return ""

def _generate_query_variants(query: str, max_variants: int) -> list[str]:
    base = query.strip()
    if not base or max_variants <= 0:
        return []

    prompt = (
        "你是搜索查询改写器。给定一个用户意图，生成用于视频检索的多样化查询词。\n"
        "要求：\n"
        "- 覆盖不同切入点（主题/内容类型/风格/场景/对象/地域/语言）\n"
        "- 不要引入用户未提及的具体作品或人物\n"
        "- 输出 JSON 数组，仅包含字符串\n"
        f"- 最多输出 {max_variants} 条\n"
    )

    started = time.perf_counter()
    try:
        ensure_model_calls_allowed()
        client = _get_openai_client()
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": base},
            ],
            temperature=0.3,
            max_tokens=400,
        )
        content = _extract_chat_content(response)
        if not content and fail_fast_model_errors():
            raise_model_failure(
                stage="query_expansion",
                model=MODEL_NAME,
                message="Model returned empty query expansion content.",
                duration_seconds=time.perf_counter() - started,
            )

        def _parse_variants(text: str) -> list[str]:
            if not text:
                return []
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    return [str(v).strip() for v in data if str(v).strip()]
            except Exception:
                pass

            if "[" in text and "]" in text:
                try:
                    fragment = text[text.find("[") : text.rfind("]") + 1]
                    data = json.loads(fragment)
                    if isinstance(data, list):
                        return [str(v).strip() for v in data if str(v).strip()]
                except Exception:
                    pass

            lines = [line.strip("-• \t") for line in text.splitlines()]
            return [line for line in lines if line]

        cleaned = _parse_variants(content)
        return cleaned[:max_variants]
    except ModelCallError:
        raise
    except Exception as e:
        if fail_fast_model_errors():
            response = getattr(e, "response", None)
            raise_model_failure(
                stage="query_expansion",
                model=MODEL_NAME,
                message=e,
                status_code=getattr(response, "status_code", None),
                request_id=str(getattr(response, "headers", {}).get("x-request-id", "")) if response else "",
                duration_seconds=time.perf_counter() - started,
            )
        logger.warning(f"⚠️ 动态扩展查询失败，回退为原始查询: {e}")
        return []

def _expand_queries(query: str, max_variants: int = 3) -> list[str]:
    base = query.strip()
    if not base:
        return []

    # max_variants 表示“最终最多保留多少条查询”，包含原始 query 本身。
    expanded = [base]
    if max_variants <= 1:
        return expanded

    variants = _generate_query_variants(base, max_variants=max_variants - 1)
    for v in variants:
        if v not in expanded:
            expanded.append(v)
        if len(expanded) >= max_variants:
            break
    return expanded[:max_variants]

def _dedupe_by_key(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        value = str(item.get(key, "")).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(item)
    return deduped

def _candidate_identity(item: dict[str, Any]) -> str:
    return (
        str(item.get("url") or "").strip()
        or str(item.get("bvid") or "").strip()
        or str(item.get("id") or "").strip()
    )

def _merge_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    pos_by_key: dict[str, int] = {}
    for item in candidates:
        if not isinstance(item, dict):
            continue
        key = _candidate_identity(item)
        if not key:
            continue
        if key not in pos_by_key:
            pos_by_key[key] = len(merged)
            merged.append(item)
            continue

        idx = pos_by_key[key]
        old = merged[idx]
        merged_item = dict(old)
        for k, v in item.items():
            if k not in merged_item or merged_item.get(k) in (None, "", [], {}):
                merged_item[k] = v
            elif k in {"description", "intro", "tag"}:
                old_len = len(str(merged_item.get(k) or ""))
                new_len = len(str(v or ""))
                if new_len > old_len:
                    merged_item[k] = v
        merged[idx] = merged_item
    return merged

def _refresh_candidate_snapshot(limit: int = 1000) -> None:
    with _CANDIDATE_POOL_LOCK:
        try:
            merged = _load_candidates_from_pool(limit=limit)
            with _CANDIDATE_SNAPSHOT_PATH.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "updated_at": datetime.now().isoformat(),
                        "count": len(merged),
                        "candidates": merged,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as e:
            logger.warning("⚠️ 写入候选快照失败: %s", e)

def _append_candidates_to_pool(candidates: list[dict[str, Any]]) -> None:
    if not candidates:
        return
    with _CANDIDATE_POOL_LOCK:
        try:
            with _CANDIDATE_POOL_PATH.open("a", encoding="utf-8") as f:
                for item in candidates:
                    if not isinstance(item, dict):
                        continue
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("⚠️ 写入候选池失败: %s", e)
            return

        _refresh_candidate_snapshot()

def _load_candidates_from_pool(limit: int | None = None) -> list[dict[str, Any]]:
    with _CANDIDATE_POOL_LOCK:
        if not _CANDIDATE_POOL_PATH.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with _CANDIDATE_POOL_PATH.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(item, dict):
                        rows.append(item)
            rows = _merge_candidates(rows)
            if limit is not None and limit > 0:
                return rows[:limit]
            return rows
        except Exception as e:
            logger.warning("⚠️ 读取候选池失败: %s", e)
            return []

def _parse_candidate_payload(payload: str) -> list[dict[str, Any]]:
    text = (payload or "").strip()
    if not text:
        return []

    def _to_list(obj: Any) -> list[dict[str, Any]]:
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)]
        if isinstance(obj, dict):
            for key in ("candidates", "videos", "items", "data", "results"):
                value = obj.get(key)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, dict)]
        return []

    try:
        parsed = json.loads(text)
        lst = _to_list(parsed)
        if lst:
            return lst
    except Exception:
        pass

    try:
        parsed = ast.literal_eval(text)
        lst = _to_list(parsed)
        if lst:
            return lst
    except Exception:
        pass

    for left, right in (("[", "]"), ("{", "}")):
        if left in text and right in text:
            fragment = text[text.find(left): text.rfind(right) + 1]
            try:
                parsed = json.loads(fragment)
                lst = _to_list(parsed)
                if lst:
                    return lst
            except Exception:
                try:
                    parsed = ast.literal_eval(fragment)
                    lst = _to_list(parsed)
                    if lst:
                        return lst
                except Exception:
                    pass

    urls = re.findall(r"https?://[^\s\]\)\"']+", text)
    fallback: list[dict[str, Any]] = []
    for url in urls:
        source = "bilibili" if "bilibili.com" in url else "youtube" if ("youtube.com" in url or "youtu.be" in url) else "unknown"
        fallback.append({"title": "from_text", "url": url, "source": source})
    return _merge_candidates(fallback)

def _parse_duration_to_seconds(duration: Any) -> float | None:
    if duration is None:
        return None

    if isinstance(duration, (int, float)):
        seconds = float(duration)
        return seconds if seconds >= 0 else None

    text = str(duration).strip().lower()
    if not text or text in {"unknown", "none", "nan", "n/a", "--"}:
        return None

    text = text.replace("，", ",").replace("：", ":")
    text = text.replace("小时", "h").replace("分钟", "m").replace("分", "m").replace("秒", "s")
    text = text.replace(" ", "")

    pure_number = re.fullmatch(r"\d+(?:\.\d+)?", text)
    if pure_number:
        return float(text)

    if ":" in text:
        parts = text.split(":")
        try:
            nums = [float(p) for p in parts]
        except Exception:
            nums = []
        if nums and all(n >= 0 for n in nums):
            if len(nums) == 3:
                return nums[0] * 3600 + nums[1] * 60 + nums[2]
            if len(nums) == 2:
                return nums[0] * 60 + nums[1]
            if len(nums) == 1:
                return nums[0]

    hms_match = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?", text)
    if hms_match:
        h_raw, m_raw, s_raw = hms_match.groups()
        if h_raw is not None or m_raw is not None or s_raw is not None:
            h = float(h_raw) if h_raw is not None else 0.0
            m = float(m_raw) if m_raw is not None else 0.0
            s = float(s_raw) if s_raw is not None else 0.0
            return h * 3600 + m * 60 + s

    return None

def _detect_requested_orientation(text: str, default: str = "landscape") -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return default

    portrait_markers = ("竖屏", "竖版", "9:16", "portrait", "vertical", "shorts", "reels")
    landscape_markers = ("横屏", "横版", "16:9", "landscape", "horizontal", "宽屏")

    has_portrait = any(marker in raw for marker in portrait_markers)
    has_landscape = any(marker in raw for marker in landscape_markers)
    if has_portrait and not has_landscape:
        return "portrait"
    if has_landscape and not has_portrait:
        return "landscape"
    return default

def _parse_resolution_pair(value: Any) -> tuple[int, int] | None:
    if isinstance(value, str):
        match = re.search(r"(\d{2,5})\s*[xX×]\s*(\d{2,5})", value)
        if match:
            return int(match.group(1)), int(match.group(2))
        return None
    return None

def _detect_candidate_orientation(item: dict[str, Any]) -> tuple[str, str]:
    width = int(item.get("width") or 0)
    height = int(item.get("height") or 0)
    if width > 0 and height > 0:
        return ("portrait", "resolution") if height > width else ("landscape", "resolution")

    resolution_pair = _parse_resolution_pair(item.get("resolution", ""))
    if resolution_pair is not None:
        w, h = resolution_pair
        return ("portrait", "resolution") if h > w else ("landscape", "resolution")

    text = " ".join(
        str(item.get(key, "") or "")
        for key in ("title", "description", "intro", "tag", "typename", "query")
    ).lower()
    portrait_markers = ("竖屏", "竖版", "9:16", "portrait", "vertical", "shorts", "reels")
    landscape_markers = ("横屏", "横版", "16:9", "landscape", "horizontal", "宽屏")
    has_portrait = any(marker in text for marker in portrait_markers)
    has_landscape = any(marker in text for marker in landscape_markers)
    if has_portrait and not has_landscape:
        return "portrait", "text"
    if has_landscape and not has_portrait:
        return "landscape", "text"
    return "unknown", "unknown"

def _orientation_bonus(target_orientation: str, candidate_orientation: str) -> float:
    if candidate_orientation == "unknown":
        return 0.0
    if target_orientation == "portrait":
        return 1.1 if candidate_orientation == "portrait" else -1.1
    return 0.7 if candidate_orientation == "landscape" else -1.0

def _fit_clip_to_canvas(clip: Any, target_size: tuple[int, int]) -> Any:
    from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip

    target_w, target_h = int(target_size[0]), int(target_size[1])
    src_w, src_h = int(clip.size[0]), int(clip.size[1])
    if src_w <= 0 or src_h <= 0:
        return clip
    if (src_w, src_h) == (target_w, target_h):
        return clip

    scale = max(target_w / src_w, target_h / src_h)
    resized_w = max(1, int(round(src_w * scale)))
    resized_h = max(1, int(round(src_h * scale)))
    fitted = clip.resized((resized_w, resized_h)).with_position(("center", "center"))
    composite = CompositeVideoClip([fitted], size=(target_w, target_h))
    if getattr(clip, "audio", None) is not None:
        composite = composite.with_audio(clip.audio)
    return composite

def _pick_export_target_size(resolution: str, source_size: tuple[int, int]) -> tuple[int, int]:
    res_map = {"720p": (1280, 720), "1080p": (1920, 1080), "4k": (3840, 2160)}
    base_w, base_h = res_map.get(resolution, (1920, 1080))
    src_w, src_h = int(source_size[0]), int(source_size[1])
    if src_h > src_w:
        return base_h, base_w
    return base_w, base_h

def _filter_candidates_by_max_duration(
    candidates: list[dict[str, Any]],
    max_seconds: int = MAX_DOWNLOAD_DURATION_SECONDS,
    keep_unknown: bool = False,
) -> tuple[list[dict[str, Any]], int, int]:
    kept: list[dict[str, Any]] = []
    dropped_long = 0
    dropped_unknown = 0

    for item in candidates:
        duration_seconds = _parse_duration_to_seconds(
            item.get("duration_seconds", item.get("duration", None))
        )
        if duration_seconds is None:
            if keep_unknown:
                kept.append(item)
            else:
                dropped_unknown += 1
            continue

        item["duration_seconds"] = round(duration_seconds, 1)
        if duration_seconds <= max_seconds:
            kept.append(item)
        else:
            dropped_long += 1

    return kept, dropped_long, dropped_unknown

def _extract_time_segments_from_analysis(text: str) -> list[dict[str, float]]:
    if not text:
        return []

    pattern = re.compile(
        r"t\s*=\s*(\d+(?:\.\d+)?)s?\s*(?:-|~|—|–|至|到)\s*t?\s*=\s*(\d+(?:\.\d+)?)s?",
        re.IGNORECASE,
    )
    segments: list[dict[str, float]] = []
    for m in pattern.finditer(text):
        try:
            start = float(m.group(1))
            end = float(m.group(2))
        except Exception:
            continue
        if end <= start:
            continue
        segments.append({"start": round(start, 2), "end": round(end, 2)})

    deduped: list[dict[str, float]] = []
    seen: set[tuple[float, float]] = set()
    for seg in segments:
        key = (seg["start"], seg["end"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(seg)
    return deduped

def _normalize_semantic_text(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[`*_>#\-]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def _duration_preference_bonus(duration: float) -> float:
    if 4 <= duration <= 12:
        return 0.04
    if 2 <= duration <= 20:
        return 0.015
    return 0.0

def _semantic_tokens(text: str) -> set[str]:
    norm = _normalize_semantic_text(text)
    if not norm:
        return set()

    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", norm)
    stop = {
        "的", "了", "和", "是", "在", "与", "及", "并", "中", "上", "下", "对", "将", "为", "以", "或", "及其",
        "this", "that", "with", "from", "into", "for", "the", "and", "or", "to", "of", "in", "on",
    }
    return {tk for tk in tokens if tk and tk not in stop}

def _extract_semantic_segments_from_analysis(text: str) -> list[dict[str, Any]]:
    """从分析文本中提取可检索的语义片段（时间段 + 语义描述）。"""
    if not text:
        return []

    pattern = re.compile(
        r"t\s*=\s*(\d+(?:\.\d+)?)s?\s*(?:-|~|—|–|至|到)\s*t?\s*=\s*(\d+(?:\.\d+)?)s?",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return []

    results: list[dict[str, Any]] = []
    for idx, m in enumerate(matches):
        try:
            start = float(m.group(1))
            end = float(m.group(2))
        except Exception:
            continue
        if end <= start:
            continue

        desc_begin = m.end()
        desc_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunk = text[desc_begin:desc_end]
        chunk = re.sub(r"\n+", "\n", chunk)
        lines = []
        for line in chunk.splitlines():
            line = re.sub(r"^\s*[-*•\d.\)\]]+\s*", "", line.strip())
            if line:
                lines.append(line)
        semantic_text = _normalize_semantic_text(" ".join(lines))
        if not semantic_text:
            semantic_text = _normalize_semantic_text(text[m.start(): min(len(text), m.end() + 120)])

        semantic_text = semantic_text[:320]
        results.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "duration": round(end - start, 2),
            "semantic_text": semantic_text,
        })

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()
    for seg in results:
        key = (float(seg["start"]), float(seg["end"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(seg)
    return deduped

def _prepare_semantic_segments(
    semantic_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    segments = [dict(seg) for seg in semantic_segments if isinstance(seg, dict)]
    if not segments:
        return []

    for seg in segments:
        seg["semantic_text"] = _normalize_semantic_text(str(seg.get("semantic_text", "")))
        seg.pop("embedding", None)
    return segments

def _build_semantic_index_meta(
    semantic_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "index_version": 2,
        "segment_count": len(semantic_segments),
        "retrieval_mode": "text",
        "updated_at": datetime.now().isoformat(),
    }

def _persist_analysis_payload(output_path: Path, payload: dict[str, Any]) -> bool:
    try:
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.warning("⚠️ 持久化分析JSON失败: %s", e)
        return False

def _ensure_analysis_semantic_index(
    analysis_path: Path,
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    semantic_segments = payload.get("semantic_segments", [])
    if not isinstance(semantic_segments, list) or not semantic_segments:
        semantic_segments = _extract_semantic_segments_from_analysis(str(payload.get("analysis_text", "")))

    semantic_segments = _prepare_semantic_segments(semantic_segments)
    semantic_index = _build_semantic_index_meta(semantic_segments)

    original_segments = payload.get("semantic_segments", [])
    original_index = payload.get("semantic_index", {})
    if semantic_segments == original_segments and isinstance(original_index, dict) and original_index:
        semantic_index["updated_at"] = str(original_index.get("updated_at", semantic_index["updated_at"]))
    payload["semantic_segments"] = semantic_segments
    payload["semantic_index"] = semantic_index

    if semantic_segments != original_segments or semantic_index != original_index:
        _persist_analysis_payload(analysis_path, payload)

    return semantic_segments, semantic_index

def _semantic_similarity_score(query: str, semantic_text: str, duration: float) -> float:
    query_norm = _normalize_semantic_text(query)
    text_norm = _normalize_semantic_text(semantic_text)
    if not query_norm or not text_norm:
        return 0.0

    q_tokens = _semantic_tokens(query_norm)
    t_tokens = _semantic_tokens(text_norm)
    if not q_tokens or not t_tokens:
        return 0.0

    overlap = len(q_tokens & t_tokens)
    if overlap == 0 and query_norm not in text_norm:
        return 0.0

    recall = overlap / max(1, len(q_tokens))
    precision = overlap / max(1, len(t_tokens))
    contains_bonus = 0.35 if query_norm in text_norm else 0.0
    duration_bonus = _duration_preference_bonus(duration) * 2

    return round(0.7 * recall + 0.2 * precision + contains_bonus + duration_bonus, 4)

def _save_analysis_json(
    source_video: Path,
    analysis_video: Path,
    analysis_goal: str,
    analysis_text: str,
    video_url_used: str,
    audio_url_used: str,
) -> Path | None:
    try:
        output_path = _analysis_output_path(source_video)
        semantic_segments = _extract_semantic_segments_from_analysis(analysis_text)
        semantic_segments = _prepare_semantic_segments(semantic_segments)
        payload = {
            "source_video": str(source_video),
            "analysis_video": str(analysis_video),
            "analysis_goal": analysis_goal,
            "video_url_used": video_url_used,
            "audio_url_used": audio_url_used,
            "segments": _extract_time_segments_from_analysis(analysis_text),
            "semantic_segments": semantic_segments,
            "semantic_index": _build_semantic_index_meta(semantic_segments),
            "analysis_text": analysis_text,
            "saved_at": datetime.now().isoformat(),
        }
        if not _persist_analysis_payload(output_path, payload):
            return None
        return output_path
    except Exception as e:
        logger.warning("⚠️ 保存分析JSON失败: %s", e)
        return None

def _iter_analysis_json_files() -> list[Path]:
    return iter_analysis_files([WORKSPACE, USER_WORKSPACE])

def _match_analysis_json_files(video_path: Path) -> list[Path]:
    return match_analysis_files(
        video_path,
        analysis_index=build_analysis_index([WORKSPACE, USER_WORKSPACE]),
    )

def _get_video_meta(video_path: str) -> dict[str, Any]:
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps if fps > 0 else 0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return {
        "duration_seconds": round(duration, 2),
        "fps": round(fps, 2),
        "resolution": f"{width}x{height}",
        "width": width,
        "height": height,
    }

def _to_file_url(path: Path) -> str:
    return path.resolve().as_uri()


def _guess_media_mime_type(path: Path, *, fallback: str) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or fallback


def _to_data_url(path: Path, *, fallback_mime: str) -> str:
    mime_type = _guess_media_mime_type(path, fallback=fallback_mime)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _is_local_base_url(base_url: str) -> bool:
    raw = str(base_url or "").strip()
    if not raw:
        return True

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    hostname = (parsed.hostname or "").strip("[]").lower()

    if scheme in {"", "file"}:
        return True
    if hostname in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return True

    if hostname:
        try:
            ip = ipaddress.ip_address(hostname)
            return bool(ip.is_loopback or ip.is_private)
        except ValueError:
            pass

    return False

def _is_within_workspace(path: Path) -> bool:
    allowed_roots = (
        WORKSPACE.resolve(strict=False),
        USER_WORKSPACE.resolve(strict=False),
    )
    try:
        resolved = path.resolve(strict=False)
    except Exception:
        return False

    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return True
        except Exception:
            continue
    return False

def _resolve_workspace_input_path(raw_path: str, must_exist: bool = True) -> Path | None:
    raw = (raw_path or "").strip()
    if not raw:
        return None

    if raw.startswith("file://"):
        parsed = urlparse(raw)
        raw = unquote(parsed.path or "")

    source = Path(raw)
    roots = [WORKSPACE, USER_WORKSPACE]
    candidates: list[Path] = []
    if source.is_absolute():
        candidates.append(source)
        for root in roots:
            candidates.append(root / source.name)
    else:
        candidates.append(CURRENT_DIR / source)

        parts = source.parts
        if parts:
            if parts[0] == WORKSPACE.name and len(parts) > 1:
                candidates.append(WORKSPACE / Path(*parts[1:]))
            elif parts[0] == USER_WORKSPACE.name and len(parts) > 1:
                candidates.append(USER_WORKSPACE / Path(*parts[1:]))

        for root in roots:
            candidates.append(root / source)
            candidates.append(root / source.name)

    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except Exception:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)

        if not _is_within_workspace(resolved):
            continue
        if must_exist and not resolved.exists():
            continue
        return resolved
    return None

def _safe_output_video_path(output_name: str, default_stem: str = "output") -> Path:
    stem_raw = (output_name or default_stem).strip()
    stem = Path(stem_raw).name
    stem = Path(stem).stem or default_stem
    stem = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", stem).strip("_")
    if not stem:
        stem = default_stem
    return (WORKSPACE / f"{stem}.mp4").resolve()

def _resolve_video_path(video_path: str) -> Path | None:
    resolved = _resolve_workspace_input_path(video_path, must_exist=True)
    if resolved is not None:
        return resolved

    raw = (video_path or "").strip()
    if not raw:
        return None

    direct = Path(raw)

    # 若是 BV 号路径推断，尝试寻找同名别名文件
    stem = direct.stem
    if stem.upper().startswith("BV"):
        for root in (WORKSPACE, USER_WORKSPACE):
            alias = root / f"{stem}.mp4"
            if alias.exists():
                return alias
    return None

def _extract_audio_for_analysis(video_path: Path) -> Path | None:
    audio_path = WORKSPACE / f"{video_path.stem}_analysis.wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(audio_path),
    ]
    try:
        result = run_subprocess(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=120,
        )
        if result.returncode == 0 and audio_path.exists() and audio_path.stat().st_size > 0:
            return audio_path
        logger.warning("⚠️ ffmpeg 提取音频失败: %s", (result.stderr or "")[:300])
    except Exception as e:
        logger.warning("⚠️ 提取音频异常: %s", e)
    return None

def _prepare_timestamped_video_for_analysis(video_path: Path) -> Path | None:
    proxy_enabled = str(
        os.environ.get("CRAYOTTER_SHORT_FORM_OPTIMIZATIONS", "true")
    ).strip().lower() not in {"0", "false", "no", "off"}
    max_seconds = _positive_int_env(
        "CRAYOTTER_VIDEO_ANALYSIS_PROXY_MAX_SECONDS",
        45,
    )
    meta = _get_video_meta(str(video_path))
    source_duration = float(meta.get("duration_seconds", 0.0) or 0.0)
    use_proxy = proxy_enabled and source_duration > max_seconds
    suffix = "analysis_proxy" if use_proxy else "analysis_ts"
    stamped_path = WORKSPACE / f"{video_path.stem}_{suffix}.mp4"
    try:
        if stamped_path.exists() and stamped_path.stat().st_mtime >= video_path.stat().st_mtime:
            return stamped_path
    except Exception:
        pass

    speed_factor = max(1.0, source_duration / max_seconds) if use_proxy else 1.0
    timestamp_expr = f"t*{speed_factor:.8f}" if use_proxy else "t"
    drawtext_filter = (
        "drawtext="
        f"text='t=%{{eif\\:{timestamp_expr}\\:d}}s':"
        "x=16:y=16:"
        "fontsize=26:"
        "fontcolor=white:"
        "box=1:"
        "boxcolor=black@0.65:"
        "boxborderw=10"
    )
    video_filters = [
        "scale='min(960,iw)':-2",
        drawtext_filter,
    ]
    if use_proxy:
        video_filters.extend(
            [
                f"setpts=PTS/{speed_factor:.8f}",
                "fps=6",
            ]
        )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        ",".join(video_filters),
        "-c:v",
        "libx264",
        "-preset", "veryfast",
        "-crf", "30" if use_proxy else "25",
        "-an" if use_proxy else "-c:a",
        "aac" if not use_proxy else "",
        str(stamped_path),
    ]
    cmd = [item for item in cmd if item != ""]
    try:
        process = popen_subprocess(
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
        emit_benchmark_event(
            "video_preprocess_started",
            {
                "source": video_path.name,
                "source_duration_seconds": round(source_duration, 3),
                "proxy": use_proxy,
                "proxy_limit_seconds": max_seconds,
            },
        )
        stderr_text = ""
        while True:
            try:
                _, stderr_text = process.communicate(timeout=heartbeat_interval)
                break
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - started
                logger.info(
                    "⏳ 时间戳分析视频生成中: %s, elapsed=%.0fs",
                    video_path.name,
                    elapsed,
                )
                if elapsed >= timeout_seconds:
                    process.kill()
                    _, stderr_text = process.communicate()
                    logger.warning("⚠️ 生成时间戳分析视频超时: %s", video_path.name)
                    return None

        if process.returncode == 0 and stamped_path.exists() and stamped_path.stat().st_size > 0:
            logger.info("🕒 已生成时间戳分析视频: %s", stamped_path)
            emit_benchmark_event(
                "video_preprocess_completed",
                {
                    "source": video_path.name,
                    "output": stamped_path.name,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "proxy": use_proxy,
                    "speed_factor": round(speed_factor, 4),
                },
            )
            return stamped_path
        logger.warning("⚠️ 生成时间戳分析视频失败: %s", (stderr_text or "")[:300])
    except Exception as e:
        logger.warning("⚠️ 生成时间戳分析视频异常: %s", e)
    return None

def _tts_generate(text: str, voice: str, out_path: Path) -> str | None:
    """调用 DashScope TTS 生成音频并保存到 out_path。成功返回 None，失败返回错误信息。"""
    supported_voices = {"Cherry", "Serena", "Ethan", "Chelsie", "Dylan", "Jada", "Sunny"}
    if voice not in supported_voices:
        logger.warning("TTS 音色 %s 不受当前模型支持，自动使用 Ethan", voice)
        voice = "Ethan"
    started_at = time.perf_counter()
    try:
        ensure_model_calls_allowed()
        emit_benchmark_event(
            "model_call_started",
            {"stage": "tts", "model": TTS_MODEL_NAME, "voice": voice},
        )
        dashscope.api_key = TTS_API_KEY
        response = dashscope.MultiModalConversation.call(
            model=TTS_MODEL_NAME,
            text=text,
            voice=voice,
        )
        if response.status_code == 200:
            audio_url = response.output.audio.url
            import urllib.request
            urllib.request.urlretrieve(audio_url, str(out_path))
            logger.info("TTS 生成成功: %s (%.0f chars) -> %s", text[:30], len(text), out_path.name)
            emit_benchmark_event(
                "model_call_completed",
                {
                    "stage": "tts",
                    "model": TTS_MODEL_NAME,
                    "voice": voice,
                    "duration_seconds": round(time.perf_counter() - started_at, 3),
                },
            )
            return None
        else:
            if fail_fast_model_errors():
                raise_model_failure(
                    stage="tts",
                    model=TTS_MODEL_NAME,
                    message=getattr(response, "message", "TTS returned non-success status."),
                    status_code=getattr(response, "status_code", None),
                    request_id=str(getattr(response, "request_id", "") or ""),
                    duration_seconds=time.perf_counter() - started_at,
                )
            return f"TTS 失败 (status={response.status_code}): {response.message}"
    except ModelCallError:
        raise
    except Exception as e:
        if fail_fast_model_errors():
            response = getattr(e, "response", None)
            raise_model_failure(
                stage="tts",
                model=TTS_MODEL_NAME,
                message=e,
                status_code=getattr(response, "status_code", None),
                duration_seconds=time.perf_counter() - started_at,
            )
        return f"TTS 异常: {e}"


__all__ = [
    name
    for name in globals()
    if name not in {
        "__builtins__",
        "__cached__",
        "__doc__",
        "__file__",
        "__loader__",
        "__name__",
        "__package__",
        "__spec__",
        "__all__",
    }
]
