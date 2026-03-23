from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import OpenAI

# ═══════════════════════════════════════════════════════════════════════════
# API 配置 - 请在此处设置你的密钥和中转站地址
# ═══════════════════════════════════════════════════════════════════════════

API_KEY = "EMPTY"
BASE_URL = "http://127.0.0.1:8902/v1"
MODEL_NAME = "Qwen3.5-122B-A10B" 
# 是否启用 Phase 2（深度剪辑研究）
# True: 执行 Phase 1 -> Phase 2 -> Phase 3
# False: 执行 Phase 1 -> Phase 3
ENABLE_PHASE2_RESEARCH = True

VIDEO_API_KEY = API_KEY
VIDEO_BASE_URL = BASE_URL
VIDEO_MODEL_NAME = MODEL_NAME  

# 配音 TTS API 配置（DashScope TTS 服务）
TTS_API_KEY = "EMPTY"
TTS_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
TTS_MODEL_NAME = "qwen3-tts-flash"

# 配置 agent 日志（始终写到仓库根目录 logs/）
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

_agent_log_file = LOGS_DIR / f"agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
_agent_file_handler = logging.FileHandler(_agent_log_file, encoding='utf-8')
_agent_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
_agent_file_handler.setLevel(logging.DEBUG)

agent_logger = logging.getLogger('agent')
if not agent_logger.handlers:
    agent_logger.addHandler(_agent_file_handler)
    agent_logger.setLevel(logging.DEBUG)
    agent_logger.info(f"Agent日志已初始化: {_agent_log_file}")

from graph import AgentState, build_graph
from tools import MEMORY_EXPERIENCE_DIR, USER_WORKSPACE, WORKSPACE, analyze_video

MEMORY_EXPERIENCE_DIR.mkdir(parents=True, exist_ok=True)
USER_WORKSPACE.mkdir(parents=True, exist_ok=True)

# 将 tools / graph 的所有日志同步写入 agent_*.log（工具调用日志可见）
for _lg_name in ('tools', 'graph'):
    _lg = logging.getLogger(_lg_name)
    if _agent_file_handler not in _lg.handlers:
        _lg.addHandler(_agent_file_handler)


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


def run_task(task: str) -> str:
    """执行一个视频编辑任务，返回最终输出。"""
    agent_logger.info(f"{'='*60}")
    agent_logger.info(f"📋 新任务开始: {task}")
    agent_logger.info(f"{'='*60}")
    
    graph = build_graph()

    deleted_before = _cleanup_workspace_before_task()
    if deleted_before > 0:
        agent_logger.info("🧹 任务前已清理 temp 文件: %s", deleted_before)
        print(f"🧹 任务前已清理 temp 文件: {deleted_before}")

    candidate_pool = WORKSPACE / "candidate_pool.jsonl"
    if candidate_pool.exists():
        try:
            candidate_pool.unlink()
            agent_logger.info("🧹 已清理候选池缓存: %s", candidate_pool)
        except Exception as e:
            agent_logger.warning("⚠️ 清理候选池缓存失败: %s", e)

    print(f"📋 任务: {task}")
    print(f"📂 工作目录: {WORKSPACE}")
    print(f"⏳ Agent 开始规划和执行...\n")
    print("=" * 60)

    start_time = time.time()

    # 初始状态
    initial_state = AgentState(user_request=task)

    # 执行图 — 流式输出中间过程
    final_state = None
    for step_output in graph.stream(
        initial_state.model_dump(),
        {"recursion_limit": 50},
        stream_mode="updates",
    ):
        # step_output 是 {node_name: state_update} 的字典
        for node_name, update in step_output.items():
            # 保存非空的状态更新
            if update is not None:
                final_state = update

    elapsed = time.time() - start_time
    agent_logger.info(f"⏱  任务完成，总耗时: {elapsed:.1f}s")
    agent_logger.info(f"{'='*60}")
    print(f"\n{'=' * 60}")
    print(f"⏱  总耗时: {elapsed:.1f}s")
    print(f"📂 输出目录: {WORKSPACE}")

    # 列出工作目录中的文件
    files = list(WORKSPACE.glob("*.mp4"))
    if files:
        print(f"📹 生成的视频文件:")
        for f in files:
            size_mb = f.stat().st_size / (1024 * 1024)
            print(f"   {f.name} ({size_mb:.1f} MB)")

    output = ""
    if final_state and "final_output" in final_state:
        output = final_state["final_output"]

    deleted_after, kept = _cleanup_workspace_after_task()
    if deleted_after > 0:
        agent_logger.info("🧹 任务后已清理中间文件: %s", deleted_after)
        print(f"🧹 任务后已清理中间文件: {deleted_after}")
    if kept:
        agent_logger.info("📌 任务后保留文件: %s", ", ".join(kept))
        print(f"📌 任务后保留文件: {', '.join(kept)}")

    exp_ok, exp_msg = _update_memory_experience_after_task(task, output)
    if exp_ok:
        print(f"🧠 经验沉淀完成: {exp_msg}")
    else:
        print(f"⚠️ 经验沉淀未完成: {exp_msg}")

    return output


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
    # API配置检查
    if API_KEY == "sk-your-api-key-here" or not API_KEY:
        print("❌ 请在 script\\agent.py 文件开头配置 API_KEY")
        print("   API_KEY = 'sk-your-actual-key'")
        sys.exit(1)

    # 传递配置到其他模块
    import graph
    import tools
    graph.API_KEY = API_KEY
    graph.BASE_URL = BASE_URL
    graph.MODEL_NAME = MODEL_NAME
    graph.ENABLE_PHASE2_RESEARCH = ENABLE_PHASE2_RESEARCH
    tools.configure(
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

    print(f"🔧 API配置:")
    print(f"   Base URL: {BASE_URL}")
    print(f"   Model: {MODEL_NAME}")
    print(f"   Enable Phase 2 Research: {ENABLE_PHASE2_RESEARCH}")
    print(f"   Video Base URL: {VIDEO_BASE_URL}")
    print(f"   Video Model: {VIDEO_MODEL_NAME}  ({'Omni 音视频' if 'omni' in VIDEO_MODEL_NAME.lower() else '纯视觉'})")
    print(f"   TTS Base URL: {TTS_BASE_URL}")
    print(f"   TTS Model: {TTS_MODEL_NAME}")
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
