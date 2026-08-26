from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import TypeVar

from .models import FaultSpec

T = TypeVar("T")


class InjectedFault(RuntimeError):
    def __init__(self, stage: str, effect: str, message: str = "") -> None:
        self.stage = stage
        self.effect = effect
        self.status_code = (
            403 if effect == "status_403" else 429 if effect == "status_429" else None
        )
        super().__init__(message or f"Injected {effect} at {stage}")


class FaultInjector:
    """Fire deterministic boundary faults on declared call occurrences."""

    def __init__(self, rules: list[FaultSpec] | None = None) -> None:
        self.rules = list(rules or [])
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def invoke(self, stage: str, call: Callable[[], T]) -> T:
        with self._lock:
            count = self._counts.get(stage, 0) + 1
            self._counts[stage] = count
            rule = next(
                (
                    item
                    for item in self.rules
                    if item.stage == stage
                    and item.occurrence <= count < item.occurrence + item.repeat
                ),
                None,
            )
        if rule is None:
            return call()
        if rule.effect == "malformed_json":
            return "{not valid json"  # type: ignore[return-value]
        if rule.effect == "empty_response":
            return ""  # type: ignore[return-value]
        if rule.effect == "timeout":
            raise TimeoutError(rule.message or f"Injected timeout at {stage}")
        if rule.effect == "connection_reset":
            raise ConnectionResetError(
                rule.message or f"Injected connection reset at {stage}"
            )
        raise InjectedFault(stage, rule.effect, rule.message)

    def serialize_counts(self) -> str:
        with self._lock:
            return json.dumps(self._counts, ensure_ascii=False, sort_keys=True)
