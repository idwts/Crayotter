from __future__ import annotations

import threading
from collections.abc import Iterable


class EventBus:
    def __init__(self) -> None:
        self._events: list[dict] = []
        self._condition = threading.Condition()
        self._next_sequence = 1

    def publish(self, event: dict) -> dict:
        with self._condition:
            stored = dict(event)
            stored["sequence"] = self._next_sequence
            self._next_sequence += 1
            self._events.append(stored)
            self._condition.notify_all()
            return stored

    def list_from(self, after_sequence: int = 0) -> list[dict]:
        with self._condition:
            return [dict(event) for event in self._events if event["sequence"] > after_sequence]

    def wait_for_events(self, after_sequence: int = 0, timeout: float = 1.0) -> list[dict]:
        with self._condition:
            existing = [dict(event) for event in self._events if event["sequence"] > after_sequence]
            if existing:
                return existing
            self._condition.wait(timeout=timeout)
            return [dict(event) for event in self._events if event["sequence"] > after_sequence]

    def extend(self, events: Iterable[dict]) -> list[dict]:
        stored: list[dict] = []
        for event in events:
            stored.append(self.publish(event))
        return stored

    def seed(self, events: Iterable[dict]) -> None:
        with self._condition:
            max_sequence = 0
            self._events = []
            for event in events:
                stored = dict(event)
                sequence = int(stored.get("sequence", 0) or 0)
                if sequence <= 0:
                    sequence = max_sequence + 1
                    stored["sequence"] = sequence
                max_sequence = max(max_sequence, sequence)
                self._events.append(stored)
            self._next_sequence = max_sequence + 1 if self._events else 1
