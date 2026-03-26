from __future__ import annotations

import re
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import agent
import graph
import tools
from tools.download_bilibili_video import download_bilibili_video
from tools.analyze_video import analyze_video


def _configure_runtime() -> None:
    if agent.API_KEY in {"EMPTY", "sk-your-api-key-here"} or not agent.API_KEY:
        raise RuntimeError("Missing API key configuration for resume run.")

    graph.API_KEY = agent.API_KEY
    graph.BASE_URL = agent.BASE_URL
    graph.MODEL_NAME = agent.MODEL_NAME
    tools.configure(
        api_key=agent.API_KEY,
        base_url=agent.BASE_URL,
        model_name=agent.MODEL_NAME,
        video_api_key=agent.VIDEO_API_KEY,
        video_base_url=agent.VIDEO_BASE_URL,
        video_model_name=agent.VIDEO_MODEL_NAME,
        tts_api_key=agent.TTS_API_KEY,
        tts_base_url=agent.TTS_BASE_URL,
        tts_model_name=agent.TTS_MODEL_NAME,
    )


def _matching_logs(pattern: str) -> list[Path]:
    candidates = []
    for dirname in ("runtime_logs", "logs"):
        log_dir = PROJECT_ROOT / dirname
        if not log_dir.exists():
            continue
        candidates.extend(log_dir.glob(pattern))
    if not candidates:
        raise FileNotFoundError(f"No log file matched {pattern}")
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


def _parse_task(*texts: str) -> str:
    patterns = [
        r"新任务开始:\s*(.+)",
        r"用户需求:\s*(.+)",
    ]
    for text in texts:
        for pattern in patterns:
            matches = re.findall(pattern, text)
            if matches:
                return matches[-1].strip()
    raise RuntimeError("Could not find the latest task text in recent logs.")


def _parse_downloads(video_log_text: str) -> list[tuple[str, str]]:
    matches = re.findall(
        r"开始下载Bilibili视频: url='([^']+)', filename='([^']+)'",
        video_log_text,
    )
    if not matches:
        raise RuntimeError("Could not find prior download records in video log.")

    downloads: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url, filename in matches:
        if filename in seen:
            continue
        seen.add(filename)
        downloads.append((url, filename))
    return downloads


def _find_task_from_logs(logs: list[Path]) -> tuple[str, Path]:
    for path in logs:
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            return _parse_task(text), path
        except RuntimeError:
            continue
    raise RuntimeError("Could not find the latest task text in recent logs.")


def _find_downloads_from_logs(logs: list[Path]) -> tuple[list[tuple[str, str]], Path]:
    for path in logs:
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            return _parse_downloads(text), path
        except RuntimeError:
            continue
    raise RuntimeError("Could not find prior download records in recent video logs.")


def _source_path(filename: str) -> Path:
    return graph.WORKSPACE / f"{filename}.mp4"


def _analysis_path(filename: str) -> Path:
    return graph.WORKSPACE / f"{filename}_analysis.json"


def _download_missing_sources(downloads: list[tuple[str, str]]) -> list[Path]:
    recovered: list[Path] = []
    for url, filename in downloads:
        target = _source_path(filename)
        if not target.exists() or target.stat().st_size == 0:
            print(f"[resume] downloading {filename} from prior run log")
            result = download_bilibili_video.invoke(
                {"url": url, "filename": filename, "prefer_h264": True}
            )
            if "success" not in str(result):
                print(f"[resume] skip {filename}: {result}")
                continue
        recovered.append(target)
    return recovered


def _analyze_sources(source_files: list[Path], user_request: str, min_ready: int = 3) -> list[Path]:
    goal = (
        "请为校园青春氛围短片找出适合剪辑的高质量片段，"
        "标注时间段、情绪、镜头特征、画面内容和适合的旁白方向。"
    )
    ready: list[Path] = []
    for source in source_files:
        analysis_json = _analysis_path(source.stem)
        if analysis_json.exists() and analysis_json.stat().st_size > 0:
            ready.append(analysis_json)
    if len(ready) >= min_ready:
        return ready

    for source in source_files:
        analysis_json = _analysis_path(source.stem)
        if analysis_json.exists() and analysis_json.stat().st_size > 0:
            continue

        print(f"[resume] analyzing {source.name}")
        last_result = ""
        for attempt in range(1, 4):
            last_result = str(
                analyze_video.invoke(
                    {
                        "video_path": str(source),
                        "analysis_goal": goal,
                    }
                )
            )
            if analysis_json.exists() and analysis_json.stat().st_size > 0:
                ready.append(analysis_json)
                if len(ready) >= min_ready:
                    return ready
                break
            print(f"[resume] analyze retry {attempt} failed for {source.name}: {last_result[:240]}")
            time.sleep(min(10 * attempt, 25))
        else:
            print(f"[resume] giving up on {source.name}: {last_result[:400]}")
    return ready


def main() -> int:
    _configure_runtime()

    latest_agent_log, latest_video_log = None, None
    agent_logs = _matching_logs("agent_*.log")
    video_logs = _matching_logs("video_agent_*.log")
    user_request, latest_agent_log = _find_task_from_logs(agent_logs)
    downloads, latest_video_log = _find_downloads_from_logs(video_logs)

    print(f"[resume] task: {user_request}")
    print(f"[resume] found {len(downloads)} prior download records")

    source_files = _download_missing_sources(downloads)
    source_files = [p for p in source_files if p.exists() and p.stat().st_size > 0]
    print(f"[resume] available source videos: {len(source_files)}")
    if not source_files:
        raise RuntimeError("No source videos are available to resume from.")

    analysis_files = _analyze_sources(source_files, user_request)
    print(f"[resume] analysis files ready: {len(analysis_files)}")
    if not analysis_files:
        raise RuntimeError("No analysis files are available; cannot continue to editing.")

    state = graph.AgentState(
        user_request=user_request,
        target_duration_seconds=graph._extract_target_duration_seconds(user_request),
        step_results=[
            f"Resumed from {latest_video_log.name}; recovered {len(source_files)} source videos.",
            f"Prepared {len(analysis_files)} analysis files for editing.",
        ],
        phase="researching",
    )

    research_update = graph.editing_research_node(state)
    state = graph.AgentState(**(state.model_dump() | research_update))

    edit_update = graph.react_editor_node(state)
    final_state = graph.AgentState(**(state.model_dump() | edit_update))
    print("[resume] final_output:")
    print(final_state.final_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
