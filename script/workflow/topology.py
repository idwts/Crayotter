"""Declarative LangGraph topology for the Crayotter workflow."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Callable

from langgraph.graph import START, StateGraph

NodeCallable = Callable[[Any], dict[str, Any]]
RouteCallable = Callable[[Any], Any]


@dataclass(frozen=True)
class WorkflowNodes:
    steering_entry: NodeCallable
    steering_after_planner: NodeCallable
    steering_after_phase1: NodeCallable
    steering_after_material_gap: NodeCallable
    steering_after_blueprint: NodeCallable
    planner: NodeCallable
    phase1_scheduler: NodeCallable
    material_gap_evaluator: NodeCallable
    editing_research: NodeCallable
    generate_editing_plan: NodeCallable
    validate_editing_plan: NodeCallable
    plan_review_gate: NodeCallable
    react_editor: NodeCallable


@dataclass(frozen=True)
class WorkflowRoutes:
    after_steering_entry: RouteCallable
    after_planner_steering: RouteCallable
    after_phase1_steering: RouteCallable
    after_material_gap_steering: RouteCallable
    after_blueprint_steering: RouteCallable
    after_react_editor: RouteCallable


def build_workflow_graph(
    *,
    state_schema: type[Any],
    nodes: WorkflowNodes,
    routes: WorkflowRoutes,
) -> Any:
    """Compile the stable graph topology from injected node implementations."""

    graph = StateGraph(state_schema)
    for field in fields(nodes):
        graph.add_node(field.name, getattr(nodes, field.name))

    graph.add_edge(START, "steering_entry")
    graph.add_conditional_edges("steering_entry", routes.after_steering_entry)

    graph.add_edge("planner", "steering_after_planner")
    graph.add_conditional_edges(
        "steering_after_planner",
        routes.after_planner_steering,
    )

    graph.add_edge("phase1_scheduler", "steering_after_phase1")
    graph.add_conditional_edges(
        "steering_after_phase1",
        routes.after_phase1_steering,
    )

    graph.add_edge("material_gap_evaluator", "steering_after_material_gap")
    graph.add_conditional_edges(
        "steering_after_material_gap",
        routes.after_material_gap_steering,
    )

    graph.add_edge("editing_research", "steering_after_blueprint")
    graph.add_conditional_edges(
        "steering_after_blueprint",
        routes.after_blueprint_steering,
    )

    graph.add_edge("generate_editing_plan", "validate_editing_plan")
    graph.add_edge("validate_editing_plan", "plan_review_gate")
    graph.add_edge("plan_review_gate", "react_editor")
    graph.add_conditional_edges("react_editor", routes.after_react_editor)

    return graph.compile()
