from __future__ import annotations

from ._shared import *


@tool
def search_youtobe_video(
    query: str,
    max_results: int = 5,
    expand_variants: int = 3,
    max_total_results: int | None = None,
) -> str:
    """在 YouTube 上搜索视频资源，返回视频标题、URL、时长列表。
    搜索结果会自动写入候选池，供后续 rank_video_candidates 聚合使用。

    Args:
        query: 搜索关键词，例如 "inception movie scenes"。支持中英文。
        max_results: 每个查询变体最多返回的视频数量，默认 5，建议范围 5~30。
        expand_variants: 自动扩展的查询变体数量，默认 3。
            设为 1 表示只用原始 query 搜索，增大可扩大覆盖面但耗时更长。
        max_total_results: 最终候选列表的上限数量。默认为 max_results × 变体数，
            可手动设置上限（如 60）以避免候选过多。
    """    
    logger.info(
        "🔍 开始搜索YouTube视频: query='%s', max_results=%s, expand_variants=%s",
        query,
        max_results,
        expand_variants,
    )
    try:
        queries = _expand_queries(query, max_variants=expand_variants)
        all_videos: list[dict[str, Any]] = []
        total_found = 0

        for q in queries:
            cmd = [
                "yt-dlp",
                f"ytsearch{max_results}:{q}",
                "--dump-json",
                "--flat-playlist",
                "--no-download",
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                logger.warning("⚠️ YouTube搜索超时: query='%s'", q)
                continue

            if result.returncode != 0:
                logger.warning("⚠️ YouTube搜索失败: query='%s', err='%s'", q, result.stderr[:200])
                continue

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                data = json.loads(line)
                duration_seconds = _parse_duration_to_seconds(
                    data.get("duration", data.get("duration_string"))
                )
                candidate = {
                    "title": data.get("title", "N/A"),
                    "url": data.get("url")
                    or f"https://www.youtube.com/watch?v={data.get('id', '')}",
                    "duration": data.get("duration_string", "unknown"),
                    "duration_seconds": duration_seconds,
                    "description": (data.get("description") or "")[:200],
                    "source": "youtube",
                    "query": q,
                }
                orientation_hint, orientation_source = _detect_candidate_orientation(candidate)
                candidate["orientation_hint"] = orientation_hint
                candidate["orientation_source"] = orientation_source
                all_videos.append(candidate)

        total_found = len(all_videos)
        deduped = _dedupe_by_key(all_videos, "url")
        duration_filtered, dropped_long, dropped_unknown = _filter_candidates_by_max_duration(
            deduped,
            max_seconds=MAX_DOWNLOAD_DURATION_SECONDS,
            keep_unknown=False,
        )

        if max_total_results is None:
            max_total_results = max_results * max(1, len(queries))
        candidates = duration_filtered[:max_total_results]

        logger.info(
            "📊 YouTube搜索统计: 查询=%s, 原始结果=%s, 去重后=%s, 过滤后=%s, 超时长剔除=%s, 时长未知剔除=%s, 候选=%s",
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
    except FileNotFoundError:
        error_msg = "错误: 未安装 yt-dlp，请运行 `pip install yt-dlp`"
        logger.error(f"❌ {error_msg}")
        return error_msg
    except Exception as e:
        error_msg = f"搜索出错: {e}"
        logger.error(f"❌ YouTube搜索异常: {e}", exc_info=True)
        return error_msg
