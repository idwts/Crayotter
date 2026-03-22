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
import logging
import operator
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import START, StateGraph
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from tools import ALL_TOOLS, MEMORY_EXPERIENCE_DIR, USER_WORKSPACE, WORKSPACE

# ═══════════════════════════════════════════════════════════════════════════
# API 配置 - 从 agent.py 传入
# ═══════════════════════════════════════════════════════════════════════════
API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
BASE_URL: str = "https://api.openai.com/v1"
MODEL_NAME: str = "gpt-4o"

graph_logger = logging.getLogger("graph")


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
    status: str = Field(default="pending", description="pending/running/done/failed")
    result: str = Field(default="", description="执行结果")


class Plan(BaseModel):
    """Phase 1 的执行计划（仅素材准备阶段）"""

    goal: str = Field(description="用户的最终目标")
    analysis: str = Field(default="", description="需求分析")
    steps: list[Step] = Field(default_factory=list, description="有序步骤列表")


class AgentState(BaseModel):
    """Planner + ReAct 混合 Agent 的全局状态"""

    # 用户输入
    user_request: str = ""

    # Phase 1: 结构化规划
    plan: Plan | None = None
    current_step_index: int = 0
    step_results: Annotated[list[str], operator.add] = Field(default_factory=list)
    messages: Annotated[list[Any], operator.add] = Field(default_factory=list)

    # 时长控制
    target_duration_seconds: float = 0.0

    # Phase 标记: "planning" → "researching" → "react" → "done"
    phase: str = "planning"

    # Phase 2: 剪辑研究蓝图
    editing_blueprint: str = ""

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
    )


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
        response = llm.invoke(
            [HumanMessage(content=f"{prompt}\n\n用户需求: {user_request}")]
        )
        text = str(response.content).strip()
        m = re.search(r"(\d+(?:\.\d+)?)", text)
        if m:
            value = float(m.group(1))
            if value > 0:
                return value
    except Exception:
        pass
    return 300.0


def _recommend_material_counts(target_duration_seconds: float) -> dict[str, int]:
    """根据目标时长推荐素材搜索数量与下载数量区间。"""
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
    roots = [WORKSPACE, USER_WORKSPACE]
    blocked_prefixes = (
        "merged_",
        "final_",
        "transitioned_",
        "narrated_",
        "output_",
        "exported_",
    )
    seen: set[str] = set()
    videos: list[Path] = []
    for root in roots:
        for fp in root.glob("*.mp4"):
            name = fp.name
            if name.startswith(blocked_prefixes) or "_clip_" in name or "_analysis" in name:
                continue
            key = str(fp.resolve(strict=False))
            if key in seen:
                continue
            seen.add(key)
            videos.append(fp)
    return videos


def _iter_analysis_json_files() -> list[Path]:
    """遍历分析文件（temp + user_temp）。"""
    roots = [WORKSPACE, USER_WORKSPACE]
    seen: set[str] = set()
    files: list[Path] = []
    for root in roots:
        for fp in root.glob("*_analysis.json"):
            key = str(fp.resolve(strict=False))
            if key in seen:
                continue
            seen.add(key)
            files.append(fp)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


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
    candidates = [
        MEMORY_EXPERIENCE_DIR / "latest_skills.md",
        MEMORY_EXPERIENCE_DIR / "latest_skills.txt",
    ]
    for c in candidates:
        if c.exists():
            try:
                return c.read_text(encoding="utf-8")[:max_chars].strip() or "(经验文件为空)"
            except Exception:
                continue

    try:
        files = sorted(
            MEMORY_EXPERIENCE_DIR.glob("experience_*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if files:
            return files[0].read_text(encoding="utf-8")[:max_chars].strip() or "(经验文件为空)"
    except Exception:
        pass
    return "(暂无经验文件)"


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


def _run_deterministic_download_step(step: Step, counts: dict[str, int]) -> str:
    """下载步骤的确定性执行：先获取 selected_videos，再逐个下载。"""
    rank_tool = _TOOL_NAME_MAP.get("rank_video_candidates")
    download_tool = _TOOL_NAME_MAP.get("download_bilibili_video")
    if rank_tool is None or download_tool is None:
        return "步骤执行失败: 缺少 rank_video_candidates 或 download_bilibili_video 工具"

    top_k = _infer_download_top_k(step.description, counts)
    max_review = int(counts.get("mllm_review", 30))
    graph_logger.info(
        "📥 下载步骤走确定性路径: top_k=%d, max_review=%d",
        top_k,
        max_review,
    )

    try:
        rank_raw = rank_tool.invoke(
            {
                "candidates_json": "[]",
                "top_k": top_k,
                "max_review": max_review,
            }
        )
    except Exception as e:
        return f"步骤执行失败: 排序阶段异常: {e}"

    try:
        rank_data = json.loads(str(rank_raw))
    except Exception:
        return f"步骤执行失败: 无法解析排序结果: {str(rank_raw)[:400]}"

    selected_videos = rank_data.get("selected_videos", [])
    if not isinstance(selected_videos, list) or not selected_videos:
        return "步骤执行失败: 排序结果中没有 selected_videos，无法下载"

    success_items: list[str] = []
    fail_items: list[str] = []

    for i, video in enumerate(selected_videos, start=1):
        if not isinstance(video, dict):
            continue
        url = str(video.get("url") or video.get("bvid") or "").strip()
        title = str(video.get("title") or "").strip()
        bvid = str(video.get("bvid") or "").strip()
        if not url:
            fail_items.append(f"{i}. {title or 'unknown'}: 缺少 url/bvid")
            continue

        safe_tail = bvid[-6:] if bvid else f"{i:02d}"
        filename = f"selected_{i}_{safe_tail}"
        try:
            download_raw = download_tool.invoke(
                {"url": url, "filename": filename, "prefer_h264": True}
            )
            parsed = json.loads(str(download_raw))
            if parsed.get("status") == "success":
                path = str(parsed.get("path", ""))
                success_items.append(f"{i}. {title or bvid or url} -> {path}")
            else:
                fail_items.append(
                    f"{i}. {title or bvid or url}: {str(download_raw)[:180]}"
                )
        except Exception as e:
            fail_items.append(f"{i}. {title or bvid or url}: {e}")

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
    return "\n".join(summary_parts)


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
- 尽量搜集丰富的资源：使用多关键词扩展与分页搜索，扩大搜索广度
- 先广度再精选：先广度搜索 → MLLM 筛选 → 下载最优一批
- 调用参数显式化：搜索和筛选工具必须显式传参（max_results / pages / max_total_results / top_k / max_review）
- `top_k` 必须由你根据任务复杂度、目标时长、候选质量自主决定，不要固定成 5
- 所有下载的视频都必须分析：每个视频需要调用一次 analyze_video
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
        {{"id": 1, "description": "具体做什么", "tool_hint": "建议工具名"}},
        ...
    ]
}}

"""


def planner_node(state: AgentState) -> dict[str, Any]:
    """Phase 1 Planner: 分析需求，生成素材准备步骤。"""
    graph_logger.info("🎯 Phase 1 — Planner 开始规划素材准备")
    llm = _get_llm().bind(max_tokens=4096)

    target_duration = _extract_target_duration_seconds(state.user_request)
    counts = _recommend_material_counts(target_duration)
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
        "\n## 历史经验（skills）\n"
        + _load_latest_memory_experience(max_chars=12000)
    )
    if state.step_results:
        context_parts.append("\n## 已完成的步骤结果")
        for i, r in enumerate(state.step_results, start=1):
            context_parts.append(f"步骤 {i}: {r[:300]}")

    response = llm.invoke([
        SystemMessage(content=prompt),
        HumanMessage(content="\n".join(context_parts)),
    ])

    # 解析 JSON 计划
    try:
        content = str(response.content)
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        plan_data = json.loads(content)
        plan = Plan(**plan_data)
    except Exception:
        plan = Plan(
            goal=state.user_request,
            analysis="自动生成的素材准备计划",
            steps=[
                Step(id=1, description="搜索相关视频素材", tool_hint="search_bilibili_video"),
                Step(id=2, description="筛选候选视频", tool_hint="rank_video_candidates"),
                Step(id=3, description="下载最佳素材", tool_hint="download_bilibili_video"),
                Step(id=4, description="分析所有已下载视频", tool_hint="analyze_video"),
            ],
        )

    for s in plan.steps:
        s.tool_hint = _normalize_tool_hint(s)

    graph_logger.info("📋 素材准备计划 (%d 步): %s", len(plan.steps), plan.goal)
    for s in plan.steps:
        graph_logger.info("   [%d] %s → %s", s.id, s.description, s.tool_hint)
    if target_duration > 0:
        graph_logger.info("⏱ 目标时长: %.1fs", target_duration)

    return {
        "plan": plan,
        "current_step_index": 0,
        "target_duration_seconds": target_duration,
        "phase": "planning",
    }


# ═══════════════════════════════════════════════════════════════════════════
# ▸ Phase 1 — Node 2: Executor (执行一个素材准备步骤)
# ═══════════════════════════════════════════════════════════════════════════
EXECUTOR_PROMPT = """\
你是视频素材准备执行器。你的任务是执行一个具体的素材准备步骤。
请调用合适的工具来完成任务，完成后总结执行结果。
工作目录:
- temp: {workspace}
- user_temp: {user_workspace}

重要:
- 操作结果中的文件路径非常重要，请在总结中完整保留
- 对历史步骤里出现过的文件路径，后续调用必须逐字复用；禁止自行改名或猜测路径
- 调 search_bilibili_video / rank_video_candidates 时，务必显式传入数量参数
- rank_video_candidates 必须显式传入 top_k，且 top_k 由你自主决定（不要依赖默认值 5）
- 若当前步骤是"搜索素材"，只做搜索与汇总，不要提前调用 rank_video_candidates
- 若当前步骤是"排序候选"，基于已累计候选池做排序
- 排序完成后优先使用返回的 selected_videos 进入下载流程
- analyze_video 仅对原始下载的源视频调用，不要分析中间产物
- 每个视频只需分析一次，不要重复分析已有 _analysis.json 的视频
"""

_executor_agents_cache: dict[tuple[str, ...], Any] = {}


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


def _resolve_step_tools(step: Step) -> list[Any]:
    """根据步骤的 tool_hint 解析应使用的工具子集（严格单工具白名单）。"""
    normalized_hint = _normalize_tool_hint(step)
    step.tool_hint = normalized_hint
    tool_obj = _TOOL_NAME_MAP.get(normalized_hint)
    if tool_obj is not None:
        return [tool_obj]
    # 兜底，避免空工具集
    fallback = _TOOL_NAME_MAP.get("search_bilibili_video")
    return [fallback] if fallback is not None else PREP_TOOLS[:1]


def _get_executor_agent(step: Step) -> tuple[Any, list[Any]]:
    """获取（或缓存）对应步骤工具集的 ReAct 子 Agent。"""
    selected_tools = _resolve_step_tools(step)
    key = tuple(sorted(getattr(t, "name", "") for t in selected_tools))

    agent = _executor_agents_cache.get(key)
    if agent is None:
        llm = _get_llm()
        agent = create_react_agent(
            model=llm,
            tools=selected_tools,
            prompt=EXECUTOR_PROMPT.format(
                workspace=WORKSPACE,
                user_workspace=USER_WORKSPACE,
            ),
        )
        _executor_agents_cache[key] = agent
    return agent, selected_tools


def executor_node(state: AgentState) -> dict[str, Any]:
    """Phase 1 Executor: 执行当前素材准备步骤。"""
    plan = state.plan
    idx = state.current_step_index

    if plan is None or idx >= len(plan.steps):
        graph_logger.info("Executor: 所有准备步骤已完成或计划为空")
        return {"should_end": True}

    step = plan.steps[idx]
    step.status = "running"
    graph_logger.info("🔧 Executor 步骤 [%d]: %s", step.id, step.description)

    # ── 构建执行上下文 ──
    context_parts: list[str] = [f"当前任务: {step.description}"]
    if step.tool_hint:
        context_parts.append(f"建议使用工具: {step.tool_hint}")
    if state.target_duration_seconds > 0:
        context_parts.append(f"目标时长(参考): {state.target_duration_seconds:.1f} 秒")

    counts = _recommend_material_counts(state.target_duration_seconds)
    context_parts.append(
        f"数量建议: max_results={counts['search_per_source']}, "
        f"pages={counts['search_pages']}, max_total_results={counts['max_candidates']}, "
        f"max_review={counts['mllm_review']}, "
        f"top_k_range={counts['top_k_min']}~{counts['top_k_max']} (由你自主决定)"
    )

    context_parts.append(
        "\n当前工作目录文件(优先复用真实路径):\n" + _build_workspace_snapshot()
    )
    context_parts.append(
        "\n用户素材目录文件(可直接复用):\n" + _build_user_workspace_snapshot()
    )
    context_parts.append(
        "\n历史经验（skills）:\n" + _load_latest_memory_experience(max_chars=8000)
    )

    # 最近 3 步结果
    if state.step_results:
        context_parts.append("\n之前步骤的结果:")
        recent = state.step_results[-3:]
        start_i = len(state.step_results) - len(recent)
        for i, r in enumerate(recent, start=start_i + 1):
            context_parts.append(f"  步骤 {i}: {r[:800]}")

    # 分析步骤：注入源视频过滤提示
    tool_hint_lower = (step.tool_hint or "").strip().lower()

    # 下载步骤：走确定性执行，避免模型使用占位BV号
    if tool_hint_lower == "download_bilibili_video":
        final_msg = _run_deterministic_download_step(step, counts)
        step.status = "done" if "成功" in final_msg else "failed"
        step.result = final_msg
        graph_logger.info("✅ 步骤 [%d] 完成(确定性下载): %s", step.id, final_msg[:220])
        return {"step_results": [final_msg], "current_step_index": idx + 1}

    if tool_hint_lower == "analyze_video":
        already_analyzed_stems: set[str] = set()
        for jp in _iter_analysis_json_files():
            already_analyzed_stems.add(jp.stem.replace("_analysis", ""))

        source_videos = [
            fp.name
            for fp in _iter_source_videos()
        ]
        not_analyzed = [
            v for v in source_videos if v.rsplit(".", 1)[0] not in already_analyzed_stems
        ]
        analyzed = [
            v for v in source_videos if v.rsplit(".", 1)[0] in already_analyzed_stems
        ]

        context_parts.append(
            "\n⚠️ 分析规则:\n"
            "- 仅分析原始下载视频，不分析中间产物\n"
            "- 不重复分析已有 _analysis.json 的视频\n"
            "- 快速完成所有视频的分析"
        )
        if not_analyzed:
            context_parts.append(f"待分析: {', '.join(not_analyzed)}")
        if analyzed:
            context_parts.append(f"已分析(跳过): {', '.join(analyzed)}")
        if not not_analyzed and analyzed:
            context_parts.append("所有源视频均已分析完毕，可直接进入下一步。")
        graph_logger.info(
            "🧠 源视频过滤: 待分析=%d, 已分析=%d", len(not_analyzed), len(analyzed)
        )

    context = "\n".join(context_parts)

    # ── 调用 ReAct 子 Agent ──
    executor, selected_tools = _get_executor_agent(step)
    graph_logger.info(
        "🧰 步骤 [%d] 工具白名单: %s",
        step.id,
        ", ".join(getattr(t, "name", "") for t in selected_tools),
    )
    try:
        result_state = executor.invoke(
            {"messages": [("user", context)]},
            config={"recursion_limit": 50},
        )
    except Exception as e:
        final_msg = f"步骤执行失败: {e}"
        step.status = "failed"
        step.result = final_msg
        graph_logger.error("❌ 步骤 [%d] 异常: %s", step.id, e)
        return {"step_results": [final_msg], "current_step_index": idx + 1}

    final_msg = _extract_final_message(result_state)
    step.status = "done"
    step.result = final_msg
    graph_logger.info("✅ 步骤 [%d] 完成: %s", step.id, final_msg[:200])

    return {"step_results": [final_msg], "current_step_index": idx + 1}


# ═══════════════════════════════════════════════════════════════════════════
# ▸ Phase 1 → Phase 3 路由: Prep Router
# ═══════════════════════════════════════════════════════════════════════════
def prep_router_node(state: AgentState) -> dict[str, Any]:
    """检查素材准备是否完成，决定继续执行还是进入 Phase 3。"""
    plan = state.plan
    idx = state.current_step_index

    # ── 还有准备步骤未执行 → 让 executor 继续 ──
    if plan and idx < len(plan.steps):
        graph_logger.info(
            "📌 Prep Router: 步骤 %d/%d，继续 Phase 1", idx + 1, len(plan.steps)
        )
        return {}

    # ── 所有准备步骤已执行完 → 检查分析 JSON ──
    analysis_files = _iter_analysis_json_files()
    source_videos = [
        fp
        for fp in _iter_source_videos()
    ]

    if analysis_files:
        graph_logger.info(
            "✅ Phase 1 完成: %d 个分析文件, %d 个源视频 → 进入 Phase 2 深度剪辑研究",
            len(analysis_files),
            len(source_videos),
        )
        return {"phase": "researching"}

    # ── 无分析 JSON → 需要重新规划 ──
    graph_logger.warning("⚠️ 准备步骤已完成但无分析 JSON，触发重新规划")
    return {"phase": "replan", "plan": None}


def route_after_prep_router(
    state: AgentState,
) -> Literal["executor", "planner", "editing_research"]:
    """Prep Router 之后的路由。"""
    if state.phase == "researching":
        return "editing_research"
    if state.phase == "replan":
        return "planner"
    if state.plan and state.current_step_index < len(state.plan.steps):
        return "executor"
    # 兜底: 重新规划
    return "planner"


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
- **自然流畅优先**: 片段之间的衔接必须有逻辑关联，避免生硬跳跃
- **多源混剪**: 从不同视频取材，避免长时间只用一个源
- **情绪连贯**: 相邻片段的情绪过渡要平滑，除非刻意制造反差
- **视觉匹配**: 相邻片段的色调、镜头风格尽量协调
- **时长精准**: 每段时长建议要务实，基于分析数据中的实际可用时长
- **严禁编造**: 所有时间段必须来自分析数据中的真实时间范围
"""


def editing_research_node(state: AgentState) -> dict[str, Any]:
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
            prep_summary += f"\n步骤 {i}: {r[:600]}"
        user_parts.append(prep_summary)

    user_parts.extend([
        f"\n## 所有视频的详细分析数据\n"
        f"请逐段精读以下所有素材分析，这是你制定剪辑蓝图的唯一信息来源:\n\n{analysis_context}",
        f"\n## 当前工作目录文件\n{workspace_snapshot}",
        f"\n## 用户素材目录文件（可作为补充素材来源）\n{user_workspace_snapshot}",
        f"\n## 历史经验（skills，请结合并优化）\n{memory_experience_text}",
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
        response = llm.invoke([
            SystemMessage(content=EDITING_RESEARCH_PROMPT),
            HumanMessage(content=user_message),
        ])
        blueprint = str(response.content).strip()
    except Exception as e:
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


def react_editor_node(state: AgentState) -> dict[str, Any]:
    """Phase 3 ReAct Editor: 基于分析数据自主创作视频。

    此节点内部运行一个完整的 ReAct 循环:
    LLM 思考 → 调工具 → 观察结果 → 再思考 → … → 完成
    """
    graph_logger.info("🎬 ═══ Phase 3 开始: ReAct Editor 自主创作 ═══")

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
            prep_summary += f"\n步骤 {i}: {r[:600]}"
        user_msg_parts.append(prep_summary)

    user_msg_parts.extend(
        [
            f"\n## 已有素材分析数据\n"
            f"以下是所有视频的多模态分析结果，可供交叉参考:\n\n{analysis_context}",
            f"\n## 当前工作目录文件\n{workspace_snapshot}",
            f"\n## 用户素材目录文件（可直接作为素材参与剪辑）\n{user_workspace_snapshot}",
            f"\n## 历史经验（skills，请优先遵循）\n{memory_experience_text}",
            "\n## 开始执行\n"
            "请先核对剪辑蓝图中的片段与分析数据，确认无误后按蓝图顺序执行剪辑。"
            "完成后请总结你的创作过程和最终成品信息。",
        ]
    )

    user_message = "\n".join(user_msg_parts)

    # ── 创建 Phase 3 ReAct Agent ──
    llm = _get_llm(temperature=0.3)  # 略高温度鼓励创意
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
        result_state = react_agent.invoke(
            {"messages": [("user", user_message)]},
            config={"recursion_limit": 100},
        )
        _log_react_tool_trace(result_state)
        final_msg = _extract_final_message(result_state)
    except Exception as e:
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
# 路由函数
# ═══════════════════════════════════════════════════════════════════════════
def route_after_executor(state: AgentState) -> Literal["prep_router"]:
    """Executor 之后总是进入 Prep Router 决策。"""
    return "prep_router"


# ═══════════════════════════════════════════════════════════════════════════
# 构建图
# ═══════════════════════════════════════════════════════════════════════════
def build_graph() -> Any:
    """构建 Planner + ReAct 混合架构图。"""

    graph = StateGraph(AgentState)

    # 添加节点
    graph.add_node("planner", planner_node)              # Phase 1: 规划素材准备
    graph.add_node("executor", executor_node)            # Phase 1: 执行准备步骤
    graph.add_node("prep_router", prep_router_node)      # Phase 1→1.5: 路由决策
    graph.add_node("editing_research", editing_research_node)  # Phase 2: 深度剪辑研究
    graph.add_node("react_editor", react_editor_node)    # Phase 3: 自主创作

    # Phase 1 边
    graph.add_edge(START, "planner")          # 入口 → Planner
    graph.add_edge("planner", "executor")     # Planner → Executor
    graph.add_conditional_edges(              # Executor → Prep Router
        "executor",
        route_after_executor,
    )
    graph.add_conditional_edges(              # Prep Router → Executor / Planner / Research
        "prep_router",
        route_after_prep_router,
    )

    # Phase 2 → Phase 3
    graph.add_edge("editing_research", "react_editor")  # 深度研究 → ReAct Editor

    # Phase 3 边
    graph.add_edge("react_editor", "__end__")  # ReAct Editor → 结束

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
