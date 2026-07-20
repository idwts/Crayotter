from __future__ import annotations

import math
import time
from typing import Literal

from pydantic import BaseModel, Field


ProcessingMode = Literal["auto", "speed", "quality"]


class MaterialBudget(BaseModel):
    source_target: int = Field(ge=1)
    source_min: int = Field(ge=1)
    candidate_cap: int = Field(ge=1)
    search_task_cap: int = Field(ge=1)
    coverage_ratio: float = Field(gt=0)
    supplement_rounds: int = Field(default=1, ge=0, le=2)


class ProcessingBudget(BaseModel):
    """Persistable best-effort budget shared by all three graph phases."""

    version: str = "processing-budget-v1"
    target_duration_seconds: float = Field(default=30.0, gt=0)
    deadline_seconds: float = Field(default=600.0, ge=60.0)
    phase1_seconds: float = Field(default=180.0, ge=1.0)
    phase2_seconds: float = Field(default=90.0, ge=1.0)
    phase3_seconds: float = Field(default=300.0, ge=1.0)
    buffer_seconds: float = Field(default=30.0, ge=0.0)
    mode: ProcessingMode = "auto"
    created_at_epoch: float = Field(default_factory=time.time)
    authorization_wait_seconds: float = Field(default=0.0, ge=0.0)
    degradation_level: int = Field(default=0, ge=0, le=4)
    material: MaterialBudget

    def processing_elapsed_seconds(self, *, now: float | None = None) -> float:
        wall = max(0.0, float(now if now is not None else time.time()) - self.created_at_epoch)
        return max(0.0, wall - self.authorization_wait_seconds)

    def remaining_seconds(self, *, now: float | None = None) -> float:
        return max(0.0, self.deadline_seconds - self.processing_elapsed_seconds(now=now))

    def phase_deadline_epoch(self, phase: str) -> float:
        offsets = {
            "phase1": self.phase1_seconds,
            "material_gap": self.phase1_seconds,
            "phase2": self.phase1_seconds + self.phase2_seconds,
            "phase3": self.deadline_seconds - self.buffer_seconds,
        }
        processing_offset = offsets.get(phase, self.deadline_seconds - self.buffer_seconds)
        return self.created_at_epoch + self.authorization_wait_seconds + processing_offset


def material_budget_for_duration(target_duration_seconds: float) -> MaterialBudget:
    duration = max(1.0, float(target_duration_seconds or 30.0))
    source_target = max(2, min(8, math.ceil(duration / 20.0) + 1))
    source_min = max(2, math.ceil(source_target * 0.65))
    candidate_cap = min(80, source_target * 10)
    search_task_cap = max(2, min(4, math.ceil(source_target / 2.0)))
    coverage_ratio = 2.0 if duration <= 30 else 1.6 if duration <= 90 else 1.35
    return MaterialBudget(
        source_target=source_target,
        source_min=source_min,
        candidate_cap=candidate_cap,
        search_task_cap=search_task_cap,
        coverage_ratio=coverage_ratio,
        supplement_rounds=1,
    )


def create_processing_budget(
    target_duration_seconds: float,
    *,
    deadline_seconds: float = 600.0,
    mode: ProcessingMode = "auto",
    created_at_epoch: float | None = None,
    phase1_seconds: float | None = None,
) -> ProcessingBudget:
    deadline = max(60.0, float(deadline_seconds or 600.0))
    # Preserve the requested 180/90/300/30 split at 600s and scale it for
    # explicitly shorter or longer deadlines.
    scale = deadline / 600.0
    resolved_phase1 = max(1.0, float(phase1_seconds)) if phase1_seconds is not None else max(1.0, 180.0 * scale)
    if mode == "speed":
        resolved_phase1 = min(resolved_phase1, max(1.0, 150.0 * scale))
    elif mode == "quality":
        resolved_phase1 = min(deadline * 0.45, max(resolved_phase1, 210.0 * scale))
    resolved_phase2 = max(1.0, 90.0 * scale)
    resolved_buffer = max(0.0, 30.0 * scale)
    resolved_phase3 = max(1.0, deadline - resolved_phase1 - resolved_phase2 - resolved_buffer)
    material = material_budget_for_duration(target_duration_seconds)
    if mode == "speed":
        target = max(2, material.source_target - 1)
        material = material.model_copy(
            update={
                "source_target": target,
                "source_min": min(target, max(2, math.ceil(target * 0.65))),
                "candidate_cap": min(80, target * 8),
                "coverage_ratio": max(1.2, material.coverage_ratio * 0.9),
            }
        )
    elif mode == "quality":
        target = min(8, material.source_target + 1)
        material = material.model_copy(
            update={
                "source_target": target,
                "source_min": min(target, max(2, math.ceil(target * 0.7))),
                "candidate_cap": min(80, target * 10),
                "coverage_ratio": material.coverage_ratio * 1.1,
            }
        )
    return ProcessingBudget(
        target_duration_seconds=max(1.0, float(target_duration_seconds or 30.0)),
        deadline_seconds=deadline,
        phase1_seconds=resolved_phase1,
        phase2_seconds=resolved_phase2,
        phase3_seconds=resolved_phase3,
        buffer_seconds=resolved_buffer,
        mode=mode,
        created_at_epoch=float(created_at_epoch if created_at_epoch is not None else time.time()),
        material=material,
    )
