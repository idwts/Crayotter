from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from phase3_rl.prompt_builder import _first_tool_call_example, _workspace_snapshot


class MediumPromptTests(unittest.TestCase):
    def test_workspace_snapshot_uses_tool_callable_relative_paths(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            material = root / "user_temp" / "materials" / "clip.mp4"
            material.parent.mkdir(parents=True)
            material.write_bytes(b"video")

            snapshot = _workspace_snapshot(root, "user_temp")

        self.assertIn("user_temp", snapshot)
        self.assertIn("materials", snapshot)
        self.assertIn("clip.mp4", snapshot)

    def test_medium_task_starts_with_video_analysis(self) -> None:
        with patch.dict("os.environ", {"MULTI_TURN_FORMAT": "qwen3_coder"}):
            example = _first_tool_call_example(
                ["inspect_video_duration", "analyze_video", "cut_video"],
                {"multi_constraint_task": True},
            )

        self.assertIn("<function=analyze_video>", example)

    def test_legacy_medium_horizon_metrics_start_with_video_analysis(self) -> None:
        with patch.dict("os.environ", {"MULTI_TURN_FORMAT": "qwen3_coder"}):
            example = _first_tool_call_example(
                ["inspect_video_duration", "analyze_video", "cut_video"],
                {"horizon_metrics": {"task_type": "medium_horizon_editing"}},
            )

        self.assertIn("<function=analyze_video>", example)


if __name__ == "__main__":
    unittest.main()
