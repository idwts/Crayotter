from __future__ import annotations

import ast
import json
import os
from functools import lru_cache
from pathlib import Path

from .horizon_metrics import requires_semantic_material_grounding


def _read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return default


@lru_cache(maxsize=1)
def _react_editor_prompt() -> str:
    graph_path = Path(__file__).resolve().parents[1] / "script" / "graph.py"
    module = ast.parse(graph_path.read_text(encoding="utf-8"), filename=str(graph_path))
    for node in module.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "REACT_EDITOR_PROMPT"
            for target in targets
        ):
            continue
        value = ast.literal_eval(node.value)
        if isinstance(value, str):
            return value
    raise RuntimeError(f"REACT_EDITOR_PROMPT not found in {graph_path}")


def _compact_rl_system_prompt(prompt_root: Path) -> str:
    return (
        "You are the Phase 3 execution agent for Crayotter. Convert the user's editing request "
        "and existing artifacts into a new, verified final video by calling the provided tools.\n"
        "Work as a persistent editing process: diagnose the prior version, preserve requested content, "
        "choose reusable material, build or revise the timeline, export, inspect, and repair when needed.\n"
        "Tool schemas are authoritative. Use real paths returned by tools and never invent or rename files. "
        "Run up to two independent inspections or cuts together; otherwise read each observation before "
        "the next dependent action. Do not merely describe edits.\n"
        "A successful task ends only after a newly produced final artifact is exported and checked. "
        "Technical failure should be repaired locally instead of restarting valid work.\n"
        f"Workspace: {prompt_root / 'temp'}\n"
        f"User materials: {prompt_root / 'user_temp'}\n"
        f"Reference memory: {prompt_root / 'memory_experience'}"
    )


def _workspace_snapshot(root: Path, relative_dir: str, max_files: int = 20) -> str:
    base = root / relative_dir
    if not base.exists():
        return f"({relative_dir} 不存在)"
    files = [item for item in base.rglob("*") if item.is_file()]
    if not files:
        return f"({relative_dir} 为空)"
    files = sorted(files, key=lambda item: item.stat().st_mtime, reverse=True)[:max_files]
    rows: list[str] = []
    for item in files:
        rel = item.relative_to(root)
        size_mb = item.stat().st_size / (1024 * 1024)
        rows.append(f"- {rel} ({size_mb:.1f}MB)")
    return "\n".join(rows)


def _analysis_context(root: Path, max_files: int = 6, max_chars_per_file: int = 2000) -> str:
    candidates = sorted(
        (list((root / "temp").glob("*_analysis.json")) + list((root / "user_temp").glob("*_analysis.json"))),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return "(无分析数据)"

    blocks: list[str] = []
    for item in candidates[:max_files]:
        try:
            payload = json.loads(item.read_text(encoding="utf-8"))
        except Exception:
            continue
        source_video = str(payload.get("source_video", "")) or item.stem.replace("_analysis", "")
        analysis_text = str(payload.get("analysis_text", "")).strip()
        segments = payload.get("segments", [])
        segment_preview: list[str] = []
        if isinstance(segments, list):
            for seg in segments[:8]:
                if not isinstance(seg, dict):
                    continue
                try:
                    start = float(seg.get("start", 0.0))
                    end = float(seg.get("end", 0.0))
                except Exception:
                    continue
                semantic = str(seg.get("semantic_text", "") or seg.get("description", "")).strip()
                preview = f"t={start:.1f}s-{end:.1f}s"
                if semantic:
                    preview += f": {semantic[:120]}"
                segment_preview.append(preview)
        block = [f"### 源视频: {source_video}", f"- 分析文件: {item.name}"]
        if segment_preview:
            block.append("- 片段摘要:")
            block.extend([f"  {row}" for row in segment_preview])
        if analysis_text:
            block.append(f"- 分析正文:\n{analysis_text[:max_chars_per_file]}")
        blocks.append("\n".join(block))
    return "\n\n".join(blocks) if blocks else "(无分析数据)"


def _first_tool_call_example(tool_names: list[str], metadata: dict) -> str:
    if requires_semantic_material_grounding(metadata) and "analyze_video" in tool_names:
        preferred_tool = "analyze_video"
    else:
        preferred_tool = str(metadata.get("bootstrap_tool_name") or "inspect_video_duration")
    if preferred_tool not in tool_names and tool_names:
        preferred_tool = tool_names[0]
    preferred_path = str(
        metadata.get("bootstrap_video_target")
        or metadata.get("previous_final_target")
        or "user_temp/materials/<video_file>.mp4"
    )
    tool_format = os.environ.get("MULTI_TURN_FORMAT", "hermes").strip().lower()
    if tool_format == "qwen3_coder":
        return (
            "<tool_call>\n"
            f"<function={preferred_tool}>\n"
            "<parameter=video_path>\n"
            f"{preferred_path}\n"
            "</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
    example = {
        "name": preferred_tool,
        "arguments": {"video_path": preferred_path},
    }
    return f"<tool_call>{json.dumps(example, ensure_ascii=False, separators=(',', ':'))}</tool_call>"


def _tool_call_protocol(tool_names: list[str], metadata: dict) -> str:
    example = _first_tool_call_example(tool_names, metadata)
    tool_format = os.environ.get("MULTI_TURN_FORMAT", "hermes").strip().lower()
    if tool_format == "qwen3_coder":
        return (
            "## 训练环境工具调用协议\n"
            "你正在 verl native tool-call 训练环境中。当前 Qwen3.5 使用 qwen3_coder 工具格式。"
            "需要调用工具时，必须只输出下面这种 XML 风格格式，不要使用 Markdown 代码块，"
            "不要输出自然语言解释：\n"
            f"{example}\n"
            "格式要求：\n"
            "- `<tool_call>` 与 `</tool_call>` 必须成对出现。\n"
            "- 工具名写成 `<function=工具名>`，并用 `</function>` 结束。\n"
            "- 每个参数写成 `<parameter=参数名>`，参数值单独占一行，并用 `</parameter>` 结束。\n"
            "- 路径优先使用当前 prompt 中列出的相对路径。\n"
            "- 首轮只调用一个工具。后续同一轮最多提交两个彼此独立的 inspect/cut 工具调用，"
            "系统会异步执行；依赖前序输出的 merge、字幕、混音、export 必须等待结果并逐步调用。\n"
            "如果本轮任务是 tool-call bootstrap，请第一轮直接调用指定工具，不要先写长篇方案。"
        )
    return (
        "## 训练环境工具调用协议\n"
        "你正在 verl native tool-call 训练环境中。需要调用工具时，必须只输出下面这种 hermes 格式，"
        "不要使用 Markdown 代码块，不要使用自然语言伪 JSON，不要省略双引号：\n"
        f"{example}\n"
        "格式要求：\n"
        "- `<tool_call>` 与 `</tool_call>` 必须成对出现。\n"
        "- 标签内部必须是单个 JSON object。\n"
        "- JSON 必须包含 `name` 和 `arguments`。\n"
        "- `arguments` 必须是 object，路径优先使用当前 prompt 中列出的相对路径。\n"
        "- 首轮只调用一个工具。后续同一轮最多提交两个彼此独立的 inspect/cut 工具调用，"
        "系统会异步执行；依赖前序输出的 merge、字幕、混音、export 必须等待结果并逐步调用。\n"
        "如果本轮任务是 tool-call bootstrap，请第一轮直接调用指定工具，不要先写长篇方案。"
    )


def _long_horizon_execution_protocol(tool_names: list[str], metadata: dict) -> str:
    if not metadata.get("long_horizon_task"):
        return ""
    disallowed = []
    if "analyze_video" not in tool_names:
        disallowed.append("analyze_video")
    if "batch_cut_video" not in tool_names:
        disallowed.append("batch_cut_video")
    disallowed_text = "、".join(disallowed) if disallowed else "未列入允许工具的工具"
    previous_target = str(metadata.get("previous_final_target") or "user_temp/previous_versions/<previous>.mp4")
    compact = os.environ.get("CRAYOTTER_RL_COMPACT_PROMPT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    if compact:
        return (
            "## Long-horizon revision contract\n"
            f"- Start by diagnosing the previous version at `{previous_target}` and the explicit feedback.\n"
            "- Preserve all requested content; change only what the feedback requires.\n"
            "- Reuse existing material and analysis before requesting new work.\n"
            "- Submit up to two independent inspections or cuts in one turn when possible; keep dependent timeline and export operations sequential.\n"
            "- Advance through concrete artifacts: diagnosis -> cuts/timeline -> export -> validation -> repair.\n"
            "- Do not stop after inspection or text planning. Produce and verify a new final video.\n"
            "- A failed tool call should trigger a targeted repair using its observation.\n"
            "- The rollout may receive an internal exploration prior; it changes the route, not the user goal."
        )
    branch_strategy = str(metadata.get("branch_strategy") or "").strip()
    branch_name = str(metadata.get("branch_strategy_name") or "").strip()
    branch_instruction = str(metadata.get("branch_strategy_instruction") or "").strip()
    preferred_stages = metadata.get("branch_preferred_stages") or []
    if isinstance(preferred_stages, (list, tuple)):
        preferred_stage_text = " -> ".join(str(item) for item in preferred_stages if str(item).strip())
    else:
        preferred_stage_text = str(preferred_stages)
    branch_block = ""
    if branch_strategy and branch_instruction:
        branch_block = (
            "\n## 本条 rollout 的分支策略\n"
            f"- strategy_id: {branch_strategy}\n"
            f"- strategy_name: {branch_name or branch_strategy}\n"
            f"- strategy_instruction: {branch_instruction}\n"
        )
        if preferred_stage_text:
            branch_block += f"- preferred_stage_order: {preferred_stage_text}\n"
        branch_block += (
            "- 你必须按这个策略做真实剪辑决策分支，而不是只在文字里描述风格。"
            "同一任务的其它 rollout 会采用不同策略，最终成片只做组内相对比较，"
            "用于反推哪些剪辑阶段获得 credit。\n"
        )
    return (
        "## Long-Horizon 离线重剪执行约束\n"
        f"- 禁止调用 {disallowed_text}；本训练环境不依赖远程视频分析 API。\n"
        "- 必须产出一个新的 final artifact，不能只检查素材或只分析上一版。\n"
        "- 如果素材已经列在 `用户素材目录文件` 中，直接用这些相对路径调用剪辑工具。\n"
        "- 像真实 LangGraph 剪辑流程一样按阶段推进：诊断上一版 -> 选择少量关键素材 -> 裁剪 -> 组织 timeline/合并 -> 导出 -> 复检。\n"
        "- 不要把所有素材逐个检查完才开始剪辑；当你已经知道上一版时长和若干候选素材时，应主动进入 cut/merge/export。\n"
        "- cut 生成可用片段后，不要停留在继续检查素材；应优先把已有片段合并成新时间线并导出。\n"
        "- 如果已经有一个或多个 `temp/*.mp4` 片段，下一阶段通常是 `merge_videos` 或 `export_video`，而不是继续检查原素材。\n"
        "- `cut_video` 参数必须是 `input_path`, `start_time`, `end_time`, `output_name`。\n"
        "- `merge_videos` 参数必须是 `video_paths`, `output_name`, `target_duration`。\n"
        "- `export_video` 参数必须是 `input_path`, `output_name`, `resolution`。\n"
        "- 可奖励闭环是：检查上一版 -> 检查素材 -> 裁剪素材 -> 合并/构建 timeline -> 导出 -> 检查导出文件。\n"
        "- 不要把上一版路径直接传给 export_video 冒充新成片。\n\n"
        f"{branch_block}"
        "合法工具调用示例（路径和时间需要替换成当前任务里的真实素材）：\n"
        f"<tool_call>\n<function=inspect_video_duration>\n<parameter=video_path>\n{previous_target}\n</parameter>\n</function>\n</tool_call>\n"
        "<tool_call>\n<function=cut_video>\n<parameter=input_path>\nuser_temp/materials/selected_1_xxx.mp4\n</parameter>\n<parameter=start_time>\n0\n</parameter>\n<parameter=end_time>\n8\n</parameter>\n<parameter=output_name>\nrevision_clip_1\n</parameter>\n</function>\n</tool_call>\n"
        "<tool_call>\n<function=merge_videos>\n<parameter=video_paths>\n[\"temp/revision_clip_1.mp4\"]\n</parameter>\n<parameter=output_name>\nrevision_merged\n</parameter>\n<parameter=target_duration>\n30\n</parameter>\n</function>\n</tool_call>\n"
        "<tool_call>\n<function=export_video>\n<parameter=input_path>\ntemp/revision_merged.mp4\n</parameter>\n<parameter=output_name>\nrevision_final\n</parameter>\n<parameter=resolution>\n1080p\n</parameter>\n</function>\n</tool_call>"
    )


def _medium_horizon_execution_protocol(tool_names: list[str], metadata: dict) -> str:
    if not requires_semantic_material_grounding(metadata):
        return ""
    available = set(tool_names)
    grounding_steps: list[str] = []
    if "analyze_video" in available:
        grounding_steps.append(
            "若候选素材没有可用分析，先对一到两个候选源视频调用 analyze_video，明确其主题和可用时间段"
        )
    if "recall_semantic_segments" in available:
        grounding_steps.append(
            "已有 analysis/semantic_segments 时，先用 recall_semantic_segments 按用户主题召回片段"
        )
    grounding_text = "；".join(grounding_steps) or "先依据已有分析确认素材与主题相关"
    return (
        "## Medium-horizon 素材语义约束\n"
        f"- {grounding_text}。\n"
        "- 在第一次 cut 之前必须建立素材语义依据，不能只看时长后任意裁剪。\n"
        "- 明确排除与用户主题冲突的游戏录屏、新闻截图、错误选题和竖屏黑边素材。\n"
        "- 至少比较两个候选片段的主题相关性，再选择用于开场和主时间线的素材。\n"
        "- 语义确认后按 analysis 返回的时间段裁剪，完成 timeline、导出和导出后复检。\n"
        "- 不要为了满足流程而使用弱相关素材；素材不确定时应继续分析或换候选。"
    )


def build_phase3_messages(
    *,
    user_request: str,
    target_duration_seconds: float,
    editing_blueprint: str,
    runtime_root: str | Path,
    tool_names: list[str],
    task_metadata: dict | None = None,
    display_runtime_root: str | Path | None = None,
) -> list[dict[str, str]]:
    root = Path(runtime_root).resolve()
    prompt_root = root if display_runtime_root is None else Path(display_runtime_root)
    compact_prompt = os.environ.get("CRAYOTTER_RL_COMPACT_PROMPT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    if compact_prompt:
        system_prompt = _compact_rl_system_prompt(prompt_root)
    else:
        system_prompt = _react_editor_prompt().format(
            workspace=prompt_root / "temp",
            user_workspace=prompt_root / "user_temp",
            memory_experience=prompt_root / "memory_experience",
        )

    memory_text = _read_text(root / "memory_experience" / "latest_skills.md", "(无历史经验)")
    user_parts = [
        f"## 用户需求\n{user_request}",
        f"\n## 目标时长\n{target_duration_seconds:.1f} 秒",
    ]
    if editing_blueprint.strip():
        user_parts.append(f"\n## 剪辑蓝图\n{editing_blueprint.strip()}")
    metadata = task_metadata or {}
    user_parts.append("\n" + _tool_call_protocol(tool_names, metadata))
    long_horizon_protocol = _long_horizon_execution_protocol(tool_names, metadata)
    if long_horizon_protocol:
        user_parts.append("\n" + long_horizon_protocol)
    medium_horizon_protocol = _medium_horizon_execution_protocol(tool_names, metadata)
    if medium_horizon_protocol:
        user_parts.append("\n" + medium_horizon_protocol)
    if metadata:
        metadata_lines: list[str] = []
        if metadata.get("long_horizon_task"):
            metadata_lines.append("- 任务类型: long-horizon agentic revision / 多轮反馈重剪")
        if metadata.get("tool_call_bootstrap"):
            metadata_lines.append("- 任务类型: tool-call bootstrap / 工具调用格式冷启动")
        for key in (
            "case_id",
            "revision_round",
            "previous_version_available",
            "previous_final_target",
            "bootstrap_tool_name",
            "bootstrap_video_target",
            "feedback",
            "preserve_requirements",
            "change_requirements",
            "branching_hint",
            "branch_strategy",
            "branch_strategy_name",
            "branch_strategy_instruction",
            "branch_preferred_stages",
        ):
            value = metadata.get(key)
            if value in (None, "", []):
                continue
            if isinstance(value, (list, tuple)):
                rendered = "; ".join(str(item) for item in value)
            else:
                rendered = str(value)
            metadata_lines.append(f"- {key}: {rendered}")
        if metadata_lines:
            user_parts.append("\n## 长程任务状态\n" + "\n".join(metadata_lines))
    first_tool_call = _first_tool_call_example(tool_names, metadata)
    user_parts.extend(
        [
            f"\n## 已有素材分析数据\n{_analysis_context(root)}",
            f"\n## 当前工作目录文件\n{_workspace_snapshot(root, 'temp')}",
            f"\n## 用户素材目录文件\n{_workspace_snapshot(root, 'user_temp')}",
            f"\n## 历史案例经验（仅供参考）\n{memory_text[:2000 if compact_prompt else 8000]}",
            f"\n## 本轮允许调用的工具\n{', '.join(tool_names)}",
            "\n## 开始执行\n"
            "第一轮回复必须直接输出一个合法 `<tool_call>...</tool_call>`，不要先写创作思路、解释、Markdown 或自然语言。"
            "看到工具返回后，再用后续工具调用推进剪辑。"
            "如果你认为当前素材已经足够，也必须先调用检查或剪辑相关工具；不要联网搜索。"
            "如果这是多轮反馈重剪任务，请先检查上一版成片和现有素材，"
            "明确哪些片段要保留、哪些问题要修正，然后重新导出并检查最终成片。",
            "\n## 首轮必须输出\n"
            "你的下一条 assistant 消息必须只包含下面这一行，不能增加任何其它字符：\n"
            f"{first_tool_call}",
        ]
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
