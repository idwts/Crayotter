from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


KNOWN_ERROR_MARKERS = (
    "出错",
    "失败",
    "异常",
    "error",
    "exception",
    "traceback",
)

RESULT_SENTINEL = "__PHASE3_RL_RESULT__"


@dataclass(slots=True)
class ToolExecutionResult:
    tool_name: str
    arguments: dict[str, Any]
    raw_result: str
    parsed_result: Any
    success: bool
    returncode: int
    stdout: str
    stderr: str
    output_paths: list[str] = field(default_factory=list)
    duration_seconds: float | None = None


def load_api_config_from_env() -> dict[str, str]:
    env = os.environ
    return {
        "api_key": env.get("CRAYOTTER_API_KEY") or env.get("OPENAI_API_KEY", ""),
        "base_url": env.get("CRAYOTTER_BASE_URL", ""),
        "model_name": env.get("CRAYOTTER_MODEL_NAME", ""),
        "video_api_key": env.get("CRAYOTTER_VIDEO_API_KEY", ""),
        "video_base_url": env.get("CRAYOTTER_VIDEO_BASE_URL", ""),
        "video_model_name": env.get("CRAYOTTER_VIDEO_MODEL_NAME", ""),
        "tts_api_key": env.get("CRAYOTTER_TTS_API_KEY", ""),
        "tts_base_url": env.get("CRAYOTTER_TTS_BASE_URL", ""),
        "tts_model_name": env.get("CRAYOTTER_TTS_MODEL_NAME", ""),
    }


def _strip_fenced_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        parts = stripped.split("```")
        if len(parts) >= 3:
            return parts[1].split("\n", 1)[-1].strip()
    return stripped


def _looks_like_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in KNOWN_ERROR_MARKERS)


def _collect_paths(value: Any, runtime_root: Path, collector: set[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _collect_paths(item, runtime_root, collector)
        return
    if isinstance(value, list):
        for item in value:
            _collect_paths(item, runtime_root, collector)
        return
    if not isinstance(value, str):
        return

    candidate = value.strip()
    if not candidate:
        return
    path = Path(candidate)
    if not path.is_absolute():
        path = (runtime_root / candidate).resolve(strict=False)
    else:
        path = path.resolve(strict=False)
    if path.exists():
        collector.add(str(path))


def parse_tool_result_text(raw_result: str, runtime_root: str | Path) -> tuple[Any, bool, list[str], float | None]:
    text = _strip_fenced_json(str(raw_result or ""))
    parsed: Any = text
    success = not _looks_like_error(text)
    output_paths: set[str] = set()
    duration_seconds: float | None = None
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = text
    else:
        if isinstance(parsed, dict):
            status = str(parsed.get("status", "")).strip().lower()
            if status:
                success = status == "success"
            dur = parsed.get("duration")
            if dur is None:
                dur = parsed.get("duration_seconds")
            if isinstance(dur, (int, float)):
                duration_seconds = float(dur)
        elif isinstance(parsed, list):
            success = True

    _collect_paths(parsed, Path(runtime_root).resolve(), output_paths)
    return parsed, success, sorted(output_paths), duration_seconds


def execute_tool_subprocess(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    runtime_root: str | Path,
    api_config: dict[str, str] | None = None,
    python_executable: str | None = None,
    timeout_seconds: int = 900,
) -> ToolExecutionResult:
    payload = {
        "tool_name": tool_name,
        "arguments": arguments,
        "runtime_root": str(Path(runtime_root).resolve()),
        "api_config": api_config or {},
    }

    process = subprocess.run(
        [python_executable or sys.executable, "-m", "phase3_rl.tool_runner"],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        cwd=str(Path(__file__).resolve().parents[1]),
    )

    stdout = process.stdout or ""
    stderr = process.stderr or ""
    result_line = ""
    for line in stdout.splitlines()[::-1]:
        if line.startswith(RESULT_SENTINEL):
            result_line = line[len(RESULT_SENTINEL) :].strip()
            break
    if not result_line:
        result_line = json.dumps(
            {
                "raw_result": f"Tool subprocess did not return structured payload for {tool_name}.",
                "success": False,
                "parsed_result": "",
                "output_paths": [],
                "duration_seconds": None,
            },
            ensure_ascii=False,
        )

    runner_payload = json.loads(result_line)
    parsed_result = runner_payload.get("parsed_result")
    success = bool(runner_payload.get("success", False)) and process.returncode == 0
    raw_result = str(runner_payload.get("raw_result", ""))
    output_paths = [str(item) for item in runner_payload.get("output_paths", [])]
    duration_seconds = runner_payload.get("duration_seconds")
    if isinstance(duration_seconds, (int, float)):
        duration_seconds = float(duration_seconds)
    else:
        duration_seconds = None

    return ToolExecutionResult(
        tool_name=tool_name,
        arguments=arguments,
        raw_result=raw_result,
        parsed_result=parsed_result,
        success=success,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        output_paths=output_paths,
        duration_seconds=duration_seconds,
    )
