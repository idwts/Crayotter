from __future__ import annotations

from ._shared import *


@tool
def rebuild_local_dataset(
    output_name: str = "editing_dataset",
    include_analysis_text: bool = False,
) -> str:
    """重构本地分析数据集，生成统一的可检索编辑数据文件。

    会扫描 WORKSPACE 下所有 `*_analysis.json`，刷新语义索引后写入聚合数据集，
    便于后续时间线规划、转场规划、旁白规划工具复用。
    """
    try:
        analysis_files = sorted(
            WORKSPACE.glob("*_analysis.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not analysis_files:
            return "数据集重构出错: 未找到 *_analysis.json，请先执行 analyze_video"

        videos: list[dict[str, Any]] = []
        total_segments = 0
        total_semantic_segments = 0

        for fp in analysis_files:
            try:
                with fp.open("r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception:
                continue

            semantic_segments, semantic_index = _ensure_analysis_semantic_index(fp, payload)
            raw_segments = payload.get("segments", [])
            segments: list[dict[str, Any]] = []
            if isinstance(raw_segments, list):
                for seg in raw_segments:
                    if not isinstance(seg, dict):
                        continue
                    try:
                        s = float(seg.get("start", 0))
                        e = float(seg.get("end", 0))
                    except Exception:
                        continue
                    if e <= s:
                        continue
                    segments.append(
                        {
                            "start": round(s, 2),
                            "end": round(e, 2),
                            "duration": round(e - s, 2),
                        }
                    )

            total_segments += len(segments)
            total_semantic_segments += len(semantic_segments)

            item = {
                "source_video": str(payload.get("source_video", "")),
                "analysis_json": str(fp),
                "analysis_goal": str(payload.get("analysis_goal", "")),
                "segments": segments,
                "semantic_segments": semantic_segments,
                "semantic_index": semantic_index,
                "saved_at": str(payload.get("saved_at", "")),
            }
            if include_analysis_text:
                item["analysis_text"] = str(payload.get("analysis_text", ""))
            videos.append(item)

        if not videos:
            return "数据集重构出错: 没有可用分析数据"

        output_path = _safe_output_data_path(output_name, suffix=".json", default_stem="editing_dataset")
        dataset = {
            "status": "success",
            "generated_at": datetime.now().isoformat(),
            "video_count": len(videos),
            "segment_count": total_segments,
            "semantic_segment_count": total_semantic_segments,
            "videos": videos,
        }
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(dataset, f, ensure_ascii=False, indent=2)

        return json.dumps(
            {
                "status": "success",
                "path": str(output_path),
                "video_count": len(videos),
                "segment_count": total_segments,
                "semantic_segment_count": total_semantic_segments,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return f"数据集重构出错: {e}"
