from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import script.graph as graph


class FakeTool:
    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[dict] = []

    def invoke(self, arguments: dict) -> str:
        self.calls.append(arguments)
        return self.result


class Phase3ExecutionGuardTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
