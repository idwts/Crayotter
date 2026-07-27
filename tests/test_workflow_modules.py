from __future__ import annotations

import unittest

from pydantic import BaseModel

from script.workflow import (
    AgentState,
    WorkflowNodes,
    WorkflowRoutes,
    build_tool_catalog,
    build_workflow_graph,
)


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _TopologyState(BaseModel):
    phase: str = "planning"
    should_end: bool = False


def _node(state: _TopologyState) -> dict[str, object]:
    return {}


class WorkflowModuleTests(unittest.TestCase):
    def test_tool_catalog_exposes_phase_specific_views(self) -> None:
        search = _FakeTool("search_material_sources")
        cut = _FakeTool("cut_video")
        unknown = _FakeTool("future_tool")

        catalog = build_tool_catalog([search, cut, unknown])

        self.assertIs(catalog.by_name["search_material_sources"], search)
        self.assertEqual(catalog.preparation, [search])
        self.assertEqual(catalog.editing, [cut])

    def test_agent_state_contract_has_compatible_defaults(self) -> None:
        state = AgentState(user_request="制作校园宣传片")

        self.assertEqual(state.phase, "planning")
        self.assertEqual(state.step_results, [])
        self.assertFalse(state.should_end)

    def test_topology_can_compile_independently_of_phase_implementations(self) -> None:
        nodes = WorkflowNodes(
            steering_entry=_node,
            steering_after_planner=_node,
            steering_after_phase1=_node,
            steering_after_material_gap=_node,
            steering_after_blueprint=_node,
            planner=_node,
            phase1_scheduler=_node,
            material_gap_evaluator=_node,
            editing_research=_node,
            generate_editing_plan=_node,
            validate_editing_plan=_node,
            plan_review_gate=_node,
            react_editor=_node,
        )
        routes = WorkflowRoutes(
            after_steering_entry=lambda state: "planner",
            after_planner_steering=lambda state: "phase1_scheduler",
            after_phase1_steering=lambda state: "material_gap_evaluator",
            after_material_gap_steering=lambda state: "editing_research",
            after_blueprint_steering=lambda state: "generate_editing_plan",
            after_react_editor=lambda state: "__end__",
        )

        compiled = build_workflow_graph(
            state_schema=_TopologyState,
            nodes=nodes,
            routes=routes,
        )
        graph_nodes = set(compiled.get_graph().nodes)

        self.assertIn("planner", graph_nodes)
        self.assertIn("material_gap_evaluator", graph_nodes)
        self.assertIn("react_editor", graph_nodes)


if __name__ == "__main__":
    unittest.main()
