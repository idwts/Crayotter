from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import subprocess
import sys
import threading
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


def result_indicates_failure(
    text: str,
    markers: tuple[str, ...] = KNOWN_ERROR_MARKERS,
    *,
    strict_status: bool = True,
    full_text: bool = False,
) -> bool:
    """Canonical failure verdict shared by graph.py and phase3_rl.

    Default order matches parse_tool_result_text: fence-strip, then a JSON dict
    with a non-empty status key decides, a JSON list always succeeds, anything
    else falls back to first-line markers. Two caller-tunable strictness axes:

    - ``strict_status=False`` skips the JSON status/list special-casing, for
      callers whose success vocabulary is wider than ``status == "success"``
      (e.g. graph.py accepts ``allowed``/``empty`` statuses).
    - ``full_text=True`` matches markers against the whole stripped text
      instead of the first non-empty line (graph.py's historical behavior).
    """
    stripped = _strip_fenced_json(str(text or ""))
    if strict_status:
        try:
            payload = json.loads(stripped)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            status = str(payload.get("status", "")).strip()
            if status:
                return status.lower() != "success"
        elif isinstance(payload, list):
            return False
    haystack = stripped.lower() if full_text else next(
        (line.strip().lower() for line in stripped.splitlines() if line.strip()),
        "",
    )
    return any(marker in haystack for marker in markers)


def _looks_like_error(text: str) -> bool:
    return result_indicates_failure(text)


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


def _tool_worker_enabled() -> bool:
    return os.environ.get("CRAYOTTER_RL_TOOL_WORKER", "") == "1"


def _worker_fingerprint(
    python_executable: str, runtime_root: str | Path, api_config: dict[str, Any]
) -> tuple[str, str, str]:
    """One worker serves exactly one fingerprint for its whole life.

    script/tools pins workspace paths as module constants at import time, so a
    worker may never cross runtime_root; api_config feeds global client state,
    so it joins the key as a canonical digest.
    """
    import hashlib

    config_digest = hashlib.sha256(
        json.dumps(api_config or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return (
        python_executable,
        str(Path(runtime_root).resolve()),
        config_digest,
    )


class _ToolWorker:
    """One long-lived `tool_runner --serve` process, serialized by a lock."""

    def __init__(self, python_executable: str) -> None:
        self.process = subprocess.Popen(
            [python_executable, "-m", "phase3_rl.tool_runner", "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        self.lock = threading.Lock()
        self.calls = 0
        self.last_used = time.monotonic()
        # A daemon pump thread owns the blocking readline so execute() can
        # enforce its deadline with queue.get(timeout=...) — a plain peek/read
        # on Windows pipes can block past the timeout and defeat it.
        self._lines: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._pump_lines, daemon=True)
        self._reader.start()

    def _pump_lines(self) -> None:
        try:
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self._lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self._lines.put("")  # EOF sentinel, wakes any waiting execute()

    def execute(self, payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
        """Send one request and wait for its matching frame.

        Raises _WorkerDead when the write failed before the request could have
        been processed (safe to cold-retry), _WorkerDiedMidRequest when the
        worker exited after accepting the request (the tool may already have
        run — never retried), and _WorkerHung on timeout (explicit failure).
        """
        import uuid

        request_id = uuid.uuid4().hex
        frame = dict(payload)
        frame["request_id"] = request_id
        with self.lock:
            try:
                assert self.process.stdin is not None
                self.process.stdin.write(json.dumps(frame, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self.kill()
                raise _WorkerDead(f"tool worker transport failed before send: {exc}") from exc
            deadline = time.monotonic() + timeout_seconds
            noise: list[str] = []
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.kill()
                    raise _WorkerHung(f"tool worker hung after {timeout_seconds}s; killed")
                try:
                    line = self._lines.get(timeout=remaining)
                except queue.Empty:
                    self.kill()
                    raise _WorkerHung(f"tool worker hung after {timeout_seconds}s; killed")
                if line == "":  # EOF after the request was accepted
                    self.kill()
                    raise _WorkerDiedMidRequest(
                        "tool worker exited mid-request; tool may already have run"
                    )
                if not line.startswith(RESULT_SENTINEL):
                    noise.append(line)  # fd1 leaks (ffmpeg & friends); keep as diagnostics
                    continue
                try:
                    result = json.loads(line[len(RESULT_SENTINEL):].strip())
                except json.JSONDecodeError:
                    noise.append(line)
                    continue
                if result.get("request_id") != request_id:
                    noise.append(line)
                    continue
                result["stdout"] = (result.get("stdout") or "") + "".join(noise)
                self.calls += 1
                self.last_used = time.monotonic()
                return result

    def kill(self) -> None:
        try:
            self.process.kill()
        except OSError:
            pass


class _WorkerDead(Exception):
    """Transport failed before the request could have been processed."""


class _WorkerDiedMidRequest(Exception):
    """Worker exited after accepting a request; the tool may already have run."""


class _WorkerHung(Exception):
    pass


_TOOL_WORKER_POOLS: dict[tuple[str, str, str], list[_ToolWorker]] = {}
_TOOL_WORKER_POOL_LOCK = threading.Lock()


def _acquire_worker(fingerprint: tuple[str, str, str], python_executable: str) -> _ToolWorker:
    max_calls = int(os.environ.get("CRAYOTTER_RL_TOOL_WORKER_MAX_CALLS", "200") or "200")
    idle_ttl = float(os.environ.get("CRAYOTTER_RL_TOOL_WORKER_IDLE_SECONDS", "900") or "900")
    pool_size = _tool_process_concurrency()
    now = time.monotonic()
    with _TOOL_WORKER_POOL_LOCK:
        # Sweep EVERY pool: episode roots are unique per rollout, so workers
        # from past episodes would otherwise idle forever in a long-lived Ray
        # worker (round-2 review finding 1).
        for past_fingerprint, past_pool in list(_TOOL_WORKER_POOLS.items()):
            for worker in list(past_pool):
                expired = worker.calls >= max_calls or worker.process.poll() is not None
                idle_expired = not worker.lock.locked() and (now - worker.last_used) > idle_ttl
                if expired or idle_expired:
                    past_pool.remove(worker)
                    worker.kill()
            if not past_pool:
                del _TOOL_WORKER_POOLS[past_fingerprint]
        pool = _TOOL_WORKER_POOLS.setdefault(fingerprint, [])
        for worker in pool:
            if not worker.lock.locked():
                return worker
        if len(pool) < pool_size:
            worker = _ToolWorker(python_executable)
            pool.append(worker)
            return worker
        # Pool exhausted: take the first worker; its lock serializes.
        return pool[0]


def release_tool_workers(runtime_root: str | Path) -> int:
    """Retire every pool worker bound to one runtime root (episode teardown).

    Called from CrayotterSubprocessTool.release at episode end; returns the
    number of workers killed.
    """
    resolved = str(Path(runtime_root).resolve())
    retired = 0
    with _TOOL_WORKER_POOL_LOCK:
        for fingerprint, pool in list(_TOOL_WORKER_POOLS.items()):
            if fingerprint[1] != resolved:
                continue
            for worker in pool:
                worker.kill()
                retired += 1
            del _TOOL_WORKER_POOLS[fingerprint]
    return retired


def _retire_worker(fingerprint: tuple[str, str, str], worker: _ToolWorker) -> None:
    with _TOOL_WORKER_POOL_LOCK:
        pool = _TOOL_WORKER_POOLS.get(fingerprint, [])
        if worker in pool:
            pool.remove(worker)
    worker.kill()


def _execute_via_worker(
    payload: dict[str, Any],
    python_executable: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    fingerprint = _worker_fingerprint(
        python_executable, payload["runtime_root"], payload.get("api_config", {})
    )
    worker = _acquire_worker(fingerprint, python_executable)
    try:
        # Same cross-process throttle as the cold path: the per-fingerprint
        # pool does not bound the global worker count across Ray workers.
        with _global_tool_process_slot():
            return worker.execute(payload, timeout_seconds)
    except (_WorkerDead, _WorkerDiedMidRequest, _WorkerHung):
        _retire_worker(fingerprint, worker)
        raise


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

    if _tool_worker_enabled():
        try:
            result = _execute_via_worker(
                payload, python_executable or sys.executable, timeout_seconds
            )
        except (_WorkerHung, _WorkerDiedMidRequest) as exc:
            # Explicit failure, never a retry: tools are not idempotent.
            return ToolExecutionResult(
                tool_name=tool_name,
                arguments=arguments,
                raw_result=f"{tool_name} 执行失败: {exc}",
                parsed_result="",
                success=False,
                returncode=1,
                stdout="",
                stderr="",
            )
        except _WorkerDead:
            pass  # request never reached the tool; cold path below is safe
        else:
            returncode = int(result.get("returncode", 1))
            success = bool(result.get("success", False)) and returncode == 0
            duration = result.get("duration_seconds")
            return ToolExecutionResult(
                tool_name=tool_name,
                arguments=arguments,
                raw_result=str(result.get("raw_result", "")),
                parsed_result=result.get("parsed_result"),
                success=success,
                returncode=returncode,
                stdout=str(result.get("stdout", "")),
                stderr=str(result.get("stderr", "")),
                output_paths=[str(item) for item in result.get("output_paths", [])],
                duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
            )

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
