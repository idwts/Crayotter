from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import _shared
from ._shared import (
    _extract_audio_for_analysis,
    _extract_chat_content,
    _get_video_client,
    _is_local_base_url,
    _prepare_timestamped_video_for_analysis,
    _resolve_video_path,
    _save_analysis_json,
    _to_file_url,
    dashscope,
    logger,
    tool,
)


def _format_api_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    response = getattr(exc, "response", None)
    if response is None:
        return message

    details: list[str] = []
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        details.append(f"status={status_code}")

    response_text = ""
    try:
        response_text = str(getattr(response, "text", "") or "").strip()
    except Exception:
        response_text = ""
    if response_text:
        details.append(f"body={response_text[:500]}")

    if not details:
        return message
    return f"{message}; {'; '.join(details)}"


def _to_dashscope_file_url(path: Path) -> str:
    # DashScope Python SDK 在 Windows 推荐使用 file://D:/... 形式。
    normalized = path.resolve().as_posix()
    return f"file://{normalized}"


def _normalize_dashscope_api_url(base_url: str) -> str:
    raw = str(base_url or "").strip()
    if not raw:
        return "https://dashscope.aliyuncs.com/api/v1"
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw

    if parsed.path.endswith("/compatible-mode/v1"):
        return f"{parsed.scheme}://{parsed.netloc}/api/v1"
    return raw


def _extract_dashscope_content(response: Any) -> str:
    # 兼容 DashScope SDK 对象/字典两种返回结构。
    try:
        output = getattr(response, "output", None)
        if output is None and isinstance(response, dict):
            output = response.get("output")
        if output is None:
            return ""

        choices = getattr(output, "choices", None)
        if choices is None and isinstance(output, dict):
            choices = output.get("choices")
        if not choices:
            return ""

        first = choices[0]
        message = getattr(first, "message", None)
        if message is None and isinstance(first, dict):
            message = first.get("message")
        if message is None:
            return ""

        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if content is None:
            return ""

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        parts.append(str(text))
                else:
                    text = getattr(item, "text", "")
                    if text:
                        parts.append(str(text))
            return "\n".join([p for p in parts if p]).strip()

        return str(content).strip()
    except Exception:
        return ""


_ANALYSIS_PROMPT_OMNI = (
    "你是一位专业的视频内容分析师。\n"
    "{goal_line}"
    "注意：视频左上角有时间戳（格式 t=XXs），请严格基于该时间戳描述内容。\n"
    "要求你必须覆盖整段视频，不允许只挑选个别片段。\n"
    "请按时间线连续输出'全片分段分析'，从开头到结尾无断档，建议每段 5~15 秒或按自然镜头边界分段。\n"
    "每个时间段都要给出：\n"
    "- 时间范围（如 t=xxs-t=xxs）\n"
    "- 画面发生了什么（人物/动作/场景变化）\n"
    "- 音频信息（对白/旁白/音乐/环境音）\n"
    "- 情绪与叙事作用\n"
    "- 是否适合用于解说剪辑（是/否 + 原因）\n"
    "在全片分段之后，再补充：\n"
    "1) 全片主题与叙事结构总结\n"
    "2) 推荐剪辑方案（按时间段组合，给出目标时长建议）\n"
    "3) 解说词写作建议（按段落对应时间范围）\n"
    "如果无法识别某部分信息，请明确标注'无法识别'并说明原因。"
)

_ANALYSIS_PROMPT_VISION = (
    "你是一位专业的视频内容分析师。\n"
    "{goal_line}"
    "注意：视频左上角有时间戳（格式 t=XXs），请严格基于该时间戳描述内容。\n"
    "要求你必须覆盖整段视频，不允许只挑选个别片段。\n"
    "请按时间线连续输出'全片分段分析'，从开头到结尾无断档，建议每段 5~15 秒或按自然镜头边界分段。\n"
    "每个时间段都要给出：\n"
    "- 时间范围（如 t=xxs-t=xxs）\n"
    "- 画面发生了什么（人物/动作/场景变化）\n"
    "- 镜头特征（景别/运动/色调/光线）\n"
    "- 情绪与叙事作用\n"
    "- 是否适合用于解说剪辑（是/否 + 原因）\n"
    "注意：本次分析仅基于视觉画面，无法获取音频信息，请勿编造音频内容。\n"
    "在全片分段之后，再补充：\n"
    "1) 全片主题与叙事结构总结\n"
    "2) 推荐剪辑方案（按时间段组合，给出目标时长建议）\n"
    "3) 解说词写作建议（按段落对应时间范围）\n"
    "如果无法识别某部分信息，请明确标注'无法识别'并说明原因。"
)


@tool
def analyze_video(
    video_path: str,
    analysis_goal: str = "详细描述视频中每个场景的内容、情绪和视觉特征",
    interval_seconds: float = 5.0,
    max_frames: int = 20,
) -> str:
    """使用多模态 AI 深度分析视频内容，输出全片时间线分析和推荐剪辑方案。
    分析结果会自动保存为 *_analysis.json，并生成可用于文本语义召回的 semantic_segments 索引。
    **每个源视频只需分析一次**，已有 _analysis.json 的视频无需重复调用。

    支持 Omni 模型（音视频同时输入）和纯视觉模型（仅视频输入），
    根据 VIDEO_MODEL_NAME 是否包含 'omni' 自动选择输入格式。

    Args:
        video_path: 要分析的视频文件路径（仅限原始下载的源视频，不要传入
            merged_*/final_*/narrated_* 等中间产物）。
            例如 "/workspace/scut_intro_video.mp4"
        analysis_goal: 分析侧重点，直接影响 AI 的描述维度。常用示例：
            - "找出适合做介绍视频的精华片段，标注时间段"（默认剪辑场景）
            - "找出视觉冲击力强的高燃片段"
            - "按场景分析画面内容和情绪"
        interval_seconds: 保留参数（当前版本未使用），勿修改，默认 5.0
        max_frames: 保留参数（当前版本未使用），勿修改，默认 20
    """
    try:
        resolved_video = _resolve_video_path(video_path)
        if resolved_video is None or not resolved_video.exists():
            return f"视频分析出错: 输入视频不存在或不在WORKSPACE目录: {video_path}"

        # 根据模型名判断是否为 Omni（支持音频输入）
        is_omni = "omni" in _shared.VIDEO_MODEL_NAME.lower()

        stamped_video = _prepare_timestamped_video_for_analysis(resolved_video)
        analysis_video = stamped_video or resolved_video

        logger.info(
            "多模态视频内容分析: model=%s is_omni=%s video=%s",
            _shared.VIDEO_MODEL_NAME, is_omni, analysis_video,
        )

        # 仅 Omni 模型才提取音频
        audio_path = _extract_audio_for_analysis(resolved_video) if is_omni else None

        goal_line = f"分析目标: {analysis_goal}\n"
        if is_omni:
            analysis_prompt = _ANALYSIS_PROMPT_OMNI.format(goal_line=goal_line)
        else:
            analysis_prompt = _ANALYSIS_PROMPT_VISION.format(goal_line=goal_line)

        # 使用通过 configure() 注入的 video client（避免 import 时值拷贝问题）
        use_local_media_url = _is_local_base_url(_shared.VIDEO_BASE_URL)

        video_candidates = [analysis_video]
        if analysis_video != resolved_video:
            video_candidates.append(resolved_video)

        video_inputs: list[dict[str, str]] = []
        for candidate in video_candidates:
            if use_local_media_url:
                media_value = _to_file_url(candidate)
            else:
                media_value = _to_dashscope_file_url(candidate)
            video_inputs.append({"value": media_value, "display": media_value})

        deduped_video_inputs: list[dict[str, str]] = []
        seen_video_values: set[str] = set()
        for item in video_inputs:
            value = item["value"]
            if value in seen_video_values:
                continue
            seen_video_values.add(value)
            deduped_video_inputs.append(item)
        video_inputs = deduped_video_inputs

        audio_inputs: list[dict[str, str]] = []
        if is_omni and audio_path is not None:
            if use_local_media_url:
                audio_value = _to_file_url(audio_path)
                audio_display = audio_value
            else:
                audio_value = _to_dashscope_file_url(audio_path)
                audio_display = audio_value
            audio_inputs = [{"value": audio_value, "display": audio_display}]

        logger.info(
            "媒体输入模式: %s | 视频输入: %s | 音频输入: %s | is_omni=%s",
            "file_url" if use_local_media_url else "data_url",
            [v["display"] for v in video_inputs],
            [a["display"] for a in audio_inputs],
            is_omni,
        )

        last_error = ""
        if not use_local_media_url:
            dashscope.api_key = _shared.VIDEO_API_KEY
            dashscope.base_http_api_url = _normalize_dashscope_api_url(_shared.VIDEO_BASE_URL)

            for vitem in video_inputs:
                vdisplay = vitem["display"]
                try:
                    response = dashscope.MultiModalConversation.call(
                        model=_shared.VIDEO_MODEL_NAME,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"video": vitem["value"]},
                                    {"text": analysis_prompt},
                                ],
                            }
                        ],
                    )
                    status_code = getattr(response, "status_code", None)
                    if status_code == 200:
                        analysis = _extract_dashscope_content(response)
                        if analysis:
                            mode_tag = "DashScope音视频" if is_omni else "DashScope视觉"
                            logger.info("视频分析完成（%s）: %s", mode_tag, analysis[:100])
                            analysis_json_path = _save_analysis_json(
                                source_video=resolved_video,
                                analysis_video=analysis_video,
                                analysis_goal=analysis_goal,
                                analysis_text=analysis,
                                video_url_used=vdisplay,
                                audio_url_used="",
                            )
                            return (
                                f"视频分析完成（{mode_tag}）:\n\n"
                                f"- media_mode: dashscope_file_url\n"
                                f"- video_input: {vdisplay}\n"
                                f"- audio_input: N/A\n\n"
                                f"- analysis_json: {str(analysis_json_path) if analysis_json_path else 'N/A'}\n\n"
                                f"{analysis}"
                            )

                    message = str(getattr(response, "message", "") or "").strip()
                    req_id = str(getattr(response, "request_id", "") or "").strip()
                    last_error = f"status={status_code}; request_id={req_id}; message={message}".strip("; ")
                    logger.warning(
                        "⚠️ DashScope 视频分析调用失败: video_input=%s, error=%s",
                        vdisplay,
                        last_error,
                    )
                except Exception as e:
                    last_error = _format_api_error(e)
                    logger.warning(
                        "⚠️ DashScope 视频分析调用异常: video_input=%s, error=%s",
                        vdisplay,
                        last_error,
                    )
                    continue

            return f"视频分析出错: DashScope 调用失败: {last_error or 'unknown error'}"

        client = _get_video_client()
        for vitem in video_inputs:
            vcontent = vitem["value"]
            vdisplay = vitem["display"]
            # Omni: 先尝试带音频，再 fallback 到无音频；视觉模型：只尝试无音频
            trial_audio_inputs = (audio_inputs + [{"value": "", "display": "N/A"}]) if is_omni else [{"value": "", "display": "N/A"}]
            for aitem in trial_audio_inputs:
                acontent = aitem["value"]
                adisplay = aitem["display"]
                try:
                    content: list[dict[str, Any]] = [
                        {"type": "video_url", "video_url": {"url": vcontent}},
                    ]
                    if acontent:
                        content.append({"type": "audio_url", "audio_url": {"url": acontent}})
                    content.append({"type": "text", "text": analysis_prompt})

                    response = client.chat.completions.create(
                        model=_shared.VIDEO_MODEL_NAME,
                        messages=[{"role": "user", "content": content}],
                        max_tokens=40960,
                        temperature=0.2,
                    )
                    analysis = _extract_chat_content(response)
                    if analysis:
                        mode_tag = "Omni音视频" if (is_omni and acontent) else ("Omni视觉" if is_omni else "视觉")
                        logger.info("视频分析完成（%s）: %s", mode_tag, analysis[:100])
                        analysis_json_path = _save_analysis_json(
                            source_video=resolved_video,
                            analysis_video=analysis_video,
                            analysis_goal=analysis_goal,
                            analysis_text=analysis,
                            video_url_used=vdisplay,
                            audio_url_used="" if adisplay == "N/A" else adisplay,
                        )
                        return (
                            f"视频分析完成（{mode_tag}）:\n\n"
                            f"- media_mode: {'file_url' if use_local_media_url else 'data_url'}\n"
                            f"- video_input: {vdisplay}\n"
                            f"- audio_input: {adisplay}\n\n"
                            f"- analysis_json: {str(analysis_json_path) if analysis_json_path else 'N/A'}\n\n"
                            f"{analysis}"
                        )
                except Exception as e:
                    last_error = _format_api_error(e)
                    logger.warning(
                        "⚠️ 视频分析调用失败: video_input=%s, audio_input=%s, media_mode=%s, error=%s",
                        vdisplay,
                        adisplay,
                        "file_url" if use_local_media_url else "data_url",
                        last_error,
                    )
                    continue

        return f"视频分析出错: 调用失败: {last_error or 'unknown error'}"

    except Exception as e:
        return f"视频分析出错: {e}"
