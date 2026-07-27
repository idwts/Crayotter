"""State contracts for the three-phase editing workflow."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

try:
    from ..orchestration import ProcessingBudget
except ImportError:  # Support the historical ``python script/agent.py`` entrypoint.
    from orchestration import ProcessingBudget


class Step(BaseModel):
    """One material-preparation step produced by the Phase 1 planner."""

    id: int = Field(description="步骤编号")
    description: str = Field(description="步骤描述")
    tool_hint: str = Field(default="", description="建议使用的工具名称")
    depends_on: list[int] = Field(default_factory=list, description="前置步骤 ID")
    arguments: dict[str, Any] = Field(default_factory=dict, description="工具调用参数")
    status: str = Field(default="pending", description="pending/running/done/failed")
    result: str = Field(default="", description="执行结果")


class StepResult(BaseModel):
    """Structured execution result for one Phase 1 DAG step."""

    step_id: int
    tool_name: str
    status: Literal["done", "failed", "skipped"]
    result: str = ""
    duration_seconds: float = 0.0
    error: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    """Phase 1 material-preparation plan."""

    goal: str = Field(description="用户的最终目标")
    analysis: str = Field(default="", description="需求分析")
    steps: list[Step] = Field(default_factory=list, description="有序步骤列表")


class AgentState(BaseModel):
    """Shared state passed between LangGraph workflow nodes."""

    user_request: str = ""
    base_user_request: str = ""
    guidance_context: str = ""
    steering_target_phase: str = ""
    current_checkpoint: str = ""
    revision: int = 1

    plan: Plan | None = None
    current_step_index: int = 0
    active_step_id: int = 0
    prep_round: int = 0
    step_results: Annotated[list[StepResult], operator.add] = Field(default_factory=list)
    messages: Annotated[list[Any], operator.add] = Field(default_factory=list)

    target_duration_seconds: float = 0.0
    processing_budget: ProcessingBudget | None = None
    budget_report: dict[str, Any] = Field(default_factory=dict)
    degradation_level: int = Field(default=0, ge=0, le=4)

    phase: str = "planning"

    editing_blueprint: str = ""
    editing_plan_version: str = ""
    editing_plan_status: str = ""
    material_gap_report: dict[str, Any] = Field(default_factory=dict)
    gap_round: int = 0
    phase2_artifact_ids: list[str] = Field(default_factory=list)

    final_output: str = ""
    should_end: bool = False

    class Config:
        arbitrary_types_allowed = True
