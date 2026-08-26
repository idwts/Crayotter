from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from script.model_runtime import sanitize_error
from script.orchestration.models import utc_now_iso


class ReplayMiss(KeyError):
    pass


def _fingerprint(stage: str, request: Any) -> str:
    raw = json.dumps(
        {"stage": stage, "request": request},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if "key" not in str(key).lower()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return sanitize_error(value, max(1200, len(value)))
    return value


class ReplayStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def record(
        self, stage: str, request: Any, *, response: Any = None, error: str = ""
    ) -> str:
        key = _fingerprint(stage, request)
        with self._lock:
            payload = self._load()
            payload[key] = {
                "stage": stage,
                "recorded_at": utc_now_iso(),
                "request": _sanitize(request),
                "response": _sanitize(response),
                "error": sanitize_error(error, 1200),
            }
            self._write(payload)
        return key

    def lookup(self, stage: str, request: Any) -> Any:
        key = _fingerprint(stage, request)
        item = self._load().get(key)
        if item is None:
            raise ReplayMiss(f"No replay fixture for {stage}:{key[:12]}")
        if item.get("error"):
            raise RuntimeError(str(item["error"]))
        return item.get("response")

    def _load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {}
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(self.path)
