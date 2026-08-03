from __future__ import annotations

from ._shared import *


def _select_temporally_diverse(
    candidates: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    remaining = sorted(candidates, key=lambda item: item["score"], reverse=True)
    selected: list[dict[str, Any]] = []
    while remaining and len(selected) < limit:
        best_index = 0
        best_rank: tuple[float, float] | None = None
        for index, item in enumerate(remaining):
            same_source = [
                chosen
                for chosen in selected
                if chosen["source_video"] == item["source_video"]
            ]
            if not selected or not same_source:
                distance = 1.0 if selected else 0.0
            else:
                duration = max(
                    1.0,
                    float(item.get("_source_duration", 0.0) or 0.0),
                )
                midpoint = float(item["start"] + item["end"]) / 2
                distance = min(
                    1.0,
                    min(
                        abs(midpoint - float(chosen["start"] + chosen["end"]) / 2)
                        / duration
                        for chosen in same_source
                    ),
                )
            rank = (float(item["score"]) + 0.02 * distance, float(item["score"]))
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best_index = index
        selected.append(remaining.pop(best_index))
    return selected


@tool
def recall_semantic_segments(
    query: str,
    top_k: int = 12,
    min_duration: float = 3.0,
    max_duration: float = 25.0,
    source_video_filters: list[str] | None = None,
) -> str:
    """基于 analyze_video 产出的分析JSON，按语义召回最匹配的视频片段。

    适用场景：先用该工具找“最像当前剪辑意图”的片段，再用 cut_video 精裁。

    Args:
        query: 当前剪辑目标的语义描述，例如“晨光下校园航拍+学生步行，氛围积极”。
        top_k: 返回片段数量上限，默认 12。
        min_duration: 片段最小时长（秒），默认 3。
        max_duration: 片段最大时长（秒），默认 25。
        source_video_filters: 可选，按源视频名关键词过滤（如 ["selected_1", "campus"]）。
    """
    try:
        q = (query or "").strip()
        if not q:
            return "语义召回出错: query 不能为空。"

        retrieval_mode = "text"

        analysis_files = _iter_analysis_json_files()
        if not analysis_files:
            return "语义召回出错: 未找到任何 *_analysis.json，请先执行 analyze_video。"

        filters = [f.strip().lower() for f in (source_video_filters or []) if str(f).strip()]
        candidates: list[dict[str, Any]] = []
        for fp in analysis_files:
            try:
                with fp.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue

            source_video = str(data.get("source_video", "")) or fp.stem.replace("_analysis", "")
            source_name = Path(source_video).name.lower()
            if filters and not any(f in source_name for f in filters):
                continue
            try:
                source_duration = float(data.get("source_duration_seconds", 0.0) or 0.0)
            except (TypeError, ValueError):
                source_duration = 0.0

            semantic_segments = data.get("semantic_segments", [])
            if not isinstance(semantic_segments, list) or not semantic_segments:
                semantic_segments = _extract_semantic_segments_from_analysis(str(data.get("analysis_text", "")))

            semantic_segments, _ = _ensure_analysis_semantic_index(fp, data)
            if source_duration <= 0 and semantic_segments:
                source_duration = max(
                    float(segment.get("end", 0.0) or 0.0)
                    for segment in semantic_segments
                    if isinstance(segment, dict)
                )

            if not semantic_segments:
                for seg in data.get("segments", []) if isinstance(data.get("segments", []), list) else []:
                    try:
                        s = float(seg.get("start", 0))
                        e = float(seg.get("end", 0))
                    except Exception:
                        continue
                    if e <= s:
                        continue
                    semantic_segments.append({
                        "start": round(s, 2),
                        "end": round(e, 2),
                        "duration": round(e - s, 2),
                        "semantic_text": "",
                    })

            for seg in semantic_segments:
                try:
                    start = float(seg.get("start", 0))
                    end = float(seg.get("end", 0))
                except Exception:
                    continue
                if end <= start:
                    continue

                duration = round(end - start, 2)
                if duration < float(min_duration) or duration > float(max_duration):
                    continue

                semantic_text = str(seg.get("semantic_text", ""))
                score = _semantic_similarity_score(q, semantic_text, duration)
                match_method = "text"
                if score <= 0:
                    continue

                candidates.append({
                    "score": score,
                    "match_method": match_method,
                    "source_video": source_video,
                    "analysis_json": str(fp),
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "duration": duration,
                    "semantic_text": semantic_text,
                    "_source_duration": source_duration,
                })

        if not candidates:
            return json.dumps({
                "status": "empty",
                "query": q,
                "message": "没有召回到匹配片段，可放宽时长范围或更换 query。",
                "results": [],
            }, ensure_ascii=False)

        k = max(1, int(top_k))
        selected = _select_temporally_diverse(candidates, k)

        results: list[dict[str, Any]] = []
        for i, item in enumerate(selected, start=1):
            item = {key: value for key, value in item.items() if not key.startswith("_")}
            results.append({
                "rank": i,
                **item,
            })

        logger.info(
            "🧠 语义片段召回: query='%s', 分析文件=%s, 候选=%s, 返回=%s",
            q,
            len(analysis_files),
            len(candidates),
            len(results),
        )

        return json.dumps({
            "status": "success",
            "query": q,
            "retrieval_mode": retrieval_mode,
            "top_k": k,
            "total_candidates": len(candidates),
            "returned": len(results),
            "results": results,
        }, ensure_ascii=False)
    except Exception as e:
        logger.error("❌ 语义片段召回异常: %s", e, exc_info=True)
        return f"语义召回出错: {e}"
