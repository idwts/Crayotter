from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from script.model_runtime import sanitize_error

from .models import CapabilityProbeResult

Probe = Callable[[], bool | dict[str, Any]]


def provider_identity(base_url: str, model: str, capability: str) -> str:
    """Build a stable provider identity that deliberately excludes credentials."""

    parsed = urlparse(str(base_url or ""))
    raw = json.dumps(
        {
            "host": (parsed.hostname or parsed.path or "local").lower(),
            "model": str(model),
            "capability": str(capability),
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CapabilityPreflight:
    def __init__(self, cache_path: Path, *, ttl_seconds: float = 900.0) -> None:
        self.cache_path = cache_path
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._lock = threading.RLock()

    def probe(
        self,
        *,
        capability: str,
        base_url: str,
        model: str,
        probe: Probe,
        force: bool = False,
    ) -> CapabilityProbeResult:
        key = provider_identity(base_url, model, capability)
        now = time.time()
        if not force:
            cached = self._load().get(key)
            if cached and float(cached.get("expires_at_epoch", 0.0)) > now:
                return CapabilityProbeResult.model_validate(cached)
        started = time.monotonic()
        available = False
        metadata: dict[str, Any] = {}
        error = ""
        error_class = ""
        try:
            raw = probe()
            if isinstance(raw, dict):
                metadata = dict(raw)
                available = bool(metadata.pop("available", True))
            else:
                available = bool(raw)
        except Exception as exc:
            error = sanitize_error(exc, 500)
            error_class = exc.__class__.__name__
        result = CapabilityProbeResult(
            capability=capability,
            provider=urlparse(base_url).hostname or "local",
            model=model,
            available=available,
            latency_seconds=round(time.monotonic() - started, 3),
            error_class=error_class,
            error=error,
            expires_at_epoch=now + self.ttl_seconds,
            metadata=metadata,
        )
        with self._lock:
            payload = self._load()
            payload[key] = result.model_dump()
            self._write(payload)
        return result

    def _load(self) -> dict[str, Any]:
        with self._lock:
            if not self.cache_path.exists():
                return {}
            try:
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

    def _write(self, payload: dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(self.cache_path)
