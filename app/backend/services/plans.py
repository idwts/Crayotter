"""Versioned editing-plan storage boundary."""

from __future__ import annotations

from pathlib import Path

from script.editing_plan import EditingPlanStore


class PlanReviewService:
    @staticmethod
    def store_for_workspace(workspace: Path) -> EditingPlanStore:
        return EditingPlanStore(workspace)
