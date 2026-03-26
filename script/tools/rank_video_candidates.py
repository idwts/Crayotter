from __future__ import annotations

from ._shared import *


@tool
def rank_video_candidates(
    candidates_json: str,
    top_k: int = 5,
    max_review: int = 0,
    request_timeout_seconds: int = 30,
) -> str:
    """使用 AI 模型对候选视频进行评分排序，选出最适合剪辑的 Top K 个视频。
    会自动聚合之前 search_bilibili_video / search_youtobe_video 写入的候选池。
    排序结果中的 selected_videos 字段包含推荐下载的视频列表。

    Args:
        candidates_json: 候选视频列表的 JSON 字符串，或传入 "[]"（空列表字符串）
            让工具自动从候选池中加载所有搜索结果。
            格式为搜索工具返回的 JSON 数组，每项包含 title/url/duration 等字段。
            ⚠️ 一般直接传 "[]" 即可，工具会自动合并之前所有搜索的候选池。
        top_k: 希望保留（推荐下载）的视频数量，默认 5。
            建议根据目标时长设置。
        max_review: AI 模型最多评审的候选数量，默认 0（表示评审全部候选）。
            若 >0 且小于候选总数，则只评审前 max_review 个。
    """
    try:
        cache_key = json.dumps(
            {
                "payload": candidates_json,
                "top_k": top_k,
                "max_review": max_review,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if cache_key in _RANK_CACHE:
            logger.info("♻️ MLLM筛选命中缓存，跳过重复评估")
            return _RANK_CACHE[cache_key]

        incoming_candidates = _parse_candidate_payload(candidates_json)
        pooled_candidates = _load_candidates_from_pool()
        candidates = _merge_candidates([*pooled_candidates, *incoming_candidates])
        candidates, dropped_long, dropped_unknown = _filter_candidates_by_max_duration(
            candidates,
            max_seconds=MAX_DOWNLOAD_DURATION_SECONDS,
            keep_unknown=False,
        )

        if not candidates:
            return (
                "筛选出错: 没有可用候选（超过10分钟或时长未知已剔除）。"
                f" 超时长剔除={dropped_long}, 时长未知剔除={dropped_unknown}"
            )

        total_candidates = len(candidates)
        review_limit = int(max_review)
        reviewed = candidates if review_limit <= 0 else candidates[: max(1, review_limit)]

        logger.info(
            "🤖 MLLM筛选开始: 入参=%s, 候选池=%s, 聚合候选=%s, 过滤后=%s, 超时长剔除=%s, 时长未知剔除=%s, 实际评估=%s, 目标保留=%s",
            len(incoming_candidates),
            len(pooled_candidates),
            len(_merge_candidates([*pooled_candidates, *incoming_candidates])),
            total_candidates,
            dropped_long,
            dropped_unknown,
            len(reviewed),
            top_k,
        )

        prompt = (
            "你是资深视频剪辑导演。请对候选视频逐条打分并排序。\n"
            "评分维度：主题匹配度、画面潜力、叙事价值、素材多样性、可剪辑性。\n"
            "对输入数组中的每一项都必须评分。\n"
            "只返回 JSON，格式如下：\n"
            "{\n"
            "  \"scored\": [\n"
            "    {\"index\": 0, \"score\": 0-10, \"reason\": \"<=30字\"}\n"
            "  ]\n"
            "}\n"
        )

        def _fallback_score(item: dict[str, Any]) -> tuple[float, str]:
            duration = float(item.get("duration_seconds") or 0)
            play = float(item.get("play") or 0)
            text = (
                f"{item.get('title', '')} {item.get('description', '')} "
                f"{item.get('intro', '')} {item.get('tag', '')}"
            ).lower()
            duration_score = 2.0
            if 20 <= duration <= 120:
                duration_score = 3.2
            elif 120 < duration <= 300:
                duration_score = 2.8
            play_score = min(2.6, 0.45 * (len(str(int(play))) if play > 0 else 0))
            keyword_score = 0.0
            for kw in ["校园", "大学", "介绍", "宣传", "历史", "科研", "学生", "学术"]:
                if kw in text:
                    keyword_score += 0.22
            score = max(0.0, min(9.8, duration_score + play_score + keyword_score + 1.1))
            return round(score, 3), "heuristic"

        client = _get_openai_client()
        scored_items: list[dict[str, Any]] = []
        batch_size = 35
        request_timeout = max(5, int(request_timeout_seconds))

        for offset in range(0, len(reviewed), batch_size):
            batch = reviewed[offset: offset + batch_size]
            content = ""
            parsed_scored: list[dict[str, Any]] = []
            logger.info(
                "MLLM筛选批次: %s-%s / %s",
                offset + 1,
                offset + len(batch),
                len(reviewed),
            )
            try:
                response = client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
                    ],
                    temperature=0.1,
                    max_tokens=1800,
                    timeout=request_timeout,
                )
                content = _extract_chat_content(response)
                parsed = json.loads(content)
                if isinstance(parsed, dict) and isinstance(parsed.get("scored"), list):
                    parsed_scored = [x for x in parsed.get("scored", []) if isinstance(x, dict)]
            except Exception:
                logger.warning(
                    "MLLM筛选批次失败，回退启发式评分: %s-%s / %s",
                    offset + 1,
                    offset + len(batch),
                    len(reviewed),
                )
                parsed_scored = []

            used_indices: set[int] = set()
            for row in parsed_scored:
                try:
                    idx = int(row.get("index", -1))
                    score = float(row.get("score", 0))
                except Exception:
                    continue
                if idx < 0 or idx >= len(batch):
                    continue
                used_indices.add(idx)
                reason = str(row.get("reason", "")).strip()[:60]
                scored_items.append({
                    "index": offset + idx,
                    "score": max(0.0, min(10.0, score)),
                    "reason": reason or "model_score",
                })

            for local_idx, item in enumerate(batch):
                if local_idx in used_indices:
                    continue
                score, reason = _fallback_score(item)
                scored_items.append({
                    "index": offset + local_idx,
                    "score": score,
                    "reason": reason,
                })

        scored_items.sort(key=lambda x: x.get("score", 0), reverse=True)

        ranked_videos: list[dict[str, Any]] = []
        for rank, row in enumerate(scored_items, start=1):
            idx = int(row.get("index", -1))
            if idx < 0 or idx >= len(reviewed):
                continue
            base = reviewed[idx]
            ranked_videos.append({
                **base,
                "rank": rank,
                "selection_score": row.get("score", None),
                "selection_reason": row.get("reason", ""),
                "review_index": idx,
            })

        selected_videos = ranked_videos[: max(0, int(top_k))] if top_k > 0 else ranked_videos
        selected_items = [
            {
                "index": int(v.get("review_index", -1)),
                "score": v.get("selection_score", 0),
                "reason": v.get("selection_reason", ""),
            }
            for v in selected_videos
        ]

        _append_candidates_to_pool(candidates)

        logger.info(
            "✅ MLLM筛选完成: 候选=%s, 评估=%s, 选中=%s",
            total_candidates,
            len(reviewed),
            len(selected_videos),
        )

        result_json = json.dumps({
            "candidate_total": total_candidates,
            "reviewed": len(reviewed),
            "input_candidates": len(incoming_candidates),
            "pool_candidates": len(pooled_candidates),
            "selected_count": len(selected_videos),
            "selected": selected_items,
            "selected_videos": selected_videos,
            "ranked_count": len(ranked_videos),
            "ranked_videos": ranked_videos,
        }, ensure_ascii=False)
        _RANK_CACHE[cache_key] = result_json
        return result_json
    except Exception as e:
        error_msg = f"筛选出错: {e}"
        logger.error(f"❌ MLLM筛选异常: {e}", exc_info=True)
        return error_msg
