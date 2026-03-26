from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from openai import OpenAI

# ═══════════════════════════════════════════════════════════════════════════
# API 配置 - 请在此处设置你的密钥和中转站地址
# ═══════════════════════════════════════════════════════════════════════════

API_KEY = os.environ.get("CRAYOTTER_API_KEY") or os.environ.get("DASHSCOPE_API_KEY") or "EMPTY"
BASE_URL = os.environ.get("CRAYOTTER_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MODEL_NAME = os.environ.get("CRAYOTTER_MODEL_NAME", "qwen-plus")

VIDEO_API_KEY = os.environ.get("CRAYOTTER_VIDEO_API_KEY") or API_KEY
VIDEO_BASE_URL = os.environ.get("CRAYOTTER_VIDEO_BASE_URL", BASE_URL)
VIDEO_MODEL_NAME = os.environ.get("CRAYOTTER_VIDEO_MODEL_NAME", "qwen-vl-max-latest")

# TTS API configuration (DashScope TTS)
TTS_API_KEY = os.environ.get("CRAYOTTER_TTS_API_KEY") or API_KEY
TTS_BASE_URL = os.environ.get("CRAYOTTER_TTS_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
TTS_MODEL_NAME = os.environ.get("CRAYOTTER_TTS_MODEL_NAME", "qwen-tts-latest")

# 配置 agent 日志（始终写到仓库根目录 logs/）
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def _select_logs_dir() -> Path:
    primary = PROJECT_ROOT / "logs"
    fallback = PROJECT_ROOT / "runtime_logs"

    for candidate in (primary, fallback):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except Exception:
            continue

    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


LOGS_DIR = _select_logs_dir()

_agent_log_file = LOGS_DIR / f"agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
_agent_file_handler = logging.FileHandler(_agent_log_file, encoding='utf-8')
_agent_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
_agent_file_handler.setLevel(logging.DEBUG)

agent_logger = logging.getLogger('agent')
if not agent_logger.handlers:
    agent_logger.addHandler(_agent_file_handler)
    agent_logger.setLevel(logging.DEBUG)
    agent_logger.info(f"Agent日志已初始化: {_agent_log_file}")

RuntimeEventCallback = Callable[[dict[str, Any]], None]

_PLAN_SUMMARY_RE = re.compile(r"素材准备计划 \((\d+) 步\): (.+)")
_STEP_START_RE = re.compile(r"Executor 步骤 \[(\d+)\]: (.+)")
_STEP_COMPLETE_RE = re.compile(r"步骤 \[(\d+)\] 完成(?:\(确定性下载\))?: (.+)")
_TOOL_CALL_RE = re.compile(r"Phase3 工具调用: ([a-zA-Z_][\w]*) args=(.+)")
_TOOL_RESULT_RE = re.compile(r"Phase3 工具结果: ([a-zA-Z_][\w]*) -> (.+)")

from graph import AgentState, build_graph
from tools import MEMORY_EXPERIENCE_DIR, USER_WORKSPACE, WORKSPACE, analyze_video

MEMORY_EXPERIENCE_DIR.mkdir(parents=True, exist_ok=True)
USER_WORKSPACE.mkdir(parents=True, exist_ok=True)

# 将 tools / graph 的所有日志同步写入 agent_*.log（工具调用日志可见）
for _lg_name in ('tools', 'graph'):
    _lg = logging.getLogger(_lg_name)
    if _agent_file_handler not in _lg.handlers:
        _lg.addHandler(_agent_file_handler)


def _emit_runtime_event(
    callback: RuntimeEventCallback | None,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if callback is None:
        return
    callback(
        {
            "type": event_type,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "payload": payload or {},
        }
    )


def _build_runtime_settings() -> dict[str, str]:
    return {
        "api_key": API_KEY,
        "base_url": BASE_URL,
        "model_name": MODEL_NAME,
        "video_api_key": VIDEO_API_KEY,
        "video_base_url": VIDEO_BASE_URL,
        "video_model_name": VIDEO_MODEL_NAME,
        "tts_api_key": TTS_API_KEY,
        "tts_base_url": TTS_BASE_URL,
        "tts_model_name": TTS_MODEL_NAME,
    }


def apply_runtime_config(config: Mapping[str, Any] | None = None) -> dict[str, str]:
    global API_KEY, BASE_URL, MODEL_NAME
    global VIDEO_API_KEY, VIDEO_BASE_URL, VIDEO_MODEL_NAME
    global TTS_API_KEY, TTS_BASE_URL, TTS_MODEL_NAME

    config = dict(config or {})

    API_KEY = str(config.get("api_key") or API_KEY or "").strip()
    BASE_URL = str(config.get("base_url") or BASE_URL or "").strip()
    MODEL_NAME = str(config.get("model_name") or MODEL_NAME or "").strip()

    VIDEO_API_KEY = str(config.get("video_api_key") or VIDEO_API_KEY or API_KEY).strip()
    VIDEO_BASE_URL = str(config.get("video_base_url") or VIDEO_BASE_URL or BASE_URL).strip()
    VIDEO_MODEL_NAME = str(config.get("video_model_name") or VIDEO_MODEL_NAME or "").strip()

    TTS_API_KEY = str(config.get("tts_api_key") or TTS_API_KEY or API_KEY).strip()
    TTS_BASE_URL = str(config.get("tts_base_url") or TTS_BASE_URL or BASE_URL).strip()
    TTS_MODEL_NAME = str(config.get("tts_model_name") or TTS_MODEL_NAME or "").strip()

    import graph as graph_module
    import tools as tools_module

    graph_module.API_KEY = API_KEY
    graph_module.BASE_URL = BASE_URL
    graph_module.MODEL_NAME = MODEL_NAME
    tools_module.configure(
        api_key=API_KEY,
        base_url=BASE_URL,
        model_name=MODEL_NAME,
        video_api_key=VIDEO_API_KEY,
        video_base_url=VIDEO_BASE_URL,
        video_model_name=VIDEO_MODEL_NAME,
        tts_api_key=TTS_API_KEY,
        tts_base_url=TTS_BASE_URL,
        tts_model_name=TTS_MODEL_NAME,
    )
    return _build_runtime_settings()


def _ensure_runtime_ready() -> None:
    apply_runtime_config()
    if API_KEY in {"EMPTY", "sk-your-api-key-here"} or not API_KEY:
        raise RuntimeError("Missing runtime API key configuration.")


def _serialize_plan(plan: Any) -> dict[str, Any]:
    if hasattr(plan, "model_dump"):
        return plan.model_dump()
    if isinstance(plan, dict):
        return plan
    return {"raw": str(plan)}


def _list_workspace_mp4_files() -> list[str]:
    return [str(path) for path in sorted(WORKSPACE.glob("*.mp4")) if path.is_file()]


def _emit_state_update(
    callback: RuntimeEventCallback | None,
    node_name: str,
    update: dict[str, Any],
) -> None:
    if callback is None or not isinstance(update, dict):
        return

    _emit_runtime_event(
        callback,
        "node_update",
        {"node": node_name, "keys": list(update.keys())},
    )

    if "phase" in update:
        _emit_runtime_event(
            callback,
            "phase_state",
            {"node": node_name, "phase": update.get("phase")},
        )

    if "plan" in update and update["plan"] is not None:
        _emit_runtime_event(
            callback,
            "plan_created",
            {
                "node": node_name,
                "plan": _serialize_plan(update["plan"]),
                "target_duration_seconds": update.get("target_duration_seconds", 0),
            },
        )

    if "step_results" in update and update["step_results"]:
        _emit_runtime_event(
            callback,
            "step_result",
            {
                "node": node_name,
                "current_step_index": update.get("current_step_index"),
                "result": str(update["step_results"][-1]),
            },
        )

    if update.get("editing_blueprint"):
        blueprint = str(update["editing_blueprint"])
        _emit_runtime_event(
            callback,
            "blueprint_created",
            {"node": node_name, "length": len(blueprint), "excerpt": blueprint[:500]},
        )

    if update.get("final_output"):
        _emit_runtime_event(
            callback,
            "final_output",
            {"node": node_name, "text": str(update["final_output"])},
        )


class _RuntimeEventLogHandler(logging.Handler):
    def __init__(self, callback: RuntimeEventCallback | None) -> None:
        super().__init__(level=logging.INFO)
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        if self.callback is None:
            return
        if not (
            record.name == "agent"
            or record.name == "graph"
            or record.name.startswith("tools")
        ):
            return

        message = record.getMessage()
        _emit_runtime_event(
            self.callback,
            "log",
            {
                "logger": record.name,
                "level": record.levelname,
                "message": message,
            },
        )

        if record.name != "graph":
            return

        if "Phase 1" in message and "Planner" in message:
            _emit_runtime_event(self.callback, "phase_started", {"phase": "phase1"})
        elif "Phase 2 开始" in message:
            _emit_runtime_event(self.callback, "phase_started", {"phase": "phase2"})
        elif "Phase 3 开始" in message:
            _emit_runtime_event(self.callback, "phase_started", {"phase": "phase3"})

        plan_match = _PLAN_SUMMARY_RE.search(message)
        if plan_match:
            _emit_runtime_event(
                self.callback,
                "plan_summary",
                {
                    "step_count": int(plan_match.group(1)),
                    "goal": plan_match.group(2),
                },
            )

        step_match = _STEP_START_RE.search(message)
        if step_match:
            _emit_runtime_event(
                self.callback,
                "step_started",
                {
                    "step_id": int(step_match.group(1)),
                    "description": step_match.group(2),
                },
            )

        step_done_match = _STEP_COMPLETE_RE.search(message)
        if step_done_match:
            _emit_runtime_event(
                self.callback,
                "step_completed",
                {
                    "step_id": int(step_done_match.group(1)),
                    "summary": step_done_match.group(2),
                },
            )

        tool_call_match = _TOOL_CALL_RE.search(message)
        if tool_call_match:
            _emit_runtime_event(
                self.callback,
                "tool_called",
                {
                    "phase": "phase3",
                    "tool_name": tool_call_match.group(1),
                    "args_preview": tool_call_match.group(2),
                },
            )

        tool_result_match = _TOOL_RESULT_RE.search(message)
        if tool_result_match:
            _emit_runtime_event(
                self.callback,
                "tool_result",
                {
                    "phase": "phase3",
                    "tool_name": tool_result_match.group(1),
                    "summary": tool_result_match.group(2),
                },
            )


def _cleanup_workspace_before_task() -> int:
    """任务开始前清理 temp，避免历史脏文件干扰。"""
    deleted = 0
    if not WORKSPACE.exists():
        return deleted

    targets = sorted(WORKSPACE.glob("**/*"), reverse=True)
    for path in targets:
        try:
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
                deleted += 1
            elif path.is_dir():
                path.rmdir()
        except Exception:
            continue
    return deleted


def _cleanup_workspace_after_task() -> tuple[int, list[str]]:
    """任务结束后清理中间文件，仅保留最终成片候选。"""
    if not WORKSPACE.exists():
        return 0, []

    all_files = [p for p in WORKSPACE.glob("**/*") if p.is_file()]
    if not all_files:
        return 0, []

    mp4_files = [p for p in all_files if p.suffix.lower() == ".mp4"]
    keep: set[Path] = set()

    final_named = [p for p in mp4_files if "final" in p.stem.lower() or "output" in p.stem.lower()]
    keep.update(final_named)
    if mp4_files:
        newest_mp4 = max(mp4_files, key=lambda p: p.stat().st_mtime)
        keep.add(newest_mp4)

    deleted = 0
    for path in sorted(all_files, reverse=True):
        if path in keep:
            continue
        try:
            path.unlink(missing_ok=True)
            deleted += 1
        except Exception:
            continue

    # 清理空目录
    for d in sorted([p for p in WORKSPACE.glob("**/*") if p.is_dir()], reverse=True):
        try:
            d.rmdir()
        except Exception:
            continue

    kept_names = [p.name for p in sorted(keep)]
    return deleted, kept_names


def _print_banner() -> None:
    print("""
╔══════════════════════════════════════════════════════════════╗
║          🎬 多模态视频自动编辑 Agent                          ║
║                                                              ║
║  架构: Planner + ReAct 混合架构                               ║
║  模型: Qwen3.5 (视觉 + 语言)                                  ║
║                                                              ║
║  Phase 1: 规划 → 搜索 → 筛选 → 下载 → 多模态分析               ║
║  Phase 2: ReAct 自主创作 → 剪辑 → 转场 → 旁白 → 导出           ║
╚══════════════════════════════════════════════════════════════╝
""")


def _load_file_text(path: Path, max_chars: int = 100000) -> str:
    try:
        return path.read_text(encoding="utf-8")[:max_chars]
    except Exception:
        return ""


def _extract_tool_trace_from_log(log_path: Path, max_lines: int = 600) -> str:
    """抽取 graph/tools 的工具调用轨迹，供经验总结使用。"""
    if not log_path.exists():
        return "(无日志)"
    lines = _load_file_text(log_path, max_chars=800000).splitlines()
    if not lines:
        return "(无日志内容)"
    keys = ("Phase3 工具调用", "Phase3 工具结果", "Executor 步骤", "工具白名单")
    picked = [ln for ln in lines if any(k in ln for k in keys)]
    if not picked:
        picked = lines[-max_lines:]
    return "\n".join(picked[-max_lines:])


def _find_latest_output_video() -> Path | None:
    mp4_files = [p for p in WORKSPACE.glob("*.mp4") if p.is_file()]
    if not mp4_files:
        return None
    final_named = [p for p in mp4_files if "final" in p.stem.lower() or "output" in p.stem.lower()]
    if final_named:
        return max(final_named, key=lambda p: p.stat().st_mtime)
    return max(mp4_files, key=lambda p: p.stat().st_mtime)


def _load_latest_skills(max_chars: int = 24000) -> str:
    latest = MEMORY_EXPERIENCE_DIR / "latest_skills.md"
    if latest.exists():
        text = _load_file_text(latest, max_chars=max_chars).strip()
        if text:
            return text
    candidates = sorted(
        MEMORY_EXPERIENCE_DIR.glob("experience_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in candidates:
        text = _load_file_text(p, max_chars=max_chars).strip()
        if text:
            return text
    return "(暂无历史经验)"


def _get_experience_client() -> OpenAI:
    return OpenAI(api_key=API_KEY, base_url=BASE_URL)


def _extract_chat_content(response: Any) -> str:
    try:
        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or ""))
                else:
                    parts.append(str(item))
            return "\n".join([p for p in parts if p]).strip()
        return str(content or "").strip()
    except Exception:
        return ""


def _build_experience_prompt(
    user_request: str,
    final_output: str,
    video_analysis: str,
    tool_trace: str,
    previous_skills: str,
) -> str:
    return f"""你是视频自动剪辑系统的资深复盘专家。请输出一份“skills风格”的最新经验文件。

目标:
1) 提炼工具使用经验（何时用哪个工具、常见失败模式、规避策略）。
2) 提炼剪辑流程经验（节奏、流畅度、音画同步、旁白衔接、转场策略）。
3) 重点评估并总结:
   - 画面流畅度与叙事连续性
   - 音频/配音/字幕与画面的同步与匹配度
4) 结合“历史经验”进行去重与升级，给出最新版本，不要简单拼接。

输出格式（严格）:
# Skills: Video Editing Agent Experience
## Context
- user_request: ...
- key_result: ...
## Tool Usage Skills
- ...
## Editing Workflow Skills
- ...
## Quality Checklist (必须可执行)
- ...
## Common Failure Patterns & Fixes
- ...
## Version Notes
- ...

输入信息如下：
[本次用户需求]
{user_request}

[本次最终总结]
{final_output or "(无)"}

[本次成片复分析]
{video_analysis or "(无)"}

[本次工具轨迹日志]
{tool_trace or "(无)"}

[历史经验]
{previous_skills or "(无)"}
"""


def _synthesize_experience(
    user_request: str,
    final_output: str,
    video_analysis: str,
    tool_trace: str,
) -> str:
    previous = _load_latest_skills()
    prompt = _build_experience_prompt(
        user_request=user_request,
        final_output=final_output,
        video_analysis=video_analysis,
        tool_trace=tool_trace,
        previous_skills=previous,
    )
    try:
        client = _get_experience_client()
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": "你输出高质量、可执行、去重后的skills经验文档。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=4096,
        )
        text = _extract_chat_content(resp)
        return text if text else previous
    except Exception as e:
        agent_logger.warning("⚠️ 经验合成失败，回退历史经验: %s", e)
        return previous


def _write_experience_files(content: str) -> tuple[Path, Path]:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    versioned = MEMORY_EXPERIENCE_DIR / f"experience_{ts}.md"
    latest = MEMORY_EXPERIENCE_DIR / "latest_skills.md"
    versioned.write_text(content, encoding="utf-8")
    latest.write_text(content, encoding="utf-8")
    return versioned, latest


def _build_final_video_analysis_prompt() -> str:
    return (
        "请对该成片做全方面全维度复盘分析，重点关注："
        "1) 画面与叙事流畅度；"
        "2) 音频/配音/字幕与画面同步与一致性；"
        "3) 转场自然性；"
        "4) 节奏控制与时长结构；"
        "5) 可改进建议（按优先级）。"
    )


def _update_memory_experience_after_task(user_request: str, final_output: str) -> tuple[bool, str]:
    """任务完成后沉淀经验：成片复分析 + 工具轨迹 + 历史经验融合。"""
    try:
        target_video = _find_latest_output_video()
        if target_video is None:
            return False, "未找到可复分析的成片视频"

        analysis_text = analyze_video.invoke(
            {
                "video_path": str(target_video),
                "analysis_goal": _build_final_video_analysis_prompt(),
            }
        )
        tool_trace = _extract_tool_trace_from_log(_agent_log_file)
        merged_skills = _synthesize_experience(
            user_request=user_request,
            final_output=final_output,
            video_analysis=str(analysis_text),
            tool_trace=tool_trace,
        )
        versioned, latest = _write_experience_files(merged_skills)
        msg = f"经验已更新: {versioned.name} + {latest.name}"
        agent_logger.info("🧠 %s", msg)
        return True, msg
    except Exception as e:
        agent_logger.error("❌ 经验更新失败: %s", e, exc_info=True)
        return False, str(e)


def run_task(
    task: str,
    *,
    event_callback: RuntimeEventCallback | None = None,
    verbose: bool = True,
) -> str:
    """执行一个视频编辑任务，返回最终输出。"""
    _ensure_runtime_ready()

    root_logger = logging.getLogger()
    event_handler = _RuntimeEventLogHandler(event_callback)
    if event_callback is not None:
        root_logger.addHandler(event_handler)
        _emit_runtime_event(event_callback, "task_started", {"task": task})

    try:
        agent_logger.info(f"{'='*60}")
        agent_logger.info(f"📋 新任务开始: {task}")
        agent_logger.info(f"{'='*60}")

        graph = build_graph()

        deleted_before = _cleanup_workspace_before_task()
        if deleted_before > 0:
            agent_logger.info("🧹 任务前已清理 temp 文件: %s", deleted_before)
            if verbose:
                print(f"🧹 任务前已清理 temp 文件: {deleted_before}")

        candidate_pool = WORKSPACE / "candidate_pool.jsonl"
        if candidate_pool.exists():
            try:
                candidate_pool.unlink()
                agent_logger.info("🧹 已清理候选池缓存: %s", candidate_pool)
            except Exception as e:
                agent_logger.warning("⚠️ 清理候选池缓存失败: %s", e)

        if verbose:
            print(f"📋 任务: {task}")
            print(f"📂 工作目录: {WORKSPACE}")
            print(f"⏳ Agent 开始规划和执行...\n")
            print("=" * 60)

        start_time = time.time()
        initial_state = AgentState(user_request=task)

        final_state = None
        for step_output in graph.stream(
            initial_state.model_dump(),
            {"recursion_limit": 50},
            stream_mode="updates",
        ):
            for node_name, update in step_output.items():
                if update is not None:
                    final_state = update
                    _emit_state_update(event_callback, node_name, update)

        elapsed = time.time() - start_time
        agent_logger.info(f"⏱  任务完成，总耗时: {elapsed:.1f}s")
        agent_logger.info(f"{'='*60}")
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"⏱  总耗时: {elapsed:.1f}s")
            print(f"📂 输出目录: {WORKSPACE}")

        files = list(WORKSPACE.glob("*.mp4"))
        if files and verbose:
            print(f"📹 生成的视频文件:")
            for f in files:
                size_mb = f.stat().st_size / (1024 * 1024)
                print(f"   {f.name} ({size_mb:.1f} MB)")

        output = ""
        if final_state and "final_output" in final_state:
            output = final_state["final_output"]
        failure_markers = (
            "ReAct 创作阶段异常:",
            "剪辑研究阶段异常:",
            "素材准备规划异常:",
        )
        if output and any(output.startswith(marker) for marker in failure_markers):
            raise RuntimeError(output)

        deleted_after, kept = _cleanup_workspace_after_task()
        if deleted_after > 0:
            agent_logger.info("🧹 任务后已清理中间文件: %s", deleted_after)
            if verbose:
                print(f"🧹 任务后已清理中间文件: {deleted_after}")
        if kept:
            agent_logger.info("📌 任务后保留文件: %s", ", ".join(kept))
            if verbose:
                print(f"📌 任务后保留文件: {', '.join(kept)}")

        exp_ok, exp_msg = _update_memory_experience_after_task(task, output)
        if verbose:
            if exp_ok:
                print(f"🧠 经验沉淀完成: {exp_msg}")
            else:
                print(f"⚠️ 经验沉淀未完成: {exp_msg}")

        _emit_runtime_event(
            event_callback,
            "task_completed",
            {
                "task": task,
                "elapsed_seconds": round(elapsed, 2),
                "final_output": output,
                "output_files": _list_workspace_mp4_files(),
            },
        )
        return output
    except Exception as exc:
        _emit_runtime_event(
            event_callback,
            "task_failed",
            {"task": task, "error": str(exc)},
        )
        raise
    finally:
        if event_callback is not None:
            root_logger.removeHandler(event_handler)


def run_interactive() -> None:
    """交互式运行。"""
    _print_banner()
    print(f"📂 工作目录: {WORKSPACE}")
    print("输入你的视频编辑需求，输入 'quit' 退出\n")

    while True:
        try:
            user_input = input("🎯 你的需求> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("👋 再见!")
            break

        result = run_task(user_input)
        if result:
            print(f"\n📝 最终总结:\n{result}\n")
        print("─" * 60 + "\n")


if __name__ == "__main__":
    try:
        settings = apply_runtime_config()
        _ensure_runtime_ready()
    except RuntimeError:
        print("❌ 请通过环境变量或服务配置提供 API_KEY")
        print("   例如设置 CRAYOTTER_API_KEY / CRAYOTTER_BASE_URL / CRAYOTTER_MODEL_NAME")
        sys.exit(1)

    print(f"🔧 API配置:")
    print(f"   Base URL: {settings['base_url']}")
    print(f"   Model: {settings['model_name']}")
    print(f"   Video Base URL: {settings['video_base_url']}")
    print(
        f"   Video Model: {settings['video_model_name']}  "
        f"({'Omni 音视频' if 'omni' in settings['video_model_name'].lower() else '纯视觉'})"
    )
    print(f"   TTS Base URL: {settings['tts_base_url']}")
    print(f"   TTS Model: {settings['tts_model_name']}")
    print()

    # 依赖检查
    missing = []
    for pkg in ["cv2", "moviepy", "langchain_openai", "langgraph"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"⚠️  缺少依赖: {missing}")
        print("   pip install langchain-core langchain-openai langgraph opencv-python moviepy openai yt-dlp")

    if len(sys.argv) > 1:
        run_task(" ".join(sys.argv[1:]))
    else:
        run_interactive()
