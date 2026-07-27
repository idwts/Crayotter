"""Reusable bounded-loop policy for future workflow-level iteration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


LoopAction = Literal["continue", "complete", "fallback", "fail"]


@dataclass(frozen=True)
class LoopPolicy:
    name: str
    max_iterations: int
    require_progress: bool = True


@dataclass(frozen=True)
class LoopDecision:
    action: LoopAction
    reason: str


class LoopController:
    """Makes loop termination explicit and testable.

    Callers persist iteration counters and progress signatures in their own
    state or artifacts, keeping this policy stateless and resume-safe.
    """

    @staticmethod
    def decide(
        policy: LoopPolicy,
        *,
        iteration: int,
        completed: bool,
        progress_changed: bool,
        fallback_available: bool,
    ) -> LoopDecision:
        if completed:
            return LoopDecision("complete", f"{policy.name} completed")
        exhausted = iteration >= policy.max_iterations
        stalled = policy.require_progress and iteration > 0 and not progress_changed
        if exhausted or stalled:
            reason = (
                f"{policy.name} reached max iterations"
                if exhausted
                else f"{policy.name} made no progress"
            )
            return LoopDecision(
                "fallback" if fallback_available else "fail",
                reason,
            )
        return LoopDecision("continue", f"{policy.name} may continue")
