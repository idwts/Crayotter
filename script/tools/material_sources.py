from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

from ._shared import (
    _detect_candidate_orientation,
    _merge_candidates as _shared_merge_candidates,
    _parse_duration_to_seconds,
)


@dataclass
class MaterialCandidate:
    id: str = ""
    source: str = "unknown"
    platform: str = "unknown"
    url: str = ""
    title: str = ""
    author: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    duration_seconds: float | None = None
    play: int = 0
    width: int = 0
    height: int = 0
    resolution: str = ""
    orientation_hint: str = "unknown"
    orientation_source: str = "unknown"
    query: str = ""
    page: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def detect_platform_from_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    host = parsed.netloc.lower()
    full = f"{host}{parsed.path}".lower()
    if "bilibili.com" in host or "b23.tv" in host:
        return "bilibili"
    if "douyin.com" in host or "iesdouyin.com" in host:
        return "douyin"
    if "xiaohongshu.com" in host or "xhslink.com" in host:
        return "xiaohongshu"
    if "rednote.com" in host:
        return "rednote"
    if "kuaishou.com" in host or "gifshow.com" in host or "ksurl.cn" in host:
        return "kuaishou"
    if "youtube.com" in host or "youtu.be" in host or "youtube" in full:
        return "youtube"
    return "unknown"


def candidate_identity(item: dict[str, Any]) -> str:
    return (
        str(item.get("url") or "").strip()
        or str(item.get("id") or "").strip()
        or str(item.get("bvid") or "").strip()
        or str(item.get("aweme_id") or "").strip()
        or str(item.get("note_id") or "").strip()
    )


def merge_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [normalize_candidate(item) for item in candidates if isinstance(item, dict)]
    return _shared_merge_candidates(normalized)


def normalize_candidate(
    item: dict[str, Any],
    *,
    source: str | None = None,
    query: str = "",
    page: int | None = None,
) -> dict[str, Any]:
    raw = dict(item or {})
    url = _pick_string(raw, "url", "webpage_url", "share_url", "arcurl")
    detected = detect_platform_from_url(url)
    platform = str(source or raw.get("source") or raw.get("platform") or detected or "unknown").strip() or "unknown"

    candidate_id = _pick_string(
        raw,
        "id",
        "bvid",
        "aweme_id",
        "note_id",
        "item_id",
        "video_id",
    )
    title = _pick_string(raw, "title", "desc", "description", "fulltitle")
    description = _pick_string(raw, "description", "desc", "intro")
    author = _extract_author(raw)
    tags = _extract_tags(raw)
    duration = _extract_duration(raw)
    width, height = _extract_resolution(raw)
    play = _extract_play(raw)
    normalized = MaterialCandidate(
        id=candidate_id,
        source=platform,
        platform=platform,
        url=url,
        title=title,
        author=author,
        description=description,
        tags=tags,
        duration_seconds=round(duration, 1) if duration is not None else None,
        play=play,
        width=width,
        height=height,
        resolution=f"{width}x{height}" if width > 0 and height > 0 else str(raw.get("resolution") or ""),
        query=query or str(raw.get("query") or ""),
        page=page if page is not None else _safe_int(raw.get("page"), default=None),
        raw=raw,
    )
    as_dict = asdict(normalized)
    for key in ("allow_unknown_duration", "duration_source"):
        if key in raw:
            as_dict[key] = raw[key]
    orientation_hint, orientation_source = _detect_candidate_orientation(as_dict)
    as_dict["orientation_hint"] = orientation_hint
    as_dict["orientation_source"] = orientation_source
    return as_dict


def _pick_string(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _extract_author(data: dict[str, Any]) -> str:
    direct = _pick_string(data, "author", "uploader", "uploader_id", "creator", "nickname")
    if direct:
        return direct
    for key in ("author", "owner", "user"):
        value = data.get(key)
        if isinstance(value, dict):
            nested = _pick_string(value, "nickname", "name", "username", "id", "uid", "userId")
            if nested:
                return nested
    return ""


def _extract_tags(data: dict[str, Any]) -> list[str]:
    for key in ("tags", "tagList", "hashtags"):
        value = data.get(key)
        if isinstance(value, list):
            tags: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    text = _pick_string(item, "name", "title", "tag_name")
                else:
                    text = str(item or "").strip()
                if text:
                    tags.append(text)
            return tags
    tag_text = _pick_string(data, "tag", "typename")
    return [tag_text] if tag_text else []


def _extract_duration(data: dict[str, Any]) -> float | None:
    for key in ("duration_seconds", "duration", "duration_string"):
        parsed = _parse_duration_to_seconds(data.get(key))
        if parsed is not None:
            return parsed
    video = data.get("video")
    if isinstance(video, dict):
        parsed = _parse_duration_to_seconds(video.get("duration"))
        if parsed is not None:
            return parsed / 1000 if parsed > 3600 else parsed
    return None


def _extract_resolution(data: dict[str, Any]) -> tuple[int, int]:
    width = _safe_int(data.get("width"), default=0) or 0
    height = _safe_int(data.get("height"), default=0) or 0
    video = data.get("video")
    if isinstance(video, dict):
        width = width or (_safe_int(video.get("width"), default=0) or 0)
        height = height or (_safe_int(video.get("height"), default=0) or 0)
    return width, height


def _extract_play(data: dict[str, Any]) -> int:
    for key in ("play", "view_count", "view", "play_count"):
        value = _safe_int(data.get(key), default=0)
        if value:
            return value
    stats = data.get("statistics")
    if isinstance(stats, dict):
        for key in ("play_count", "view_count", "digg_count"):
            value = _safe_int(stats.get(key), default=0)
            if value:
                return value
    return 0


def _safe_int(value: Any, *, default: int | None = 0) -> int | None:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default
