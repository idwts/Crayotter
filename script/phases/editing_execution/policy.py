"""Bounded fallback policy for Phase 3 execution loops."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReactBudget:
    remaining_seconds: float
    max_tool_calls: int
    max_encoding_calls: int
    recursion_limit: int


def should_try_short_form_fallback(
    *,
    enabled: bool,
    target_duration_seconds: float,
    has_blueprint: bool,
) -> bool:
    return enabled and 0 < target_duration_seconds <= 20 and has_blueprint


def build_react_budget(remaining_seconds: float) -> ReactBudget:
    max_tool_calls = max(4, min(20, int(remaining_seconds // 8)))
    max_encoding_calls = max(1, min(6, int(remaining_seconds // 35)))
    return ReactBudget(
        remaining_seconds=remaining_seconds,
        max_tool_calls=max_tool_calls,
        max_encoding_calls=max_encoding_calls,
        recursion_limit=max(12, min(48, max_tool_calls * 2 + 4)),
    )
