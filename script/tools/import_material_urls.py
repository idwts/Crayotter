from __future__ import annotations

from ._shared import *
from .material_sources import detect_platform_from_url, merge_candidates, normalize_candidate


@tool
def import_material_urls(urls_json: str, query: str = "") -> str:
    """将用户提供的素材 URL/分享链接导入候选池，适用于抖音、小红书、快手、B站、YouTube 等平台。"""
    urls = _parse_urls(urls_json)
    candidates: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for url in urls:
        platform = detect_platform_from_url(url)
        metadata = _probe_url_metadata(url)
        if metadata is None:
            failures.append({"url": url, "platform": platform, "error": "metadata_probe_failed"})
            metadata = {"url": url, "title": url, "source": platform}
        metadata["url"] = str(metadata.get("webpage_url") or metadata.get("url") or url)
        metadata["allow_unknown_duration"] = True
        candidates.append(normalize_candidate(metadata, source=platform, query=query))

    merged = merge_candidates(candidates)
    _append_candidates_to_pool(merged)
    return json.dumps(
        {
            "candidates": merged,
            "imported_count": len(merged),
            "failures": failures,
        },
        ensure_ascii=False,
    )


def _parse_urls(payload: str) -> list[str]:
    text = str(payload or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        if isinstance(parsed, dict):
            for key in ("urls", "items", "data"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return [str(item).strip() for item in value if str(item).strip()]
    except Exception:
        pass
    return re.findall(r"https?://[^\s\]\)\"'，。；;]+", text)


def _probe_url_metadata(url: str) -> dict[str, Any] | None:
    cmd = ["yt-dlp", "-J", "--no-playlist", "--skip-download", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        parsed = json.loads(result.stdout)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None
