from __future__ import annotations

from ._shared import *


def _load_narration_profile(voice: str) -> tuple[float, str]:
    profile_path = DATASETS_DIR / "narration_profiles.json"
    default_cps = 4.3
    default_style = "客观、清晰、与画面强关联"
    if not profile_path.exists():
        return default_cps, default_style
    try:
        with profile_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return default_cps, default_style

    voices = payload.get("voices", {}) if isinstance(payload, dict) else {}
    defaults = payload.get("defaults", {}) if isinstance(payload, dict) else {}
    picked = voices.get(voice, {}) if isinstance(voices, dict) else {}
    cps = float(picked.get("chars_per_second", defaults.get("chars_per_second", default_cps)) or default_cps)
    style = str(picked.get("recommended_style", default_style) or default_style)
    return max(2.0, min(cps, 8.0)), style


def _pick_analysis_for_video(video_path: Path, raw_analysis_path: str) -> tuple[Path | None, dict[str, Any] | None]:
    if raw_analysis_path:
        resolved = _resolve_workspace_input_path(raw_analysis_path, must_exist=True)
        if resolved and resolved.suffix.lower() == ".json":
            try:
                with resolved.open("r", encoding="utf-8") as f:
                    return resolved, json.load(f)
            except Exception:
                return None, None

    candidates = _match_analysis_json_files(video_path) or _iter_analysis_json_files()
    for fp in candidates:
        try:
            with fp.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        src = str(data.get("source_video", "")).lower()
        if video_path.name.lower() in src or video_path.stem.lower() in src:
            return fp, data
    if candidates:
        try:
            with candidates[0].open("r", encoding="utf-8") as f:
                return candidates[0], json.load(f)
        except Exception:
            pass
    return None, None


def _extract_candidate_segments(analysis_data: dict[str, Any], max_segments: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    semantic_segments = analysis_data.get("semantic_segments", [])
    if isinstance(semantic_segments, list):
        for seg in semantic_segments:
            if not isinstance(seg, dict):
                continue
            try:
                s = float(seg.get("start", 0))
                e = float(seg.get("end", 0))
            except Exception:
                continue
            if e <= s:
                continue
            candidates.append(
                {
                    "start": round(s, 2),
                    "end": round(e, 2),
                    "summary": str(seg.get("semantic_text", "")).strip()[:180],
                }
            )

    if not candidates and isinstance(analysis_data.get("segments"), list):
        for seg in analysis_data.get("segments", []):
            if not isinstance(seg, dict):
                continue
            try:
                s = float(seg.get("start", 0))
                e = float(seg.get("end", 0))
            except Exception:
                continue
            if e <= s:
                continue
            candidates.append(
                {
                    "start": round(s, 2),
                    "end": round(e, 2),
                    "summary": "",
                }
            )

    candidates.sort(key=lambda x: x["start"])
    if len(candidates) <= max_segments:
        return candidates

    # 均匀采样，保证覆盖整片时间轴
    sampled: list[dict[str, Any]] = []
    step = len(candidates) / max_segments
    for i in range(max_segments):
        sampled.append(candidates[min(len(candidates) - 1, int(i * step))])
    # 去重
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()
    for seg in sampled:
        key = (seg["start"], seg["end"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(seg)
    return deduped


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    content = (text or "").strip()
    if not content:
        return []
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return [x for x in parsed if isinstance(x, dict)]
        if isinstance(parsed, dict) and isinstance(parsed.get("segments"), list):
            return [x for x in parsed.get("segments", []) if isinstance(x, dict)]
    except Exception:
        pass

    if "[" in content and "]" in content:
        fragment = content[content.find("[") : content.rfind("]") + 1]
        try:
            parsed = json.loads(fragment)
            if isinstance(parsed, list):
                return [x for x in parsed if isinstance(x, dict)]
        except Exception:
            pass
    return []


@tool
def plan_narration_segments(
    video_path: str,
    analysis_json_path: str = "",
    topic: str = "",
    voice: str = "Cherry",
    style_hint: str = "宣传片、信息准确、情绪积极",
    max_segments: int = 14,
    output_name: str = "narration_segments_plan",
) -> str:
    """根据视频分析结果自动生成“音画同步”的分段旁白规划。

    该工具会：
    1) 读取分析数据中的时间段；
    2) 调用模型生成每段旁白文案（强约束贴合画面）；
    3) 校验并修正时间范围，避免越界和重叠；
    4) 保存为可直接传给 add_narration_segments 的 JSON 文件。
    """
    try:
        resolved_video = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved_video is None:
            return f"旁白规划出错: 输入视频不存在或不在WORKSPACE: {video_path}"

        video_meta = _get_video_meta(str(resolved_video))
        video_dur = float(video_meta.get("duration_seconds", 0.0) or 0.0)
        if video_dur <= 0:
            return "旁白规划出错: 无法读取视频时长"

        analysis_path, analysis_data = _pick_analysis_for_video(resolved_video, analysis_json_path)
        if analysis_data is None:
            return "旁白规划出错: 未找到可用分析JSON，请先执行 analyze_video"

        safe_max_segments = max(1, min(int(max_segments), 30))
        candidates = _extract_candidate_segments(analysis_data, safe_max_segments)
        if not candidates:
            return "旁白规划出错: 分析结果中没有可用时间段"

        speech_rate, voice_style = _load_narration_profile(voice)
        story_topic = (topic or "").strip() or str(analysis_data.get("analysis_goal", "")).strip() or "围绕视频画面进行解说"

        prompt = (
            "你是一名专业纪录片/宣传片配音文案编辑。请根据给定时间段与画面摘要，为每段生成一句或两句解说。\n"
            "硬性要求：\n"
            "1) 只输出 JSON 数组，不要任何额外文本。\n"
            "2) 每个元素必须包含: start, end, text。\n"
            "3) start/end 必须使用输入候选时间段，不要自行发明时间。\n"
            "4) 文案必须严格贴合画面摘要，不得编造无关信息。\n"
            "5) 宣传片语气，信息准确，中文自然。\n"
            f"6) 音色风格参考: {voice_style}。\n"
            "7) 每段文案尽量简洁，避免超过该段可朗读长度。\n"
        )

        user_payload = {
            "topic": story_topic,
            "style_hint": style_hint,
            "voice": voice,
            "video_duration_seconds": video_dur,
            "candidates": candidates,
            "output_schema_example": [
                {"start": 0.0, "end": 6.2, "text": "这所大学的晨光，把历史与青春同时点亮。"}
            ],
        }

        client = _get_openai_client()
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            temperature=0.25,
            max_tokens=2200,
        )
        content = _extract_chat_content(response)
        raw_segments = _extract_json_array(content)
        if not raw_segments:
            return "旁白规划出错: 模型未返回有效 JSON 段落"

        # 归一化与时长保护
        normalized: list[dict[str, Any]] = []
        warnings: list[str] = []
        for idx, seg in enumerate(raw_segments, start=1):
            text = str(seg.get("text", "")).strip()
            if not text:
                warnings.append(f"段 {idx}: text 为空，已跳过")
                continue
            try:
                s = float(seg.get("start", 0))
                e = float(seg.get("end", 0))
            except Exception:
                warnings.append(f"段 {idx}: 时间格式错误，已跳过")
                continue
            s = max(0.0, min(s, video_dur))
            e = max(0.0, min(e, video_dur))
            if e <= s:
                warnings.append(f"段 {idx}: end<=start，已跳过")
                continue

            # 按音色语速估算可读字符，超长则截断以减轻音画错位
            slot = e - s
            max_chars = max(6, int(slot * speech_rate * 1.05))
            clean_text = re.sub(r"\s+", "", text)
            if len(clean_text) > max_chars:
                text = clean_text[:max_chars]
                warnings.append(f"段 {idx}: 文案过长，已按时长截断")

            normalized.append({"text": text, "start": round(s, 2), "end": round(e, 2)})

        normalized.sort(key=lambda x: x["start"])
        # 去重叠保护
        for i in range(len(normalized) - 1):
            cur = normalized[i]
            nxt = normalized[i + 1]
            if cur["end"] > nxt["start"]:
                cur["end"] = round(max(cur["start"] + 0.1, nxt["start"] - 0.05), 2)
                if cur["end"] <= cur["start"]:
                    cur["end"] = round(cur["start"] + 0.1, 2)
                warnings.append(f"段 {i+1}: 与后段重叠，已自动收缩")

        normalized = [x for x in normalized if x["end"] > x["start"]]
        if not normalized:
            return "旁白规划出错: 归一化后无有效段落"

        output_path = _safe_output_data_path(output_name, suffix=".json", default_stem="narration_segments_plan")
        payload = {
            "status": "success",
            "video_path": str(resolved_video),
            "analysis_json": str(analysis_path) if analysis_path else "",
            "voice": voice,
            "speech_rate_chars_per_second": speech_rate,
            "segments": normalized,
            "warnings": warnings,
        }
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        return json.dumps(
            {
                "status": "success",
                "path": str(output_path),
                "segment_count": len(normalized),
                "warnings": warnings,
                "segments": normalized,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return f"旁白规划出错: {e}"
