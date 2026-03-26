from __future__ import annotations

import asyncio

from ._shared import *


@tool
def search_bilibili_video(
    query: str,
    max_results: int = 5,
    pages: int = 2,
    expand_variants: int = 3,
    max_total_results: int | None = None,
    per_request_timeout_seconds: int = 30,
    total_timeout_seconds: int = 120,
) -> str:
    """在 Bilibili 上搜索视频资源，返回视频标题、BV号、时长、播放量等信息。
    搜索结果会自动写入候选池，供后续 rank_video_candidates 聚合使用。

    Args:
        query: 搜索关键词，例如 "华南理工大学 宣传片"。支持中英文。
        max_results: 每个查询变体每页最多返回的视频数量，默认 5。
            建议范围 10~30，过小会漏掉优质视频。
        pages: 每个查询变体搜索的页数，默认 2（即第1页+第2页）。
            增大可扩大搜索范围，建议 1~3，过大会增加耗时。
        expand_variants: 自动扩展的查询变体数量，默认 3。
            会基于原始 query 生成 expand_variants 个相近关键词并分别搜索。
            设为 1 表示只用原始 query，增大可提升覆盖面。
        max_total_results: 最终候选列表的上限数量。默认自动计算为
            max_results × pages × 变体数，可手动设置（如 60）以控制候选数量。
    """
    logger.info(
        "🔍 开始搜索Bilibili视频: query='%s', max_results=%s, pages=%s, expand_variants=%s",
        query,
        max_results,
        pages,
        expand_variants,
    )
    per_request_timeout = max(5, int(per_request_timeout_seconds))
    total_timeout = max(per_request_timeout, int(total_timeout_seconds))
    try:
        queries = _expand_queries(query, max_variants=expand_variants)

        def _clean_text(value: Any) -> str:
            return re.sub(r"<[^>]+>", "", str(value or "")).strip()

        def _play_count(value: Any) -> int:
            if isinstance(value, (int, float)):
                return int(value)
            text = str(value or "").strip().lower().replace(",", "")
            if not text:
                return 0
            try:
                return int(float(text))
            except Exception:
                unit_match = re.fullmatch(r"(\d+(?:\.\d+)?)(万|亿)", text)
                if not unit_match:
                    return 0
                n = float(unit_match.group(1))
                return int(n * 10_000) if unit_match.group(2) == "万" else int(n * 100_000_000)

        # 尝试导入 bilibili_api
        try:
            from bilibili_api import search as bili_search
            from bilibili_api import sync
        except ImportError:
            error_msg = "错误: 未安装 bilibili-api-python，请运行 `pip install bilibili-api-python aiohttp`"
            logger.error(f"❌ {error_msg}")
            return error_msg

        # 使用同步方式运行异步代码
        async def do_search():
            all_videos: list[dict[str, Any]] = []

            for q in queries:
                for page in range(1, max(1, pages) + 1):
                    try:
                        result = await asyncio.wait_for(
                            bili_search.search_by_type(
                                keyword=q,
                                search_type=bili_search.SearchObjectType.VIDEO,
                                page=page,
                                page_size=max_results,
                            ),
                            timeout=per_request_timeout,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Bilibili搜索超时: query='%s', page=%s, timeout=%ss",
                            q,
                            page,
                            per_request_timeout,
                        )
                        continue

                    logger.debug(
                        "B站搜索返回结构: %s",
                        list(result.keys()) if isinstance(result, dict) else type(result),
                    )

                    rows = result.get("result", []) if isinstance(result, dict) else []
                    if not isinstance(rows, list):
                        rows = []
                    logger.debug("获取到 %s 条搜索结果", len(rows))

                    for v in rows:
                        try:
                            bvid = str(v.get("bvid") or "").strip()
                            if not bvid:
                                continue

                            raw_duration = v.get("duration", "")
                            all_videos.append({
                                "title": _clean_text(v.get("title", "")),
                                "bvid": bvid,
                                "url": str(v.get("arcurl") or f"https://www.bilibili.com/video/{bvid}"),
                                "author": str(v.get("author") or v.get("up_name") or v.get("uname") or "").strip(),
                                "duration": raw_duration,
                                "duration_seconds": _parse_duration_to_seconds(raw_duration),
                                "play": _play_count(v.get("play", v.get("view", 0))),
                                "description": _clean_text(v.get("description", "") or v.get("desc", ""))[:1000],
                                "intro": _clean_text(v.get("intro", ""))[:1000],
                                "tag": _clean_text(v.get("tag", ""))[:300],
                                "typename": _clean_text(v.get("typename", ""))[:120],
                                "pubdate": v.get("pubdate", None),
                                "favorites": v.get("favorites", None),
                                "review": v.get("review", None),
                                "danmaku": v.get("danmaku", None),
                                "source": "bilibili",
                                "query": q,
                                "page": page,
                            })
                        except Exception as e:
                            logger.warning(f"解析B站视频数据出错: {e}")
                            continue

            return all_videos

        try:
            all_videos = sync(asyncio.wait_for(do_search(), timeout=total_timeout))
        except asyncio.TimeoutError:
            logger.warning("Bilibili整体搜索超时: query='%s', timeout=%ss", query, total_timeout)
            return f"Bilibili 搜索超时（>{total_timeout}s），请稍后重试或缩小搜索范围。"
        total_found = len(all_videos)
        deduped = _dedupe_by_key(all_videos, "bvid")
        duration_filtered, dropped_long, dropped_unknown = _filter_candidates_by_max_duration(
            deduped,
            max_seconds=MAX_DOWNLOAD_DURATION_SECONDS,
            keep_unknown=False,
        )

        if max_total_results is None:
            max_total_results = max_results * max(1, pages) * max(1, len(queries))
        candidates = duration_filtered[:max_total_results]
        
        if not candidates:
            logger.warning(f"⚠️  未找到Bilibili相关视频: {query}")
            return f"未找到相关视频: {query}"

        logger.info(
            "📊 Bilibili搜索统计: 查询=%s, 原始结果=%s, 去重后=%s, 过滤后=%s, 超时长剔除=%s, 时长未知剔除=%s, 候选=%s",
            len(queries),
            total_found,
            len(deduped),
            len(duration_filtered),
            dropped_long,
            dropped_unknown,
            len(candidates),
        )
        _append_candidates_to_pool(candidates)
        return json.dumps(candidates, ensure_ascii=False, indent=2)
    
    except Exception as e:
        error_msg = f"搜索B站视频出错: {e}"
        logger.error(f"❌ Bilibili搜索异常: {e}", exc_info=True)
        return error_msg
