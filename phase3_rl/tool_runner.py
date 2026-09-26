from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .tool_runtime import RESULT_SENTINEL, parse_tool_result_text

# Registry of module-level latches that persist across requests and affect
# observation semantics. Cold runs get a fresh process per call, so the
# long-lived --serve worker resets each registered latch before every request
# to keep cold/worker paths observationally equivalent. Registry standard:
# "persists across requests and changes later observations" — client caches
# pinned by the api_config fingerprint are intentionally excluded.
_LATCH_RESETTERS = (
    ("script.tools.analyze_video", "reset_analysis_failure_circuit"),
    ("script.tools.analyze_video", "reset_analysis_model_fallbacks"),
)


def _reset_known_latches() -> None:
    for module_name, attr in _LATCH_RESETTERS:
        try:
            module = __import__(module_name, fromlist=[attr])
            getattr(module, attr)()
        except Exception:
            pass  # reset failure must not block execution


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


def _failure_payload(message: str) -> dict:
    return {
        "raw_result": message,
        "parsed_result": "",
        "success": False,
        "output_paths": [],
        "duration_seconds": None,
    }


def _prepare_tools(api_config: dict):
    """Import and configure the application tool package once per process."""
    project_root = Path(__file__).resolve().parents[1]
    for import_root in (project_root, project_root / "script"):
        if str(import_root) not in sys.path:
            sys.path.insert(0, str(import_root))
    from app.runtime_paths import configure_runtime_environment
    from script import tools as tools_module

    configure_runtime_environment()
    tools_module.configure(**{key: value for key, value in api_config.items() if value is not None})
    return {getattr(tool, "name", ""): tool for tool in tools_module.ALL_TOOLS}


def _execute_request(payload: dict, tool_map) -> tuple[dict, int]:
    """Run one tool call; return (result payload, three-state returncode)."""
    runtime_root = str(payload["runtime_root"])
    tool_name = str(payload["tool_name"])
    arguments = dict(payload.get("arguments", {}))
    _configure_episode_environment(runtime_root)
    if tool_name not in tool_map:
        return _failure_payload(f"Unknown tool: {tool_name}"), 1
    _reset_known_latches()
    tool = tool_map[tool_name]
    try:
        raw_result = tool.invoke(arguments)
        parsed_result, success, output_paths, duration_seconds = parse_tool_result_text(raw_result, runtime_root)
        return (
            {
                "raw_result": raw_result,
                "parsed_result": parsed_result,
                "success": success,
                "output_paths": output_paths,
                "duration_seconds": duration_seconds,
            },
            0 if success else 2,
        )
    except Exception as exc:
        return _failure_payload(f"{tool_name} execution failed: {exc}"), 1


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        api_config = dict(payload.get("api_config", {}))
        # Validate required fields before doing any work.
        str(payload["runtime_root"])
        str(payload["tool_name"])
    except Exception as exc:
        _emit(_failure_payload(f"Invalid runner payload: {exc}"))
        return 1

    _configure_episode_environment(str(payload["runtime_root"]))
    try:
        tool_map = _prepare_tools(api_config)
    except Exception as exc:
        _emit(_failure_payload(f"Tool import failed: {exc}"))
        return 1

    result, returncode = _execute_request(payload, tool_map)
    _emit(result)
    return returncode


def serve() -> int:
    """Long-lived worker: read one JSON request per line, emit one frame each.

    Frames carry the echoed request_id and the three-state returncode so the
    parent can preserve the cold path's reward semantics. Requests are
    serialized by the parent; per-request stdout/stderr is captured into the
    frame so tool prints cannot corrupt the framing channel. Known limitation:
    C-level grandchildren (ffmpeg) inherit the worker's fd2 (DEVNULL), so
    their stderr is lost in worker mode while the cold path captures it.
    """
    import contextlib
    import io

    tool_maps: dict[str, dict] = {}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = ""
        try:
            payload = json.loads(line)
            request_id = str(payload.get("request_id", ""))
            config_key = json.dumps(payload.get("api_config", {}), sort_keys=True, ensure_ascii=False)
            tool_map = tool_maps.get(config_key)
            if tool_map is None:
                _configure_episode_environment(str(payload["runtime_root"]))
                tool_map = _prepare_tools(dict(payload.get("api_config", {})))
                tool_maps[config_key] = tool_map
            captured_out = io.StringIO()
            captured_err = io.StringIO()
            with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
                result, returncode = _execute_request(payload, tool_map)
            result["stdout"] = captured_out.getvalue()
            result["stderr"] = captured_err.getvalue()
        except Exception as exc:
            result, returncode = _failure_payload(f"worker request failed: {exc}"), 1
            result["stdout"] = ""
            result["stderr"] = ""
        result["request_id"] = request_id
        result["returncode"] = returncode
        _emit(result)
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    if "--serve" in sys.argv[1:]:
        raise SystemExit(serve())
    raise SystemExit(main())
