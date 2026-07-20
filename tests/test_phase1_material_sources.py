from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import script.graph as graph


class _FakeTool:
    def __init__(self, name: str, response: Any) -> None:
        self.name = name
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def invoke(self, arguments: dict[str, Any]) -> str:
        self.calls.append(dict(arguments))
        return json.dumps(self.response, ensure_ascii=False)


class _CapturingScheduler:
    def __init__(self) -> None:
        self.plans = []

    def run(self, plan, execute, resume=True, allow_partial_failure=False):
        self.plans.append(plan)
        states = {}
        for task in plan.tasks:
            result = execute(task, {})
            states[task.id] = type(
                "State",
                (),
                {"status": "completed", "result": result.data},
            )()
        return states


class Phase1MaterialSourceTests(unittest.TestCase):
    def test_phase1_downloads_use_platform_agnostic_download_tool(self) -> None:
        selected = [
            {
                "source": "douyin",
                "url": "https://www.douyin.com/video/7336481666707229992",
                "title": "抖音素材",
            },
            {
                "source": "xiaohongshu",
                "url": "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9",
                "title": "小红书素材",
            },
        ]
        fake_tool = _FakeTool(
            "download_material_video",
            {
                "status": "success",
                "path": str(graph.WORKSPACE / "selected.mp4"),
                "source": "douyin",
            },
        )
        scheduler = _CapturingScheduler()
        state = graph.AgentState(user_request="做一个校园宣传片")

        fake_probe = SimpleNamespace(
            width=1280,
            height=720,
            average_fps=30.0,
            is_hdr=False,
            to_dict=lambda: {"width": 1280, "height": 720, "average_fps": 30.0},
        )
        with patch.dict(graph._TOOL_NAME_MAP, {"download_material_video": fake_tool}, clear=False), patch.object(
            graph, "probe_media", return_value=fake_probe
        ), patch.object(Path, "write_text", return_value=1):
            downloaded = graph._run_phase1_downloads(state, scheduler, selected)

        self.assertEqual(len(downloaded), 2)
        self.assertEqual(scheduler.plans[0].tasks[0].tool_name, "download_material_video")
        self.assertEqual(scheduler.plans[0].tasks[1].tool_name, "download_material_video")
        self.assertEqual(fake_tool.calls[0]["source"], "douyin")
        self.assertEqual(fake_tool.calls[1]["source"], "xiaohongshu")
        self.assertNotIn("download_bilibili_video", {task.tool_name for task in scheduler.plans[0].tasks})
        for task in scheduler.plans[0].tasks:
            self.assertEqual(task.resources.get("download_pool"), 1)
            self.assertEqual(task.resources.get("ffmpeg_pool"), 1)


if __name__ == "__main__":
    unittest.main()
