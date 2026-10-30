from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import script.graph as graph


class FakeTool:
    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[dict] = []

    def invoke(self, arguments: dict) -> str:
        self.calls.append(arguments)
        return self.result


class Phase3ExecutionGuardTests(unittest.TestCase):
    def test_planner_rejects_duration_lost_to_transition_overlap(self) -> None:
        # Regression: ten 3s cuts become 27.3s after nine 0.3s overlaps.
        response = SimpleNamespace(content=json.dumps({
            "clips": [{"source_path": "/source.mp4", "start": 0, "end": 3} for _ in range(10)]
        }))
        scheduler = SimpleNamespace(run=lambda plan, execute, **kwargs: execute(plan.tasks[0], {}))
        with (
            patch.object(graph, "_artifact_registry"),
            patch.object(graph, "_resource_scheduler", return_value=scheduler),
            patch.object(graph, "_iter_source_videos", return_value=[Path("/source.mp4")]),
            patch.object(graph, "_build_full_analysis_context", return_value=""),
            patch.object(graph, "_get_llm", return_value=MagicMock()),
            patch.object(graph, "_invoke_llm", return_value=response),
            patch.object(graph, "_normalize_controlled_clip_bounds", return_value=30.0),
        ):
            with self.assertRaisesRegex(RuntimeError, "计划预计成片时长偏差过大"):
                graph._build_controlled_edit_plan(graph.AgentState(user_request="30秒测试", target_duration_seconds=30))

    def test_transition_failure_falls_back_to_plain_merge(self) -> None:
        transition_planner = FakeTool(
            json.dumps(
                {
                    "status": "success",
                    "transition_plan": [
                        {"cut_index": 0, "transition_type": "crossfade", "duration": 0.6}
                    ],
                }
            )
        )
        transition_tool = FakeTool("转场出错: ffmpeg 合成失败")
        merge_tool = FakeTool(
            json.dumps(
                {
                    "status": "success",
                    "path": "G:/tmp/phase3_assembled.mp4",
                    "total_duration": 39.8,
                }
            )
        )

        with patch.dict(
            graph._TOOL_NAME_MAP,
            {
                "plan_transition_timeline": transition_planner,
                "add_transition": transition_tool,
                "merge_videos": merge_tool,
            },
            clear=False,
        ):
            result = graph._merge_timeline_with_transition_fallback(
                paths=["G:/tmp/clip_01.mp4", "G:/tmp/clip_02.mp4"],
                target_duration_seconds=40.0,
                output_name="phase3_assembled",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["path"], "G:/tmp/phase3_assembled.mp4")
        self.assertEqual(len(transition_tool.calls), 1)
        self.assertEqual(len(merge_tool.calls), 1)
        self.assertEqual(merge_tool.calls[0]["target_duration"], 40.0)

    def test_react_trace_handler_stops_repeated_merge_calls(self) -> None:
        handler = graph._RealtimeToolTraceHandler()

        for index in range(graph.REACT_MAX_MERGE_VIDEO_CALLS):
            handler.on_tool_start(
                {"name": "merge_videos"},
                "{}",
                run_id=f"merge-{index}",
            )

        with self.assertRaises(RuntimeError):
            handler.on_tool_start(
                {"name": "merge_videos"},
                "{}",
                run_id="merge-over-limit",
            )

    def test_controlled_clip_bounds_clamp_partial_eof_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.mp4"
            source.write_bytes(b"video")
            plan = graph.ControlledEditPlan(
                clips=[
                    graph.ControlledClip(
                        source_path=str(source),
                        start=6.0,
                        end=9.0,
                    )
                ]
            )
            with patch.object(
                graph,
                "probe_media",
                return_value=SimpleNamespace(duration_seconds=7.45),
            ), patch.object(graph, "_emit_orchestration_event"):
                total = graph._normalize_controlled_clip_bounds(plan, [str(source)])

            self.assertAlmostEqual(total, 1.45)
            self.assertEqual(plan.clips[0].end, 7.45)

    def test_controlled_clip_bounds_reject_start_past_eof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.mp4"
            source.write_bytes(b"video")
            plan = graph.ControlledEditPlan(
                clips=[
                    graph.ControlledClip(
                        source_path=str(source),
                        start=9.0,
                        end=11.0,
                    )
                ]
            )
            with patch.object(
                graph,
                "probe_media",
                return_value=SimpleNamespace(duration_seconds=7.45),
            ):
                with self.assertRaisesRegex(RuntimeError, "越过源视频 EOF"):
                    graph._normalize_controlled_clip_bounds(plan, [str(source)])


if __name__ == "__main__":
    unittest.main()
