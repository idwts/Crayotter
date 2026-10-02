from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, TextIO


KNOWN_ERROR_MARKERS = (
    "出错",
    "失败",
    "异常",
    "error",
    "exception",
    "traceback",
)

RESULT_SENTINEL = "__PHASE3_RL_RESULT__"

_DEFAULT_TOOL_PROCESS_CONCURRENCY = 2
_TOOL_PROCESS_SEMAPHORES: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    tuple[int, asyncio.Semaphore],
] = weakref.WeakKeyDictionary()


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
    first_line = next(
        (line.strip().lower() for line in text.splitlines() if line.strip()),
        "",
    )
    return any(marker in first_line for marker in KNOWN_ERROR_MARKERS)


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
    if "\n" in candidate or "\r" in candidate:
        for match in re.finditer(
            r"(?im)^\s*(?:[-*]\s*)?"
            r"(?:analysis_json|source_video|output_path|final_video|path)"
            r"\s*:\s*(?P<path>/[^\r\n]+|[A-Za-z]:[\\/][^\r\n]+)\s*$",
            candidate,
        ):
            _collect_paths(match.group("path").strip(), runtime_root, collector)
        return
    if len(candidate) > 4096:
        return
    path = Path(candidate)
    if not path.is_absolute():
        path = (runtime_root / candidate).resolve(strict=False)
    else:
        path = path.resolve(strict=False)
    try:
        if path.exists():
            collector.add(str(path))
    except OSError:
        return


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

    with _global_tool_process_slot():
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


def _tool_process_concurrency() -> int:
    raw = os.environ.get(
        "CRAYOTTER_RL_TOOL_PROCESS_CONCURRENCY",
        str(_DEFAULT_TOOL_PROCESS_CONCURRENCY),
    )
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return _DEFAULT_TOOL_PROCESS_CONCURRENCY


def _global_tool_slot_count() -> int:
    raw = os.environ.get("CRAYOTTER_RL_GLOBAL_TOOL_SLOTS", "0")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


@contextmanager
def _global_tool_process_slot() -> Iterator[None]:
    """Bound tool subprocesses across Ray workers with POSIX advisory locks."""

    count = _global_tool_slot_count()
    if count <= 0 or os.name != "posix":
        yield
        return

    import fcntl

    root = Path(
        os.environ.get("CRAYOTTER_RL_GLOBAL_TOOL_SLOT_DIR")
        or Path(os.environ.get("TMPDIR", "/tmp")) / "crayotter-tool-slots"
    )
    root.mkdir(parents=True, exist_ok=True)
    wait_seconds = max(
        1.0,
        float(os.environ.get("CRAYOTTER_RL_GLOBAL_TOOL_SLOT_TIMEOUT", "3600")),
    )
    deadline = time.monotonic() + wait_seconds
    handle: TextIO | None = None

    while handle is None:
        for index in range(count):
            candidate = (root / f"slot-{index}.lock").open("a+", encoding="utf-8")
            try:
                fcntl.flock(candidate.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                candidate.close()
                continue
            candidate.seek(0)
            candidate.truncate()
            candidate.write(f"pid={os.getpid()} acquired={time.time():.6f}\n")
            candidate.flush()
            handle = candidate
            break
        if handle is not None:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"timed out after {wait_seconds:.1f}s waiting for one of {count} global tool slots"
            )
        time.sleep(0.05)

    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _tool_process_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    limit = _tool_process_concurrency()
    current = _TOOL_PROCESS_SEMAPHORES.get(loop)
    if current is None or current[0] != limit:
        current = (limit, asyncio.Semaphore(limit))
        _TOOL_PROCESS_SEMAPHORES[loop] = current
    return current[1]


async def execute_tool_subprocess_async(**kwargs: Any) -> ToolExecutionResult:
    """Run a tool off-loop while bounding concurrent FFmpeg/process pressure."""

    async with _tool_process_semaphore():
        return await asyncio.to_thread(execute_tool_subprocess, **kwargs)
