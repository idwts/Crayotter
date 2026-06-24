"""
多模态视频自动编辑 Agent — Planner + Deep Research + ReAct 混合架构

核心思想:
  Phase 1 (结构化规划 — Planner → Executor 循环):
    搜索 → 筛选 → 下载 → 多模态分析 → 保存分析 JSON
    此阶段步骤可预见、可复用，用传统 Plan-and-Execute 确保可靠执行。

  Phase 2 (深度剪辑研究 — Editing Research):
    给定所有分析 JSON + 用户需求，纯推理（不调用工具），
    Deep Research 式深度研读每个视频片段的内容/情绪/视觉/音频特征，
    跨视频关联分析，输出结构化「剪辑蓝图」：
    叙事结构、片段选择排序、转场衔接设计、节奏规划、旁白策略、吸引力优化。

  Phase 3 (自主创作 — ReAct Agent):
    以剪辑蓝图为核心指导 + 完整工具集，
    让 Agent 自主执行裁剪/合并/转场/旁白/导出，
    不断迭代直到成片满意。
"""

from __future__ import annotations

import json
import math
import hashlib
import logging
import operator
import os
import re
import shutil
import subprocess
import time
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Annotated, Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI
from langgraph.graph import START, StateGraph
from langgraph.prebuilt import create_react_agent
from langgraph.types import Send
from pydantic import BaseModel, Field

from app.media_index import build_analysis_index, iter_analysis_files, iter_video_files, match_analysis_files
from app.steering import SteeringCoordinator, classify_guidance
from editing_plan import (
    EditingPlan,
    EditingPlanStore,
    EditingScene,
    normalize_plan_timeline,
    validate_editing_plan,
)
from memory_reference import INJECTION_MEMORY_CHAR_LIMIT, load_memory_reference
from tools import ALL_TOOLS, MEMORY_EXPERIENCE_DIR, USER_WORKSPACE, WORKSPACE
from tools._shared import _tts_generate
from tools.narration_pipeline import compose_prepared_narration, narration_audio_path
from model_runtime import (
    ModelCallError,
    emit_benchmark_event,
    ensure_model_calls_allowed,
    fail_fast_model_errors,
    model_abort_requested,
    request_model_abort,
    raise_model_failure,
)
from orchestration import (
    ArtifactRef,
    ArtifactRegistry,
    ExecutionPlan,
    ResourcePoolConfig,
    ResourceScheduler,
    RetryPolicy,
    TaskExecutionResult,
    TaskSpec,
)

# ═══════════════════════════════════════════════════════════════════════════
# API 配置 - 从 agent.py 传入
# ═══════════════════════════════════════════════════════════════════════════
API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
BASE_URL: str = "https://api.openai.com/v1"
MODEL_NAME: str = "gpt-4o"
ENABLE_PHASE2_RESEARCH: bool = True
ENABLE_PLAN_REVIEW: bool = True
REVISION: int = max(1, int(os.environ.get("CRAYOTTER_REVISION", "1") or 1))
DIRECT_PHASE3_EXECUTION: bool = False
PREFER_LOCAL_MATERIALS: bool = False
SEARCH_POOL_SIZE: int = 4
DOWNLOAD_POOL_SIZE: int = 2
VIDEO_ANALYSIS_POOL_SIZE: int = 2
LLM_POOL_SIZE: int = 2
FFMPEG_POOL_SIZE: int = 2
TTS_POOL_SIZE: int = 2
EXPORT_POOL_SIZE: int = 1
SHORT_FORM_OPTIMIZATIONS: bool = str(
    os.environ.get("CRAYOTTER_SHORT_FORM_OPTIMIZATIONS", "true")
).strip().lower() not in {"0", "false", "no", "off"}
SHORT_FORM_MAX_SOURCES: int = max(
    1,
    min(4, int(os.environ.get("CRAYOTTER_SHORT_FORM_MAX_SOURCES", "2") or 2)),
)

graph_logger = logging.getLogger("graph")
RUNTIME_EVENT_SINK: Any = None
_STEERING_COORDINATOR: SteeringCoordinator | None = None


class _RealtimeToolTraceHandler(BaseCallbackHandler):
    def __init__(self) -> None:
        self.raise_error = True
        self._started: dict[str, tuple[str, float]] = {}
        self._model_started: dict[str, float] = {}
        self._lock = threading.RLock()

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: Any,
        **kwargs: Any,
    ) -> None:
        name = str(serialized.get("name") or kwargs.get("name") or "unknown_tool")
        key = str(run_id)
        with self._lock:
            self._started[key] = (name, time.perf_counter())
        graph_logger.info("🛠️ Phase3 工具开始: %s run_id=%s", name, key)
        emit_benchmark_event(
            "tool_started",
            {"phase": "phase3", "tool_name": name, "run_id": key},
        )

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        **kwargs: Any,
    ) -> None:
        key = str(run_id)
        with self._lock:
            self._model_started[key] = time.perf_counter()
        emit_benchmark_event(
            "model_call_started",
            {"stage": "phase3_react", "model": MODEL_NAME, "run_id": key},
        )

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        key = str(run_id)
        with self._lock:
            started = self._model_started.pop(key, time.perf_counter())
        emit_benchmark_event(
            "model_call_completed",
            {
                "stage": "phase3_react",
                "model": MODEL_NAME,
                "run_id": key,
                "duration_seconds": round(time.perf_counter() - started, 3),
            },
        )

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        key = str(run_id)
        with self._lock:
            started = self._model_started.pop(key, time.perf_counter())
        request_model_abort()
        emit_benchmark_event(
            "model_call_failed",
            {
                "stage": "phase3_react",
                "model": MODEL_NAME,
                "run_id": key,
                "duration_seconds": round(time.perf_counter() - started, 3),
                "error": str(error)[:500],
            },
        )

    def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        key = str(run_id)
        with self._lock:
            name, started = self._started.pop(
                key,
                (str(kwargs.get("name") or "unknown_tool"), time.perf_counter()),
            )
        duration = time.perf_counter() - started
        graph_logger.info(
            "📦 Phase3 工具完成: %s run_id=%s duration=%.3fs",
            name,
            key,
            duration,
        )
        emit_benchmark_event(
            "tool_completed",
            {
                "phase": "phase3",
                "tool_name": name,
                "run_id": key,
                "duration_seconds": round(duration, 3),
            },
        )
        self._apply_guidance_after_tool(name)

    @staticmethod
    def _apply_guidance_after_tool(tool_name: str) -> None:
        coordinator = _steering_coordinator()
        if coordinator is None:
            return
        checkpoint = f"after_tool:{tool_name}"
        result = coordinator.apply_pending(checkpoint, "phase3")
        if not result.get("applied"):
            return
        required_phase = str(result.get("required_phase") or "phase3")
        _emit_orchestration_event(
            "steering_replan_started",
            {
                "checkpoint": checkpoint,
                "required_phase": required_phase,
                "categories": result.get("categories", []),
                "revision": REVISION,
            },
        )
        raise SteeringReplanRequested(required_phase)

    def on_tool_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        key = str(run_id)
        with self._lock:
            name, started = self._started.pop(
                key,
                (str(kwargs.get("name") or "unknown_tool"), time.perf_counter()),
            )
        duration = time.perf_counter() - started
        graph_logger.error(
            "❌ Phase3 工具失败: %s run_id=%s duration=%.3fs error=%s",
            name,
            key,
            duration,
            error,
        )
        emit_benchmark_event(
            "tool_failed",
            {
                "phase": "phase3",
                "tool_name": name,
                "run_id": key,
                "duration_seconds": round(duration, 3),
                "error": str(error)[:500],
            },
        )


def _log_react_tool_trace(result_state: dict[str, Any]) -> None:
    """将 ReAct 阶段的工具轨迹显式写入 graph 日志，便于 agent_*.log 复盘。"""
    try:
        msgs = result_state.get("messages", []) if isinstance(result_state, dict) else []
        if not isinstance(msgs, list):
            return

        for m in msgs:
            if isinstance(m, AIMessage):
                tool_calls = getattr(m, "tool_calls", None) or []
                for tc in tool_calls:
                    try:
                        name = tc.get("name", "unknown_tool")
                        args = tc.get("args", {})
                        graph_logger.info("🛠️ Phase3 工具调用: %s args=%s", name, str(args)[:400])
                    except Exception:
                        continue
            elif isinstance(m, ToolMessage):
                t_name = getattr(m, "name", None) or "unknown_tool"
                content = getattr(m, "content", "")
                graph_logger.info("📦 Phase3 工具结果: %s -> %s", t_name, str(content)[:500])
    except Exception as e:
        graph_logger.warning("⚠️ 记录 Phase3 工具轨迹失败: %s", e)

# ═══════════════════════════════════════════════════════════════════════════
# State 定义
# ═══════════════════════════════════════════════════════════════════════════


class Step(BaseModel):
    """一个执行步骤。"""

    id: int = Field(description="步骤编号")
    description: str = Field(description="步骤描述")
    tool_hint: str = Field(default="", description="建议使用的工具名称")
    depends_on: list[int] = Field(default_factory=list, description="前置步骤 ID")
    arguments: dict[str, Any] = Field(default_factory=dict, description="工具调用参数")
    status: str = Field(default="pending", description="pending/running/done/failed")
    result: str = Field(default="", description="执行结果")


class StepResult(BaseModel):
    """Phase 1 单个 DAG 步骤的结构化执行结果。"""

    step_id: int
    tool_name: str
    status: Literal["done", "failed", "skipped"]
    result: str = ""
    duration_seconds: float = 0.0
    error: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    """Phase 1 的执行计划（仅素材准备阶段）"""

    goal: str = Field(description="用户的最终目标")
    analysis: str = Field(default="", description="需求分析")
    steps: list[Step] = Field(default_factory=list, description="有序步骤列表")


class AgentState(BaseModel):
    """Planner + ReAct 混合 Agent 的全局状态"""

    # 用户输入
    user_request: str = ""
    base_user_request: str = ""
    guidance_context: str = ""
    steering_target_phase: str = ""
    current_checkpoint: str = ""
    revision: int = 1

    # Phase 1: 结构化规划
    plan: Plan | None = None
    current_step_index: int = 0
    active_step_id: int = 0
    prep_round: int = 0
    step_results: Annotated[list[StepResult], operator.add] = Field(default_factory=list)
    messages: Annotated[list[Any], operator.add] = Field(default_factory=list)

    # 时长控制
    target_duration_seconds: float = 0.0

    # Phase 标记: "planning" → "researching" → "react" → "done"
    phase: str = "planning"

    # Phase 2: 剪辑研究蓝图
    editing_blueprint: str = ""
    editing_plan_version: str = ""
    editing_plan_status: str = ""
    material_gap_report: dict[str, Any] = Field(default_factory=dict)
    gap_round: int = 0
    phase2_artifact_ids: list[str] = Field(default_factory=list)

    # 最终输出
    final_output: str = ""
    should_end: bool = False

    class Config:
        arbitrary_types_allowed = True


# ═══════════════════════════════════════════════════════════════════════════
# 工具分组
# ═══════════════════════════════════════════════════════════════════════════
_TOOL_NAME_MAP: dict[str, Any] = {
    getattr(t, "name", ""): t for t in ALL_TOOLS
}

# Phase 1: 素材准备工具
PREP_TOOL_NAMES = {
    "search_bilibili_video",
    "download_bilibili_video",
    "rank_video_candidates",
    "analyze_video",
    "inspect_video_duration",
}
REMOTE_PREP_TOOL_NAMES = {
    "search_bilibili_video",
    "download_bilibili_video",
    "rank_video_candidates",
}

# Phase 3: 剪辑创作工具
EDITING_TOOL_NAMES = {
    "recall_semantic_segments",
    "analyze_video",
    "batch_cut_video",
    "cut_video",
    "merge_videos",
    "inspect_video_duration",
    "list_transition_presets",
    "plan_transition_timeline",
    "add_transition",
    "validate_narration_timeline",
    "build_edit_timeline_from_segments",
    "align_narration_to_timeline",
    "validate_timeline_constraints",
    "score_cut_continuity",
    "recommend_transition_for_cut",
    "duck_background_audio",
    "normalize_loudness",
    "add_narration",
    "add_narration_segments",
    "add_subtitles",
    "export_video",
}

PREP_TOOLS = [t for t in ALL_TOOLS if getattr(t, "name", "") in PREP_TOOL_NAMES]
EDITING_TOOLS = [t for t in ALL_TOOLS if getattr(t, "name", "") in EDITING_TOOL_NAMES]


def _resource_pool_config() -> ResourcePoolConfig:
    return ResourcePoolConfig(
        search_pool=SEARCH_POOL_SIZE,
        download_pool=DOWNLOAD_POOL_SIZE,
        video_analysis_pool=VIDEO_ANALYSIS_POOL_SIZE,
        llm_pool=LLM_POOL_SIZE,
        ffmpeg_pool=FFMPEG_POOL_SIZE,
        tts_pool=TTS_POOL_SIZE,
        export_pool=EXPORT_POOL_SIZE,
    )


def _emit_orchestration_event(event_type: str, payload: dict[str, Any]) -> None:
    graph_logger.info("scheduler_event type=%s payload=%s", event_type, str(payload)[:800])
    if callable(RUNTIME_EVENT_SINK):
        RUNTIME_EVENT_SINK(event_type, payload)


def _steering_coordinator() -> SteeringCoordinator | None:
    global _STEERING_COORDINATOR
    steering_dir = str(os.environ.get("CRAYOTTER_STEERING_DIR", "")).strip()
    if not steering_dir:
        return None
    if _STEERING_COORDINATOR is None:
        _STEERING_COORDINATOR = SteeringCoordinator(
            workspace=WORKSPACE,
            steering_dir=steering_dir,
            revision=REVISION,
            event_sink=_emit_orchestration_event,
            classifier=_classify_guidance_with_llm,
        )
    return _STEERING_COORDINATOR


def _classify_guidance_with_llm(content: str) -> dict[str, Any]:
    fallback = classify_guidance(content)
    if fallback.get("category") in {"pause", "unsupported"}:
        return fallback
    prompt = (
        "你是视频编辑工作流指导分类器。只返回 JSON，字段为 "
        "category、required_phase、impact、normalized_guidance。"
        "category 只能是 material/style/narrative/narration/subtitle/general/global；"
        "required_phase 只能是 phase1/phase2/phase3；"
        "impact 只能是 local/phase。"
        "需要新素材或主题完全改变归 phase1；叙事结构和整体风格归 phase2；"
        "旁白、字幕和局部剪辑归 phase3。"
    )
    try:
        response = _get_llm(temperature=0.0).bind(max_tokens=300).invoke(
            [
                SystemMessage(content=prompt),
                HumanMessage(content=content),
            ]
        )
        parsed = _parse_json_object(str(response.content))
        category = str(parsed.get("category") or "")
        required_phase = str(parsed.get("required_phase") or "")
        impact = str(parsed.get("impact") or "")
        if category not in {
            "material",
            "style",
            "narrative",
            "narration",
            "subtitle",
            "general",
            "global",
        }:
            return fallback
        if required_phase not in {"phase1", "phase2", "phase3"} or impact not in {"local", "phase"}:
            return fallback
        return {
            "category": category,
            "required_phase": required_phase,
            "impact": impact,
            "normalized_guidance": str(
                parsed.get("normalized_guidance") or content
            ).strip(),
        }
    except Exception as exc:
        graph_logger.warning("指导分类模型不可用，使用关键词降级: %s", exc)
        return fallback


def _effective_user_request(base_request: str, guidance_context: str) -> str:
    base = str(base_request or "").strip()
    guidance = str(guidance_context or "").strip()
    if not guidance:
        return base
    return (
        f"{base}\n\n"
        "## 用户运行中追加指导（优先级高于历史经验）\n"
        f"{guidance}"
    )


def _apply_steering_checkpoint(
    state: AgentState,
    checkpoint: str,
    current_phase: str,
) -> dict[str, Any]:
    coordinator = _steering_coordinator()
    base_request = state.base_user_request or state.user_request
    target_duration = (
        state.target_duration_seconds
        or _extract_target_duration_seconds(base_request)
    )
    existing_blueprint = state.editing_blueprint
    blueprint_path = WORKSPACE / "editing_blueprint.md"
    if not existing_blueprint and blueprint_path.exists():
        try:
            existing_blueprint = blueprint_path.read_text(encoding="utf-8")
        except OSError:
            existing_blueprint = ""
    if coordinator is None:
        return {
            "base_user_request": base_request,
            "user_request": state.user_request,
            "steering_target_phase": "",
            "current_checkpoint": checkpoint,
            "revision": REVISION,
            "target_duration_seconds": target_duration,
            "editing_blueprint": existing_blueprint,
        }
    result = coordinator.apply_pending(checkpoint, current_phase)
    guidance_context = coordinator.guidance_text()
    target_phase = str(result.get("required_phase") or "")
    if target_phase:
        _emit_orchestration_event(
            "steering_replan_started",
            {
                "checkpoint": checkpoint,
                "required_phase": target_phase,
                "categories": result.get("categories", []),
                "revision": REVISION,
            },
        )
        coordinator = _steering_coordinator()
        if coordinator is not None:
            result = coordinator.apply_pending("after_each_tool_call", "phase3")
            required_phase = str(result.get("required_phase") or "")
            if required_phase:
                raise SteeringReplanRequested(required_phase)
    elif state.steering_target_phase:
        _emit_orchestration_event(
            "steering_replan_completed",
            {
                "checkpoint": checkpoint,
                "required_phase": state.steering_target_phase,
                "revision": REVISION,
            },
        )
    return {
        "base_user_request": base_request,
        "user_request": _effective_user_request(base_request, guidance_context),
        "guidance_context": guidance_context,
        "steering_target_phase": target_phase,
        "current_checkpoint": checkpoint,
        "revision": REVISION,
        "target_duration_seconds": target_duration,
        "editing_blueprint": existing_blueprint,
    }


def steering_entry_node(state: AgentState) -> dict[str, Any]:
    return _apply_steering_checkpoint(state, "entry", "phase1")


def steering_after_planner_node(state: AgentState) -> dict[str, Any]:
    return _apply_steering_checkpoint(state, "after_planner", "phase1")


def steering_after_phase1_node(state: AgentState) -> dict[str, Any]:
    return _apply_steering_checkpoint(state, "after_material_analysis", "phase1")


def steering_after_material_gap_node(state: AgentState) -> dict[str, Any]:
    return _apply_steering_checkpoint(state, "phase1_boundary", "phase1")


def steering_after_blueprint_node(state: AgentState) -> dict[str, Any]:
    return _apply_steering_checkpoint(state, "after_blueprint_generation", "phase2")


def _route_steering_entry(state: AgentState) -> str:
    target = state.steering_target_phase
    has_analysis = bool(_iter_analysis_json_files())
    if target == "phase3" and has_analysis:
        return "react_editor"
    if target == "phase2" and has_analysis and ENABLE_PHASE2_RESEARCH:
        return "editing_research"
    return "planner"


def _route_after_planner_steering(state: AgentState) -> str:
    return "planner" if state.steering_target_phase == "phase1" else "phase1_scheduler"


def _route_after_phase1_steering(state: AgentState) -> str:
    return "planner" if state.steering_target_phase == "phase1" else "material_gap_evaluator"


def _route_after_material_gap_steering(state: AgentState) -> str:
    target = state.steering_target_phase
    if target == "phase1":
        return "planner"
    if target == "phase2" and ENABLE_PHASE2_RESEARCH:
        return "editing_research"
    if target == "phase3":
        return "generate_editing_plan" if ENABLE_PLAN_REVIEW else "react_editor"
    return route_after_material_gap(state)


def _route_after_blueprint_steering(state: AgentState) -> str:
    target = state.steering_target_phase
    if target == "phase1":
        return "planner"
    if target == "phase2":
        return "editing_research"
    return "generate_editing_plan" if ENABLE_PLAN_REVIEW else "react_editor"


def _artifact_registry() -> ArtifactRegistry:
    return ArtifactRegistry(WORKSPACE)


def _editing_plan_store() -> EditingPlanStore:
    return EditingPlanStore(WORKSPACE)


def _register_plan_artifact(plan: EditingPlan, kind: str = "editing_plan") -> None:
    store = _editing_plan_store()
    path = store.version_path(plan.version)
    if not path.exists():
        return
    _artifact_registry().register(
        kind=kind,
        producer_task_id=f"editing_plan_{plan.version}",
        phase="phase2",
        path=path,
        artifact_id=f"editing_plan_{plan.version}",
        metadata={
            "version": plan.version,
            "status": plan.status,
            "revision": REVISION,
        },
    )


def _fallback_editing_plan(state: AgentState) -> EditingPlan:
    source_paths = [str(path.resolve()) for path in _iter_source_videos()]
    analysis_paths = [str(path.resolve()) for path in _iter_analysis_json_files()]
    target = state.target_duration_seconds or _extract_target_duration_seconds(state.user_request) or 30.0
    scene_count = max(1, min(6, len(source_paths) or 1))
    scene_duration = max(2.0, target / scene_count)
    scenes: list[EditingScene] = []
    for index in range(scene_count):
        source = source_paths[index % len(source_paths)] if source_paths else ""
        start = round(index * scene_duration, 3)
        end = round(start + scene_duration, 3)
        scenes.append(
            EditingScene(
                scene_id=f"scene_{index + 1:02d}",
                start=start,
                end=end,
                narrative_purpose=f"分镜 {index + 1}: 承接用户需求并展示核心素材",
                source_path=source,
                source_start=0.0,
                source_end=scene_duration,
                crop="fit_center_crop",
                transition="crossfade" if index else "",
            )
        )
    return normalize_plan_timeline(
        EditingPlan(
            version="v001",
            status="DRAFT",
            user_request=state.user_request,
            target_duration_seconds=target,
            aspect_ratio="9:16" if _user_requested_vertical(state.user_request) else "16:9",
            style="根据用户需求和素材分析确定",
            pacing="中等节奏，按分镜叙事推进",
            narration_strategy="按分镜补充简洁旁白",
            subtitle_strategy="优先使用旁白同步字幕",
            bgm_strategy="当前工具链不主动选曲，仅保留空间",
            scenes=scenes,
            source_analysis_paths=analysis_paths,
            source_video_paths=source_paths,
            blueprint_markdown=state.editing_blueprint,
        )
    )


def _user_requested_vertical(request: str) -> bool:
    text = str(request or "").lower()
    return any(marker in text for marker in ("竖屏", "竖版", "vertical", "9:16", "shorts", "抖音", "小红书"))


def generate_editing_plan_node(state: AgentState) -> dict[str, Any]:
    store = _editing_plan_store()
    approved = store.approved()
    if approved is not None:
        return {"editing_plan_version": approved.version, "editing_plan_status": approved.status, "phase": "react"}
    current = store.current()
    if current is not None and current.status in {"VALIDATED", "WAITING_FOR_USER_REVIEW", "REVISING", "FROZEN"}:
        return {"editing_plan_version": current.version, "editing_plan_status": current.status, "phase": "plan_review"}

    source_paths = [str(path.resolve()) for path in _iter_source_videos()]
    analysis_paths = [str(path.resolve()) for path in _iter_analysis_json_files()]
    prompt = (
        "你是视频剪辑计划生成器。根据用户需求、剪辑蓝图、素材路径和分析摘要生成可审阅 EditingPlan JSON。"
        "只允许引用 source_video_paths 中的真实素材路径。scene 的 start/end 是成片时间轴，"
        "source_start/source_end 是原素材入点/出点。输出字段必须包含 version、user_request、"
        "target_duration_seconds、aspect_ratio、style、pacing、narration_strategy、subtitle_strategy、"
        "bgm_strategy、scenes。scenes 每项包含 scene_id、start、end、narrative_purpose、source_path、"
        "source_start、source_end、crop、transition、subtitle、narration、alternatives、locked。只返回 JSON。"
    )
    try:
        response = _invoke_llm(
            _get_llm(temperature=0.15).bind(max_tokens=5000),
            [
                SystemMessage(content=prompt),
                HumanMessage(
                    content=json.dumps(
                        {
                            "user_request": state.user_request,
                            "target_duration_seconds": state.target_duration_seconds,
                            "source_video_paths": source_paths,
                            "source_analysis_paths": analysis_paths,
                            "blueprint_markdown": state.editing_blueprint[:20000],
                            "analysis": _build_full_analysis_context()[:30000],
                        },
                        ensure_ascii=False,
                    )
                ),
            ],
            "editing_plan_generator",
        )
        parsed = _parse_json_object(str(response.content))
        parsed["version"] = "v001"
        parsed["status"] = "DRAFT"
        parsed["user_request"] = state.user_request
        parsed["source_video_paths"] = source_paths
        parsed["source_analysis_paths"] = analysis_paths
        parsed["blueprint_markdown"] = state.editing_blueprint
        plan = normalize_plan_timeline(EditingPlan.model_validate(parsed))
    except ModelCallError:
        raise
    except Exception as exc:
        graph_logger.warning("剪辑计划生成失败，使用降级计划: %s", exc)
        _emit_orchestration_event("editing_plan_fallback", {"reason": str(exc)[:300]})
        plan = _fallback_editing_plan(state)

    store.save_plan(plan)
    _register_plan_artifact(plan)
    _emit_orchestration_event(
        "editing_plan_created",
        {
            "version": plan.version,
            "scene_count": len(plan.scenes),
            "target_duration_seconds": plan.target_duration_seconds,
            "path": str(store.version_path(plan.version)),
        },
    )
    return {"editing_plan_version": plan.version, "editing_plan_status": plan.status, "phase": "plan_review"}


def validate_editing_plan_node(state: AgentState) -> dict[str, Any]:
    store = _editing_plan_store()
    plan = store.current()
    if plan is None:
        raise RuntimeError("缺少可校验的剪辑计划")
    report = validate_editing_plan(plan, allowed_source_paths=plan.source_video_paths)
    report_path = store.root / f"editing_plan_{plan.version}_validation.json"
    report_path.write_text(json.dumps(report.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    if not report.ok:
        _emit_orchestration_event(
            "editing_plan_validation_failed",
            {"version": plan.version, "issues": [issue.model_dump() for issue in report.issues]},
        )
        raise RuntimeError(
            "剪辑计划校验失败: "
            + "; ".join(issue.message for issue in report.issues if issue.severity == "error")
        )
    plan.status = "VALIDATED"
    store.save_plan(plan)
    _register_plan_artifact(plan)
    _emit_orchestration_event(
        "editing_plan_validated",
        {"version": plan.version, "warnings": [issue.model_dump() for issue in report.issues]},
    )
    return {"editing_plan_version": plan.version, "editing_plan_status": plan.status, "phase": "plan_review"}


def plan_review_gate_node(state: AgentState) -> dict[str, Any]:
    store = _editing_plan_store()
    plan = store.current()
    if plan is None:
        return {"phase": "react"}
    coordinator = _steering_coordinator()
    if not ENABLE_PLAN_REVIEW or coordinator is None:
        plan = store.approve(plan.version)
        _register_plan_artifact(plan, kind="approved_editing_plan")
        _emit_orchestration_event(
            "editing_plan_frozen",
            {"version": plan.version, "auto_approved": coordinator is None or not ENABLE_PLAN_REVIEW},
        )
        return {"editing_plan_version": plan.version, "editing_plan_status": plan.status, "phase": "react"}

    approved = store.approved()
    if approved is not None:
        return {"editing_plan_version": approved.version, "editing_plan_status": approved.status, "phase": "react"}

    plan.status = "WAITING_FOR_USER_REVIEW"
    store.save_plan(plan)
    control = coordinator.store.read_control()
    if control.get("status") != "requested" or control.get("mode") != "plan_review":
        control = coordinator.store.request_pause("plan_review")
    _emit_orchestration_event(
        "plan_review_waiting",
        {"version": plan.version, "pause_token": control.get("token", ""), "path": str(store.version_path(plan.version))},
    )
    coordinator.wait_if_paused("plan_review")
    approved = store.approved()
    if approved is None:
        current = store.current()
        if current is None:
            raise RuntimeError("计划审阅恢复后未找到当前计划")
        approved = store.approve(current.version)
    _register_plan_artifact(approved, kind="approved_editing_plan")
    _emit_orchestration_event("editing_plan_frozen", {"version": approved.version, "path": str(store.approved_path)})
    return {"editing_plan_version": approved.version, "editing_plan_status": approved.status, "phase": "react"}


def _register_revision_final(path: str | Path, producer_task_id: str) -> None:
    candidate = Path(path).resolve(strict=False)
    if not candidate.exists() or not candidate.is_file():
        return
    revision_suffix = f"_r{REVISION:03d}"
    if not candidate.stem.endswith(revision_suffix):
        snapshot = candidate.with_name(
            f"{candidate.stem}{revision_suffix}{candidate.suffix}"
        )
        if not snapshot.exists():
            shutil.copy2(candidate, snapshot)
        candidate = snapshot
    _artifact_registry().register(
        artifact_id=f"phase3_final_video_r{REVISION:03d}",
        kind="final_video",
        producer_task_id=producer_task_id,
        phase="phase3",
        path=candidate,
        metadata={"revision": REVISION, "current": True},
    )


def _register_latest_react_video() -> None:
    candidates = [path for path in WORKSPACE.glob("*.mp4") if path.is_file()]
    if not candidates:
        return
    preferred = [
        path
        for path in candidates
        if "final" in path.stem.lower() or "output" in path.stem.lower()
    ]
    _register_revision_final(
        max(preferred or candidates, key=lambda path: path.stat().st_mtime),
        "phase3_react_fallback",
    )


def _resource_scheduler(registry: ArtifactRegistry) -> ResourceScheduler:
    coordinator = _steering_coordinator()
    return ResourceScheduler(
        pools=_resource_pool_config(),
        workspace=WORKSPACE,
        artifact_registry=registry,
        event_sink=_emit_orchestration_event,
        cancel_requested=model_abort_requested,
        safe_point=(
            lambda context: coordinator.scheduler_safe_point(
                f"{context['plan_id']}:idle",
                str(context.get("phase") or "phase3"),
            )
            if coordinator is not None
            else None
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# LLM 实例
# ═══════════════════════════════════════════════════════════════════════════
def _get_llm(temperature: float = 0.2) -> ChatOpenAI:
    graph_logger.info("🔍 _get_llm() model=%s", MODEL_NAME)
    return ChatOpenAI(
        model=MODEL_NAME,
        temperature=temperature,
        api_key=API_KEY,
        base_url=BASE_URL,
        max_retries=0 if fail_fast_model_errors() else 2,
    )


def _invoke_llm(llm: Any, messages: list[Any], stage: str) -> Any:
    ensure_model_calls_allowed()
    started = time.perf_counter()
    emit_benchmark_event("model_call_started", {"stage": stage, "model": MODEL_NAME})
    try:
        response = llm.invoke(messages)
        content = str(getattr(response, "content", "") or "").strip()
        duration = time.perf_counter() - started
        if not content:
            if fail_fast_model_errors():
                raise_model_failure(
                    stage=stage,
                    model=MODEL_NAME,
                    message="Model returned empty content.",
                    duration_seconds=duration,
                )
            raise RuntimeError("Model returned empty content.")
        emit_benchmark_event(
            "model_call_completed",
            {
                "stage": stage,
                "model": MODEL_NAME,
                "duration_seconds": round(duration, 3),
            },
        )
        return response
    except ModelCallError:
        raise
    except Exception as exc:
        if fail_fast_model_errors():
            response = getattr(exc, "response", None)
            raise_model_failure(
                stage=stage,
                model=MODEL_NAME,
                message=exc,
                status_code=getattr(response, "status_code", None),
                request_id=str(getattr(response, "headers", {}).get("x-request-id", "")) if response else "",
                duration_seconds=time.perf_counter() - started,
            )
        raise


# ═══════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════
def _extract_target_duration_seconds(user_request: str) -> float:
    """从用户需求中推断目标时长（秒）。"""
    llm = _get_llm(temperature=0.0).bind(max_tokens=160)
    prompt = (
        "请根据用户需求推断成片目标时长（单位秒）。\n"
        "- 若用户明确提到时长，返回对应秒数\n"
        "- 若用户未明确提到时长，返回 300（约5分钟）\n"
        "仅返回一个数字，不要输出其他内容。"
    )
    try:
        response = _invoke_llm(
            llm,
            [HumanMessage(content=f"{prompt}\n\n用户需求: {user_request}")]
            ,
            "target_duration",
        )
        text = str(response.content).strip()
        m = re.search(r"(\d+(?:\.\d+)?)", text)
        if m:
            value = float(m.group(1))
            if value > 0:
                return value
    except ModelCallError:
        raise
    except Exception as exc:
        if fail_fast_model_errors():
            raise_model_failure(
                stage="target_duration_parse",
                model=MODEL_NAME,
                message=exc,
            )
        pass
    if fail_fast_model_errors():
        raise_model_failure(
            stage="target_duration_parse",
            model=MODEL_NAME,
            message="Could not parse a positive duration from model output.",
        )
    return 300.0


def _recommend_material_counts(target_duration_seconds: float) -> dict[str, int]:
    """根据目标时长推荐素材搜索数量与下载数量区间。"""
    if SHORT_FORM_OPTIMIZATIONS and 0 < target_duration_seconds <= 20:
        return {
            "search_per_source": 8,
            "search_pages": 1,
            "max_candidates": 24,
            "mllm_review": 24,
            "top_k_min": 1,
            "top_k_max": SHORT_FORM_MAX_SOURCES,
        }
    if target_duration_seconds <= 0:
        return {
            "search_per_source": 30,
            "search_pages": 2,
            "max_candidates": 100,
            "mllm_review": 100,
            "top_k_min": 6,
            "top_k_max": 12,
        }
    if target_duration_seconds <= 90:
        return {
            "search_per_source": 28,
            "search_pages": 2,
            "max_candidates": 100,
            "mllm_review": 100,
            "top_k_min": 6,
            "top_k_max": 10,
        }
    if target_duration_seconds <= 180:
        return {
            "search_per_source": 40,
            "search_pages": 3,
            "max_candidates": 180,
            "mllm_review": 180,
            "top_k_min": 10,
            "top_k_max": 16,
        }
    if target_duration_seconds <= 360:
        return {
            "search_per_source": 50,
            "search_pages": 3,
            "max_candidates": 240,
            "mllm_review": 240,
            "top_k_min": 14,
            "top_k_max": 22,
        }
    return {
        "search_per_source": 60,
        "search_pages": 4,
        "max_candidates": 320,
        "mllm_review": 320,
        "top_k_min": 18,
        "top_k_max": 28,
    }


def _step_result_text(result: StepResult | dict[str, Any] | str) -> str:
    if isinstance(result, StepResult):
        return result.result or result.error
    if isinstance(result, dict):
        return str(result.get("result") or result.get("error") or result)
    return str(result)


def _step_result_map(state: AgentState) -> dict[int, StepResult]:
    results: dict[int, StepResult] = {}
    for raw in state.step_results:
        try:
            result = raw if isinstance(raw, StepResult) else StepResult.model_validate(raw)
        except Exception:
            continue
        results[result.step_id] = result
    return results


def _fallback_prep_plan(
    user_request: str,
    counts: dict[str, int],
) -> Plan:
    top_k = max(1, (counts["top_k_min"] + counts["top_k_max"]) // 2)
    return Plan(
        goal=user_request,
        analysis="自动生成的素材准备 DAG",
        steps=[
            Step(
                id=1,
                description="搜索相关视频素材",
                tool_hint="search_bilibili_video",
                arguments={
                    "query": user_request,
                    "max_results": counts["search_per_source"],
                    "pages": counts["search_pages"],
                    "expand_variants": 2 if counts["search_pages"] == 1 else 3,
                    "max_total_results": counts["max_candidates"],
                },
            ),
            Step(
                id=2,
                description="筛选候选视频",
                tool_hint="rank_video_candidates",
                depends_on=[1],
                arguments={
                    "candidates_json": "[]",
                    "top_k": top_k,
                    "max_review": counts["mllm_review"],
                    "selection_goal": user_request,
                },
            ),
            Step(
                id=3,
                description="下载最佳素材",
                tool_hint="download_bilibili_video",
                depends_on=[2],
                arguments={},
            ),
            Step(
                id=4,
                description="分析所有已下载视频",
                tool_hint="analyze_video",
                depends_on=[3],
                arguments={"analysis_goal": user_request},
            ),
        ],
    )


def _plan_has_cycle(steps: list[Step]) -> bool:
    ids = {step.id for step in steps}
    indegree = {step.id: 0 for step in steps}
    children: dict[int, list[int]] = {step.id: [] for step in steps}
    for step in steps:
        for dependency in step.depends_on:
            if dependency not in ids:
                continue
            indegree[step.id] += 1
            children[dependency].append(step.id)

    ready = [step_id for step_id, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        step_id = ready.pop()
        visited += 1
        for child_id in children[step_id]:
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                ready.append(child_id)
    return visited != len(steps)


def _validate_and_normalize_plan(
    plan: Plan,
    user_request: str,
    counts: dict[str, int],
) -> Plan:
    """校验 Agent 提议的 DAG，并强制补齐素材准备阶段的不变量。"""
    if not plan.steps:
        return plan

    raw_ids = [step.id for step in plan.steps]
    if any(step_id <= 0 for step_id in raw_ids) or len(raw_ids) != len(set(raw_ids)):
        graph_logger.warning("⚠️ Planner DAG 步骤 ID 非法，回退为标准 DAG")
        return _fallback_prep_plan(user_request, counts)

    valid_ids = set(raw_ids)
    normalized: list[Step] = []
    for step in plan.steps:
        step.tool_hint = _normalize_tool_hint(step)
        step.depends_on = sorted(
            {
                int(dependency)
                for dependency in step.depends_on
                if int(dependency) in valid_ids and int(dependency) != step.id
            }
        )
        step.arguments = dict(step.arguments or {})
        normalized.append(step)

    short_form = (
        SHORT_FORM_OPTIMIZATIONS
        and counts.get("search_pages") == 1
        and counts.get("max_candidates") <= 24
    )
    if short_form:
        kept_search_ids = {
            step.id
            for step in [
                item for item in normalized
                if item.tool_hint == "search_bilibili_video"
            ][:2]
        }
        normalized = [
            step
            for step in normalized
            if step.tool_hint != "search_bilibili_video" or step.id in kept_search_ids
        ]
        kept_ids = {step.id for step in normalized}
        for step in normalized:
            step.depends_on = [dependency for dependency in step.depends_on if dependency in kept_ids]
            if step.tool_hint == "search_bilibili_video":
                step.arguments["max_results"] = min(
                    int(step.arguments.get("max_results", 8) or 8),
                    8,
                )
                step.arguments["pages"] = 1
                step.arguments["expand_variants"] = min(
                    int(step.arguments.get("expand_variants", 2) or 2),
                    2,
                )
                step.arguments["max_total_results"] = min(
                    int(step.arguments.get("max_total_results", 24) or 24),
                    24,
                )
            elif step.tool_hint == "rank_video_candidates":
                step.arguments["top_k"] = min(
                    int(step.arguments.get("top_k", SHORT_FORM_MAX_SOURCES) or SHORT_FORM_MAX_SOURCES),
                    SHORT_FORM_MAX_SOURCES,
                )
                step.arguments["max_review"] = min(
                    int(step.arguments.get("max_review", 24) or 24),
                    24,
                )

    if DIRECT_PHASE3_EXECUTION:
        normalized = [
            step for step in normalized
            if step.tool_hint not in REMOTE_PREP_TOOL_NAMES
        ]
        kept_ids = {step.id for step in normalized}
        for step in normalized:
            step.depends_on = [item for item in step.depends_on if item in kept_ids]

    search_ids = [step.id for step in normalized if step.tool_hint == "search_bilibili_video"]
    rank_ids = [step.id for step in normalized if step.tool_hint == "rank_video_candidates"]
    download_ids = [step.id for step in normalized if step.tool_hint == "download_bilibili_video"]

    positions = {step.id: index for index, step in enumerate(normalized)}
    for step in normalized:
        required: set[int] = set(step.depends_on)
        if step.tool_hint == "rank_video_candidates":
            required.update(search_ids)
        elif step.tool_hint == "download_bilibili_video":
            required.update(rank_ids)
        elif step.tool_hint == "analyze_video":
            required.update(
                download_id
                for download_id in download_ids
                if positions.get(download_id, -1) < positions.get(step.id, -1)
            )
        step.depends_on = sorted(required)

    if _plan_has_cycle(normalized):
        graph_logger.warning("⚠️ Planner DAG 存在环路，回退为标准 DAG")
        return _fallback_prep_plan(user_request, counts)

    plan.steps = normalized
    return plan


def _build_tool_catalog(tools: list[Any] | None = None) -> str:
    """构建工具目录文字。"""
    target_tools = tools or ALL_TOOLS
    rows: list[str] = []
    for i, tool in enumerate(target_tools, start=1):
        name = getattr(tool, "name", None) or getattr(tool, "__name__", "unknown_tool")
        description = (getattr(tool, "description", "") or "").strip()
        short_desc = description.splitlines()[0].strip() if description else ""
        rows.append(f"{i}. {name} — {short_desc}" if short_desc else f"{i}. {name}")
    return "\n".join(rows) if rows else "(无可用工具)"


def _build_workspace_snapshot(max_files: int = 40) -> str:
    """获取工作目录文件快照。"""
    try:
        files = [p for p in WORKSPACE.glob("**/*") if p.is_file()]
        if not files:
            return "(工作目录为空)"
        files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]
        rows: list[str] = []
        for p in files:
            try:
                size_mb = p.stat().st_size / (1024 * 1024)
                rel = p.relative_to(WORKSPACE)
                rows.append(f"- {rel} ({size_mb:.1f}MB)")
            except Exception:
                rows.append(f"- {p.name}")
        return "\n".join(rows)
    except Exception:
        return "(工作目录快照读取失败)"


def _iter_source_videos() -> list[Path]:
    """遍历可作为源素材的视频（temp + user_temp），排除中间产物。"""
    blocked_prefixes = (
        "merged_",
        "final_",
        "transitioned_",
        "narrated_",
        "output_",
        "exported_",
    )
    seen: set[str] = set()
    seen_file_ids: set[tuple[int, int, int]] = set()
    videos: list[Path] = []
    for fp in iter_video_files([WORKSPACE, USER_WORKSPACE]):
        name = fp.name
        if name.startswith(blocked_prefixes) or "_clip_" in name or "_analysis" in name:
            continue
        key = str(fp.resolve(strict=False))
        if key in seen:
            continue
        try:
            stat = fp.stat()
            file_id = (int(stat.st_dev), int(stat.st_ino), int(stat.st_size))
            if stat.st_ino and file_id in seen_file_ids:
                continue
            if stat.st_ino:
                seen_file_ids.add(file_id)
        except OSError:
            pass
        seen.add(key)
        videos.append(fp)
    return videos


def _iter_analysis_json_files() -> list[Path]:
    """遍历分析文件（temp + user_temp）。"""
    return iter_analysis_files([WORKSPACE, USER_WORKSPACE])


def _collect_analysis_overview() -> dict[str, Any]:
    """汇总现有分析素材的数量与可用时长，供本地素材充分性判断使用。"""
    items: list[dict[str, Any]] = []
    total_available_duration = 0.0

    for fp in _iter_analysis_json_files():
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue

        source_video = str(data.get("source_video", ""))
        segments = data.get("segments", [])
        available_duration = 0.0
        segment_count = 0

        if isinstance(segments, list):
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                try:
                    start = float(segment.get("start", 0))
                    end = float(segment.get("end", 0))
                except (TypeError, ValueError):
                    continue
                duration = max(0.0, end - start)
                if duration <= 0:
                    continue
                available_duration += duration
                segment_count += 1

        total_available_duration += available_duration
        items.append(
            {
                "analysis_file": fp.name,
                "source_video": source_video,
                "segment_count": segment_count,
                "available_duration_seconds": round(available_duration, 1),
            }
        )

    return {
        "analysis_count": len(items),
        "total_available_duration_seconds": round(total_available_duration, 1),
        "items": items[:20],
    }


def _build_direct_phase3_plan(user_request: str) -> Plan:
    """构建跳过素材搜集的执行计划，仅补齐现有素材分析。"""
    source_videos = _iter_source_videos()
    analysis_files = _iter_analysis_json_files()
    if not source_videos and not analysis_files:
        raise RuntimeError(
            "已启用直达 Phase 3，但当前未找到可复用的本地素材或既有分析数据。"
            "请先上传/准备素材，或关闭“直达 Phase 3”。"
        )

    steps: list[Step] = []
    if source_videos:
        steps.append(
            Step(
                id=1,
                description="跳过素材搜集，仅分析现有本地素材和已有源视频，为 Phase 3 准备多模态上下文",
                tool_hint="analyze_video",
                arguments={"analysis_goal": user_request},
            )
        )

    return Plan(
        goal=user_request,
        analysis="直达 Phase 3 已启用：跳过搜索、筛选与下载，仅复用现有本地素材/历史源视频。",
        steps=steps,
    )


def _build_local_first_plan(user_request: str) -> Plan:
    """构建本地素材优先计划：先分析本地，再按需联网补充。"""
    return Plan(
        goal=user_request,
        analysis=(
            "本地素材优先已启用：先分析当前工程中的本地素材与已有源视频。"
            "若现有素材已足够，则直接进入后续剪辑；不足时再联网搜索补充。"
        ),
        steps=[
            Step(
                id=1,
                description="优先分析现有本地素材和已有源视频",
                tool_hint="analyze_video",
                arguments={"analysis_goal": user_request},
            ),
            Step(
                id=2,
                description="如本地素材不足，再搜索补充素材",
                tool_hint="search_bilibili_video",
                depends_on=[1],
                arguments={"query": user_request},
            ),
            Step(
                id=3,
                description="筛选最适合作为补充的候选素材",
                tool_hint="rank_video_candidates",
                depends_on=[2],
                arguments={"candidates_json": "[]", "selection_goal": user_request},
            ),
            Step(
                id=4,
                description="下载补充素材",
                tool_hint="download_bilibili_video",
                depends_on=[3],
            ),
            Step(
                id=5,
                description="分析所有新增补充素材",
                tool_hint="analyze_video",
                depends_on=[4],
                arguments={"analysis_goal": user_request},
            ),
        ],
    )


LOCAL_SUFFICIENCY_PROMPT = """\
你是视频素材充分性评估器。

请根据用户目标时长、任务要求和当前已分析素材概况，判断现有本地素材是否已经足够直接进入剪辑阶段。

判断原则：
- 必须先判断“内容是否匹配当前任务”，再判断“覆盖度是否足够”，最后才判断“时长是否够”。
- 如果本地素材的主体、场景、主题或视觉类型与用户需求不匹配，必须返回 false，不能因为时长够长就判定为足够。
- 例如：用户要“校园视频”，但本地素材是讲座、风景或访谈，即使有 5 分钟，也必须返回 false。
- “足够”表示这些素材已经足以完成一个可交付的版本，不要求绝对完美，但不能明显缺少关键内容。
- 如果用户需求明显需要多场景、多段落、多人物或较长成片，而现有素材覆盖和可用时长明显不足，应返回 false。
- 不要假设后续还能联网补素材；只根据当前已有素材判断。

仅返回 JSON：
{
  "content_match": false,
  "coverage_match": false,
  "duration_match": false,
  "sufficient": true,
  "reason": "一句简短中文原因",
  "confidence": "high"
}
"""


def _assess_local_material_sufficiency(state: AgentState) -> tuple[bool, str]:
    """判断现有本地分析素材是否已足够跳过联网搜集。"""
    overview = _collect_analysis_overview()
    analysis_count = int(overview.get("analysis_count", 0) or 0)
    total_available_duration = float(overview.get("total_available_duration_seconds", 0.0) or 0.0)

    if analysis_count <= 0:
        return False, "暂无已分析素材。"

    target_duration = state.target_duration_seconds if state.target_duration_seconds > 0 else 300.0
    fallback_threshold = max(target_duration * 1.4, target_duration + 20.0)
    fallback_sufficient = total_available_duration >= fallback_threshold
    fallback_reason = (
        f"现有分析素材约 {total_available_duration:.1f}s，可用时长"
        f"{'达到' if fallback_sufficient else '尚未达到'}保守阈值 {fallback_threshold:.1f}s。"
    )

    try:
        llm = _get_llm(temperature=0.0).bind(max_tokens=320)
        response = _invoke_llm(
            llm,
            [
                SystemMessage(content=LOCAL_SUFFICIENCY_PROMPT),
                HumanMessage(
                    content=(
                        f"用户需求: {state.user_request}\n"
                        f"目标时长: {target_duration:.1f} 秒\n"
                        f"本地素材目录快照:\n{_build_user_workspace_snapshot()}\n\n"
                        f"现有分析概况(JSON):\n{json.dumps(overview, ensure_ascii=False, indent=2)}\n\n"
                        f"分析上下文摘要:\n{_build_full_analysis_context()[:10000]}"
                    )
                ),
            ],
            "local_material_sufficiency",
        )
        content = str(response.content).strip()
        if "```json" in content:
            content = content.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in content:
            content = content.split("```", 1)[1].split("```", 1)[0]
        parsed = json.loads(content)
        content_match = bool(parsed.get("content_match", False))
        coverage_match = bool(parsed.get("coverage_match", False))
        duration_match = bool(parsed.get("duration_match", fallback_sufficient))
        sufficient = bool(parsed.get("sufficient", False))
        reason = str(parsed.get("reason", "")).strip() or fallback_reason
        confidence = str(parsed.get("confidence", "")).strip().lower()
        if not content_match:
            return False, f"{reason}（内容与当前任务不匹配，不能仅因时长充足而跳过联网补充）"
        if not coverage_match:
            return False, reason
        if sufficient and confidence == "low":
            return False, f"{reason}（低置信度，按保守策略继续联网补充）"
        if sufficient and (duration_match or fallback_sufficient):
            return True, reason
        return False, reason
    except ModelCallError:
        raise
    except Exception as exc:
        if fail_fast_model_errors():
            raise_model_failure(
                stage="local_material_sufficiency_parse",
                model=MODEL_NAME,
                message=exc,
            )
        graph_logger.warning("⚠️ 本地素材充分性评估失败，按保守策略继续联网补充: %s", exc)
        return False, f"本地素材评估失败，按保守策略继续联网补充。{fallback_reason}"


def _build_user_workspace_snapshot(max_files: int = 40) -> str:
    """获取 user_temp 文件快照。"""
    try:
        files = [p for p in USER_WORKSPACE.glob("**/*") if p.is_file()]
        if not files:
            return "(user_temp 为空)"
        files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]
        rows: list[str] = []
        for p in files:
            try:
                size_mb = p.stat().st_size / (1024 * 1024)
                rel = p.relative_to(USER_WORKSPACE)
                rows.append(f"- {rel} ({size_mb:.1f}MB)")
            except Exception:
                rows.append(f"- {p.name}")
        return "\n".join(rows)
    except Exception:
        return "(user_temp 快照读取失败)"


def _load_latest_memory_experience(max_chars: int = 16000) -> str:
    """读取最新 skills 经验，用于注入下一轮剪辑上下文。"""
    safe_limit = min(max_chars, INJECTION_MEMORY_CHAR_LIMIT)
    return load_memory_reference(MEMORY_EXPERIENCE_DIR, max_chars=safe_limit)


def _build_full_analysis_context() -> str:
    """读取所有分析 JSON，构建完整的分析上下文供 Phase 2/2 使用。

    Enhanced: 包含每个片段的时长计算和更结构化的输出，方便深度研究。
    """
    json_files = _iter_analysis_json_files()
    if not json_files:
        return "(无分析数据)"

    blocks: list[str] = []
    total_available_duration = 0.0

    for fp in json_files:
        try:
            with fp.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        source_video = str(data.get("source_video", ""))
        analysis_text = str(data.get("analysis_text", ""))
        segments = data.get("segments", [])

        seg_lines: list[str] = []
        video_seg_duration = 0.0
        if isinstance(segments, list):
            for seg in segments:
                if isinstance(seg, dict):
                    s = seg.get("start")
                    e = seg.get("end")
                    if s is not None and e is not None:
                        dur = round(float(e) - float(s), 2)
                        video_seg_duration += dur
                        seg_lines.append(f"    t={s}s ~ t={e}s  (时长 {dur}s)")

        total_available_duration += video_seg_duration

        block_parts = [
            f"📽️ 源视频: {source_video}",
            f"   分析文件: {fp.name}",
        ]
        if seg_lines:
            block_parts.append(
                f"   推荐片段 ({len(seg_lines)} 段, 总可用时长 {video_seg_duration:.1f}s):"
            )
            block_parts.extend(seg_lines[:40])
        if analysis_text:
            block_parts.append(f"   分析详情:\n{analysis_text[:3000]}")
        blocks.append("\n".join(block_parts))

    summary = (
        f"━━━ 素材总览: {len(blocks)} 个源视频, "
        f"总可用片段时长 {total_available_duration:.1f}s ━━━\n\n"
    )
    return summary + "\n\n".join(blocks)


def _looks_like_tool_call_text(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    patterns = ["<tool_call>", '"name"', '"arguments"', "assistant to=", "<|tool_call|>"]
    return any(p in t for p in patterns)


def _extract_final_message(result_state: dict[str, Any]) -> str:
    """从 ReAct Agent 结果中提取最终自然语言回复。"""
    messages = result_state.get("messages", []) if isinstance(result_state, dict) else []
    ai_texts: list[str] = []
    tool_texts: list[str] = []

    for msg in messages:
        mtype = getattr(msg, "type", "")
        content = getattr(msg, "content", "")
        content_text = str(content) if content is not None else ""
        if not content_text:
            continue
        if mtype == "ai":
            ai_texts.append(content_text)
        elif mtype == "tool":
            tool_texts.append(content_text)

    for text in reversed(ai_texts):
        if not _looks_like_tool_call_text(text):
            return text

    if tool_texts:
        return f"工具执行完成，关键结果:\n{tool_texts[-1][:1200]}"
    if ai_texts:
        return ai_texts[-1]
    return ""


def _infer_download_top_k(step_description: str, counts: dict[str, int]) -> int:
    """从步骤描述推断下载数量，推断失败时使用建议区间中值。"""
    text = step_description or ""
    patterns = [
        r"top\s*(\d+)",
        r"Top\s*(\d+)",
        r"筛选出\s*(\d+)\s*个",
        r"下载\s*(\d+)\s*个",
    ]
    for p in patterns:
        m = re.search(p, text, flags=re.IGNORECASE)
        if m:
            try:
                value = int(m.group(1))
                if value > 0:
                    return value
            except Exception:
                continue
    low = int(counts.get("top_k_min", 4))
    high = int(counts.get("top_k_max", max(low, 8)))
    if high < low:
        high = low
    return max(1, (low + high) // 2)


def _selected_videos_from_results(step_results: list[StepResult]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for raw in step_results:
        try:
            result = raw if isinstance(raw, StepResult) else StepResult.model_validate(raw)
        except Exception:
            continue
        if result.tool_name != "rank_video_candidates" or result.status != "done":
            continue
        items = result.data.get("selected_videos", [])
        if isinstance(items, list):
            selected.extend(item for item in items if isinstance(item, dict))
    return selected


def _run_deterministic_download_step(
    step: Step,
    counts: dict[str, int],
    user_request: str,
    selected_videos: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """下载步骤的确定性执行，使用排序步骤输出并有界并行下载。"""
    rank_tool = _TOOL_NAME_MAP.get("rank_video_candidates")
    download_tool = _TOOL_NAME_MAP.get("download_bilibili_video")
    if rank_tool is None or download_tool is None:
        raise RuntimeError("缺少 rank_video_candidates 或 download_bilibili_video 工具")

    top_k = _infer_download_top_k(step.description, counts)
    max_review = int(counts.get("mllm_review", 30))
    selected_videos = list(selected_videos or [])
    if not selected_videos:
        rank_raw = rank_tool.invoke(
            {
                "candidates_json": "[]",
                "top_k": top_k,
                "max_review": max_review,
                "selection_goal": user_request,
            }
        )
        try:
            rank_data = json.loads(str(rank_raw))
        except Exception as exc:
            raise RuntimeError(f"无法解析排序结果: {str(rank_raw)[:400]}") from exc
        selected_videos = rank_data.get("selected_videos", [])

    if not isinstance(selected_videos, list) or not selected_videos:
        raise RuntimeError("排序结果中没有 selected_videos，无法下载")

    success_items: list[str] = []
    fail_items: list[str] = []
    downloaded_paths: list[str] = []
    workers = min(max(1, DOWNLOAD_POOL_SIZE), len(selected_videos))
    graph_logger.info(
        "📥 确定性并行下载开始: total=%d, concurrency=%d",
        len(selected_videos),
        workers,
    )

    def download_one(index_and_video: tuple[int, dict[str, Any]]) -> tuple[int, str, str, str]:
        i, video = index_and_video
        if not isinstance(video, dict):
            return i, "", "", "候选数据不是对象"
        title = str(video.get("title") or "").strip()
        bvid = str(video.get("bvid") or "").strip()
        url = str(bvid or video.get("url") or "").strip()
        if not url:
            return i, title or "unknown", "", "缺少 url/bvid"

        safe_tail = bvid[-6:] if bvid else f"{i:02d}"
        filename = f"selected_{i}_{safe_tail}"
        try:
            download_raw = download_tool.invoke(
                {"url": url, "filename": filename, "prefer_h264": True}
            )
            parsed = json.loads(str(download_raw))
            if parsed.get("status") == "success":
                path = str(parsed.get("path", ""))
                return i, title or bvid or url, path, ""
            return i, title or bvid or url, "", str(download_raw)[:180]
        except Exception as exc:
            return i, title or bvid or url, "", str(exc)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="video-download") as executor:
        futures = {
            executor.submit(download_one, item): item[0]
            for item in enumerate(selected_videos, start=1)
        }
        completed_rows: list[tuple[int, str, str, str]] = []
        for future in as_completed(futures):
            completed_rows.append(future.result())

    for i, label, path, error in sorted(completed_rows, key=lambda item: item[0]):
        if path:
            downloaded_paths.append(path)
            success_items.append(f"{i}. {label} -> {path}")
        else:
            fail_items.append(f"{i}. {label}: {error}")

    summary_parts = [
        "下载步骤（确定性执行）完成",
        f"- 计划下载: {len(selected_videos)}",
        f"- 成功: {len(success_items)}",
        f"- 失败: {len(fail_items)}",
    ]
    if success_items:
        summary_parts.append("- 成功明细:")
        summary_parts.extend(success_items[:20])
    if fail_items:
        summary_parts.append("- 失败明细:")
        summary_parts.extend(fail_items[:20])
    if not downloaded_paths:
        raise RuntimeError("\n".join(summary_parts))
    return "\n".join(summary_parts), {
        "downloaded_paths": downloaded_paths,
        "failed_count": len(fail_items),
        "selected_count": len(selected_videos),
    }


# ═══════════════════════════════════════════════════════════════════════════
# ▸ Phase 1 — Node 1: Planner (仅规划素材准备步骤)
# ═══════════════════════════════════════════════════════════════════════════
PLANNER_PROMPT = """\
你是一位资深视频编辑导演。
用户想要自动编辑一段视频。你的任务是规划**素材准备**阶段的步骤。

⚠️ 重要: 你**只需规划到"所有视频分析完成"为止**。
后续的剪辑/合并/转场/旁白/导出将由另一个创作 AI 自主完成，不需要你规划。

## 你需要规划的步骤范围
1. 搜索视频素材（使用 search_bilibili_video，多关键词扩展）
2. 筛选候选视频（rank_video_candidates，从候选池中精选 Top K）
3. 下载候选中最优的一批视频（download_bilibili_video，在一个步骤里下载）
4. 对所有下载视频进行多模态分析（analyze_video，在一个步骤里一起分析）

## 可用工具
{tool_catalog}

## 关键原则
- 当前用户需求与当前素材分析是唯一的任务目标来源；历史案例 memory 只能提供方法参考，绝不能改写题材、素材类型、关键词、风格或目标时长
- 尽量搜集丰富的资源：使用多关键词扩展与分页搜索，扩大搜索广度
- 先广度再精选：先广度搜索 → MLLM 筛选 → 下载最优一批
- 调用参数显式化：搜索和筛选工具必须显式传参（max_results / pages / max_total_results / top_k / max_review）
- `top_k` 必须由你根据任务复杂度、目标时长、候选质量自主决定，不要固定成 5
- 所有下载的视频都必须分析：每个视频需要调用一次 analyze_video
- 将互不依赖的搜索拆成多个步骤，并让这些搜索步骤的 `depends_on` 为空，以便并行执行
- `rank_video_candidates` 必须依赖全部搜索步骤
- `download_bilibili_video` 必须依赖排序步骤
- `analyze_video` 必须依赖下载步骤
- 每个步骤都必须在 `arguments` 中给出完整工具参数；下载步骤的素材列表由调度器从排序结果自动注入
- 每个步骤的 `tool_hint` 必须且只能填写一个工具名，且从以下四个中选择：
    - search_bilibili_video
    - rank_video_candidates
    - download_bilibili_video
    - analyze_video
- **不要包含剪辑、合并、转场、旁白、导出步骤** — 这些全部交给后续创作 AI

## 素材数量建议（可按复杂度上下浮动）
{sizing_hint}

工作目录:
- temp: {workspace}
- user_temp: {user_workspace}
- memory_experience: {memory_experience}

## 输出格式
请以 JSON 格式输出计划，严格按以下结构:
{{
    "goal": "用户的最终目标",
    "analysis": "你对需求的分析和创意构思",
    "steps": [
        {{
            "id": 1,
            "description": "具体做什么",
            "tool_hint": "建议工具名",
            "depends_on": [],
            "arguments": {{"query": "明确的搜索关键词"}}
        }},
        ...
    ]
}}

"""


def planner_node(state: AgentState) -> dict[str, Any]:
    """Phase 1 Planner: 分析需求，生成素材准备步骤。"""
    graph_logger.info("🎯 Phase 1 — Planner 开始规划素材准备")
    target_duration = _extract_target_duration_seconds(state.user_request)
    counts = _recommend_material_counts(target_duration)
    if DIRECT_PHASE3_EXECUTION:
        plan = _build_direct_phase3_plan(state.user_request)
        graph_logger.info("⏩ 直达 Phase 3 已启用，跳过联网素材搜集")
    elif PREFER_LOCAL_MATERIALS and state.gap_round == 0 and _iter_source_videos():
        plan = _build_local_first_plan(state.user_request)
        graph_logger.info("🏠 本地素材优先已启用，先复用现有素材再决定是否联网补充")
    else:
        llm = _get_llm().bind(max_tokens=4096)
        sizing_hint = (
            f"- 每平台搜索数量 max_results: {counts['search_per_source']}\n"
            f"- 分页 pages: {counts['search_pages']}\n"
            f"- 候选池上限 max_total_results: {counts['max_candidates']}\n"
            f"- MLLM 评估数 max_review: {counts['mllm_review']}\n"
            f"- 下载数量 top_k 建议区间: {counts['top_k_min']}~{counts['top_k_max']}（最终由你自主决定）\n"
        )

        prompt = PLANNER_PROMPT.format(
            workspace=WORKSPACE,
            user_workspace=USER_WORKSPACE,
            memory_experience=MEMORY_EXPERIENCE_DIR,
            tool_catalog=_build_tool_catalog(PREP_TOOLS),
            sizing_hint=sizing_hint,
        )

        context_parts: list[str] = [f"用户需求: {state.user_request}"]
        context_parts.append(
            "\n## 用户本地素材目录 user_temp\n"
            + _build_user_workspace_snapshot()
        )
        context_parts.append(
            "\n## 历史案例经验（仅供参考，不能覆盖当前任务目标）\n"
            + _load_latest_memory_experience(max_chars=12000)
        )
        if state.step_results:
            context_parts.append("\n## 已完成的步骤结果")
            for i, r in enumerate(state.step_results, start=1):
                context_parts.append(f"步骤 {i}: {_step_result_text(r)[:300]}")
        if state.material_gap_report:
            context_parts.append(
                "\n## 素材缺口报告\n"
                + json.dumps(state.material_gap_report, ensure_ascii=False)
                + "\n请只针对缺口生成补充搜索计划，避免重复已有素材。"
            )

        response = _invoke_llm(
            llm,
            [
                SystemMessage(content=prompt),
                HumanMessage(content="\n".join(context_parts)),
            ],
            "phase1_planner",
        )

        try:
            content = str(response.content)
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            plan_data = json.loads(content)
            plan = Plan(**plan_data)
        except Exception as exc:
            if fail_fast_model_errors():
                raise_model_failure(
                    stage="phase1_planner_parse",
                    model=MODEL_NAME,
                    message=exc,
                )
            plan = _fallback_prep_plan(state.user_request, counts)

    plan = _validate_and_normalize_plan(plan, state.user_request, counts)

    graph_logger.info("📋 素材准备计划 (%d 步): %s", len(plan.steps), plan.goal)
    for s in plan.steps:
        graph_logger.info(
            "   [%d] %s → %s, depends_on=%s",
            s.id,
            s.description,
            s.tool_hint,
            s.depends_on,
        )
    if target_duration > 0:
        graph_logger.info("⏱ 目标时长: %.1fs", target_duration)

    return {
        "plan": plan,
        "current_step_index": 0,
        "prep_round": 0,
        "target_duration_seconds": target_duration,
        "phase": "planning",
    }


# ═══════════════════════════════════════════════════════════════════════════
# ▸ Phase 1 — Node 2: Executor (执行一个素材准备步骤)
# ═══════════════════════════════════════════════════════════════════════════
def _normalize_tool_hint(step: Step) -> str:
    """将 tool_hint 规范为 Phase1 的单一合法工具名。"""
    valid = {
        "search_bilibili_video",
        "rank_video_candidates",
        "download_bilibili_video",
        "analyze_video",
    }
    hint = (step.tool_hint or "").strip()
    if hint in valid:
        return hint

    desc = (step.description or "").lower()
    if "search_bilibili_video" in hint or "搜索" in desc:
        return "search_bilibili_video"
    if "rank_video_candidates" in hint or "筛选" in desc or "排序" in desc:
        return "rank_video_candidates"
    if "download_bilibili_video" in hint or "下载" in desc:
        return "download_bilibili_video"
    if "analyze_video" in hint or "分析" in desc:
        return "analyze_video"
    return "search_bilibili_video"


def _run_deterministic_analysis_step(
    source_videos: list[Path],
    analysis_goal: str,
) -> str:
    tool_obj = _TOOL_NAME_MAP.get("analyze_video")
    if tool_obj is None:
        raise RuntimeError("analyze_video 工具未注册。")

    total = len(source_videos)
    if total == 0:
        raise RuntimeError("没有可分析的视频文件")
    workers = max(1, min(VIDEO_ANALYSIS_POOL_SIZE, total))
    graph_logger.info("🧠 确定性视频分析开始: total=%d, concurrency=%d", total, workers)
    results: dict[str, str] = {}

    def analyze_one(video_path: Path) -> tuple[str, str]:
        ensure_model_calls_allowed()
        result = tool_obj.invoke(
            {
                "video_path": str(video_path.resolve()),
                "analysis_goal": analysis_goal,
            }
        )
        return video_path.name, str(result)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="video-analysis") as executor:
        futures = {
            executor.submit(analyze_one, video_path): video_path
            for video_path in source_videos
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            video_path = futures[future]
            try:
                name, result = future.result()
            except ModelCallError:
                for pending in futures:
                    pending.cancel()
                raise
            except Exception as exc:
                name = video_path.name
                result = f"视频分析出错: {exc}"
            results[name] = result
            succeeded = "视频分析完成" in result
            graph_logger.info(
                "%s 视频分析进度: %d/%d, file=%s",
                "✅" if succeeded else "⚠️",
                completed,
                total,
                name,
            )

    analysis_index = build_analysis_index([WORKSPACE, USER_WORKSPACE])
    missing = [
        video_path.name
        for video_path in source_videos
        if not match_analysis_files(video_path, analysis_index=analysis_index)
    ]
    if missing:
        failure_details = [
            f"- {name}: {results.get(name, '未返回结果')[:240]}"
            for name in missing
        ]
        raise RuntimeError(
            "视频多模态分析未全部完成：\n"
            + "\n".join(failure_details)
            + "\n任务已停止，不会重新搜索素材或重复分析。"
        )

    return (
        f"视频分析完成：共 {total} 个源视频，全部生成分析文件；"
        f"最大并发数 {workers}。"
    )


def executor_node(state: AgentState | dict[str, Any]) -> dict[str, Any]:
    """确定性执行一个由 DAG 调度器分发的 Phase 1 步骤。"""
    if not isinstance(state, AgentState):
        state = AgentState.model_validate(state)
    if state.plan is None:
        raise RuntimeError("Phase 1 执行缺少计划")

    step = next(
        (item for item in state.plan.steps if item.id == state.active_step_id),
        None,
    )
    if step is None and 0 <= state.current_step_index < len(state.plan.steps):
        step = state.plan.steps[state.current_step_index]
    if step is None:
        raise RuntimeError(f"找不到待执行步骤: {state.active_step_id}")

    tool_name = _normalize_tool_hint(step)
    arguments = dict(step.arguments or {})
    counts = _recommend_material_counts(state.target_duration_seconds)
    started_at = time.perf_counter()
    graph_logger.info("🔧 Executor 步骤 [%d]: %s", step.id, step.description)

    try:
        if model_abort_requested():
            raise RuntimeError("模型失败熔断已开启，拒绝调度新步骤。")
        result_text = ""
        result_data: dict[str, Any] = {}

        if tool_name == "search_bilibili_video":
            tool_obj = _TOOL_NAME_MAP.get(tool_name)
            if tool_obj is None:
                raise RuntimeError(f"{tool_name} 工具未注册")
            arguments.setdefault("query", state.user_request)
            arguments.setdefault("max_results", counts["search_per_source"])
            arguments.setdefault("pages", counts["search_pages"])
            arguments.setdefault("max_total_results", counts["max_candidates"])
            raw_result = tool_obj.invoke(arguments)
            candidates = json.loads(str(raw_result))
            if not isinstance(candidates, list) or not candidates:
                raise RuntimeError("搜索未返回有效候选素材")
            result_text = f"搜索完成，获得 {len(candidates)} 个候选素材。"
            result_data = {"candidate_count": len(candidates)}

        elif tool_name == "rank_video_candidates":
            tool_obj = _TOOL_NAME_MAP.get(tool_name)
            if tool_obj is None:
                raise RuntimeError(f"{tool_name} 工具未注册")
            arguments.setdefault("candidates_json", "[]")
            arguments.setdefault(
                "top_k",
                _infer_download_top_k(step.description, counts),
            )
            arguments.setdefault("max_review", counts["mllm_review"])
            arguments.setdefault("selection_goal", state.user_request)
            raw_result = tool_obj.invoke(arguments)
            ranking = json.loads(str(raw_result))
            selected_videos = (
                ranking.get("selected_videos", [])
                if isinstance(ranking, dict)
                else []
            )
            if not selected_videos:
                raise RuntimeError("排序未选出有效素材")
            result_text = f"排序完成，选出 {len(selected_videos)} 个素材。"
            result_data = {
                "selected_videos": selected_videos,
                "ranking": ranking,
            }

        elif tool_name == "download_bilibili_video":
            selected_videos = _selected_videos_from_results(state.step_results)
            result_text, result_data = _run_deterministic_download_step(
                step,
                counts,
                state.user_request,
                selected_videos=selected_videos,
            )

        elif tool_name == "analyze_video":
            analysis_index = build_analysis_index([WORKSPACE, USER_WORKSPACE])
            source_videos = _iter_source_videos()
            pending_source_videos = [
                video_path
                for video_path in source_videos
                if not match_analysis_files(
                    video_path,
                    analysis_index=analysis_index,
                )
            ]
            if pending_source_videos:
                result_text = _run_deterministic_analysis_step(
                    pending_source_videos,
                    str(arguments.get("analysis_goal") or state.user_request),
                )
                result_data = {
                    "analyzed_paths": [
                        str(path.resolve()) for path in pending_source_videos
                    ]
                }
            elif source_videos:
                reused_paths = [
                    str(matches[0].resolve())
                    for video_path in source_videos
                    if (
                        matches := match_analysis_files(
                            video_path,
                            analysis_index=analysis_index,
                        )
                    )
                ]
                result_text = (
                    f"检测到 {len(reused_paths)} 个可复用分析文件，无需重复分析。"
                )
                result_data = {
                    "analysis_paths": reused_paths,
                    "reused": True,
                }
            else:
                raise RuntimeError("没有可分析的视频文件")
        else:
            raise RuntimeError(f"Phase 1 不支持工具: {tool_name}")

        duration = time.perf_counter() - started_at
        graph_logger.info(
            "✅ 步骤 [%d] 完成: %s (%.2fs)",
            step.id,
            result_text[:300],
            duration,
        )
        return {
            "step_results": [
                StepResult(
                    step_id=step.id,
                    tool_name=tool_name,
                    status="done",
                    result=result_text,
                    duration_seconds=duration,
                    data=result_data,
                )
            ]
        }
    except ModelCallError:
        raise
    except Exception as exc:
        duration = time.perf_counter() - started_at
        error_text = str(exc)
        graph_logger.error(
            "❌ 步骤 [%d] 失败: %s (%.2fs)",
            step.id,
            error_text,
            duration,
        )
        return {
            "step_results": [
                StepResult(
                    step_id=step.id,
                    tool_name=tool_name,
                    status="failed",
                    result=f"步骤执行失败: {error_text}",
                    duration_seconds=duration,
                    error=error_text,
                )
            ]
        }


# ═══════════════════════════════════════════════════════════════════════════
# ▸ Phase 1 — Resource-aware scheduler
# ═══════════════════════════════════════════════════════════════════════════
def _task_artifact(
    *,
    artifact_id: str,
    kind: str,
    path: str | Path,
    task: TaskSpec,
    metadata: dict[str, Any] | None = None,
) -> ArtifactRef:
    return ArtifactRef(
        id=artifact_id,
        kind=kind,
        path=str(Path(path).resolve(strict=False)),
        producer_task_id=task.id,
        phase=task.phase,
        metadata=dict(metadata or {}),
    )


def _phase1_search_steps(state: AgentState) -> list[Step]:
    if state.plan is None:
        return []
    return [
        step
        for step in state.plan.steps
        if _normalize_tool_hint(step) == "search_bilibili_video"
    ]


def _run_phase1_search_and_rank(
    state: AgentState,
    scheduler: ResourceScheduler,
) -> list[dict[str, Any]]:
    search_steps = _phase1_search_steps(state)
    if not search_steps:
        return []
    counts = _recommend_material_counts(state.target_duration_seconds)
    tasks: list[TaskSpec] = []
    for step in search_steps:
        arguments = dict(step.arguments or {})
        arguments.setdefault("query", state.user_request)
        arguments.setdefault("max_results", counts["search_per_source"])
        arguments.setdefault("pages", counts["search_pages"])
        arguments.setdefault("max_total_results", counts["max_candidates"])
        arguments["request_concurrency"] = 1
        tasks.append(
            TaskSpec(
                id=f"phase1_r{state.gap_round}_search_{step.id}",
                phase="phase1",
                kind="material_search",
                tool_name="search_bilibili_video",
                description=step.description,
                arguments=arguments,
                resources={"search_pool": 1, "llm_pool": 1},
                retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
            )
        )

    rank_task_id = f"phase1_r{state.gap_round}_rank"
    tasks.append(
        TaskSpec(
            id=rank_task_id,
            phase="phase1",
            kind="candidate_ranking",
            tool_name="rank_video_candidates",
            description="聚合并排序全部搜索候选",
            depends_on=[task.id for task in tasks],
            arguments={
                "candidates_json": "[]",
                "top_k": _infer_download_top_k("", counts),
                "max_review": counts["mllm_review"],
                "selection_goal": state.user_request,
            },
            resources={"llm_pool": 1},
            conflict_keys=[f"write:{WORKSPACE / 'candidate_pool_snapshot.json'}"],
            retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
        )
    )
    plan = ExecutionPlan(
        plan_id=f"phase1_search_rank_round_{state.gap_round}",
        phase="phase1",
        goal=state.user_request,
        tasks=tasks,
    )

    def execute(task: TaskSpec, dependencies: dict[str, Any]) -> TaskExecutionResult:
        tool_obj = _TOOL_NAME_MAP.get(task.tool_name)
        if tool_obj is None:
            raise RuntimeError(f"Phase 1 工具未注册: {task.tool_name}")
        if task.tool_name == "search_bilibili_video":
            raw = tool_obj.invoke(task.arguments)
            parsed = json.loads(str(raw))
            if not isinstance(parsed, list) or not parsed:
                raise RuntimeError(f"搜索没有返回候选: {str(raw)[:300]}")
            return TaskExecutionResult(
                data={
                    "candidate_count": len(parsed),
                    "candidates": parsed,
                }
            )

        candidates = [
            candidate
            for dependency in dependencies.values()
            for candidate in dependency.result.get("candidates", [])
            if isinstance(candidate, dict)
        ]
        rank_arguments = dict(task.arguments)
        rank_arguments["candidates_json"] = json.dumps(candidates, ensure_ascii=False)
        raw = tool_obj.invoke(rank_arguments)
        parsed = json.loads(str(raw))
        selected = parsed.get("selected_videos", []) if isinstance(parsed, dict) else []
        if not selected:
            raise RuntimeError(f"候选排序没有选出素材: {str(raw)[:300]}")
        artifacts: list[ArtifactRef] = []
        snapshot = WORKSPACE / "candidate_pool_snapshot.json"
        if snapshot.exists():
            artifacts.append(
                _task_artifact(
                    artifact_id=f"{task.id}_candidate_manifest",
                    kind="candidate_manifest",
                    path=snapshot,
                    task=task,
                    metadata={"selected_count": len(selected)},
                )
            )
        return TaskExecutionResult(
            data={"selected_videos": selected, "ranking": parsed},
            artifacts=artifacts,
        )

    states = scheduler.run(plan, execute, resume=True)
    return list(states[rank_task_id].result.get("selected_videos", []))


def _run_phase1_downloads(
    state: AgentState,
    scheduler: ResourceScheduler,
    selected_videos: list[dict[str, Any]],
    *,
    start_index: int = 1,
) -> list[str]:
    if not selected_videos:
        return []
    tasks: list[TaskSpec] = []
    for index, video in enumerate(selected_videos, start=start_index):
        bvid = str(video.get("bvid") or "").strip()
        url = str(bvid or video.get("url") or "").strip()
        if not url:
            continue
        filename = f"selected_r{state.gap_round}_{index}_{(bvid[-6:] if bvid else index)}"
        output_path = WORKSPACE / f"{filename}.mp4"
        tasks.append(
            TaskSpec(
                id=f"phase1_r{state.gap_round}_download_{index}",
                phase="phase1",
                kind="material_download",
                tool_name="download_bilibili_video",
                description=str(video.get("title") or url),
                arguments={"url": url, "filename": filename, "prefer_h264": True},
                resources={"download_pool": 1},
                conflict_keys=[f"write:{output_path.resolve(strict=False)}"],
                output_kinds=["source_video"],
                retry=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            )
        )
    plan_id = (
        f"phase1_download_round_{state.gap_round}"
        if start_index == 1
        else f"phase1_download_round_{state.gap_round}_{start_index}"
    )
    plan = ExecutionPlan(
        plan_id=plan_id,
        phase="phase1",
        goal=state.user_request,
        tasks=tasks,
    )

    def execute(task: TaskSpec, dependencies: dict[str, Any]) -> TaskExecutionResult:
        raw = _TOOL_NAME_MAP["download_bilibili_video"].invoke(task.arguments)
        try:
            parsed = json.loads(str(raw))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"素材下载失败: {str(raw)[:500]}") from exc
        if parsed.get("status") != "success" or not parsed.get("path"):
            raise RuntimeError(f"素材下载失败: {str(raw)[:300]}")
        path = str(parsed["path"])
        return TaskExecutionResult(
            data={"path": path},
            artifacts=[
                _task_artifact(
                    artifact_id=f"{task.id}_video",
                    kind="source_video",
                    path=path,
                    task=task,
                    metadata={"source": task.description},
                )
            ],
        )

    states = scheduler.run(plan, execute, resume=True)
    return [
        str(state.result.get("path"))
        for state in states.values()
        if state.status == "completed" and state.result.get("path")
    ]


def _run_phase1_analyses(
    state: AgentState,
    scheduler: ResourceScheduler,
) -> list[str]:
    analysis_index = build_analysis_index([WORKSPACE, USER_WORKSPACE])
    source_videos = _iter_source_videos()
    pending = [
        path
        for path in source_videos
        if not match_analysis_files(path, analysis_index=analysis_index)
    ]
    if not pending:
        return [
            str(matches[0].resolve())
            for video_path in source_videos
            if (matches := match_analysis_files(video_path, analysis_index=analysis_index))
        ]

    tasks = [
        TaskSpec(
            id=f"phase1_analysis_{uuid.uuid5(uuid.NAMESPACE_URL, str(path.resolve())).hex[:12]}",
            phase="phase1",
            kind="video_analysis",
            tool_name="analyze_video",
            description=f"分析素材 {path.name}",
            arguments={
                "video_path": str(path.resolve()),
                "analysis_goal": state.user_request,
            },
            resources={"video_analysis_pool": 1, "ffmpeg_pool": 1},
            conflict_keys=[f"analysis:{path.resolve(strict=False)}"],
            output_kinds=["video_analysis"],
            retry=RetryPolicy(max_attempts=1),
        )
        for path in pending
    ]
    plan = ExecutionPlan(
        plan_id=f"phase1_analysis_round_{state.gap_round}",
        phase="phase1",
        goal=state.user_request,
        tasks=tasks,
    )

    def execute(task: TaskSpec, dependencies: dict[str, Any]) -> TaskExecutionResult:
        raw = str(_TOOL_NAME_MAP["analyze_video"].invoke(task.arguments))
        video_path = Path(str(task.arguments["video_path"]))
        index = build_analysis_index([WORKSPACE, USER_WORKSPACE])
        matches = match_analysis_files(video_path, analysis_index=index)
        if not matches:
            raise RuntimeError(f"视频分析未生成 JSON: {raw[:500]}")
        analysis_path = matches[0]
        return TaskExecutionResult(
            data={"analysis_path": str(analysis_path.resolve())},
            artifacts=[
                _task_artifact(
                    artifact_id=f"{task.id}_json",
                    kind="video_analysis",
                    path=analysis_path,
                    task=task,
                    metadata={"source_video": str(video_path.resolve())},
                )
            ],
        )

    states = scheduler.run(
        plan,
        execute,
        resume=True,
        allow_partial_failure=True,
    )
    refreshed_index = build_analysis_index([WORKSPACE, USER_WORKSPACE])
    completed_paths = [
        str(matches[0].resolve())
        for video_path in source_videos
        if (matches := match_analysis_files(video_path, analysis_index=refreshed_index))
    ]
    required_successes = max(1, math.ceil(len(source_videos) * 2 / 3))
    failed_states = [item for item in states.values() if item.status == "failed"]
    if len(completed_paths) < required_successes:
        details = "; ".join(item.error[:240] for item in failed_states)
        raise RuntimeError(
            "素材分析成功数不足，无法安全进入后续剪辑阶段："
            f"成功 {len(completed_paths)}/{len(source_videos)}，"
            f"最低要求 {required_successes}。失败详情：{details or 'unknown'}"
        )
    if failed_states:
        graph_logger.warning(
            "⚠️ 素材分析部分失败，按降级策略继续：成功 %d/%d，失败 %d，最低要求 %d",
            len(completed_paths),
            len(source_videos),
            len(failed_states),
            required_successes,
        )
        _emit_orchestration_event(
            "analysis_batch_degraded",
            {
                "source_count": len(source_videos),
                "analyzed_count": len(completed_paths),
                "failed_count": len(failed_states),
                "required_successes": required_successes,
                "failed_tasks": [
                    {"task_id": item.task_id, "error": item.error[:500]}
                    for item in failed_states
                ],
            },
        )
    return completed_paths


def phase1_scheduler_node(state: AgentState) -> dict[str, Any]:
    """将 Planner DAG 转换为资源感知任务，并完成素材准备。"""
    graph_logger.info("⚙️ Phase 1 — Resource Scheduler 开始")
    registry = _artifact_registry()
    scheduler = _resource_scheduler(registry)
    source_before = _iter_source_videos()

    local_only = (
        PREFER_LOCAL_MATERIALS
        and state.gap_round == 0
        and bool(source_before)
    )
    selected: list[dict[str, Any]] = []
    downloaded: list[str] = []
    if not DIRECT_PHASE3_EXECUTION and not local_only:
        selected = _run_phase1_search_and_rank(state, scheduler)
        if SHORT_FORM_OPTIMIZATIONS and 0 < state.target_duration_seconds <= 20:
            max_sources = max(1, min(SHORT_FORM_MAX_SOURCES, len(selected)))
            analyses: list[str] = []
            for offset, video in enumerate(selected[:max_sources], start=1):
                downloaded.extend(
                    _run_phase1_downloads(
                        state,
                        scheduler,
                        [video],
                        start_index=offset,
                    )
                )
                analyses = _run_phase1_analyses(state, scheduler)
                metrics = _material_gap_metrics(state)
                if (
                    metrics["analyzed_count"] >= 1
                    and metrics["usable_seconds"] >= metrics["target_seconds"] * 2
                    and metrics["topic_coverage_ratio"] >= 0.15
                    and metrics["orientation_match_ratio"] >= 0.5
                ):
                    graph_logger.info(
                        "✅ 短片素材早停: analyzed=%s usable=%.1fs target=%.1fs",
                        metrics["analyzed_count"],
                        metrics["usable_seconds"],
                        metrics["target_seconds"],
                    )
                    _emit_orchestration_event(
                        "short_form_material_early_stop",
                        {
                            "source_downloaded_count": len(downloaded),
                            "analysis_count": len(analyses),
                            **metrics,
                        },
                    )
                    break
        else:
            downloaded = _run_phase1_downloads(state, scheduler, selected)
            analyses = _run_phase1_analyses(state, scheduler)
    else:
        analyses = _run_phase1_analyses(state, scheduler)

    summary = (
        f"资源调度完成：候选 {len(selected)}，新增下载 {len(downloaded)}，"
        f"分析文件 {len(analyses)}。"
    )
    graph_logger.info("✅ %s", summary)
    return {
        "phase": "material_gap",
        "step_results": [
            StepResult(
                step_id=10000 + state.gap_round,
                tool_name="resource_scheduler",
                status="done",
                result=summary,
                data={
                    "selected_count": len(selected),
                    "downloaded_paths": downloaded,
                    "analysis_paths": analyses,
                },
            )
        ],
    }


def _material_gap_metrics(state: AgentState) -> dict[str, Any]:
    source_videos = _iter_source_videos()
    analysis_index = build_analysis_index([WORKSPACE, USER_WORKSPACE])
    analyzed = [
        video
        for video in source_videos
        if match_analysis_files(video, analysis_index=analysis_index)
    ]
    usable_seconds = 0.0
    topic_text_parts: list[str] = []
    portrait = 0
    landscape = 0
    for video in analyzed:
        try:
            import cv2

            capture = cv2.VideoCapture(str(video))
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            capture.release()
            if height > width:
                portrait += 1
            elif width > 0 and height > 0:
                landscape += 1
        except Exception:
            pass
        matches = match_analysis_files(video, analysis_index=analysis_index)
        if not matches:
            continue
        try:
            payload = json.loads(matches[0].read_text(encoding="utf-8"))
        except Exception:
            continue
        topic_text_parts.append(str(payload.get("analysis_text", "")))
        segments = payload.get("semantic_segments") or payload.get("segments") or []
        if isinstance(segments, list):
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                try:
                    usable_seconds += max(
                        0.0,
                        float(segment.get("end", 0)) - float(segment.get("start", 0)),
                    )
                except Exception:
                    continue

    request_tokens = set(re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]", state.user_request.lower()))
    material_tokens = set(
        re.findall(
            r"[a-zA-Z0-9]+|[\u4e00-\u9fff]",
            "\n".join(topic_text_parts).lower(),
        )
    )
    topic_coverage = (
        len(request_tokens & material_tokens) / max(1, len(request_tokens))
        if request_tokens
        else 1.0
    )
    wants_portrait = bool(re.search(r"竖屏|vertical|9\s*:\s*16", state.user_request, re.I))
    orientation_matches = portrait if wants_portrait else landscape
    orientation_ratio = orientation_matches / max(1, len(analyzed))
    target = max(1.0, state.target_duration_seconds or 300.0)
    required_sources = 1 if target <= 15 else 2
    return {
        "source_count": len(source_videos),
        "analyzed_count": len(analyzed),
        "analysis_complete_ratio": len(analyzed) / max(1, len(source_videos)),
        "usable_seconds": round(usable_seconds, 2),
        "target_seconds": round(target, 2),
        "duration_coverage_ratio": round(usable_seconds / target, 3),
        "topic_coverage_ratio": round(topic_coverage, 3),
        "orientation_match_ratio": round(orientation_ratio, 3),
        "required_sources": required_sources,
        "requested_orientation": "portrait" if wants_portrait else "landscape",
    }


def material_gap_evaluator_node(state: AgentState) -> dict[str, Any]:
    """评估素材是否足以进入编辑研究，并给出补充或失败决策。"""
    graph_logger.info("🧪 Material Gap Evaluator 开始")
    registry = _artifact_registry()
    scheduler = _resource_scheduler(registry)
    metrics = _material_gap_metrics(state)
    task = TaskSpec(
        id=f"material_gap_round_{state.gap_round}",
        phase="material_gap",
        kind="material_gap_evaluation",
        description="评估素材主题、时长、画幅和叙事覆盖",
        arguments={"metrics": metrics, "user_request": state.user_request},
        resources={"llm_pool": 1},
        output_kinds=["material_gap_report"],
        retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
    )
    plan = ExecutionPlan(
        plan_id=f"material_gap_round_{state.gap_round}",
        phase="material_gap",
        goal=state.user_request,
        tasks=[task],
    )

    def execute(task_spec: TaskSpec, dependencies: dict[str, Any]) -> TaskExecutionResult:
        deterministic_sufficient = (
            metrics["source_count"] >= metrics["required_sources"]
            and metrics["analysis_complete_ratio"] >= 2 / 3
            and metrics["duration_coverage_ratio"] >= 1.0
            and metrics["topic_coverage_ratio"] >= 0.15
            and metrics["orientation_match_ratio"] >= 0.5
        )
        prompt = (
            "你是视频素材缺口评估器。基于用户目标和确定性指标判断素材是否足够支撑完整叙事。"
            "只返回 JSON："
            '{"decision":"proceed|supplement|fail","score":0-100,'
            '"gaps":["..."],"supplement_queries":["..."],"reason":"..."}。'
            "只有完全没有可用分析素材时才返回 fail；其余不足返回 supplement。"
        )
        response = _invoke_llm(
            _get_llm(temperature=0.0).bind(max_tokens=800),
            [
                SystemMessage(content=prompt),
                HumanMessage(
                    content=json.dumps(
                        {
                            "user_request": state.user_request,
                            "metrics": metrics,
                            "deterministic_sufficient": deterministic_sufficient,
                        },
                        ensure_ascii=False,
                    )
                ),
            ],
            "material_gap_evaluator",
        )
        try:
            report = _parse_json_object(str(response.content))
        except Exception:
            report = {}
        decision = str(report.get("decision") or "").lower()
        if metrics["analyzed_count"] == 0:
            decision = "fail"
        elif deterministic_sufficient and decision != "fail":
            decision = "proceed"
        elif decision not in {"proceed", "supplement", "fail"}:
            decision = "supplement"
        if DIRECT_PHASE3_EXECUTION and metrics["analyzed_count"] > 0:
            decision = "proceed"
        if state.gap_round >= 2 and decision == "supplement":
            decision = "proceed" if metrics["analyzed_count"] > 0 else "fail"
        report.update(
            {
                "decision": decision,
                "metrics": metrics,
                "round": state.gap_round,
                "evaluated_at": time.time(),
            }
        )
        report_path = WORKSPACE / f"material_gap_report_round_{state.gap_round}.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return TaskExecutionResult(
            data={"report": report},
            artifacts=[
                _task_artifact(
                    artifact_id=f"material_gap_report_{state.gap_round}",
                    kind="material_gap_report",
                    path=report_path,
                    task=task_spec,
                    metadata={"decision": decision},
                )
            ],
        )

    states = scheduler.run(plan, execute, resume=True)
    report = dict(states[task.id].result.get("report", {}))
    decision = str(report.get("decision") or "fail")
    _emit_orchestration_event(
        "evaluator_decision",
        {"phase": "material_gap", "decision": decision, "report": report},
    )
    graph_logger.info("🧪 Material Gap 决策: %s", decision)
    if decision == "fail":
        raise RuntimeError(f"素材缺口评估失败: {report.get('reason') or report}")
    if decision == "supplement":
        return {
            "phase": "supplement",
            "material_gap_report": report,
            "gap_round": state.gap_round + 1,
        }
    next_phase = "react" if DIRECT_PHASE3_EXECUTION or not ENABLE_PHASE2_RESEARCH else "researching"
    return {
        "phase": next_phase,
        "material_gap_report": report,
    }


# ═══════════════════════════════════════════════════════════════════════════
# ▸ Phase 1 → Phase 3 路由: Prep Router
# ═══════════════════════════════════════════════════════════════════════════
def prep_router_node(state: AgentState) -> dict[str, Any]:
    """汇总一轮并行结果，并决定继续调度或进入后续阶段。"""
    plan = state.plan
    result_map = _step_result_map(state)

    if (
        PREFER_LOCAL_MATERIALS
        and plan is not None
        and plan.steps
        and plan.steps[0].id in result_map
        and _normalize_tool_hint(plan.steps[0]) == "analyze_video"
    ):
        remaining_remote_steps = [
            step
            for step in plan.steps
            if step.id not in result_map
            and _normalize_tool_hint(step) in REMOTE_PREP_TOOL_NAMES
        ]
        if remaining_remote_steps:
            sufficient, reason = _assess_local_material_sufficiency(state)
            if sufficient:
                next_phase = "researching" if ENABLE_PHASE2_RESEARCH else "react"
                graph_logger.info("🏠 本地素材已满足需求，跳过联网补充步骤: %s", reason)
                return {
                    "phase": next_phase,
                    "current_step_index": len(plan.steps),
                    "prep_round": state.prep_round + 1,
                }
            graph_logger.info("🌐 本地素材暂不足，继续联网补充: %s", reason)

    if plan:
        blocking_failures = [
            result
            for result in result_map.values()
            if result.status == "failed"
            and result.tool_name != "search_bilibili_video"
        ]
        if blocking_failures:
            detail = "; ".join(
                f"步骤 {result.step_id}: {result.error or result.result}"
                for result in blocking_failures
            )
            raise RuntimeError(f"Phase 1 关键步骤失败: {detail}")

        unresolved_steps = [
            step for step in plan.steps if step.id not in result_map
        ]
        if unresolved_steps:
            unresolved_ids = [step.id for step in unresolved_steps]
            graph_logger.info(
                "📌 Prep Router: 已完成 %d/%d，待调度=%s",
                len(result_map),
                len(plan.steps),
                unresolved_ids,
            )
            return {
                "current_step_index": len(result_map),
                "prep_round": state.prep_round + 1,
            }

        successful_searches = [
            result
            for result in result_map.values()
            if result.tool_name == "search_bilibili_video"
            and result.status == "done"
        ]
        failed_searches = [
            result
            for result in result_map.values()
            if result.tool_name == "search_bilibili_video"
            and result.status == "failed"
        ]
        if failed_searches and not successful_searches:
            detail = "; ".join(
                result.error or result.result for result in failed_searches
            )
            raise RuntimeError(f"全部素材搜索分支失败: {detail}")

    if plan and len(result_map) < len(plan.steps):
        graph_logger.info(
            "📌 Prep Router: 已完成 %d/%d，继续 Phase 1",
            len(result_map),
            len(plan.steps),
        )
        return {}

    # ── 所有准备步骤已执行完 → 检查分析 JSON ──
    analysis_files = _iter_analysis_json_files()
    source_videos = [
        fp
        for fp in _iter_source_videos()
    ]

    analysis_index = build_analysis_index([WORKSPACE, USER_WORKSPACE])
    missing_analysis = [
        fp.name
        for fp in source_videos
        if not match_analysis_files(fp, analysis_index=analysis_index)
    ]

    if analysis_files and not missing_analysis:
        if DIRECT_PHASE3_EXECUTION:
            graph_logger.info(
                "✅ 直达 Phase 3: %d 个分析文件, %d 个源视频 → 直接进入 Phase 3",
                len(analysis_files),
                len(source_videos),
            )
            return {"phase": "react"}
        if not ENABLE_PHASE2_RESEARCH:
            graph_logger.info(
                "✅ Phase 1 完成: %d 个分析文件, %d 个源视频 → Phase 2 已禁用，直接进入 Phase 3",
                len(analysis_files),
                len(source_videos),
            )
            return {"phase": "react"}
        graph_logger.info(
            "✅ Phase 1 完成: %d 个分析文件, %d 个源视频 → 进入 Phase 2 深度剪辑研究",
            len(analysis_files),
            len(source_videos),
        )
        return {"phase": "researching"}

    if missing_analysis:
        raise RuntimeError(
            "素材准备未完成：以下源视频没有生成分析结果："
            f"{', '.join(missing_analysis[:8])}。"
            "任务已停止，不会重新搜索或重复分析。请检查视频模型权限、API Key 或网络连接后重试。"
        )

    raise RuntimeError(
        "素材准备完成后没有生成任何可用的分析数据。"
        "任务已停止，不会重新进入 Planner。请检查素材下载结果和多模态模型配置后重试。"
    )


def _build_analysis_failure_message(result: str) -> str:
    text = str(result or "")
    lowered = text.lower()
    if "403" in lowered or "access denied" in lowered:
        return (
            "视频多模态分析失败：DashScope 返回 403 Access denied。"
            "当前 API Key 或账号没有所配置视频模型的调用权限。"
            "任务已停止，不会重新搜索素材或重复分析。"
        )
    if "10054" in lowered or "connectionreseterror" in lowered:
        return (
            "视频多模态分析失败：远程服务连接被重置。"
            "任务已停止，不会重新搜索素材或重复分析；请检查网络和视频模型服务后重试。"
        )
    return (
        "视频多模态分析没有生成任何分析文件。"
        "任务已停止，不会重新搜索素材或重复分析。"
        f"最后结果：{text[:300]}"
    )


def route_after_prep_router(
    state: AgentState,
) -> Any:
    """Prep Router 之后的路由。"""
    if state.phase == "researching":
        return "editing_research"
    if state.phase == "react":
        return "generate_editing_plan" if ENABLE_PLAN_REVIEW else "react_editor"
    if state.phase == "replan":
        return "planner"
    if state.plan:
        return _ready_prep_steps(state)
    # 兜底: 重新规划
    return "planner"


def route_after_material_gap(state: AgentState) -> str:
    if state.phase == "supplement":
        return "planner"
    if state.phase == "researching":
        return "editing_research"
    if state.phase == "react":
        return "react_editor"
    raise RuntimeError(f"Material Gap 返回未知阶段: {state.phase}")


def _ready_prep_steps(state: AgentState) -> list[Send]:
    """选择当前所有 ready steps，并按全局并发上限生成 Send。"""
    if state.plan is None:
        raise RuntimeError("Phase 1 调度缺少计划")

    result_map = _step_result_map(state)
    pending_steps = [
        step for step in state.plan.steps if step.id not in result_map
    ]
    ready_steps: list[Step] = []
    for step in pending_steps:
        waiting_on = [
            dependency
            for dependency in step.depends_on
            if dependency not in result_map
        ]
        if waiting_on:
            graph_logger.info(
                "⏸️ DAG 等待步骤 [%d]: depends_on=%s",
                step.id,
                waiting_on,
            )
            continue
        ready_steps.append(step)

    if not ready_steps:
        pending_ids = [step.id for step in pending_steps]
        raise RuntimeError(f"Phase 1 DAG 无可调度步骤，待处理: {pending_ids}")

    selected: list[Step] = []
    selected_resource_tools: set[str] = set()
    for step in ready_steps:
        if len(selected) >= max(1, SEARCH_POOL_SIZE):
            break
        tool_name = _normalize_tool_hint(step)
        if tool_name in {
            "rank_video_candidates",
            "download_bilibili_video",
            "analyze_video",
        }:
            if tool_name in selected_resource_tools:
                continue
            selected_resource_tools.add(tool_name)
        selected.append(step)

    step_indexes = {
        step.id: index for index, step in enumerate(state.plan.steps)
    }
    sends: list[Send] = []
    for step in selected:
        tool_name = _normalize_tool_hint(step)
        graph_logger.info(
            "🚀 DAG 调度步骤 [%d]: tool=%s, depends_on=%s",
            step.id,
            tool_name,
            step.depends_on,
        )
        sends.append(
            Send(
                "executor",
                {
                    "user_request": state.user_request,
                    "plan": state.plan.model_dump(),
                    "current_step_index": step_indexes[step.id],
                    "active_step_id": step.id,
                    "prep_round": state.prep_round,
                    "step_results": [
                        result.model_dump()
                        if isinstance(result, StepResult)
                        else result
                        for result in state.step_results
                    ],
                    "phase": "preparing",
                    "target_duration_seconds": state.target_duration_seconds,
                },
            )
        )
    return sends


# ═══════════════════════════════════════════════════════════════════════════
# ▸ Phase 2 — Editing Research (深度剪辑研究)
# ═══════════════════════════════════════════════════════════════════════════
EDITING_RESEARCH_PROMPT = """\
你是一位顶级视频剪辑研究员和叙事设计专家。

你的任务是**深度研究**所有素材视频的分析数据，结合用户需求，
制定一份详细的「剪辑蓝图」，指导后续的剪辑执行。

⚠️ 你在这个阶段**不需要调用任何工具**，只需要深度思考和输出文字方案。

═════════════════════════════════════════
  第一阶段: 素材深度理解
═════════════════════════════════════════

请对每个源视频的分析数据进行逐段精读，回答：

1. **内容图谱**: 每个视频覆盖了哪些主题/场景/人物？
   - 列出每段的核心内容标签（如：风景、人物采访、动作场面、数据展示…）
   - 标注每段的信息密度（高/中/低）

2. **情绪光谱**: 每个片段传达什么情绪？
   - 标注：激昂/温暖/紧张/幽默/沉稳/震撼/悲伤/神秘…
   - 识别视频内情绪转折点

3. **视觉特征**: 每段的画面特点
   - 镜头类型（远景/中景/近景/特写/航拍/运动镜头…）
   - 色调/光线（明亮/暗沉/暖色/冷色…）
   - 画面运动感（静态/缓慢推移/快速运动…）

4. **音频特征**: 每段的声音元素
   - 人声（对白/旁白/采访）/ 音乐 / 环境音 / 无声
   - 哪些段落有可用的原声？哪些需要后期配音？

═════════════════════════════════════════
  第二阶段: 跨视频关联分析
═════════════════════════════════════════

不同视频之间的片段如何关联？

1. **主题呼应**: 哪些不同视频的片段可以围绕同一主题组合？
2. **视觉连续性**: 哪些片段在画面风格上可以自然衔接？
   - 色调相近的片段对
   - 镜头运动方向匹配的片段对
   - 场景逻辑连贯的片段对（如：俯瞰→近景过渡）
3. **情绪曲线设计**: 整体情绪如何起承转合？
   - 开场应选择什么情绪？（抓人眼球 vs 循序渐进）
   - 高潮段落在哪里？
   - 收尾用什么情绪落点？
4. **节奏规划**: 快-慢-快的节奏交替如何安排？
   - 每段建议时长
   - 信息密度的疏密有致

═════════════════════════════════════════
  第三阶段: 输出剪辑蓝图
═════════════════════════════════════════

请输出一份结构化的剪辑蓝图，包含以下部分：

### 1. 叙事结构选择
说明你选择的叙事框架（时间线/对比/问题→解答/情感递进/总分总/倒叙…），
以及为什么这种结构最适合用户需求。

### 2. 片段选择与排序
按预期播放顺序列出每个片段：
```
序号 | 源视频 | 时间段 | 内容摘要 | 选择理由 | 建议时长 | 情绪标签
```

并额外输出一列（供执行阶段检索使用）：
```
召回查询词 | 该步希望召回的语义特征（人物/场景/动作/情绪/镜头）
```

### 3. 转场衔接设计
对每个相邻片段之间的转场进行设计：
```
片段A → 片段B:
  - 衔接逻辑: 为什么A后面接B是自然的？（内容/视觉/情绪的关联）
  - 推荐转场类型: crossfade / fade_through_black / cut（硬切）
  - 转场时长建议
```

### 4. 节奏与时长规划
- 总时长目标: X秒
- 节奏曲线: 描述整体快慢变化
- 每段时长分配

### 5. 旁白/解说策略（分段配音规划）
- 为每个片段或片段组设计旁白文案
- 明确每段旁白的起止时间（基于合并后的时间轴）
- 标注哪些段落刻意留白（让画面自己说话）
- 旁白的语气和风格
- 场景转折处的旁白过渡设计
- 输出格式示例:
  | 段落 | 时间段 | 旁白文案 | 备注 |
  |------|--------|----------|------|
  | 片段1-2 | 0s-10s | "这座百年学府..." | 开场悬念 |
  | 片段3 | 10s-15s | （留白） | 航拍画面 |
  | 片段4-5 | 15s-25s | "走进校园..." | 转折到介绍 |

### 6. 吸引力优化策略
- **开场钩子**: 为什么选择这个片段开场？它能在3秒内抓住观众吗？
- **信息递进**: 观众为什么会继续看下去？
- **高潮设计**: 最精彩/最有冲击力的部分在哪里？
- **结尾印象**: 结尾留给观众什么记忆点？

═════════════════════════════════════════
  关键原则
═════════════════════════════════════════
- **当前任务优先**: 用户需求与当前素材分析永远高于历史案例 memory；严禁把历史案例里的题材、关键词、风格、节奏或时长结构迁移成当前目标
- **自然流畅优先**: 片段之间的衔接必须有逻辑关联，避免生硬跳跃
- **多源混剪**: 从不同视频取材，避免长时间只用一个源
- **情绪连贯**: 相邻片段的情绪过渡要平滑，除非刻意制造反差
- **视觉匹配**: 相邻片段的色调、镜头风格尽量协调
- **时长精准**: 每段时长建议要务实，基于分析数据中的实际可用时长
- **严禁编造**: 所有时间段必须来自分析数据中的真实时间范围
"""


def _legacy_editing_research_node(state: AgentState) -> dict[str, Any]:
    """Phase 2 Editing Research: 深度分析素材，生成剪辑蓝图。

    这是一个纯推理节点，不调用任何工具。
    LLM 深度研究所有视频分析数据，输出结构化的剪辑策略。
    """
    graph_logger.info("🔬 ═══ Phase 2 开始: 深度剪辑研究 ═══")

    # ── 构建完整上下文 ──
    analysis_context = _build_full_analysis_context()
    workspace_snapshot = _build_workspace_snapshot()
    user_workspace_snapshot = _build_user_workspace_snapshot()
    memory_experience_text = _load_latest_memory_experience(max_chars=12000)

    user_parts: list[str] = [
        f"## 用户需求\n{state.user_request}",
    ]
    if state.target_duration_seconds > 0:
        user_parts.append(
            f"\n## 目标时长\n{state.target_duration_seconds:.1f} 秒"
        )

    # Phase 1 准备结果摘要
    if state.step_results:
        prep_summary = "\n## Phase 1 素材准备摘要"
        recent = state.step_results[-5:]
        start_i = len(state.step_results) - len(recent)
        for i, r in enumerate(recent, start=start_i + 1):
            prep_summary += f"\n步骤 {i}: {_step_result_text(r)[:600]}"
        user_parts.append(prep_summary)

    user_parts.extend([
        f"\n## 所有视频的详细分析数据\n"
        f"请逐段精读以下所有素材分析，这是你制定剪辑蓝图的唯一信息来源:\n\n{analysis_context}",
        f"\n## 当前工作目录文件\n{workspace_snapshot}",
        f"\n## 用户素材目录文件（可作为补充素材来源）\n{user_workspace_snapshot}",
        f"\n## 历史案例经验（仅供参考，不能覆盖当前任务目标）\n{memory_experience_text}",
        "\n## 开始深度研究\n"
        "请严格按照研究框架，逐阶段输出你的分析和剪辑蓝图。"
        "记住：你现在只需要深度思考，不需要执行任何工具操作。",
    ])

    user_message = "\n".join(user_parts)

    # ── 调用 LLM (纯推理，无工具) ──
    llm = _get_llm(temperature=0.4)  # 稍高温度鼓励创造性思考

    graph_logger.info("📝 分析上下文长度: %d 字", len(analysis_context))
    graph_logger.info("📝 总提示长度: %d 字", len(user_message) + len(EDITING_RESEARCH_PROMPT))

    try:
        response = _invoke_llm(
            llm,
            [
                SystemMessage(content=EDITING_RESEARCH_PROMPT),
                HumanMessage(content=user_message),
            ],
            "phase2_research",
        )
        blueprint = str(response.content).strip()
    except ModelCallError:
        raise
    except Exception as e:
        if fail_fast_model_errors():
            raise_model_failure(
                stage="phase2_research",
                model=MODEL_NAME,
                message=e,
            )
        blueprint = ""
        graph_logger.error("❌ 剪辑研究异常: %s", e, exc_info=True)

    if not blueprint:
        graph_logger.warning("⚠️ 剪辑研究未产出蓝图，Phase 3 将自行决策")
        blueprint = "(剪辑研究未能产出蓝图，请自行根据分析数据制定剪辑方案)"
    else:
        graph_logger.info("🔬 剪辑蓝图生成完成 (%d 字)", len(blueprint))
        graph_logger.info("📝 蓝图摘要: %s", blueprint[:500])

    return {
        "editing_blueprint": blueprint,
        "phase": "react",
    }


def _compact_editing_research_node(state: AgentState) -> dict[str, Any]:
    """短片专用蓝图生成：单个 LLM 任务，减少多 Agent 排队和整合成本。"""
    graph_logger.info("🔬 Phase 2 使用短片 compact blueprint")
    registry = _artifact_registry()
    scheduler = _resource_scheduler(registry)
    analysis_files = _iter_analysis_json_files()
    task = TaskSpec(
        id="phase2_compact_blueprint",
        phase="phase2",
        kind="compact_blueprint",
        description="为短片生成单次可执行剪辑蓝图",
        arguments={
            "target_duration_seconds": state.target_duration_seconds,
            "analysis_count": len(analysis_files),
        },
        resources={"llm_pool": 1},
        output_kinds=["editing_blueprint", "editing_blueprint_json"],
        retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
    )
    plan = ExecutionPlan(
        plan_id="phase2_compact_blueprint",
        phase="phase2",
        goal=state.user_request,
        tasks=[task],
    )

    def execute(task_spec: TaskSpec, dependencies: dict[str, Any]) -> TaskExecutionResult:
        analysis_context = _build_full_analysis_context()
        prompt = (
            "你是短片剪辑蓝图生成器。只基于素材分析生成一份可执行蓝图，"
            "包含源视频路径、真实裁剪时间段、镜头顺序、字幕/旁白时间线、"
            "输出分辨率建议和总时长校验。不得调用工具，不得编造不存在的时间段。"
        )
        response = _invoke_llm(
            _get_llm(temperature=0.2).bind(max_tokens=3600),
            [
                SystemMessage(content=prompt),
                HumanMessage(
                    content=(
                        f"用户需求:\n{state.user_request}\n"
                        f"目标时长: {state.target_duration_seconds:.1f}s\n\n"
                        f"素材分析:\n{analysis_context[:50000]}"
                    )
                ),
            ],
            "phase2_compact_blueprint",
        )
        blueprint = str(response.content).strip()
        if not blueprint:
            raise RuntimeError("短片 compact blueprint 返回空结果")
        markdown_path = WORKSPACE / "editing_blueprint.md"
        json_path = WORKSPACE / "editing_blueprint.json"
        markdown_path.write_text(blueprint, encoding="utf-8")
        json_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "mode": "compact_short_form",
                    "user_request": state.user_request,
                    "target_duration_seconds": state.target_duration_seconds,
                    "blueprint_markdown": blueprint,
                    "source_analysis_paths": [str(path.resolve()) for path in analysis_files],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return TaskExecutionResult(
            data={"content": blueprint, "path": str(markdown_path)},
            artifacts=[
                _task_artifact(
                    artifact_id="editing_blueprint_markdown",
                    kind="editing_blueprint",
                    path=markdown_path,
                    task=task_spec,
                ),
                _task_artifact(
                    artifact_id="editing_blueprint_json",
                    kind="editing_blueprint_json",
                    path=json_path,
                    task=task_spec,
                ),
            ],
        )

    try:
        states = scheduler.run(plan, execute, resume=True)
        blueprint = str(states[task.id].result.get("content", "")).strip()
        return {
            "editing_blueprint": blueprint,
            "phase2_artifact_ids": states[task.id].artifact_ids,
            "phase": "react",
        }
    except ModelCallError:
        raise
    except Exception as exc:
        graph_logger.warning("短片 compact blueprint 失败，回退完整 Phase 2: %s", exc)
        _emit_orchestration_event(
            "phase2_compact_fallback",
            {"reason": str(exc)[:500]},
        )
        return _legacy_editing_research_node(state)


def editing_research_node(state: AgentState) -> dict[str, Any]:
    """并行研究每个素材，再由唯一 Integrator 生成最终蓝图。"""
    graph_logger.info("🔬 ═══ Phase 2 开始: 并行剪辑研究 ═══")
    analysis_files = _iter_analysis_json_files()
    if not analysis_files:
        return _legacy_editing_research_node(state)
    if SHORT_FORM_OPTIMIZATIONS and 0 < state.target_duration_seconds <= 20:
        return _compact_editing_research_node(state)

    registry = _artifact_registry()
    scheduler = _resource_scheduler(registry)
    source_tasks: list[TaskSpec] = []
    for path in analysis_files:
        source_id = uuid.uuid5(uuid.NAMESPACE_URL, str(path.resolve())).hex[:12]
        source_tasks.append(
            TaskSpec(
                id=f"phase2_source_{source_id}",
                phase="phase2",
                kind="source_research",
                description=f"研究素材 {path.name}",
                arguments={
                    "analysis_path": str(path.resolve()),
                    "analysis_size": path.stat().st_size,
                    "analysis_mtime_ns": path.stat().st_mtime_ns,
                },
                resources={"llm_pool": 1},
                output_kinds=["source_research"],
                retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
            )
        )

    source_ids = [task.id for task in source_tasks]
    topic_prompts = {
        "narrative": "设计叙事结构、开场钩子、高潮和结尾记忆点。",
        "visual": "分析跨素材视觉连续性、镜头顺序、色调和转场逻辑。",
        "pacing": "设计目标时长内的片段分配、信息密度和节奏曲线。",
        "narration": "设计严格贴合画面的分段旁白、留白和字幕策略。",
    }
    topic_tasks = [
        TaskSpec(
            id=f"phase2_topic_{name}",
            phase="phase2",
            kind="topic_research",
            description=instruction,
            arguments={"topic": name, "instruction": instruction},
            depends_on=source_ids,
            resources={"llm_pool": 1},
            output_kinds=["topic_research"],
            retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
        )
        for name, instruction in topic_prompts.items()
    ]
    integrator_id = "phase2_blueprint_integrator"
    integrator_task = TaskSpec(
        id=integrator_id,
        phase="phase2",
        kind="blueprint_integrator",
        description="整合全部素材研究和专题策略",
        depends_on=[task.id for task in topic_tasks],
        resources={"llm_pool": 1},
        output_kinds=["editing_blueprint", "editing_blueprint_json"],
        retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
    )
    plan = ExecutionPlan(
        plan_id="phase2_parallel_research",
        phase="phase2",
        goal=state.user_request,
        tasks=[*source_tasks, *topic_tasks, integrator_task],
    )

    def execute(task: TaskSpec, dependencies: dict[str, Any]) -> TaskExecutionResult:
        if task.kind == "source_research":
            analysis_path = Path(str(task.arguments["analysis_path"]))
            payload = json.loads(analysis_path.read_text(encoding="utf-8"))
            prompt = (
                "你是视频素材研究员。只基于输入分析数据，输出结构化素材卡片："
                "主题与场景、可用真实时间段、情绪、镜头、音频、叙事用途、风险。"
                "严禁编造不存在的时间段。"
            )
            response = _invoke_llm(
                _get_llm(temperature=0.2).bind(max_tokens=2200),
                [
                    SystemMessage(content=prompt),
                    HumanMessage(
                        content=json.dumps(
                            {
                                "user_request": state.user_request,
                                "target_duration_seconds": state.target_duration_seconds,
                                "analysis": payload,
                            },
                            ensure_ascii=False,
                        )[:50000]
                    ),
                ],
                "phase2_source_research",
            )
            content = str(response.content).strip()
            output_path = WORKSPACE / f"{task.id}.md"
            output_path.write_text(content, encoding="utf-8")
            return TaskExecutionResult(
                data={"content": content, "path": str(output_path)},
                artifacts=[
                    _task_artifact(
                        artifact_id=f"{task.id}_artifact",
                        kind="source_research",
                        path=output_path,
                        task=task,
                        metadata={"analysis_path": str(analysis_path)},
                    )
                ],
            )

        dependency_content = "\n\n".join(
            str(item.result.get("content", "")) for item in dependencies.values()
        )
        if task.kind == "topic_research":
            prompt = (
                "你是剪辑策略专家。根据全部素材卡片和当前用户需求完成指定专题研究。"
                "所有片段时间必须来自素材卡片，不得调用工具，不得编造事实。"
            )
            response = _invoke_llm(
                _get_llm(temperature=0.3).bind(max_tokens=2400),
                [
                    SystemMessage(content=prompt),
                    HumanMessage(
                        content=(
                            f"用户需求:\n{state.user_request}\n"
                            f"目标时长: {state.target_duration_seconds:.1f}s\n"
                            f"专题任务: {task.description}\n\n"
                            f"素材卡片:\n{dependency_content[:60000]}"
                        )
                    ),
                ],
                f"phase2_topic_{task.arguments.get('topic')}",
            )
            content = str(response.content).strip()
            output_path = WORKSPACE / f"{task.id}.md"
            output_path.write_text(content, encoding="utf-8")
            return TaskExecutionResult(
                data={"content": content, "path": str(output_path)},
                artifacts=[
                    _task_artifact(
                        artifact_id=f"{task.id}_artifact",
                        kind="topic_research",
                        path=output_path,
                        task=task,
                    )
                ],
            )

        analysis_context = _build_full_analysis_context()
        prompt = (
            EDITING_RESEARCH_PROMPT
            + "\n\n你是唯一 Blueprint Integrator。整合专题结果，消除矛盾，"
            "输出最终可执行蓝图。必须包含真实源视频路径、真实时间段、排序、"
            "转场、总时长分配和分段旁白时间线。"
        )
        response = _invoke_llm(
            _get_llm(temperature=0.25).bind(max_tokens=6000),
            [
                SystemMessage(content=prompt),
                HumanMessage(
                    content=(
                        f"用户需求:\n{state.user_request}\n"
                        f"目标时长: {state.target_duration_seconds:.1f}s\n\n"
                        f"专题研究:\n{dependency_content[:50000]}\n\n"
                        f"原始分析索引:\n{analysis_context[:30000]}"
                    )
                ),
            ],
            "phase2_blueprint_integrator",
        )
        blueprint = str(response.content).strip()
        if not blueprint:
            raise RuntimeError("Blueprint Integrator 返回空结果")
        markdown_path = WORKSPACE / "editing_blueprint.md"
        json_path = WORKSPACE / "editing_blueprint.json"
        markdown_path.write_text(blueprint, encoding="utf-8")
        json_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "user_request": state.user_request,
                    "target_duration_seconds": state.target_duration_seconds,
                    "blueprint_markdown": blueprint,
                    "source_analysis_paths": [str(path.resolve()) for path in analysis_files],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return TaskExecutionResult(
            data={"content": blueprint, "path": str(markdown_path)},
            artifacts=[
                _task_artifact(
                    artifact_id="editing_blueprint_markdown",
                    kind="editing_blueprint",
                    path=markdown_path,
                    task=task,
                ),
                _task_artifact(
                    artifact_id="editing_blueprint_json",
                    kind="editing_blueprint_json",
                    path=json_path,
                    task=task,
                ),
            ],
        )

    try:
        states = scheduler.run(plan, execute, resume=True)
        blueprint = str(states[integrator_id].result.get("content", "")).strip()
        if not blueprint:
            raise RuntimeError("并行研究未产出最终蓝图")
        graph_logger.info("🔬 并行剪辑蓝图生成完成 (%d 字)", len(blueprint))
        return {
            "editing_blueprint": blueprint,
            "phase2_artifact_ids": states[integrator_id].artifact_ids,
            "phase": "react",
        }
    except ModelCallError:
        raise
    except Exception as exc:
        graph_logger.warning("Phase 2 并行研究失败，回退单次研究节点: %s", exc)
        _emit_orchestration_event(
            "phase2_fallback",
            {"reason": str(exc)[:500]},
        )
        return _legacy_editing_research_node(state)


# ═══════════════════════════════════════════════════════════════════════════
# ▸ Phase 3 — ReAct Editor (自主创作剪辑)
# ═══════════════════════════════════════════════════════════════════════════
REACT_EDITOR_PROMPT = """\
你是一位经验丰富的视频剪辑师和内容创作者。

你现在拥有了所有素材视频的详细分析数据（每个片段的时间、内容、画面描述等）。
你的任务是**根据用户需求，自主完成整个视频的创作**。

═════════════════════════════════════════
  第一步: 构思（先深度思考，不要急着调工具！）
═════════════════════════════════════════
请仔细阅读所有视频的分析数据，然后回答以下问题：
1. 用户要的是什么类型的视频？（宣传片 / 解说 / Vlog / 混剪…）
2. 什么样的叙事结构最吸引观众？（时间线 / 对比 / 问题→解答 / 情感递进…）
3. 哪些片段最适合做开场？哪些适合高潮？哪些适合收尾？
4. 每段大概需要多少秒？总时长能否达标？
5. 旁白/解说的风格和核心要点是什么？

请把你的思考**用文字写出来**，形成一份"剪辑脚本"，然后再开始执行。
**特别重要**: 在脚本中为每个片段或片段组设计对应的旁白文案和起止时间，
确保旁白内容与画面内容严格匹配。

═════════════════════════════════════════
  第二步: 执行剪辑
═════════════════════════════════════════
- 优先根据 Phase 2 剪辑蓝图中已明确的来源视频与时间段，直接调用 `cut_video` 精确裁剪
- 当蓝图中某段描述不够精确时，再用 `recall_semantic_segments` 做文本语义检索辅助定位
- 若某个源视频需要一次性提取多个连续片段，可补充使用 `batch_cut_video`
- 用 `merge_videos` 按你规划的顺序合并片段
- 用 `inspect_video_duration` 随时检查时长
- 先调用 `list_transition_presets` 查看可用转场，再用 `plan_transition_timeline` 生成逐切点方案
- 用 `add_transition` 在片段间添加专业转场（支持 transition_plan 逐切点配置）

═════════════════════════════════════════
    第三步: 成片复分析（配音前强制）
═════════════════════════════════════════
在完成合并/转场并且 `inspect_video_duration` 确认时长后，
**必须先对当前成片执行一次 `analyze_video`**（禁止跳过）。

复分析要求：
- 分析对象必须是“当前待配音的成片视频”（如 merged/transitioned 结果）
- 让分析覆盖全片并输出逐段时间轴内容
- 在你的思考中先给出“成片分段解说提纲”（每段讲什么、为什么这样讲）
- 然后再进入配音步骤

⚠️ 旁白文案最终必须同时结合：
1) 之前的素材分析与剪辑蓝图；
2) 这次成片复分析结果。

═════════════════════════════════════════
    第四步: 分段配音 + 字幕（核心！）
═════════════════════════════════════════
⚠️ **必须使用 `add_narration_segments` 进行分段配音**，不要用 `add_narration`。

`add_narration_segments` 接受一个 segments 列表，每段包含:
- text: 该段旁白文案
- start: 旁白开始时间（秒），对应合并后视频的时间轴
- end: 旁白结束时间（秒），TTS 超出会被截断

示例调用:
```
add_narration_segments(
    video_path="/workspace/transitioned.mp4",
    segments=[
        {{"text": "这座百年学府...", "start": 0, "end": 10}},
        {{"text": "走进图书馆...", "start": 15, "end": 25}},
        {{"text": "食堂里的美食...", "start": 30, "end": 40}}
    ],
    voice="Cherry",
    add_subtitle=True
)
```

分段配音原则:
- **时间对齐**: 每段旁白的 start/end 必须与对应片段的实际时间轴匹配
  - 裁剪后记录每个片段的时长，合并后推算每段在总时间轴上的位置
  - 例: clip_01=5s, clip_02=5s, clip_03=6s → clip_03 的旁白 start=10, end=16
- **内容匹配**: 旁白文案必须描述该时间段画面中的实际内容，严禁编造
- **场景转折**: 从一个场景到另一个场景时，旁白应有自然的过渡和转折
- **适当留白**: 并非每秒都需要旁白。在视觉冲击力强的片段（如航拍、美景）可以留白让画面说话
- **可以合并**: 如果相邻多个片段属于同一主题，可以写一段连续的旁白覆盖它们
- **字幕**: add_subtitle=True（默认）会自动在底部添加与旁白同步的字幕
- **单独加字幕**: 如果只需要字幕不需要配音，可以用 `add_subtitles` 工具
- `add_narration_segments` 前先用 `align_narration_to_timeline` 生成段落，再用 `validate_narration_timeline` 校验并修正
- 对于约 60s 成片，旁白覆盖建议不低于 70%（允许留白但避免大段静默）

═════════════════════════════════════════
    第五步: 检验和导出
═════════════════════════════════════════
- 用 `inspect_video_duration` 检查最终时长
- 如果超长/过短：重新调整
- 满意后，调用 `export_video` 导出最终成品

═════════════════════════════════════════
  创作原则
═════════════════════════════════════════
- **当前任务优先**: 历史案例 memory 只能提供工具/流程参考，不能改写当前任务目标、素材理解、题材判断、旁白风格或成片定位
- **开场抓人**: 选择最具视觉冲击力或悬念感的片段
- **多源混剪**: 从不同源视频中选精华，避免只用单一来源
- **蓝图优先执行**: 先按深度研究蓝图直接裁剪；仅在时间段不明确时再补充文本语义检索
- **节奏感**: 起承转合清晰，避免平铺直叙
- **时长精准**: 严格按目标时长控制每段和总时长
  - 用 batch_cut_video 时，为每个源视频传入合理的 target_duration（目标总时长 ÷ 源视频数）
  - 裁剪完成后立即用 inspect_video_duration 检查实际时长，不符合就重剪
- **转场专业化**: 优先使用 transition_plan 对不同切点用不同效果（如渐黑 + 缩放 + 滑动）
- **旁白贴合**: 旁白必须基于分析数据中的实际画面内容撰写，严禁编造
- **成片优先**: 配音文案以“成片复分析”时间轴为主，不得只依据原素材分析
- **音画同步**: 旁白的时间段必须与对应画面对齐，场景切换时旁白要有转折

═════════════════════════════════════════
  工具使用注意事项
═════════════════════════════════════════
- `add_transition(video_paths=[...])` 接受视频路径**列表**，不是两个独立参数
- `add_transition` 支持 `transition_plan=[{{"cut_index":0,"transition_type":"fade_through_black","duration":0.9}}, ...]`
- 列表中不要传入重复的文件路径
- 文件路径必须使用工具返回的真实路径，不要猜测或自行拼接
- `recall_semantic_segments` 返回的是文本语义候选时间段，不会自动裁剪；要再调用 `cut_video`
- 配音前必须先对成片执行 `analyze_video`，并基于该结果生成解说
- 配音前必须先 `align_narration_to_timeline` 与 `validate_narration_timeline`
- 配音前必须先 `validate_narration_timeline`
- **配音必须用 `add_narration_segments`**，禁止使用 `add_narration`
- 工作目录:
  - temp: {workspace}
  - user_temp: {user_workspace}
  - memory_experience: {memory_experience}
"""


class ShortFormClip(BaseModel):
    source_path: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    label: str = ""


class ShortFormNarration(BaseModel):
    text: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class ShortFormEditPlan(BaseModel):
    clips: list[ShortFormClip] = Field(min_length=3, max_length=4)
    narration: list[ShortFormNarration] = Field(min_length=1, max_length=3)
    voice: str = "Ethan"
    output_name: str = "short_form_promo"


class ShortFormExecutionError(RuntimeError):
    pass


class ControlledClip(BaseModel):
    scene_id: str = ""
    source_path: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    label: str = ""


class ControlledNarration(BaseModel):
    scene_id: str = ""
    text: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class ControlledEditPlan(BaseModel):
    clips: list[ControlledClip] = Field(min_length=1, max_length=24)
    narration: list[ControlledNarration] = Field(default_factory=list, max_length=24)
    voice: str = "Ethan"
    output_name: str = "output_final"
    resolution: Literal["720p", "1080p", "4k"] = "1080p"


class ControlledNarrationPlan(BaseModel):
    narration: list[ControlledNarration] = Field(default_factory=list, max_length=24)
    voice: str = "Ethan"


class SteeringReplanRequested(RuntimeError):
    def __init__(self, required_phase: str) -> None:
        super().__init__(f"Steering requested replan from {required_phase}.")
        self.required_phase = required_phase


def _approved_editing_plan() -> EditingPlan | None:
    try:
        return _editing_plan_store().approved()
    except Exception:
        return None


def _controlled_plan_from_approved(plan: EditingPlan) -> ControlledEditPlan:
    clips = [
        ControlledClip(
            scene_id=scene.scene_id,
            source_path=str(Path(scene.source_path).resolve(strict=False)) if scene.source_path else "",
            start=scene.source_start,
            end=scene.source_end,
            label=scene.narrative_purpose or scene.scene_id,
        )
        for scene in plan.scenes
        if scene.source_path
    ]
    narration = [
        ControlledNarration(
            scene_id=scene.scene_id,
            text=scene.narration,
            start=scene.start,
            end=scene.end,
        )
        for scene in plan.scenes
        if scene.narration.strip()
    ]
    resolution: Literal["720p", "1080p", "4k"] = "1080p"
    if plan.target_duration_seconds and plan.target_duration_seconds <= 20:
        resolution = "720p"
    return ControlledEditPlan(
        clips=clips,
        narration=narration,
        voice="Ethan",
        output_name=f"approved_plan_{plan.version}",
        resolution=resolution,
    )


SHORT_FORM_PLAN_PROMPT = """\
你是短视频剪辑计划器。根据用户需求、Phase 2 蓝图和素材分析，生成可直接执行的 JSON。

硬性要求：
- 只选择输入中真实存在的 source_path。
- 选择 3~4 个场景互补片段，总时长严格贴合用户目标时长。
- 每个片段至少 2 秒，start/end 必须来自分析数据的有效原视频时间段。
- 优先覆盖用户主题中最关键的三类画面元素。
- 旁白使用 1~3 段，每段起止时间必须位于成片时间轴内，不重叠，文案简短自然。
- voice 固定为 Ethan。
- output_name 使用简短英文或拼音文件名，不要带扩展名。
- 只返回 JSON，不要 Markdown。

格式：
{
  "clips": [
    {"source_path": "绝对路径", "start": 0.0, "end": 2.5, "label": "场景"}
  ],
  "narration": [
    {"text": "旁白", "start": 0.0, "end": 4.5}
  ],
  "voice": "Ethan",
  "output_name": "short_form_promo"
}
"""
def _short_form_target_duration(state: AgentState) -> float:
    return max(1.0, float(state.target_duration_seconds or 15.0))


def _short_form_output_name(state: AgentState) -> str:
    target = int(round(_short_form_target_duration(state)))
    return f"short_form_{target}s_promo"


def _short_form_plan_prompt(state: AgentState) -> str:
    target = _short_form_target_duration(state)
    return (
        SHORT_FORM_PLAN_PROMPT
        + "\n\n当前任务约束：\n"
        f"- 目标总时长: {target:.1f} 秒。\n"
        f"- 片段总时长允许误差: ±{max(1.0, target * 0.08):.1f} 秒。\n"
        f"- 旁白起止时间必须位于 0 到 {target:.1f} 秒之间。\n"
        f"- 推荐 output_name: {_short_form_output_name(state)}。\n"
    )


def _user_explicitly_requested_high_resolution(text: str) -> bool:
    return bool(re.search(r"1080p|1920\s*[x×]\s*1080|4k|3840\s*[x×]\s*2160|高清|超清", text, re.I))


def _parse_json_object(text: str) -> dict[str, Any]:
    content = str(text or "").strip()
    if "```json" in content:
        content = content.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in content:
        content = content.split("```", 1)[1].split("```", 1)[0]
    return json.loads(content)


def _build_controlled_edit_plan(state: AgentState) -> ControlledEditPlan:
    registry = _artifact_registry()
    scheduler = _resource_scheduler(registry)
    task = TaskSpec(
        id="phase3_edit_planner",
        phase="phase3",
        kind="edit_planner",
        description="将剪辑蓝图转换为确定性编辑 DAG",
        arguments={
            "user_request": state.user_request,
            "target_duration_seconds": state.target_duration_seconds,
            "blueprint_digest": hashlib.sha256(
                state.editing_blueprint.encode("utf-8")
            ).hexdigest(),
        },
        resources={"llm_pool": 1},
        output_kinds=["phase3_plan"],
        retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
    )
    execution_plan = ExecutionPlan(
        plan_id="phase3_edit_planner",
        phase="phase3",
        goal=state.user_request,
        tasks=[task],
    )

    def execute(task_spec: TaskSpec, dependencies: dict[str, Any]) -> TaskExecutionResult:
        source_paths = [str(path.resolve()) for path in _iter_source_videos()]
        prompt = (
            "你是确定性视频编辑计划器。根据用户需求、剪辑蓝图和素材分析生成 JSON。"
            "只允许引用 source_paths 中的真实路径；所有 start/end 必须来自分析中的有效时间段。"
            "片段按播放顺序输出。相邻片段会使用转场重叠：目标不超过30秒时每个切点约重叠0.3秒，"
            "更长视频每个切点约重叠0.6秒；扣除这些重叠后的预计成片时长应接近 "
            "target_duration_seconds。"
            "旁白时间使用合并后时间轴，必须位于成片范围内且不重叠。"
            "只返回 JSON，结构为："
            '{"clips":[{"source_path":"","start":0,"end":5,"label":""}],'
            '"narration":[{"text":"","start":0,"end":5}],'
            '"voice":"Ethan","output_name":"output_final","resolution":"720p"}。'
        )
        response = _invoke_llm(
            _get_llm(temperature=0.1).bind(max_tokens=5000),
            [
                SystemMessage(content=prompt),
                HumanMessage(
                    content=(
                        f"user_request:\n{state.user_request}\n\n"
                        f"target_duration_seconds: {state.target_duration_seconds}\n\n"
                        f"source_paths:\n{json.dumps(source_paths, ensure_ascii=False)}\n\n"
                        f"editing_blueprint:\n{state.editing_blueprint[:30000]}\n\n"
                        f"analysis:\n{_build_full_analysis_context()[:40000]}"
                    )
                ),
            ],
            "phase3_edit_planner",
        )
        parsed = _parse_json_object(str(response.content))
        plan = ControlledEditPlan.model_validate(parsed)
        if (
            0 < state.target_duration_seconds <= 20
            and not _user_explicitly_requested_high_resolution(state.user_request)
        ):
            plan.resolution = "720p"
        available = {str(Path(path).resolve()) for path in source_paths}
        total = 0.0
        for clip in plan.clips:
            resolved = str(Path(clip.source_path).resolve())
            if resolved not in available:
                raise RuntimeError(f"Phase 3 计划引用未知素材: {clip.source_path}")
            clip.source_path = resolved
            if clip.end <= clip.start or clip.end - clip.start < 0.5:
                raise RuntimeError("Phase 3 计划包含无效裁剪区间")
            total += clip.end - clip.start
        target = state.target_duration_seconds or total
        transition_overlap = (
            (0.3 if target <= 30 else 0.6) * max(0, len(plan.clips) - 1)
        )
        effective_total = max(0.0, total - transition_overlap)
        tolerance = max(2.0, target * 0.2)
        if target > 0 and abs(effective_total - target) > tolerance:
            raise RuntimeError(
                "Phase 3 计划预计成片时长偏差过大: "
                f"raw={total:.2f}, overlap={transition_overlap:.2f}, "
                f"effective={effective_total:.2f}, target={target:.2f}"
            )
        for segment in plan.narration:
            if segment.end <= segment.start or segment.end > total + 0.5:
                raise RuntimeError("Phase 3 计划包含越界旁白时间段")
        plan_path = WORKSPACE / "phase3_execution_plan.json"
        plan_path.write_text(
            json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return TaskExecutionResult(
            data={"plan": plan.model_dump()},
            artifacts=[
                _task_artifact(
                    artifact_id="phase3_execution_plan",
                    kind="phase3_plan",
                    path=plan_path,
                    task=task_spec,
                )
            ],
        )

    states = scheduler.run(execution_plan, execute, resume=True)
    return ControlledEditPlan.model_validate(states[task.id].result["plan"])


def _state_with_current_guidance(state: AgentState) -> AgentState:
    coordinator = _steering_coordinator()
    if coordinator is None:
        return state
    base_request = state.base_user_request or state.user_request
    guidance = coordinator.guidance_text()
    return state.model_copy(
        update={
            "base_user_request": base_request,
            "guidance_context": guidance,
            "user_request": _effective_user_request(base_request, guidance),
            "revision": REVISION,
        }
    )


def _phase3_checkpoint(
    state: AgentState,
    checkpoint: str,
    *,
    allow_local_categories: set[str] | None = None,
) -> AgentState:
    coordinator = _steering_coordinator()
    if coordinator is None:
        return state
    result = coordinator.apply_pending(checkpoint, "phase3")
    required_phase = str(result.get("required_phase") or "")
    categories = set(str(item) for item in result.get("categories", []))
    if required_phase in {"phase1", "phase2"}:
        raise SteeringReplanRequested(required_phase)
    if required_phase == "phase3" and categories:
        allowed = allow_local_categories or set()
        if not categories.issubset(allowed):
            raise SteeringReplanRequested("phase3")
    return _state_with_current_guidance(state)


def _build_controlled_narration_plan(
    state: AgentState,
    *,
    video_path: str,
    analysis_path: str,
    duration_seconds: float,
) -> ControlledNarrationPlan:
    registry = _artifact_registry()
    scheduler = _resource_scheduler(registry)
    analysis_file = Path(analysis_path)
    task = TaskSpec(
        id="phase3_narration_planner",
        phase="phase3",
        kind="narration_planner",
        description="基于成片复分析和最新用户指导生成旁白计划",
        arguments={
            "user_request": state.user_request,
            "video_path": video_path,
            "analysis_path": analysis_path,
            "analysis_size": analysis_file.stat().st_size if analysis_file.exists() else 0,
            "duration_seconds": duration_seconds,
        },
        resources={"llm_pool": 1},
        output_kinds=["narration_plan"],
        retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
    )
    execution_plan = ExecutionPlan(
        plan_id="phase3_narration_planner",
        phase="phase3",
        goal=state.user_request,
        tasks=[task],
    )

    def execute(task_spec: TaskSpec, dependencies: dict[str, Any]) -> TaskExecutionResult:
        analysis_text = analysis_file.read_text(encoding="utf-8") if analysis_file.exists() else ""
        prompt = (
            "你是视频旁白计划器。必须基于当前成片复分析和用户最新指导生成旁白。"
            "旁白时间段必须位于视频时长内、互不重叠，并为视觉强段落保留适当留白。"
            "如果用户要求减少旁白或字幕，应减少段数并缩短文案。"
            "只返回 JSON："
            '{"narration":[{"text":"","start":0,"end":5}],"voice":"Ethan"}。'
        )
        response = _invoke_llm(
            _get_llm(temperature=0.1).bind(max_tokens=3000),
            [
                SystemMessage(content=prompt),
                HumanMessage(
                    content=(
                        f"user_request:\n{state.user_request}\n\n"
                        f"duration_seconds: {duration_seconds}\n\n"
                        f"assembled_analysis:\n{analysis_text[:30000]}"
                    )
                ),
            ],
            "phase3_narration_planner",
        )
        plan = ControlledNarrationPlan.model_validate(
            _parse_json_object(str(response.content))
        )
        for segment in plan.narration:
            if segment.end <= segment.start or segment.end > duration_seconds + 0.5:
                raise RuntimeError("旁白计划包含越界时间段")
        plan_path = WORKSPACE / "phase3_narration_plan.json"
        plan_path.write_text(
            json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return TaskExecutionResult(
            data={"plan": plan.model_dump()},
            artifacts=[
                _task_artifact(
                    artifact_id="phase3_narration_plan",
                    kind="narration_plan",
                    path=plan_path,
                    task=task_spec,
                )
            ],
        )

    states = scheduler.run(execution_plan, execute, resume=True)
    return ControlledNarrationPlan.model_validate(states[task.id].result["plan"])


def _subtitle_segments(
    narration: list[ControlledNarration],
    guidance_context: str,
) -> list[dict[str, Any]]:
    segments = [item.model_dump() for item in narration]
    lowered = guidance_context.lower()
    if any(marker in lowered for marker in ("字幕不要太多", "减少字幕", "字幕简洁", "少字幕")):
        segments = segments[::2] or segments[:1]
    return segments


def _run_controlled_editor_legacy(state: AgentState) -> str:
    plan = _build_controlled_edit_plan(state)
    registry = _artifact_registry()
    scheduler = _resource_scheduler(registry)
    cut_tasks: list[TaskSpec] = []
    for index, clip in enumerate(plan.clips, start=1):
        output_name = f"phase3_clip_{index:03d}"
        cut_tasks.append(
            TaskSpec(
                id=f"phase3_cut_{index:03d}",
                phase="phase3",
                kind="clip_cut",
                tool_name="cut_video",
                description=clip.label or f"裁剪片段 {index}",
                arguments={
                    "input_path": clip.source_path,
                    "start_time": clip.start,
                    "end_time": clip.end,
                    "output_name": output_name,
                },
                resources={"ffmpeg_pool": 1},
                conflict_keys=[f"write:{(WORKSPACE / f'{output_name}.mp4').resolve()}"],
                output_kinds=["video_clip"],
                retry=RetryPolicy(max_attempts=1),
            )
        )
    cut_ids = [task.id for task in cut_tasks]
    merge_task = TaskSpec(
        id="phase3_merge",
        phase="phase3",
        kind="timeline_merge",
        tool_name="add_transition",
        description="串行规划转场并合成主时间线",
        depends_on=cut_ids,
        resources={"ffmpeg_pool": 1},
        conflict_keys=[f"write:{(WORKSPACE / 'phase3_assembled.mp4').resolve()}"],
        output_kinds=["assembled_video"],
    )
    analyze_task = TaskSpec(
        id="phase3_assembled_analysis",
        phase="phase3",
        kind="assembled_analysis",
        tool_name="analyze_video",
        description="复分析当前成片",
        depends_on=[merge_task.id],
        resources={"video_analysis_pool": 1, "ffmpeg_pool": 1},
        conflict_keys=["phase3:assembled_analysis"],
        output_kinds=["assembled_analysis"],
    )
    tts_tasks = [
        TaskSpec(
            id=f"phase3_tts_{index:03d}",
            phase="phase3",
            kind="tts_segment",
            description=f"生成旁白音频 {index}",
            arguments={
                "index": index,
                "text": segment.text,
                "start": segment.start,
                "end": segment.end,
                "voice": plan.voice,
                "audio_path": str(narration_audio_path("phase3_tts", index)),
            },
            resources={"tts_pool": 1},
            conflict_keys=[
                f"write:{narration_audio_path('phase3_tts', index).resolve(strict=False)}"
            ],
            output_kinds=["narration_audio"],
            retry=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
        )
        for index, segment in enumerate(plan.narration, start=1)
    ]
    narration_task = TaskSpec(
        id="phase3_narration_mix",
        phase="phase3",
        kind="narration_mix",
        tool_name="add_narration_segments",
        description="串行混合旁白和字幕",
        depends_on=[merge_task.id, analyze_task.id, *[task.id for task in tts_tasks]],
        resources={"ffmpeg_pool": 1},
        conflict_keys=[f"write:{(WORKSPACE / 'phase3_narrated.mp4').resolve()}"],
        output_kinds=["narrated_video"],
    )
    subtitle_task = TaskSpec(
        id="phase3_subtitles",
        phase="phase3",
        kind="subtitle_render",
        tool_name="add_subtitles",
        description="串行烧录字幕",
        depends_on=[narration_task.id],
        resources={"ffmpeg_pool": 1},
        conflict_keys=[f"write:{(WORKSPACE / 'phase3_subtitled.mp4').resolve()}"],
        output_kinds=["subtitled_video"],
    )
    export_task = TaskSpec(
        id="phase3_export",
        phase="phase3",
        kind="final_export",
        tool_name="export_video",
        description="独占导出最终成片",
        depends_on=[subtitle_task.id],
        resources={"export_pool": 1, "ffmpeg_pool": 1},
        conflict_keys=["phase3:final_export"],
        output_kinds=["export_candidate"],
    )
    evaluate_task = TaskSpec(
        id="phase3_evaluator",
        phase="phase3",
        kind="final_evaluator",
        description="校验最终成片时长和文件有效性",
        depends_on=[export_task.id],
        resources={"ffmpeg_pool": 1},
        output_kinds=["phase3_evaluation"],
    )
    execution_plan = ExecutionPlan(
        plan_id="phase3_controlled_execution",
        phase="phase3",
        goal=state.user_request,
        tasks=[
            *cut_tasks,
            merge_task,
            analyze_task,
            *tts_tasks,
            narration_task,
            subtitle_task,
            export_task,
            evaluate_task,
        ],
    )

    def execute(task: TaskSpec, dependencies: dict[str, Any]) -> TaskExecutionResult:
        if task.kind == "clip_cut":
            raw = _TOOL_NAME_MAP["cut_video"].invoke(task.arguments)
            parsed = json.loads(str(raw))
            path = str(parsed.get("path") or "")
            if parsed.get("status") != "success" or not path:
                raise RuntimeError(f"裁剪失败: {str(raw)[:300]}")
            return TaskExecutionResult(
                data={"path": path},
                artifacts=[
                    _task_artifact(
                        artifact_id=f"{task.id}_video",
                        kind="video_clip",
                        path=path,
                        task=task,
                    )
                ],
            )

        if task.kind == "timeline_merge":
            paths = [str(dependencies[task_id].result["path"]) for task_id in cut_ids]
            transition_plan: list[dict[str, Any]] = []
            if len(paths) > 1:
                plan_raw = _TOOL_NAME_MAP["plan_transition_timeline"].invoke(
                    {
                        "video_paths": paths,
                        "style": "cinematic",
                        "base_duration": 0.3
                        if state.target_duration_seconds <= 30
                        else 0.6,
                    }
                )
                plan_data = json.loads(str(plan_raw))
                transition_plan = list(plan_data.get("transition_plan", []))
            raw = _TOOL_NAME_MAP["add_transition"].invoke(
                {
                    "video_paths": paths,
                    "transition_type": "crossfade",
                    "duration": 0.3 if state.target_duration_seconds <= 30 else 0.6,
                    "transition_plan": transition_plan,
                    "output_name": "phase3_assembled",
                }
            )
            parsed = json.loads(str(raw))
            path = str(parsed.get("path") or "")
            if parsed.get("status") != "success" or not path:
                raise RuntimeError(f"主时间线合并失败: {str(raw)[:300]}")
            return TaskExecutionResult(
                data={"path": path},
                artifacts=[
                    _task_artifact(
                        artifact_id="phase3_assembled_video",
                        kind="assembled_video",
                        path=path,
                        task=task,
                    )
                ],
            )

        if task.kind == "assembled_analysis":
            merged_path = str(dependencies[merge_task.id].result["path"])
            raw = str(
                _TOOL_NAME_MAP["analyze_video"].invoke(
                    {
                        "video_path": merged_path,
                        "analysis_goal": "核对当前成片逐段画面、叙事连续性与旁白匹配",
                    }
                )
            )
            matches = match_analysis_files(
                Path(merged_path),
                analysis_index=build_analysis_index([WORKSPACE, USER_WORKSPACE]),
            )
            if not matches:
                raise RuntimeError(f"当前成片复分析未生成 JSON: {raw[:500]}")
            return TaskExecutionResult(
                data={
                    "analysis": raw,
                    "analysis_path": str(matches[0].resolve()),
                },
                artifacts=[
                    _task_artifact(
                        artifact_id="phase3_assembled_analysis_json",
                        kind="assembled_analysis",
                        path=matches[0],
                        task=task,
                    )
                ],
            )

        if task.kind == "tts_segment":
            audio_path = Path(str(task.arguments["audio_path"]))
            error = _tts_generate(
                str(task.arguments["text"]),
                str(task.arguments["voice"]),
                audio_path,
            )
            if error or not audio_path.exists():
                raise RuntimeError(error or "TTS 未生成音频文件")
            return TaskExecutionResult(
                data={
                    "audio_path": str(audio_path.resolve()),
                    "text": task.arguments["text"],
                    "start": task.arguments["start"],
                    "end": task.arguments["end"],
                },
                artifacts=[
                    _task_artifact(
                        artifact_id=f"{task.id}_audio",
                        kind="narration_audio",
                        path=audio_path,
                        task=task,
                        metadata={
                            "start": task.arguments["start"],
                            "end": task.arguments["end"],
                            "text": task.arguments["text"],
                        },
                    )
                ],
            )

        if task.kind == "narration_mix":
            merged_path = str(dependencies[merge_task.id].result["path"])
            if not plan.narration:
                return TaskExecutionResult(data={"path": merged_path})
            segments = [item.model_dump() for item in plan.narration]
            validation_raw = _TOOL_NAME_MAP["validate_narration_timeline"].invoke(
                {"video_path": merged_path, "segments": segments}
            )
            validation = json.loads(str(validation_raw))
            if validation.get("status") == "fail":
                raise RuntimeError(f"旁白时间线校验失败: {validation}")
            prepared_segments = [
                dependencies[task_id].result
                for task_id in [item.id for item in tts_tasks]
            ]
            raw = compose_prepared_narration(
                video_path=merged_path,
                prepared_segments=prepared_segments,
                output_name="phase3_narrated",
            )
            parsed = json.loads(str(raw))
            path = str(parsed.get("path") or "")
            if parsed.get("status") != "success" or not path:
                raise RuntimeError(f"旁白混合失败: {str(raw)[:300]}")
            return TaskExecutionResult(
                data={"path": path, "narration": parsed},
                artifacts=[
                    _task_artifact(
                        artifact_id="phase3_narrated_video",
                        kind="narrated_video",
                        path=path,
                        task=task,
                    )
                ],
            )

        if task.kind == "subtitle_render":
            input_path = str(dependencies[narration_task.id].result["path"])
            if not plan.narration:
                return TaskExecutionResult(data={"path": input_path})
            raw = _TOOL_NAME_MAP["add_subtitles"].invoke(
                {
                    "video_path": input_path,
                    "subtitles": [item.model_dump() for item in plan.narration],
                    "output_name": "phase3_subtitled",
                }
            )
            try:
                parsed = json.loads(str(raw))
            except json.JSONDecodeError:
                graph_logger.warning("字幕渲染失败，沿用未加字幕视频继续导出: %s", str(raw)[:300])
                emit_benchmark_event(
                    "subtitle_render_skipped",
                    {
                        "phase": "phase3",
                        "reason": str(raw)[:300],
                        "fallback_react": False,
                    },
                )
                return TaskExecutionResult(
                    data={"path": input_path, "subtitle_status": "skipped", "subtitle_error": str(raw)[:300]}
                )
            path = str(parsed.get("path") or "")
            if parsed.get("status") != "success" or not path:
                graph_logger.warning("字幕渲染未成功，沿用未加字幕视频继续导出: %s", str(raw)[:300])
                emit_benchmark_event(
                    "subtitle_render_skipped",
                    {
                        "phase": "phase3",
                        "reason": str(raw)[:300],
                        "fallback_react": False,
                    },
                )
                return TaskExecutionResult(
                    data={"path": input_path, "subtitle_status": "skipped", "subtitle_error": str(raw)[:300]}
                )
            return TaskExecutionResult(
                data={"path": path},
                artifacts=[
                    _task_artifact(
                        artifact_id="phase3_subtitled_video",
                        kind="subtitled_video",
                        path=path,
                        task=task,
                    )
                ],
            )

        if task.kind == "final_export":
            input_path = str(dependencies[subtitle_task.id].result["path"])
            raw = _TOOL_NAME_MAP["export_video"].invoke(
                {
                    "input_path": input_path,
                    "output_name": plan.output_name,
                    "resolution": plan.resolution,
                }
            )
            parsed = json.loads(str(raw))
            path = str(parsed.get("path") or "")
            if parsed.get("status") != "success" or not path:
                raise RuntimeError(f"最终导出失败: {str(raw)[:300]}")
            return TaskExecutionResult(
                data={"path": path, "export": parsed},
                artifacts=[
                    _task_artifact(
                        artifact_id="phase3_export_candidate",
                        kind="export_candidate",
                        path=path,
                        task=task,
                    )
                ],
            )

        final_path = str(dependencies[export_task.id].result["path"])
        raw = _TOOL_NAME_MAP["inspect_video_duration"].invoke({"video_path": final_path})
        metadata = json.loads(str(raw))
        duration = float(metadata.get("duration_seconds", 0.0) or 0.0)
        target = state.target_duration_seconds or duration
        tolerance = max(1.0, target * 0.08)
        decision = "finish" if duration > 0 and abs(duration - target) <= tolerance else "fallback_react"
        report = {
            "decision": decision,
            "path": final_path,
            "duration_seconds": duration,
            "target_duration_seconds": target,
            "tolerance_seconds": tolerance,
        }
        report_path = WORKSPACE / "phase3_evaluation.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if decision != "finish":
            raise RuntimeError(f"最终成片质量校验未通过: {report}")
        _emit_orchestration_event(
            "evaluator_decision",
            {"phase": "phase3", **report},
        )
        return TaskExecutionResult(
            data=report,
            artifacts=[
                _task_artifact(
                    artifact_id="phase3_final_video",
                    kind="final_video",
                    path=final_path,
                    task=task,
                    metadata={"duration_seconds": duration},
                ),
                _task_artifact(
                    artifact_id="phase3_evaluation",
                    kind="phase3_evaluation",
                    path=report_path,
                    task=task,
                )
            ],
        )

    states = scheduler.run(execution_plan, execute, resume=True)
    evaluation = states[evaluate_task.id].result
    return json.dumps(
        {
            "status": "success",
            "executor": "controlled_dag",
            "path": evaluation["path"],
            "duration_seconds": evaluation["duration_seconds"],
            "clip_count": len(plan.clips),
            "narration_segments": len(plan.narration),
        },
        ensure_ascii=False,
    )


def _run_controlled_editor(state: AgentState) -> str:
    state = _phase3_checkpoint(
        state,
        "before_timeline_execution",
        allow_local_categories={"general", "narration", "subtitle"},
    )
    approved_plan = _approved_editing_plan()
    plan = (
        _controlled_plan_from_approved(approved_plan)
        if approved_plan is not None
        else _build_controlled_edit_plan(state)
    )
    plan_version = approved_plan.version if approved_plan is not None else ""
    registry = _artifact_registry()

    cut_tasks: list[TaskSpec] = []
    for index, clip in enumerate(plan.clips, start=1):
        output_name = f"phase3_clip_{index:03d}"
        cut_tasks.append(
            TaskSpec(
                id=f"phase3_cut_{index:03d}",
                phase="phase3",
                kind="clip_cut",
                tool_name="cut_video",
                description=f"并行裁剪片段 {index}",
                arguments={
                    "plan_version": plan_version,
                    "scene_id": clip.scene_id,
                    "execution_step_id": f"phase3_cut_{index:03d}",
                    "input_path": clip.source_path,
                    "start_time": clip.start,
                    "end_time": clip.end,
                    "output_name": output_name,
                },
                resources={"ffmpeg_pool": 1},
                conflict_keys=[f"write:{(WORKSPACE / f'{output_name}.mp4').resolve()}"],
                output_kinds=["video_clip"],
            )
        )
    cut_ids = [task.id for task in cut_tasks]
    merge_task = TaskSpec(
        id="phase3_timeline_merge",
        phase="phase3",
        kind="timeline_merge",
        tool_name="add_transition",
        description="串行组装主时间线和转场",
        arguments={"clip_count": len(cut_tasks), "user_request": state.user_request},
        depends_on=cut_ids,
        resources={"ffmpeg_pool": 1},
        conflict_keys=[f"write:{(WORKSPACE / 'phase3_assembled.mp4').resolve()}"],
        output_kinds=["assembled_video"],
    )
    analyze_task = TaskSpec(
        id="phase3_assembled_analysis",
        phase="phase3",
        kind="assembled_analysis",
        tool_name="analyze_video",
        description="复分析主时间线供旁白对齐",
        depends_on=[merge_task.id],
        resources={"video_analysis_pool": 1},
        output_kinds=["assembled_analysis"],
    )
    visual_plan = ExecutionPlan(
        plan_id="phase3_visual_execution",
        phase="phase3",
        goal=state.user_request,
        tasks=[*cut_tasks, merge_task, analyze_task],
    )

    def execute_visual(
        task: TaskSpec,
        dependencies: dict[str, Any],
    ) -> TaskExecutionResult:
        if task.kind == "clip_cut":
            raw = _TOOL_NAME_MAP["cut_video"].invoke(
                {
                    "input_path": task.arguments["input_path"],
                    "start_time": task.arguments["start_time"],
                    "end_time": task.arguments["end_time"],
                    "output_name": task.arguments["output_name"],
                }
            )
            parsed = json.loads(str(raw))
            path = str(parsed.get("path") or "")
            if parsed.get("status") != "success" or not path:
                raise RuntimeError(f"裁剪失败: {str(raw)[:300]}")
            return TaskExecutionResult(
                data={"path": path},
                artifacts=[
                    _task_artifact(
                        artifact_id=f"{task.id}_video",
                        kind="video_clip",
                        path=path,
                        task=task,
                        metadata={
                            "plan_version": task.arguments.get("plan_version", ""),
                            "scene_id": task.arguments.get("scene_id", ""),
                            "execution_step_id": task.arguments.get("execution_step_id", task.id),
                        },
                    )
                ],
            )

        if task.kind == "timeline_merge":
            paths = [str(dependencies[task_id].result["path"]) for task_id in cut_ids]
            transition_plan: list[dict[str, Any]] = []
            if len(paths) > 1:
                transition_raw = _TOOL_NAME_MAP["plan_transition_timeline"].invoke(
                    {
                        "video_paths": paths,
                        "style": "cinematic",
                        "base_duration": 0.3
                        if state.target_duration_seconds <= 30
                        else 0.6,
                    }
                )
                transition_plan = list(
                    json.loads(str(transition_raw)).get("transition_plan", [])
                )
            raw = _TOOL_NAME_MAP["add_transition"].invoke(
                {
                    "video_paths": paths,
                    "transition_type": "crossfade",
                    "duration": 0.3 if state.target_duration_seconds <= 30 else 0.6,
                    "transition_plan": transition_plan,
                    "output_name": "phase3_assembled",
                }
            )
            parsed = json.loads(str(raw))
            path = str(parsed.get("path") or "")
            if parsed.get("status") != "success" or not path:
                raise RuntimeError(f"主时间线合并失败: {str(raw)[:300]}")
            return TaskExecutionResult(
                data={"path": path},
                artifacts=[
                    _task_artifact(
                        artifact_id="phase3_assembled_video",
                        kind="assembled_video",
                        path=path,
                        task=task,
                    )
                ],
            )

        merged_path = str(dependencies[merge_task.id].result["path"])
        raw = str(
            _TOOL_NAME_MAP["analyze_video"].invoke(
                {
                    "video_path": merged_path,
                    "analysis_goal": "核对当前成片逐段画面、叙事连续性与旁白匹配",
                }
            )
        )
        matches = match_analysis_files(
            Path(merged_path),
            analysis_index=build_analysis_index([WORKSPACE, USER_WORKSPACE]),
        )
        if not matches:
            raise RuntimeError(f"当前成片复分析未生成 JSON: {raw[:500]}")
        return TaskExecutionResult(
            data={"path": merged_path, "analysis_path": str(matches[0].resolve())},
            artifacts=[
                _task_artifact(
                    artifact_id="phase3_assembled_analysis_json",
                    kind="assembled_analysis",
                    path=matches[0],
                    task=task,
                )
            ],
        )

    visual_states = _resource_scheduler(registry).run(
        visual_plan,
        execute_visual,
        resume=True,
    )
    merged_path = str(visual_states[merge_task.id].result["path"])
    analysis_path = str(visual_states[analyze_task.id].result["analysis_path"])
    duration_raw = _TOOL_NAME_MAP["inspect_video_duration"].invoke(
        {"video_path": merged_path}
    )
    duration_seconds = float(
        json.loads(str(duration_raw)).get("duration_seconds", 0.0) or 0.0
    )

    state = _phase3_checkpoint(
        state,
        "before_narration_generation",
        allow_local_categories={"narration", "subtitle"},
    )
    if approved_plan is not None and plan.narration:
        narration_plan = ControlledNarrationPlan(narration=plan.narration, voice=plan.voice)
    else:
        narration_plan = _build_controlled_narration_plan(
            state,
            video_path=merged_path,
            analysis_path=analysis_path,
            duration_seconds=duration_seconds,
        )
    tts_tasks = [
        TaskSpec(
            id=f"phase3_tts_{index:03d}",
            phase="phase3",
            kind="tts_segment",
            description=f"并行生成旁白音频 {index}",
            arguments={
                "plan_version": plan_version,
                "scene_id": segment.scene_id,
                "execution_step_id": f"phase3_tts_{index:03d}",
                "text": segment.text,
                "start": segment.start,
                "end": segment.end,
                "voice": narration_plan.voice,
                "audio_path": str(narration_audio_path("phase3_tts", index)),
            },
            resources={"tts_pool": 1},
            conflict_keys=[
                f"write:{narration_audio_path('phase3_tts', index).resolve(strict=False)}"
            ],
            output_kinds=["narration_audio"],
            retry=RetryPolicy(
                max_attempts=2,
                backoff_seconds=0.5,
                retryable_errors=["拒绝访问", "Permission", "timeout", "TTS"],
            ),
        )
        for index, segment in enumerate(narration_plan.narration, start=1)
    ]
    narration_task = TaskSpec(
        id="phase3_narration_mix",
        phase="phase3",
        kind="narration_mix",
        tool_name="add_narration_segments",
        description="混合旁白音频",
        arguments={
            "video_path": merged_path,
            "segments": [item.model_dump() for item in narration_plan.narration],
        },
        depends_on=[task.id for task in tts_tasks],
        resources={"ffmpeg_pool": 1},
        conflict_keys=[f"write:{(WORKSPACE / 'phase3_narrated.mp4').resolve()}"],
        output_kinds=["narrated_video"],
    )
    narration_execution = ExecutionPlan(
        plan_id="phase3_narration_execution",
        phase="phase3",
        goal=state.user_request,
        tasks=[*tts_tasks, narration_task],
    )

    def execute_narration(
        task: TaskSpec,
        dependencies: dict[str, Any],
    ) -> TaskExecutionResult:
        if task.kind == "tts_segment":
            audio_path = Path(str(task.arguments["audio_path"]))
            error = _tts_generate(
                str(task.arguments["text"]),
                str(task.arguments["voice"]),
                audio_path,
            )
            if error or not audio_path.exists():
                raise RuntimeError(error or "TTS 未生成音频文件")
            return TaskExecutionResult(
                data={
                    "audio_path": str(audio_path.resolve()),
                    "text": task.arguments["text"],
                    "start": task.arguments["start"],
                    "end": task.arguments["end"],
                },
                artifacts=[
                    _task_artifact(
                        artifact_id=f"{task.id}_audio",
                        kind="narration_audio",
                        path=audio_path,
                        task=task,
                        metadata={
                            "plan_version": task.arguments.get("plan_version", ""),
                            "scene_id": task.arguments.get("scene_id", ""),
                            "execution_step_id": task.arguments.get("execution_step_id", task.id),
                        },
                    )
                ],
            )
        if not narration_plan.narration:
            return TaskExecutionResult(data={"path": merged_path})
        validation_raw = _TOOL_NAME_MAP["validate_narration_timeline"].invoke(
            {
                "video_path": merged_path,
                "segments": [item.model_dump() for item in narration_plan.narration],
            }
        )
        validation = json.loads(str(validation_raw))
        if validation.get("status") == "fail":
            raise RuntimeError(f"旁白时间线校验失败: {validation}")
        prepared_segments = [
            dependencies[item.id].result for item in tts_tasks
        ]
        raw = compose_prepared_narration(
            video_path=merged_path,
            prepared_segments=prepared_segments,
            output_name="phase3_narrated",
        )
        parsed = json.loads(str(raw))
        path = str(parsed.get("path") or "")
        if parsed.get("status") != "success" or not path:
            raise RuntimeError(f"旁白混合失败: {str(raw)[:300]}")
        return TaskExecutionResult(
            data={"path": path},
            artifacts=[
                _task_artifact(
                    artifact_id="phase3_narrated_video",
                    kind="narrated_video",
                    path=path,
                    task=task,
                )
            ],
        )

    narration_states = _resource_scheduler(registry).run(
        narration_execution,
        execute_narration,
        resume=True,
    )
    narrated_path = str(narration_states[narration_task.id].result["path"])

    state = _phase3_checkpoint(
        state,
        "before_subtitle_generation",
        allow_local_categories={"subtitle"},
    )
    subtitles = _subtitle_segments(narration_plan.narration, state.guidance_context)
    subtitle_task = TaskSpec(
        id="phase3_subtitles",
        phase="phase3",
        kind="subtitle_render",
        tool_name="add_subtitles",
        description="烧录字幕",
        arguments={
            "video_path": narrated_path,
            "subtitles": subtitles,
            "output_name": "phase3_subtitled",
        },
        resources={"ffmpeg_pool": 1},
        conflict_keys=[f"write:{(WORKSPACE / 'phase3_subtitled.mp4').resolve()}"],
        output_kinds=["subtitled_video"],
    )
    subtitle_execution = ExecutionPlan(
        plan_id="phase3_subtitle_execution",
        phase="phase3",
        goal=state.user_request,
        tasks=[subtitle_task],
    )

    def execute_subtitle(
        task: TaskSpec,
        dependencies: dict[str, Any],
    ) -> TaskExecutionResult:
        if not subtitles:
            return TaskExecutionResult(data={"path": narrated_path})
        raw = _TOOL_NAME_MAP["add_subtitles"].invoke(task.arguments)
        try:
            parsed = json.loads(str(raw))
        except json.JSONDecodeError:
            graph_logger.warning("字幕渲染失败，沿用未加字幕视频继续导出: %s", str(raw)[:300])
            emit_benchmark_event(
                "subtitle_render_skipped",
                {
                    "phase": "phase3",
                    "reason": str(raw)[:300],
                    "fallback_react": False,
                },
            )
            return TaskExecutionResult(
                data={"path": narrated_path, "subtitle_status": "skipped", "subtitle_error": str(raw)[:300]}
            )
        path = str(parsed.get("path") or "")
        if parsed.get("status") != "success" or not path:
            graph_logger.warning("字幕渲染未成功，沿用未加字幕视频继续导出: %s", str(raw)[:300])
            emit_benchmark_event(
                "subtitle_render_skipped",
                {
                    "phase": "phase3",
                    "reason": str(raw)[:300],
                    "fallback_react": False,
                },
            )
            return TaskExecutionResult(
                data={"path": narrated_path, "subtitle_status": "skipped", "subtitle_error": str(raw)[:300]}
            )
        return TaskExecutionResult(
            data={"path": path},
            artifacts=[
                _task_artifact(
                    artifact_id="phase3_subtitled_video",
                    kind="subtitled_video",
                    path=path,
                    task=task,
                )
            ],
        )

    subtitle_states = _resource_scheduler(registry).run(
        subtitle_execution,
        execute_subtitle,
        resume=True,
    )
    subtitled_path = str(subtitle_states[subtitle_task.id].result["path"])

    state = _phase3_checkpoint(state, "before_export")
    export_output_name = f"{plan.output_name}_r{REVISION:03d}"
    export_task = TaskSpec(
        id="phase3_export",
        phase="phase3",
        kind="final_export",
        tool_name="export_video",
        description="独占导出最终成片",
        arguments={
            "input_path": subtitled_path,
            "output_name": export_output_name,
            "resolution": plan.resolution,
            "revision": REVISION,
        },
        resources={"export_pool": 1, "ffmpeg_pool": 1},
        conflict_keys=["phase3:final_export"],
        output_kinds=["export_candidate"],
    )
    evaluate_task = TaskSpec(
        id="phase3_evaluator",
        phase="phase3",
        kind="final_evaluator",
        description="校验最终成片时长和文件有效性",
        arguments={"revision": REVISION},
        depends_on=[export_task.id],
        resources={"ffmpeg_pool": 1},
        output_kinds=["phase3_evaluation"],
    )
    export_execution = ExecutionPlan(
        plan_id="phase3_export_execution",
        phase="phase3",
        goal=state.user_request,
        tasks=[export_task, evaluate_task],
    )

    def execute_export(
        task: TaskSpec,
        dependencies: dict[str, Any],
    ) -> TaskExecutionResult:
        if task.kind == "final_export":
            raw = _TOOL_NAME_MAP["export_video"].invoke(
                {
                    "input_path": subtitled_path,
                    "output_name": export_output_name,
                    "resolution": plan.resolution,
                }
            )
            parsed = json.loads(str(raw))
            path = str(parsed.get("path") or "")
            if parsed.get("status") != "success" or not path:
                raise RuntimeError(f"最终导出失败: {str(raw)[:300]}")
            return TaskExecutionResult(
                data={"path": path},
                artifacts=[
                    _task_artifact(
                        artifact_id=f"phase3_export_candidate_r{REVISION:03d}",
                        kind="export_candidate",
                        path=path,
                        task=task,
                        metadata={"revision": REVISION},
                    )
                ],
            )

        final_path = str(dependencies[export_task.id].result["path"])
        raw = _TOOL_NAME_MAP["inspect_video_duration"].invoke(
            {"video_path": final_path}
        )
        metadata = json.loads(str(raw))
        duration = float(metadata.get("duration_seconds", 0.0) or 0.0)
        target = state.target_duration_seconds or duration
        tolerance = max(1.0, target * 0.08)
        decision = (
            "finish"
            if duration > 0 and abs(duration - target) <= tolerance
            else "fallback_react"
        )
        report = {
            "decision": decision,
            "path": final_path,
            "duration_seconds": duration,
            "target_duration_seconds": target,
            "tolerance_seconds": tolerance,
            "revision": REVISION,
        }
        if decision != "finish" and duration > target + tolerance:
            repair_name = f"{Path(final_path).stem}_repair_r{REVISION:03d}"
            try:
                repair_raw = _TOOL_NAME_MAP["cut_video"].invoke(
                    {
                        "input_path": final_path,
                        "start_time": 0.0,
                        "end_time": target,
                        "output_name": repair_name,
                    }
                )
                repair = json.loads(str(repair_raw))
                repaired_path = str(repair.get("path") or "")
                if repair.get("status") == "success" and repaired_path:
                    final_path = repaired_path
                    duration = target
                    decision = "finish"
                    report.update(
                        {
                            "decision": decision,
                            "path": final_path,
                            "duration_seconds": duration,
                            "repair": "trim_tail",
                        }
                    )
            except Exception as exc:
                report["repair_error"] = str(exc)[:300]
        report_path = WORKSPACE / f"phase3_evaluation_r{REVISION:03d}.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if decision != "finish":
            raise RuntimeError(f"最终成片质量校验未通过: {report}")
        _emit_orchestration_event("evaluator_decision", {"phase": "phase3", **report})
        return TaskExecutionResult(
            data=report,
            artifacts=[
                _task_artifact(
                    artifact_id=f"phase3_final_video_r{REVISION:03d}",
                    kind="final_video",
                    path=final_path,
                    task=task,
                    metadata={
                        "duration_seconds": duration,
                        "revision": REVISION,
                        "current": True,
                    },
                ),
                _task_artifact(
                    artifact_id=f"phase3_evaluation_r{REVISION:03d}",
                    kind="phase3_evaluation",
                    path=report_path,
                    task=task,
                    metadata={"revision": REVISION},
                ),
            ],
        )

    export_states = _resource_scheduler(registry).run(
        export_execution,
        execute_export,
        resume=True,
    )
    evaluation = export_states[evaluate_task.id].result
    _emit_orchestration_event(
        "phase3_path",
        {
            "phase3_path": "controlled_dag",
            "fallback_react": False,
            "target_duration_seconds": state.target_duration_seconds,
            "duration_seconds": evaluation["duration_seconds"],
            "source_used_count": len({clip.source_path for clip in plan.clips}),
            "ffmpeg_encode_pass_count": (
                len(plan.clips)
                + 2
                + (1 if narration_plan.narration else 0)
                + (1 if subtitles else 0)
            ),
            "ffmpeg_codec": "libx264",
        },
    )
    return json.dumps(
        {
            "status": "success",
            "executor": "controlled_dag",
            "path": evaluation["path"],
            "duration_seconds": evaluation["duration_seconds"],
            "clip_count": len(plan.clips),
            "narration_segments": len(narration_plan.narration),
            "revision": REVISION,
        },
        ensure_ascii=False,
    )


def _build_short_form_edit_plan(state: AgentState, analysis_context: str) -> ShortFormEditPlan:
    llm = _get_llm(temperature=0.1).bind(max_tokens=2400)
    target_duration = _short_form_target_duration(state)
    response = _invoke_llm(
        llm,
        [
            SystemMessage(content=_short_form_plan_prompt(state)),
            HumanMessage(
                content=(
                    f"用户需求:\n{state.user_request}\n\n"
                    f"Phase 2 蓝图:\n{state.editing_blueprint[:12000]}\n\n"
                    f"素材分析:\n{analysis_context[:30000]}"
                )
            ),
        ],
        "phase3_short_plan",
    )
    try:
        plan = ShortFormEditPlan.model_validate(_parse_json_object(str(response.content)))
    except Exception as exc:
        if fail_fast_model_errors():
            raise_model_failure(
                stage="phase3_short_plan_parse",
                model=MODEL_NAME,
                message=exc,
            )
        raise ShortFormExecutionError(f"短片编辑计划解析失败: {exc}") from exc

    available = {str(path.resolve()) for path in _iter_source_videos()}
    total_duration = 0.0
    for clip in plan.clips:
        resolved = str(Path(clip.source_path).resolve())
        if resolved not in available:
            raise ShortFormExecutionError(f"短片计划引用了未知素材: {clip.source_path}")
        clip.source_path = resolved
        if clip.end <= clip.start:
            raise ShortFormExecutionError("短片计划包含无效裁剪时间段")
        if clip.end - clip.start < 1.8:
            raise ShortFormExecutionError("短片计划包含过短片段")
        total_duration += clip.end - clip.start
    tolerance = max(1.0, target_duration * 0.08)
    if total_duration > target_duration + tolerance:
        excess = total_duration - target_duration
        for clip in reversed(plan.clips):
            clip_duration = clip.end - clip.start
            reducible = max(0.0, clip_duration - 1.8)
            if reducible <= 0:
                continue
            reduction = min(excess, reducible)
            clip.end = round(clip.end - reduction, 3)
            excess -= reduction
            if excess <= 0.01:
                break
        total_duration = sum(item.end - item.start for item in plan.clips)
        if abs(total_duration - target_duration) <= tolerance:
            graph_logger.info(
                "短片计划自动裁尾到目标时长: target=%.1fs adjusted=%.2fs",
                target_duration,
                total_duration,
            )
    if abs(total_duration - target_duration) > tolerance:
        raise ShortFormExecutionError(
            f"短片计划总时长不符合目标 {target_duration:.1f} 秒: {total_duration:.2f}"
        )
    adjusted_narration: list[ShortFormNarration] = []
    for item in plan.narration:
        if item.start >= target_duration:
            continue
        if item.end > target_duration:
            item.end = target_duration
        if item.end <= item.start or item.end > target_duration + tolerance:
            raise ShortFormExecutionError("短片计划包含超出目标时长的旁白时间段")
        adjusted_narration.append(item)
    if not adjusted_narration:
        raise ShortFormExecutionError("短片计划没有可用旁白时间段")
    plan.narration = adjusted_narration
    if not str(plan.output_name or "").strip():
        plan.output_name = _short_form_output_name(state)
    return plan


def _invoke_phase3_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    tool_obj = _TOOL_NAME_MAP.get(tool_name)
    if tool_obj is None:
        raise ShortFormExecutionError(f"Phase 3 工具未注册: {tool_name}")
    run_id = str(uuid.uuid4())
    started = time.perf_counter()
    graph_logger.info("🛠️ Phase3 工具开始: %s run_id=%s", tool_name, run_id)
    emit_benchmark_event(
        "tool_started",
        {"phase": "phase3", "tool_name": tool_name, "run_id": run_id},
    )
    try:
        result = str(tool_obj.invoke(arguments))
    except ModelCallError:
        raise
    except Exception as exc:
        duration = time.perf_counter() - started
        graph_logger.error(
            "❌ Phase3 工具失败: %s run_id=%s duration=%.3fs error=%s",
            tool_name,
            run_id,
            duration,
            exc,
        )
        emit_benchmark_event(
            "tool_failed",
            {
                "phase": "phase3",
                "tool_name": tool_name,
                "run_id": run_id,
                "duration_seconds": round(duration, 3),
                "error": str(exc)[:500],
            },
        )
        raise ShortFormExecutionError(f"{tool_name} 执行失败: {exc}") from exc

    duration = time.perf_counter() - started
    lowered = result.lower()
    failure_markers = ("出错", "失败", "error", '"status": "fail"')
    if any(marker in lowered for marker in failure_markers):
        raise ShortFormExecutionError(f"{tool_name} 返回失败: {result[:500]}")
    graph_logger.info(
        "📦 Phase3 工具完成: %s run_id=%s duration=%.3fs",
        tool_name,
        run_id,
        duration,
    )
    emit_benchmark_event(
        "tool_completed",
        {
            "phase": "phase3",
            "tool_name": tool_name,
            "run_id": run_id,
            "duration_seconds": round(duration, 3),
        },
    )
    return result


def _run_short_form_visual_compose_ffmpeg(
    plan: ShortFormEditPlan,
    *,
    output_name: str,
) -> str | None:
    if not plan.clips:
        return None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    output_path = WORKSPACE / f"{output_name}.mp4"
    cmd = [ffmpeg, "-hide_banner", "-y"]
    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    for index, clip in enumerate(plan.clips):
        duration = max(0.1, float(clip.end) - float(clip.start))
        cmd.extend(
            [
                "-ss",
                f"{float(clip.start):.3f}",
                "-t",
                f"{duration:.3f}",
                "-i",
                clip.source_path,
            ]
        )
        filter_parts.append(
            f"[{index}:v]setpts=PTS-STARTPTS,"
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1"
            f"[v{index}]"
        )
        concat_inputs.append(f"[v{index}]")
    filter_parts.append(
        "".join(concat_inputs)
        + f"concat=n={len(plan.clips)}:v=1:a=0[vout]"
    )
    cmd.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-r",
            "30",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
    except Exception as exc:
        graph_logger.warning("短片 FFmpeg 快速合成不可用，回退旧路径: %s", exc)
        return None
    elapsed = time.perf_counter() - started
    if completed.returncode != 0 or not output_path.exists():
        graph_logger.warning(
            "短片 FFmpeg 快速合成失败，回退旧路径: %s",
            (completed.stderr or completed.stdout)[-800:],
        )
        return None
    emit_benchmark_event(
        "tool_completed",
        {
            "phase": "phase3",
            "tool_name": "short_form_visual_compose_ffmpeg",
            "duration_seconds": round(elapsed, 3),
            "ffmpeg_codec": "libx264",
            "ffmpeg_preset": "veryfast",
        },
    )
    return str(output_path.resolve())


def _run_short_form_editor(state: AgentState) -> str:
    graph_logger.info("⚡ Phase 3 使用短片结构化执行器")
    analysis_context = _build_full_analysis_context()
    plan = _build_short_form_edit_plan(state, analysis_context)
    target_duration = _short_form_target_duration(state)
    output_stem = str(plan.output_name or _short_form_output_name(state)).strip()

    merged_path = _run_short_form_visual_compose_ffmpeg(
        plan,
        output_name=f"{output_stem}_visual",
    )
    fast_visual = bool(merged_path)
    if not merged_path:
        def cut_one(item: tuple[int, ShortFormClip]) -> tuple[int, str]:
            index, clip = item
            raw = _invoke_phase3_tool(
                "cut_video",
                {
                    "input_path": clip.source_path,
                    "start_time": clip.start,
                    "end_time": clip.end,
                    "output_name": f"short_clip_{index:02d}",
                },
            )
            parsed = json.loads(raw)
            return index, str(parsed["path"])

        cut_paths: list[tuple[int, str]] = []
        with ThreadPoolExecutor(
            max_workers=min(len(plan.clips), max(1, FFMPEG_POOL_SIZE)),
            thread_name_prefix="short-form-cut",
        ) as executor:
            futures = {
                executor.submit(cut_one, item): item[0]
                for item in enumerate(plan.clips, start=1)
            }
            for future in as_completed(futures):
                cut_paths.append(future.result())
        ordered_paths = [path for _, path in sorted(cut_paths)]

        merged = json.loads(
            _invoke_phase3_tool(
                "merge_videos",
                {
                    "video_paths": ordered_paths,
                    "output_name": f"{output_stem}_merged",
                    "target_duration": target_duration,
                    "tolerance": 0.02,
                },
            )
        )
        merged_path = str(merged["path"])

    _invoke_phase3_tool(
        "analyze_video",
        {
            "video_path": merged_path,
            "analysis_goal": f"逐段核对{target_duration:.1f}秒短片的画面内容与旁白匹配关系",
        },
    )
    narration = [item.model_dump() for item in plan.narration]
    validation = json.loads(
        _invoke_phase3_tool(
            "validate_narration_timeline",
            {
                "video_path": merged_path,
                "segments": narration,
                "min_segment_duration": 1.2,
                "max_silence_gap": 4.0,
            },
        )
    )
    if validation.get("status") == "fail":
        raise ShortFormExecutionError("旁白时间线校验失败")

    narrated = json.loads(
        _invoke_phase3_tool(
            "add_narration_segments",
            {
                "video_path": merged_path,
                "segments": narration,
                "voice": "Ethan",
                "add_subtitle": True,
                "output_name": f"{output_stem}_narrated",
                "min_narration_coverage_ratio": 0.35,
            },
        )
    )
    if fast_visual:
        final_path = str(narrated["path"])
    else:
        exported = json.loads(
            _invoke_phase3_tool(
                "export_video",
                {
                    "input_path": str(narrated["path"]),
                    "output_name": f"{output_stem}_r{REVISION:03d}",
                    "resolution": "720p",
                },
            )
        )
        final_path = str(exported["path"])
    final_meta = json.loads(
        _invoke_phase3_tool(
            "inspect_video_duration",
            {"video_path": final_path},
        )
    )
    duration = float(final_meta.get("duration_seconds", 0.0) or 0.0)
    _register_revision_final(final_path, "phase3_short_form")
    if abs(duration - target_duration) > max(1.0, target_duration * 0.08):
        raise ShortFormExecutionError(f"最终成片时长不合格: {duration:.3f}s")
    _emit_orchestration_event(
        "phase3_path",
        {
            "phase3_path": "short_form",
            "fallback_react": False,
            "target_duration_seconds": target_duration,
            "duration_seconds": duration,
            "source_used_count": len({clip.source_path for clip in plan.clips}),
            "ffmpeg_encode_pass_count": 2 if fast_visual else len(plan.clips) + 3,
            "ffmpeg_codec": "libx264",
        },
    )
    return json.dumps(
        {
            "status": "success",
            "executor": "short_form",
            "path": final_path,
            "duration_seconds": duration,
            "clip_count": len(plan.clips),
            "narration_segments": len(plan.narration),
            "fast_visual_compose": fast_visual,
        },
        ensure_ascii=False,
    )


def react_editor_node(state: AgentState) -> dict[str, Any]:
    """Phase 3 ReAct Editor: 基于分析数据自主创作视频。

    此节点内部运行一个完整的 ReAct 循环:
    LLM 思考 → 调工具 → 观察结果 → 再思考 → … → 完成
    """
    graph_logger.info("🎬 ═══ Phase 3 开始: ReAct Editor 自主创作 ═══")
    try:
        state = _phase3_checkpoint(
            state,
            "before_timeline_execution",
            allow_local_categories={"general", "narration", "subtitle"},
        )
    except SteeringReplanRequested as exc:
        updated = _state_with_current_guidance(state)
        return {
            "phase": {
                "phase1": "planning",
                "phase2": "researching",
                "phase3": "react",
            }.get(exc.required_phase, "react"),
            "should_end": False,
            "user_request": updated.user_request,
            "guidance_context": updated.guidance_context,
        }

    if _iter_analysis_json_files():
        try:
            final_msg = _run_controlled_editor(state)
            graph_logger.info("🎬 ═══ Phase 3 受控 DAG 执行完成 ═══")
            return {
                "phase": "done",
                "should_end": True,
                "final_output": final_msg,
            }
        except SteeringReplanRequested as exc:
            updated = _state_with_current_guidance(state)
            return {
                "phase": {
                    "phase1": "planning",
                    "phase2": "researching",
                    "phase3": "react",
                }.get(exc.required_phase, "react"),
                "should_end": False,
                "user_request": updated.user_request,
                "guidance_context": updated.guidance_context,
            }
        except ModelCallError:
            raise
        except Exception as exc:
            graph_logger.warning("Phase 3 受控 DAG 失败，回退现有执行路径: %s", exc)
            _emit_orchestration_event(
                "phase3_fallback",
                {"reason": str(exc)[:500]},
            )

    if (
        SHORT_FORM_OPTIMIZATIONS
        and 0 < state.target_duration_seconds <= 20
        and state.editing_blueprint
    ):
        try:
            final_msg = _run_short_form_editor(state)
            graph_logger.info("🎬 ═══ Phase 3 完成 ═══")
            graph_logger.info("📝 最终输出: %s", final_msg[:300])
            return {
                "phase": "done",
                "should_end": True,
                "final_output": final_msg,
            }
        except ModelCallError:
            raise
        except ShortFormExecutionError as exc:
            graph_logger.warning("短片结构化执行失败，回退 ReAct: %s", exc)
            emit_benchmark_event(
                "short_form_fallback",
                {"reason": str(exc)[:500]},
            )

    # ── 构建完整上下文 ──
    analysis_context = _build_full_analysis_context()
    workspace_snapshot = _build_workspace_snapshot()
    user_workspace_snapshot = _build_user_workspace_snapshot()
    memory_experience_text = _load_latest_memory_experience(max_chars=12000)

    user_msg_parts: list[str] = [
        f"## 用户需求\n{state.user_request}",
    ]
    if state.target_duration_seconds > 0:
        user_msg_parts.append(
            f"\n## 目标时长\n{state.target_duration_seconds:.1f} 秒"
        )

    # 剪辑蓝图 (来自 Phase 2 深度研究)
    if state.editing_blueprint:
        user_msg_parts.append(
            f"\n## 剪辑蓝图（由专业剪辑研究员事先制定，请以此为核心指导）\n{state.editing_blueprint}"
        )

    # Phase 1 准备结果摘要
    if state.step_results:
        prep_summary = "\n## Phase 1 素材准备摘要"
        recent = state.step_results[-5:]
        start_i = len(state.step_results) - len(recent)
        for i, r in enumerate(recent, start=start_i + 1):
            prep_summary += f"\n步骤 {i}: {_step_result_text(r)[:600]}"
        user_msg_parts.append(prep_summary)

    user_msg_parts.extend(
        [
            f"\n## 已有素材分析数据\n"
            f"以下是所有视频的多模态分析结果，可供交叉参考:\n\n{analysis_context}",
            f"\n## 当前工作目录文件\n{workspace_snapshot}",
            f"\n## 用户素材目录文件（可直接作为素材参与剪辑）\n{user_workspace_snapshot}",
            f"\n## 历史案例经验（仅供参考，不能覆盖当前任务目标）\n{memory_experience_text}",
            "\n## 开始执行\n"
            "请先核对剪辑蓝图中的片段与分析数据，确认无误后按蓝图顺序执行剪辑。"
            "完成后请总结你的创作过程和最终成品信息。",
        ]
    )

    user_message = "\n".join(user_msg_parts)

    # ── 创建 Phase 3 ReAct Agent ──
    llm = _get_llm(temperature=0.3).bind_tools(
        EDITING_TOOLS,
        parallel_tool_calls=False,
    )
    react_agent = create_react_agent(
        model=llm,
        tools=EDITING_TOOLS,
        prompt=REACT_EDITOR_PROMPT.format(
            workspace=WORKSPACE,
            user_workspace=USER_WORKSPACE,
            memory_experience=MEMORY_EXPERIENCE_DIR,
        ),
    )

    graph_logger.info(
        "🧰 ReAct Editor 工具集: %s",
        ", ".join(getattr(t, "name", "") for t in EDITING_TOOLS),
    )
    graph_logger.info("📝 分析上下文长度: %d 字", len(analysis_context))

    try:
        trace_handler = _RealtimeToolTraceHandler()
        result_state = react_agent.invoke(
            {"messages": [("user", user_message)]},
            config={"recursion_limit": 100, "callbacks": [trace_handler]},
        )
        if model_abort_requested() and fail_fast_model_errors():
            raise_model_failure(
                stage="phase3_react",
                model=MODEL_NAME,
                message="A Phase 3 model call failed.",
            )
        _log_react_tool_trace(result_state)
        final_msg = _extract_final_message(result_state)
        _register_latest_react_video()
    except SteeringReplanRequested as exc:
        updated = _state_with_current_guidance(state)
        return {
            "phase": {
                "phase1": "planning",
                "phase2": "researching",
                "phase3": "react",
            }.get(exc.required_phase, "react"),
            "should_end": False,
            "user_request": updated.user_request,
            "guidance_context": updated.guidance_context,
        }
    except ModelCallError:
        raise
    except Exception as e:
        if fail_fast_model_errors():
            raise_model_failure(
                stage="phase3_react",
                model=MODEL_NAME,
                message=e,
            )
        final_msg = f"ReAct 创作阶段异常: {e}"
        graph_logger.error("❌ ReAct Editor 异常: %s", e, exc_info=True)

    graph_logger.info("🎬 ═══ Phase 3 完成 ═══")
    graph_logger.info("📝 最终输出: %s", final_msg[:300])

    return {
        "phase": "done",
        "should_end": True,
        "final_output": final_msg,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 构建图
# ═══════════════════════════════════════════════════════════════════════════
def build_graph() -> Any:
    """构建 Planner + ReAct 混合架构图。"""

    graph = StateGraph(AgentState)

    # 添加节点
    graph.add_node("steering_entry", steering_entry_node)
    graph.add_node("steering_after_planner", steering_after_planner_node)
    graph.add_node("steering_after_phase1", steering_after_phase1_node)
    graph.add_node("steering_after_material_gap", steering_after_material_gap_node)
    graph.add_node("steering_after_blueprint", steering_after_blueprint_node)
    graph.add_node("planner", planner_node)              # Phase 1: 规划素材准备
    graph.add_node("phase1_scheduler", phase1_scheduler_node)
    graph.add_node("material_gap_evaluator", material_gap_evaluator_node)
    graph.add_node("editing_research", editing_research_node)  # Phase 2: 深度剪辑研究
    graph.add_node("generate_editing_plan", generate_editing_plan_node)
    graph.add_node("validate_editing_plan", validate_editing_plan_node)
    graph.add_node("plan_review_gate", plan_review_gate_node)
    graph.add_node("react_editor", react_editor_node)    # Phase 3: 自主创作

    # Phase 1 边
    graph.add_edge(START, "steering_entry")
    graph.add_conditional_edges("steering_entry", _route_steering_entry)
    graph.add_edge("planner", "steering_after_planner")
    graph.add_conditional_edges("steering_after_planner", _route_after_planner_steering)
    graph.add_edge("phase1_scheduler", "steering_after_phase1")
    graph.add_conditional_edges("steering_after_phase1", _route_after_phase1_steering)
    graph.add_edge("material_gap_evaluator", "steering_after_material_gap")
    graph.add_conditional_edges(
        "steering_after_material_gap",
        _route_after_material_gap_steering,
    )

    # Phase 2 → Phase 3
    graph.add_edge("editing_research", "steering_after_blueprint")
    graph.add_conditional_edges("steering_after_blueprint", _route_after_blueprint_steering)
    graph.add_edge("generate_editing_plan", "validate_editing_plan")
    graph.add_edge("validate_editing_plan", "plan_review_gate")
    graph.add_edge("plan_review_gate", "react_editor")

    # Phase 3 边
    graph.add_conditional_edges(
        "react_editor",
        lambda state: (
            "__end__"
            if state.should_end or state.phase == "done"
            else "planner"
            if state.phase == "planning"
            else "editing_research"
            if state.phase == "researching"
            else "react_editor"
        ),
    )

    return graph.compile()


"""
图的可视化:

Phase 1 (素材准备)              Phase 2 (深度研究)      Phase 3 (自主创作)

    ┌─────────┐
    │  START   │
    └────┬─────┘
         │
         ▼
    ┌─────────┐     需要重新规划
    │ Planner │◄────────────────┐
    └────┬────┘                 │
         │                      │
         ▼                      │
    ┌──────────┐                │
    │ Executor │                │
    └────┬─────┘                │
         │                      │
         ▼                      │
    ┌─────────────┐  replan     │
    │ Prep Router ├─────────────┘
    └──────┬──────┘
           │
      还有步骤 ──► Executor
           │
      所有分析
      JSON就绪
           │
           ▼
   ┌─────────────────┐
   │ Editing Research │   ← 纯推理，无工具
   │ (深度分析素材)  │     输出「剪辑蓝图」
   └────────┬────────┘
            │
            ▼
   ┌──────────────┐
   │ ReAct Editor  │   ← 以蓝图为指导
   │ (执行剪辑)    │     思考→工具→观察→...
   └───────┬──────┘
           │
       ┌───▼───┐
       │  END  │
       └───────┘
"""
