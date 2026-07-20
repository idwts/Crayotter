from __future__ import annotations

import re
from typing import Any


def normalize_adapter_candidate(
    item: dict[str, Any], *, platform: str, query: str
) -> dict[str, Any]:
    """Emit the stable candidate fields consumed by the existing ranking path.

    This small normalizer intentionally does not import ``tools._shared`` so the
    adapter layer remains usable in lightweight workers without model SDKs.
    """
    raw = dict(item or {})
    url = _pick(raw, "url", "webpage_url", "share_url", "arcurl")
    candidate_id = _pick(raw, "id", "bvid", "aweme_id", "note_id", "item_id", "video_id")
    title = _pick(raw, "title", "display_title", "desc", "description", "fulltitle")
    description = _pick(raw, "description", "desc", "intro")
    author = _author(raw)
    duration = _duration(raw)
    width, height = _resolution(raw)
    orientation = "portrait" if height > width > 0 else "landscape" if width > height > 0 else "square" if width == height and width > 0 else "unknown"
    play = _integer(raw.get("play") or raw.get("view_count") or raw.get("play_count"))
    if not play and isinstance(raw.get("statistics"), dict):
        stats = raw["statistics"]
        play = _integer(stats.get("play_count") or stats.get("view_count") or stats.get("digg_count"))
    return {
        "id": candidate_id,
        "source": platform,
        "platform": platform,
        "url": url,
        "title": title,
        "author": author,
        "description": description,
        "tags": _tags(raw),
        "duration_seconds": round(duration, 1) if duration is not None else None,
        "play": play,
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}" if width and height else str(raw.get("resolution") or ""),
        "orientation_hint": orientation,
        "orientation_source": "resolution" if orientation != "unknown" else "unknown",
        "query": query,
        "page": _integer(raw.get("page")) or None,
        "raw": raw,
    }


def merge_adapter_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        identity = str(candidate.get("url") or candidate.get("id") or "").strip()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(candidate)
    return result


def _pick(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None and not isinstance(value, (dict, list)) and str(value).strip():
            return str(value).strip()
    return ""


def _author(data: dict[str, Any]) -> str:
    direct = _pick(data, "author", "uploader", "creator", "nickname")
    if direct:
        return direct
    for key in ("author", "owner", "user"):
        nested = data.get(key)
        if isinstance(nested, dict):
            found = _pick(nested, "nickname", "name", "username", "id", "uid")
            if found:
                return found
    return ""


def _duration(data: dict[str, Any]) -> float | None:
    for key in ("duration_seconds", "duration", "duration_string"):
        parsed = _duration_value(data.get(key))
        if parsed is not None:
            return parsed
    video = data.get("video")
    if isinstance(video, dict):
        parsed = _duration_value(video.get("duration"))
        if parsed is not None:
            return parsed / 1000 if parsed > 3600 else parsed
    return None


def _duration_value(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    if re.fullmatch(r"\d+(?::\d+){1,2}", text):
        parts = [int(part) for part in text.split(":")]
        total = 0
        for part in parts:
            total = total * 60 + part
        return float(total)
    return None


def _resolution(data: dict[str, Any]) -> tuple[int, int]:
    width = _integer(data.get("width"))
    height = _integer(data.get("height"))
    video = data.get("video")
    if isinstance(video, dict):
        width = width or _integer(video.get("width"))
        height = height or _integer(video.get("height"))
    return width, height


def _integer(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _tags(data: dict[str, Any]) -> list[str]:
    value = data.get("tags") or data.get("tagList") or data.get("hashtags")
    if not isinstance(value, list):
        text = _pick(data, "tag", "typename")
        return [text] if text else []
    result: list[str] = []
    for item in value:
        text = _pick(item, "name", "title", "tag_name") if isinstance(item, dict) else str(item or "").strip()
        if text:
            result.append(text)
    return result
