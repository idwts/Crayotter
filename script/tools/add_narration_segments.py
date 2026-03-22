from __future__ import annotations
from ._shared import *
from ._shared import (
    Any,
    MODEL_NAME,
    Path,
    WORKSPACE,
    _extract_chat_content,
    _get_openai_client,
    _resolve_workspace_input_path,
    _safe_output_video_path,
    _tts_generate,
    logger,
    tool,
)

def _resolve_subtitle_font_path(custom_font: str | None = None) -> str | None:
    """解析可用字幕字体路径，优先使用用户传入字体。"""
    project_root = Path(__file__).resolve().parent.parent
    candidates: list[Path] = []

    if custom_font:
        cf = Path(custom_font)
        if cf.is_absolute():
            candidates.append(cf)
        else:
            candidates.append(project_root / cf)
            candidates.append(WORKSPACE / cf)

    candidates.extend(
        [
            project_root / "AlibabaPuHuiTi-3-55-Regular" / "AlibabaPuHuiTi-3-55-Regular.ttf",
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
        ]
    )

    seen: set[str] = set()
    for p in candidates:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        try:
            if p.exists() and p.is_file():
                return str(p)
        except Exception:
            continue
    return None


def _wrap_subtitle_text_by_pixels(
    text: str,
    font_path: str,
    font_size: int,
    max_width_px: int,
) -> str:
    """按像素宽度换行，避免固定字符数换行导致的越界与截断。"""
    try:
        from PIL import ImageFont

        font = ImageFont.truetype(font_path, size=max(1, int(font_size)))
        lines: list[str] = []
        current = ""

        for ch in text:
            if ch == "\n":
                if current.strip():
                    lines.append(current.strip())
                current = ""
                continue

            candidate = current + ch
            bbox = font.getbbox(candidate)
            width = max(0, bbox[2] - bbox[0])
            if current and width > max_width_px:
                lines.append(current.strip())
                current = ch
            else:
                current = candidate

        if current.strip():
            lines.append(current.strip())

        return "\n".join(lines) if lines else text
    except Exception:
        # Pillow 度量失败时回退到原文，避免中断主流程。
        return text


def _normalize_rewritten_text(raw: str) -> str:
    txt = (raw or "").strip()
    txt = txt.replace("\n", " ").replace("\r", " ")
    txt = re.sub(r"\s+", " ", txt)
    txt = txt.strip("\"'` ")
    return txt


def _heuristic_shorten_text(text: str, target_chars: int) -> str:
    """在 LLM 不可用时的保底压缩策略。"""
    if target_chars <= 0:
        return text
    compact = re.sub(r"[，。！？；：、,.!?;:]+", "，", text).strip()
    if len(compact) <= target_chars:
        return compact
    out = compact[:target_chars].rstrip("，。！？；：、,.!?;:")
    if out and out[-1] not in "。！？.!?":
        out += "。"
    return out


def _rewrite_text_to_fit_duration(
    text: str,
    max_duration: float,
    measured_duration: float,
    attempt_index: int,
) -> str:
    """将文案压缩到更适配时长的表达，不引入新事实。"""
    safe_max = max(0.3, float(max_duration))
    safe_measured = max(0.3, float(measured_duration))
    # 根据当前超时比例估算目标字数，逐轮收紧 5%。
    ratio = min(1.0, safe_max / safe_measured)
    tighten = max(0.65, 0.95 - 0.05 * max(0, attempt_index))
    target_chars = max(6, int(len(text) * ratio * tighten))

    prompt = (
        "你是视频旁白精修助手。请在不改变核心信息与画面对应关系的前提下，"
        "把文案压缩得更短，以便在指定时长内自然读完。\n"
        "要求:\n"
        f"1) 中文输出，目标不超过 {target_chars} 字。\n"
        "2) 不要引入新事实，不要改变主语对象。\n"
        "3) 保留原文关键信息与语义顺序。\n"
        "4) 只输出改写后的最终文案，不要解释。"
    )

    try:
        client = _get_openai_client()
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            temperature=0.2,
            max_tokens=220,
        )
        rewritten = _normalize_rewritten_text(_extract_chat_content(resp))
        if rewritten:
            return rewritten
    except Exception:
        pass

    return _heuristic_shorten_text(text, target_chars)


@tool
def add_narration_segments(
    video_path: str,
    segments: list[dict],
    voice: str = "Cherry",
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
    3. 工具会为每段独立生成 TTS，按时间偏移精确混合到视频中

    Args:
        video_path: 输入视频文件路径（已完成剪辑/合并/转场的视频）。
            例如 "/workspace/transitioned.mp4"
        segments: 分段旁白列表，每个元素是一个 dict：
            - "text": str — 该段旁白文案（必填）
            - "start": float — 旁白开始时间（秒），对应视频时间轴（必填）
            - "end": float — 旁白结束时间（秒），TTS 超出此时长将被截断（必填）
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
        from moviepy.audio.AudioClip import CompositeAudioClip
        from moviepy.audio.io.AudioFileClip import AudioFileClip
        from moviepy.video.io.VideoFileClip import VideoFileClip
        from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
        from moviepy.video.VideoClip import TextClip

        resolved_video = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved_video is None:
            return f"分段配音出错: 输入视频不存在或不在WORKSPACE: {video_path}"

        if not segments or not isinstance(segments, list):
            return "分段配音出错: segments 参数必须是非空列表"

        # ── 1. 加载视频 ──
        video = VideoFileClip(str(resolved_video))
        video_dur = video.duration
        resolved_font = _resolve_subtitle_font_path(subtitle_font)
        if add_subtitle and not resolved_font:
            logger.warning("字幕字体未找到，将跳过字幕渲染: %s", subtitle_font)

        # ── 2. 逐段生成 TTS 音频 ──
        audio_clips: list[Any] = []
        subtitle_clips: list[Any] = []
        success_count = 0
        fail_messages: list[str] = []
        target_coverage = 0.0
        actual_coverage = 0.0

        for idx, seg in enumerate(segments):
            text = seg.get("text", "").strip()
            start = float(seg.get("start", 0))
            end = float(seg.get("end", 0))

            if not text:
                fail_messages.append(f"段 {idx+1}: text 为空，已跳过")
                continue
            if end <= start:
                fail_messages.append(f"段 {idx+1}: end({end}) <= start({start})，已跳过")
                continue
            if start >= video_dur:
                fail_messages.append(f"段 {idx+1}: start({start}) >= 视频时长({video_dur:.1f})，已跳过")
                continue

            end = min(end, video_dur)
            max_duration = end - start
            target_coverage += max_duration

            safe_stem = _safe_output_video_path(output_name, default_stem="narrated_segmented").stem
            audio_path = WORKSPACE / f"{safe_stem}_seg{idx+1}.mp3"
            final_text = text
            seg_audio = None
            measured_dur = 0.0

            try:
                for attempt in range(4):
                    err = _tts_generate(final_text, voice, audio_path)
                    if err:
                        fail_messages.append(f"段 {idx+1}: {err}")
                        break

                    if seg_audio is not None:
                        try:
                            seg_audio.close()
                        except Exception:
                            pass
                    seg_audio = AudioFileClip(str(audio_path))
                    measured_dur = float(seg_audio.duration)

                    if measured_dur <= max_duration:
                        break

                    if attempt < 3:
                        rewritten = _rewrite_text_to_fit_duration(
                            text=final_text,
                            max_duration=max_duration,
                            measured_duration=measured_dur,
                            attempt_index=attempt,
                        )
                        rewritten = _normalize_rewritten_text(rewritten)
                        if rewritten and rewritten != final_text:
                            logger.info(
                                "段 %d 文案压缩: %.2fs -> %.2fs(目标), 字数 %d -> %d",
                                idx + 1,
                                measured_dur,
                                max_duration,
                                len(final_text),
                                len(rewritten),
                            )
                            final_text = rewritten
                            continue
                    break

                if seg_audio is None:
                    continue

                if measured_dur > max_duration:
                    fail_messages.append(
                        f"段 {idx+1}: 文案压缩后仍超时({measured_dur:.2f}s/{max_duration:.2f}s)，已做末尾截断。"
                    )
                    seg_audio = seg_audio.subclipped(0, max_duration)
                    measured_dur = float(seg_audio.duration)

                actual_coverage += min(measured_dur, max_duration)
                if measured_dur < max_duration * 0.75:
                    fail_messages.append(
                        f"段 {idx+1}: 旁白明显短于分配时段({measured_dur:.2f}s/{max_duration:.2f}s)，建议增加文案字数或缩短该段 end。"
                    )

                seg_audio = seg_audio.with_start(start)
                audio_clips.append(seg_audio)
                success_count += 1
            except Exception as ae:
                if seg_audio is not None:
                    try:
                        seg_audio.close()
                    except Exception:
                        pass
                fail_messages.append(f"段 {idx+1}: 音频加载失败: {ae}")
                continue

            if add_subtitle and resolved_font:
                try:
                    sub_duration = min(seg_audio.duration, max_duration)

                    subtitle_box_w = max(320, int(video.size[0] - 120))
                    max_subtitle_h = max(120, int(video.size[1] * 0.35))
                    bottom_safe_margin = max(40, int(video.size[1] * 0.06))
                    # 某些字体在 TextClip 渲染时会出现基线裁切，额外留出底部安全垫。
                    baseline_safe_lift = max(8, int(video.size[1] * 0.01))

                    text_clip_obj = None
                    # 字号自适应: 优先大字号，放不下时逐步减小，保证不出画。
                    for fs in (44, 42, 40, 38, 36, 34, 32, 30, 28):
                        display_text = _wrap_subtitle_text_by_pixels(
                            text=final_text,
                            font_path=resolved_font,
                            font_size=fs,
                            max_width_px=subtitle_box_w,
                        )
                        # 追加一行轻量留白，避免下沿字形被裁切。
                        render_text = f"{display_text}\n "

                        candidate = TextClip(
                            text=render_text,
                            font_size=fs,
                            color="white",
                            stroke_color="black",
                            stroke_width=2,
                            font=resolved_font,
                            text_align="center",
                            size=(subtitle_box_w, None),
                            duration=sub_duration,
                        )

                        if candidate.h <= max_subtitle_h:
                            text_clip_obj = candidate
                            break
                        candidate.close()

                    if text_clip_obj is None:
                        display_text = _wrap_subtitle_text_by_pixels(
                            text=final_text,
                            font_path=resolved_font,
                            font_size=28,
                            max_width_px=subtitle_box_w,
                        )
                        render_text = f"{display_text}\n "
                        text_clip_obj = TextClip(
                            text=render_text,
                            font_size=28,
                            color="white",
                            stroke_color="black",
                            stroke_width=2,
                            font=resolved_font,
                            text_align="center",
                            size=(subtitle_box_w, max_subtitle_h),
                            duration=sub_duration,
                        )

                    y_pos = max(20, int(video.size[1] - bottom_safe_margin - text_clip_obj.h - baseline_safe_lift))
                    logger.info(
                        "字幕布局: 段=%d, font=%s, clip_h=%d, y=%d, video_h=%d, safe_margin=%d, baseline_lift=%d",
                        idx + 1,
                        Path(resolved_font).name,
                        int(text_clip_obj.h),
                        int(y_pos),
                        int(video.size[1]),
                        int(bottom_safe_margin),
                        int(baseline_safe_lift),
                    )
                    txt_clip = text_clip_obj.with_position(("center", y_pos))
                    txt_clip = txt_clip.with_start(start)
                    subtitle_clips.append(txt_clip)
                except Exception as se:
                    logger.warning("段 %d 字幕创建失败: %s", idx+1, se)

        if success_count == 0:
            video.close()
            return f"分段配音出错: 所有 {len(segments)} 段均失败。详情: {'; '.join(fail_messages)}"

        # ── 3. 混合音频 ──
        all_audio_parts: list[Any] = []
        if video.audio is not None:
            all_audio_parts.append(video.audio.with_volume_scaled(0.2))
        all_audio_parts.extend(audio_clips)
        mixed_audio = CompositeAudioClip(all_audio_parts)

        # ── 4. 叠加字幕 ──
        if subtitle_clips:
            final_video = CompositeVideoClip([video] + subtitle_clips)
        else:
            final_video = video

        final_video = final_video.with_audio(mixed_audio)

        # ── 5. 输出 ──
        output_path = _safe_output_video_path(output_name, default_stem="narrated_segmented")
        final_video.write_videofile(
            str(output_path), codec="libx264", audio_codec="aac", logger=None
        )

        for ac in audio_clips:
            ac.close()
        for sc in subtitle_clips:
            try:
                sc.close()
            except Exception:
                pass
        video.close()
        final_video.close()

        result = {
            "status": "success",
            "path": str(output_path),
            "total_segments": len(segments),
            "success_segments": success_count,
            "has_subtitles": add_subtitle and len(subtitle_clips) > 0,
            "subtitle_font_used": resolved_font if add_subtitle else None,
            "target_narration_seconds": round(target_coverage, 3),
            "actual_narration_seconds": round(actual_coverage, 3),
            "narration_coverage_ratio": round(actual_coverage / max(video_dur, 0.01), 4),
        }
        if target_coverage > 0:
            target_ratio = actual_coverage / target_coverage
            result["target_slot_coverage_ratio"] = round(target_ratio, 4)
            if target_ratio < min_narration_coverage_ratio:
                fail_messages.append(
                    f"旁白相对分配时段覆盖率偏低: {target_ratio:.2%} < {min_narration_coverage_ratio:.2%}，"
                    "建议增加文案长度/段数，或先用 align_narration_to_timeline 重新对齐。"
                )
        if fail_messages:
            result["warnings"] = fail_messages
        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        return f"分段配音出错: {e}"
