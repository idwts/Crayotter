from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .tool_runtime import RESULT_SENTINEL, parse_tool_result_text


def _emit(payload: dict) -> None:
    print(f"{RESULT_SENTINEL}{json.dumps(payload, ensure_ascii=False)}")


def _configure_episode_environment(runtime_root: str | Path) -> Path:
    """Pin all runtime workspaces to the current rollout episode.

    Ray workers import the application tool package before individual rollout
    subprocesses are launched.  The imported package publishes its workspace
    paths through environment variables, so inheriting those values here can
    make a later episode resolve files against the worker's project-level
    workspace.  Always overwrite them before importing any application module.
    """

    episode_root = Path(runtime_root).expanduser().resolve(strict=False)
    task_workspace = episode_root / "temp"
    user_workspace = episode_root / "user_temp"

    # Direct assignment is intentional.  setdefault() would preserve stale
    # values inherited from the long-lived AgentLoopWorker process.
    os.environ["CRAYOTTER_RUNTIME_ROOT"] = str(episode_root)
    os.environ["CRAYOTTER_TASK_WORKSPACE"] = str(task_workspace)
    os.environ["CRAYOTTER_USER_WORKSPACE"] = str(user_workspace)

    task_workspace.mkdir(parents=True, exist_ok=True)
    user_workspace.mkdir(parents=True, exist_ok=True)
    return episode_root


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        runtime_root = str(payload["runtime_root"])
        tool_name = str(payload["tool_name"])
        arguments = dict(payload.get("arguments", {}))
        api_config = dict(payload.get("api_config", {}))
    except Exception as exc:
        _emit(
            {
                "raw_result": f"Invalid runner payload: {exc}",
                "parsed_result": "",
                "success": False,
                "output_paths": [],
                "duration_seconds": None,
            }
        )
        return 1

    _configure_episode_environment(runtime_root)
    project_root = Path(__file__).resolve().parents[1]
    for import_root in (project_root, project_root / "script"):
        if str(import_root) not in sys.path:
            sys.path.insert(0, str(import_root))

    try:
        from app.runtime_paths import configure_runtime_environment
        from script import tools as tools_module
    except Exception as exc:
        _emit(
            {
                "raw_result": f"Tool import failed: {exc}",
                "parsed_result": "",
                "success": False,
                "output_paths": [],
                "duration_seconds": None,
            }
        )
        return 1

    configure_runtime_environment()
    tools_module.configure(**{key: value for key, value in api_config.items() if value is not None})
    tool_map = {getattr(tool, "name", ""): tool for tool in tools_module.ALL_TOOLS}
    if tool_name not in tool_map:
        _emit(
            {
                "raw_result": f"Unknown tool: {tool_name}",
                "parsed_result": "",
                "success": False,
                "output_paths": [],
                "duration_seconds": None,
            }
        )
        return 1

    tool = tool_map[tool_name]
    try:
        raw_result = tool.invoke(arguments)
        parsed_result, success, output_paths, duration_seconds = parse_tool_result_text(raw_result, runtime_root)
        _emit(
            {
                "raw_result": raw_result,
                "parsed_result": parsed_result,
                "success": success,
                "output_paths": output_paths,
                "duration_seconds": duration_seconds,
            }
        )
        return 0 if success else 2
    except Exception as exc:
        _emit(
            {
                "raw_result": f"{tool_name} execution failed: {exc}",
                "parsed_result": "",
                "success": False,
                "output_paths": [],
                "duration_seconds": None,
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
