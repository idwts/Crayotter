from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_ABORT_EVENT = threading.Event()
_EVENT_LOCK = threading.RLock()
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|api[_-]?key\s*[=:]\s*\S+)", re.IGNORECASE)


def _enabled(name: str, default: bool = False) -> bool:
    value = str(os.environ.get(name, "")).strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def fail_fast_model_errors() -> bool:
    return _enabled("CRAYOTTER_FAIL_FAST_MODEL_ERRORS")


def benchmark_mode() -> bool:
    return _enabled("CRAYOTTER_BENCHMARK_MODE")


def reset_model_abort() -> None:
    _ABORT_EVENT.clear()


def model_abort_requested() -> bool:
    return _ABORT_EVENT.is_set()


def request_model_abort() -> None:
    _ABORT_EVENT.set()


def sanitize_error(value: Any, limit: int = 1200) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = _SECRET_RE.sub("<redacted>", text)
    return text[:limit]


def _runtime_root() -> Path:
    raw = os.environ.get("CRAYOTTER_RUNTIME_ROOT", "").strip()
    return Path(raw or Path.cwd()).resolve()


def _event_path() -> Path | None:
    raw = os.environ.get("CRAYOTTER_BENCHMARK_EVENTS_PATH", "").strip()
    if not raw:
        return None
    path = Path(raw).resolve()
    root = _runtime_root()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("Benchmark event path must stay inside CRAYOTTER_RUNTIME_ROOT.") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def emit_benchmark_event(event_type: str, payload: dict[str, Any] | None = None) -> None:
    path = _event_path()
    if path is None:
        return
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "monotonic": time.monotonic(),
        "type": event_type,
        "payload": payload or {},
    }
    with _EVENT_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class ModelCallError(RuntimeError):
    def __init__(
        self,
        *,
        stage: str,
        model: str,
        message: Any,
        status_code: Any = None,
        request_id: str = "",
        duration_seconds: float = 0.0,
    ) -> None:
        self.stage = str(stage)
        self.model = str(model)
        self.status_code = status_code
        self.request_id = sanitize_error(request_id, 160)
        self.duration_seconds = max(0.0, float(duration_seconds or 0.0))
        self.safe_message = sanitize_error(message)
        super().__init__(
            f"MODEL_CALL_FAILED stage={self.stage} model={self.model} "
            f"status={self.status_code or 'unknown'} request_id={self.request_id or 'unknown'} "
            f"error={self.safe_message}"
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "model": self.model,
            "status_code": self.status_code,
            "request_id": self.request_id,
            "duration_seconds": round(self.duration_seconds, 3),
            "error": self.safe_message,
        }


def raise_model_failure(
    *,
    stage: str,
    model: str,
    message: Any,
    status_code: Any = None,
    request_id: str = "",
    duration_seconds: float = 0.0,
) -> None:
    error = ModelCallError(
        stage=stage,
        model=model,
        message=message,
        status_code=status_code,
        request_id=request_id,
        duration_seconds=duration_seconds,
    )
    request_model_abort()
    emit_benchmark_event("model_call_failed", error.to_payload())
    raise error


def ensure_model_calls_allowed() -> None:
    if model_abort_requested():
        raise RuntimeError("Model failure circuit is open; no new model calls are allowed.")
