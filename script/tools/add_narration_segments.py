from __future__ import annotations

import math

from . import _shared
from ._shared import *
from ._shared import (
    Any,
    Path,
    WORKSPACE,
    _resolve_workspace_input_path,
    _safe_output_video_path,
    _tts_generate,
    logger,
    tool,
)
from .subtitle_layout import burn_subtitles, resolve_subtitle_font

# Kept as an import alias for callers of the original helper.
_resolve_subtitle_font_path = resolve_subtitle_font


@tool
def add_narration_segments(
    video_path: str,
    segments: list[dict],
    voice: str = "Ethan",
    add_subtitle: bool = True,
    subtitle_font: str = "./AlibabaPuHuiTi-3-55-Regular/AlibabaPuHuiTi-3-55-Regular.ttf",
    min_narration_coverage_ratio: float = 0.35,
    output_name: str = "narrated_segmented",
) -> str:
    """为视频按时间段添加分段配音和字幕，实现音画同步。

    与 add_narration 的区别：add_narration 是一整段旁白覆盖全视频，
    本工具按时间段精确放置每段配音，确保旁白内容与对应画面同步。
    同时自动添加字幕（可关闭）。

    使用流程：
    1. 根据剪辑蓝图和分析数据，为每个片段或片段组编写对应的旁白文案
    2. 指定每段旁白的起止时间（与视频时间轴对齐）
    3. 工具为每段生成一次 TTS，保持原开始时间，按实测时长对齐配音和字幕
    4. 完整配音超出下一段开始时间或视频结尾时明确报错，不改写或截断台词

    Args:
        video_path: 输入视频文件路径（已完成剪辑/合并/转场的视频）。
            例如 "/workspace/transitioned.mp4"
        segments: 分段旁白列表，每个元素是一个 dict：
            - "text": str — 该段旁白文案（必填）
            - "start": float — 旁白开始时间（秒），对应视频时间轴（必填）
            - "end": float — 旁白结束时间（秒），建议结束时间；按实测配音时长对齐字幕，可用时间不足则报错（必填）
            示例：
            [
                {"text": "新疆大学始建于1924年", "start": 10.0, "end": 20.0},
                {"text": "校园内拥有中亚最大的图书馆", "start": 25.0, "end": 35.0},
                {"text": "这里的食堂美食让人流连忘返", "start": 45.0, "end": 55.0}
            ]
            注意事项：
            - 多个片段可以共用一段旁白（合并为一个 segment，跨越多个片段）
            - 也可以为每个片段单独写旁白
            - 片段间可以留白（无旁白），让画面自己说话
            - start/end 必须在视频时长范围内
        voice: TTS 音色，可选值：
            "Cherry"（阳光积极、亲切自然小姐姐（女性））/
            "Serena"（温柔小姐姐（女性））/
            "Ethan"（标准普通话，阳光、温暖、活力、朝气（男性））/
            "Moon"（率性帅气的月白（男性））
        add_subtitle: 是否同时添加字幕，默认 True。
            字幕会显示在视频底部，与旁白同步出现/消失。
        subtitle_font: 字幕字体路径。支持相对项目根目录或绝对路径。
            默认优先使用项目中的 Alibaba 普惠体。
        min_narration_coverage_ratio: 全片旁白覆盖率阈值（0~1），低于该值会返回警告。
        output_name: 输出文件名（不含扩展名），默认 "narrated_segmented"。
    """
    try:
        from ._native_ffmpeg import mix_narration_native, probe_video
        from .narration_pipeline import align_prepared_narration

        resolved_video = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved_video is None:
            return f"分段配音出错: 输入视频不存在或不在WORKSPACE: {video_path}"
        if not segments or not isinstance(segments, list):
            return "分段配音出错: segments 参数必须是非空列表"

        video_dur = probe_video(resolved_video).duration
        resolved_font = _resolve_subtitle_font_path(subtitle_font)
        if add_subtitle and not resolved_font:
            logger.warning("字幕字体未找到，将跳过字幕渲染: %s", subtitle_font)
        safe_stem = _safe_output_video_path(output_name, default_stem="narrated_segmented").stem
        prepared = []
        target_coverage = 0.0
        for idx, segment in enumerate(segments, start=1):
            text = str(segment.get("text") or "").strip()
            start, end = float(segment["start"]), float(segment["end"])
            if not text or not all(math.isfinite(value) for value in (start, end)) or start < 0 or end <= start or start >= video_dur:
                return f"分段配音出错: 段 {idx} 文案或时间范围无效"
            target_coverage += min(end, video_dur) - start
            audio_path = WORKSPACE / f"{safe_stem}_seg{idx}.mp3"
            error = _tts_generate(text, voice, audio_path)
            if error:
                return f"分段配音出错: 段 {idx}: {error}"
            prepared.append({**segment, "text": text, "start": start, "end": end, "audio_path": str(audio_path)})

        aligned = align_prepared_narration(prepared, video_dur)
        output_path = _safe_output_video_path(output_name, default_stem="narrated_segmented")
        mix_narration_native(resolved_video, output_path, [
            (Path(item["audio_path"]), item["start"], item["end"]) for item in aligned
        ])
        subtitles = [{"text": item["text"], "start": item["start"], "end": item["end"]} for item in aligned]
        if add_subtitle and resolved_font:
            burn_subtitles(output_path, output_path, subtitles, resolved_font)
        actual_coverage = sum(item["audio_duration_seconds"] for item in aligned)
        result = {
            "status": "success", "path": str(output_path),
            "total_segments": len(segments), "success_segments": len(aligned),
            "has_subtitles": bool(add_subtitle and resolved_font),
            "subtitle_font_used": resolved_font if add_subtitle else None,
            "target_narration_seconds": round(target_coverage, 3),
            "actual_narration_seconds": round(actual_coverage, 3),
            "narration_coverage_ratio": round(actual_coverage / video_dur, 4),
            "target_slot_coverage_ratio": round(actual_coverage / target_coverage, 4),
            "segments": aligned,
        }
        if actual_coverage / target_coverage < min_narration_coverage_ratio:
            result["warnings"] = ["旁白相对原分配时段覆盖率偏低，请检查文案或时间安排。"]
        return json.dumps(result, ensure_ascii=False)
    except ModelCallError:
        raise
    except Exception as exc:
        return f"分段配音出错: {exc}"
