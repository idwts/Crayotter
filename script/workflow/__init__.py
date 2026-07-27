"""Stable workflow contracts shared by the Crayotter graph.

The phase implementations still live in :mod:`script.graph`.  This package
contains the pieces that should remain stable while those implementations are
split into smaller modules: state schemas, tool groups, and graph topology.
"""

from .state import AgentState, Plan, Step, StepResult
from .loops import LoopController, LoopDecision, LoopPolicy
from .skills import SkillRegistry, ToolSkill
from .tool_catalog import (
    EDITING_TOOL_NAMES,
    PREP_TOOL_NAMES,
    REMOTE_PREP_TOOL_NAMES,
    ToolCatalog,
    build_tool_catalog,
)
from .topology import WorkflowNodes, WorkflowRoutes, build_workflow_graph

__all__ = [
    "AgentState",
    "EDITING_TOOL_NAMES",
    "LoopController",
    "LoopDecision",
    "LoopPolicy",
    "PREP_TOOL_NAMES",
    "Plan",
    "REMOTE_PREP_TOOL_NAMES",
    "Step",
    "StepResult",
    "SkillRegistry",
    "ToolCatalog",
    "ToolSkill",
    "WorkflowNodes",
    "WorkflowRoutes",
    "build_tool_catalog",
    "build_workflow_graph",
]
