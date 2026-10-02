from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .tool_runtime import ToolExecutionResult, parse_tool_result_text


_LOCAL_ANALYSIS_PROMPT = """你是视频剪辑素材分析器。请围绕以下目标分析完整视频：
{analysis_goal}

按时间顺序覆盖整段视频，依据视频采样帧的时间信息输出连续片段。每段必须包含：
- 时间范围，格式为 t=起始秒s-t=结束秒s
- 人物、动作、场景和镜头变化
- 景别、运动、色调和光线
- 情绪与叙事作用
- 对当前剪辑目标的可用性

最后给出全片主题、叙事结构和推荐剪辑片段。只描述可从画面确认的信息，不得编造音频内容。
"""


def local_rollout_analysis_enabled() -> bool:
    return os.environ.get("CRAYOTTER_RL_ANALYZE_VIDEO_BACKEND", "api").strip().lower() == "local_rollout"


def _analysis_cache_dir() -> Path:
    configured = os.environ.get("CRAYOTTER_ANALYSIS_CACHE_DIR", "").strip()
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parent / "cache" / "video_analysis"


def _analysis_cache_key(source_video: Path, analysis_goal: str, model_tag: str) -> str:
    digest = hashlib.sha256()
    stat = source_video.stat()
    digest.update(str(stat.st_size).encode("ascii"))
    with source_video.open("rb") as handle:
        digest.update(handle.read(1024 * 1024))
    payload = {
        "file": digest.hexdigest(),
        "goal": analysis_goal,
        "model": model_tag,
        "prompt_version": "local-rollout-video-v2",
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _resolve_episode_video(episode_root: Path, raw_path: str) -> Path:
    candidate = Path(str(raw_path or "").strip())
    if not candidate.is_absolute():
        candidate = episode_root / candidate
    candidate = candidate.resolve(strict=False)
    try:
        candidate.relative_to(episode_root.resolve())
    except ValueError as exc:
        raise ValueError(f"Video path is outside the rollout workspace: {candidate}") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"Video does not exist: {candidate}")
    return candidate


def _analysis_output_path(episode_root: Path, source_video: Path) -> Path:
    user_temp = episode_root / "user_temp"
    return user_temp / f"{source_video.stem}_analysis.json"


def _restore_cache(cache_path: Path, output_path: Path, source_video: Path) -> bool:
    if not cache_path.is_file():
        return False
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    semantic_segments = payload.get("semantic_segments")
    if not isinstance(semantic_segments, list) or not semantic_segments:
        return False
    payload["source_video"] = str(source_video)
    payload["analysis_video"] = str(source_video)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, output_path)
    return True


class _AsyncCacheLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle: Any = None

    async def __aenter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        if os.name == "posix":
            import fcntl

            await asyncio.to_thread(fcntl.flock, self.handle.fileno(), fcntl.LOCK_EX)
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if self.handle is not None:
            if os.name == "posix":
                import fcntl

                await asyncio.to_thread(fcntl.flock, self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


async def analyze_video_with_rollout(
    agent_loop: Any,
    episode_root: Path,
    parameters: dict[str, Any],
) -> ToolExecutionResult:
    started_at = time.perf_counter()
    source_video: Path | None = None
    try:
        source_video = _resolve_episode_video(episode_root, str(parameters.get("video_path", "")))
        analysis_goal = str(
            parameters.get("analysis_goal")
            or "详细描述视频中每个场景的内容、情绪和视觉特征"
        )
        max_tokens = int(os.environ.get("CRAYOTTER_RL_LOCAL_VIDEO_MAX_TOKENS", "16384"))
        context_limit = int(os.environ.get("CRAYOTTER_RL_LOCAL_VIDEO_CONTEXT_LENGTH", "65536"))
        model_tag = os.environ.get("CRAYOTTER_RL_LOCAL_VIDEO_MODEL_TAG", "qwen3.5-rollout-v1")
        cache_key = _analysis_cache_key(source_video, analysis_goal, f"local-rollout:{model_tag}")
        cache_root = _analysis_cache_dir()
        cache_path = cache_root / f"{cache_key}.json"
        output_path = _analysis_output_path(episode_root, source_video)

        async with _AsyncCacheLock(cache_root / "locks" / f"{cache_key}.lock"):
            if _restore_cache(cache_path, output_path, source_video):
                raw_result = (
                    "视频分析完成（本地 rollout vLLM 缓存复用）:\n\n"
                    f"- analysis_json: {output_path}\n"
                    f"- source_video: {source_video}"
                )
            else:
                from script.tools._shared import _save_analysis_json

                messages = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "video",
                                "video": str(source_video),
                            },
                            {
                                "type": "text",
                                "text": _LOCAL_ANALYSIS_PROMPT.format(analysis_goal=analysis_goal),
                            },
                        ],
                    }
                ]
                image_patch_size = getattr(
                    getattr(agent_loop.processor, "image_processor", None),
                    "patch_size",
                    14,
                )
                images, videos, audios = await asyncio.to_thread(
                    agent_loop.dataset_cls._process_multi_modal_info,
                    messages,
                    image_patch_size,
                    agent_loop.data_config,
                )
                if not videos:
                    raise RuntimeError("verl multimodal loader returned no video data")

                mm_processor_kwargs = {"do_sample_frames": False}
                from verl.utils.chat_template import apply_chat_template
                from verl.utils.tokenizer import (
                    build_multimodal_processor_inputs,
                    normalize_token_ids,
                )

                raw_prompt = await asyncio.to_thread(
                    apply_chat_template,
                    agent_loop.processor,
                    messages,
                    tools=None,
                    add_generation_prompt=True,
                    tokenize=False,
                    **agent_loop.apply_chat_template_kwargs,
                )
                model_inputs = await asyncio.to_thread(
                    build_multimodal_processor_inputs,
                    agent_loop.processor,
                    text=[raw_prompt],
                    images=images,
                    videos=videos,
                    audio=audios,
                    mm_processor_kwargs=mm_processor_kwargs,
                )
                prompt_ids = normalize_token_ids(model_inputs.pop("input_ids"))
                required_context = len(prompt_ids) + max_tokens
                if required_context > context_limit:
                    raise ValueError(
                        "Local video analysis requires "
                        f"{required_context} tokens ({len(prompt_ids)} prompt + "
                        f"{max_tokens} response), exceeding context limit {context_limit}. "
                        "Increase CRAYOTTER_RL_LOCAL_VIDEO_CONTEXT_LENGTH."
                    )
                output = await agent_loop.server_manager.generate(
                    request_id=f"crayotter-video-analysis-{uuid4().hex}",
                    prompt_ids=prompt_ids,
                    sampling_params={
                        "max_tokens": max_tokens,
                        "temperature": 0.2,
                        "top_p": 0.9,
                    },
                    image_data=images,
                    video_data=videos,
                    audio_data=audios,
                    mm_processor_kwargs=mm_processor_kwargs,
                )
                analysis_text = agent_loop.tokenizer.decode(
                    output.token_ids,
                    skip_special_tokens=True,
                ).strip()
                if not analysis_text:
                    raise RuntimeError("local rollout vLLM returned empty video analysis")

                saved_path = _save_analysis_json(
                    source_video=source_video,
                    analysis_video=source_video,
                    analysis_goal=analysis_goal,
                    analysis_text=analysis_text,
                    video_url_used=str(source_video),
                    audio_url_used="",
                    output_path=output_path,
                )
                if saved_path is None:
                    raise RuntimeError("failed to persist local video analysis")
                saved_payload = json.loads(saved_path.read_text(encoding="utf-8"))
                semantic_segments = saved_payload.get("semantic_segments")
                if not isinstance(semantic_segments, list) or not semantic_segments:
                    raise RuntimeError(
                        "local rollout video analysis contained no parseable timed segments"
                    )
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = cache_path.with_suffix(f"{cache_path.suffix}.{os.getpid()}.tmp")
                shutil.copy2(saved_path, temporary)
                os.replace(temporary, cache_path)
                raw_result = (
                    "视频分析完成（本地 rollout vLLM）:\n\n"
                    f"- media_mode: verl_internal_video\n"
                    f"- frame_sampling: qwen_vl_utils_default\n"
                    f"- analysis_json: {saved_path}\n"
                    f"- source_video: {source_video}\n\n"
                    f"{analysis_text}"
                )

        parsed, success, output_paths, parsed_duration = parse_tool_result_text(
            raw_result,
            episode_root,
        )
        return ToolExecutionResult(
            tool_name="analyze_video",
            arguments=parameters,
            raw_result=raw_result,
            parsed_result=parsed,
            success=success,
            returncode=0,
            stdout="",
            stderr="",
            output_paths=output_paths,
            duration_seconds=parsed_duration or (time.perf_counter() - started_at),
        )
    except Exception as exc:
        raw_result = f"视频分析出错: 本地 rollout vLLM 调用失败: {exc}"
        return ToolExecutionResult(
            tool_name="analyze_video",
            arguments=parameters,
            raw_result=raw_result,
            parsed_result=raw_result,
            success=False,
            returncode=1,
            stdout="",
            stderr=str(exc),
            output_paths=[],
            duration_seconds=time.perf_counter() - started_at,
        )
