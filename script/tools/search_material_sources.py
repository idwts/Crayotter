from __future__ import annotations

from ._shared import *
from .material_sources import merge_candidates, normalize_candidate
from .search_bilibili_video import search_bilibili_video


@tool
def search_material_sources(
    query: str,
    platforms: list[str] | None = None,
    max_results: int = 5,
    pages: int = 2,
    expand_variants: int = 3,
    max_total_results: int | None = None,
    request_concurrency: int | None = None,
) -> str:
    """跨素材源搜索视频候选。v1 只对 Bilibili 执行关键词搜索，其他平台预留适配器并返回结构化跳过信息。"""
    requested = [str(item).strip().lower() for item in (platforms or ["bilibili"]) if str(item).strip()]
    if not requested:
        requested = ["bilibili"]

    all_candidates: list[dict[str, Any]] = []
    unsupported: list[dict[str, str]] = []
    supported_requested = [platform for platform in requested if platform == "bilibili"]
    platforms_to_run = list(supported_requested)
    if not platforms_to_run:
        platforms_to_run.append("bilibili")

    for platform in requested:
        if platform == "bilibili":
            continue
        unsupported.append(
            {
                "platform": platform,
                "status": "unsupported_search",
                "reason": "v1 only supports keyword search for bilibili; falling back to bilibili keyword search.",
            }
        )

    for platform in platforms_to_run:
        if platform == "bilibili":
            raw = search_bilibili_video.invoke(
                {
                    "query": query,
                    "max_results": max_results,
                    "pages": pages,
                    "expand_variants": expand_variants,
                    "max_total_results": max_total_results,
                    "request_concurrency": request_concurrency,
                }
            )
            try:
                parsed = json.loads(str(raw))
            except Exception:
                parsed = []
            if isinstance(parsed, list):
                all_candidates.extend(
                    normalize_candidate(item, source="bilibili", query=query)
                    for item in parsed
                    if isinstance(item, dict)
                )
            continue

    merged = merge_candidates(all_candidates)
    _append_candidates_to_pool(merged)
    return json.dumps({"candidates": merged, "unsupported": unsupported}, ensure_ascii=False)
