from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase3_rl.exploration import next_counterfactual_profile
from phase3_rl.semantic_artifact import _normalize_result, _select_artifact_segments


class CounterfactualBranchTests(unittest.TestCase):
    def test_four_consecutive_rollouts_get_distinct_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                "os.environ",
                {"CRAYOTTER_RL_COUNTERFACTUAL_COUNTER_DIR": temp_dir},
            ):
                profiles = [next_counterfactual_profile("fixture_unique_test") for _ in range(4)]

        self.assertEqual({item["branch_index"] for item in profiles}, {0, 1, 2, 3})
        self.assertEqual(len({item["prefix_id"] for item in profiles}), 1)
        self.assertTrue(all(item["branch_point_event_index"] == 0 for item in profiles))


class SemanticArtifactTests(unittest.TestCase):
    def test_only_generated_video_artifacts_are_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.mp4"
            clip = Path(temp_dir) / "clip.mp4"
            source.write_bytes(b"source")
            clip.write_bytes(b"clip")
            events = [
                {
                    "tool_name": "inspect_video_duration",
                    "stage": "validation",
                    "success": True,
                    "output_paths": [str(source)],
                },
                {
                    "tool_name": "cut_video",
                    "stage": "rough_cut",
                    "success": True,
                    "output_paths": [str(clip)],
                },
            ]

            selected = _select_artifact_segments(events, 4)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["path"], clip)

    def test_semantic_values_are_clamped_and_keyed_by_segment(self) -> None:
        result = _normalize_result(
            {
                "segments": [
                    {
                        "segment_id": "segment_001",
                        "request_fulfillment_delta": 2.0,
                        "coverage_delta": -2.0,
                        "confidence": 0.8,
                    },
                    {"segment_id": "unknown", "request_fulfillment_delta": 1.0},
                ]
            },
            {"segment_001"},
            "judge",
            2,
        )

        self.assertTrue(result["eligible"])
        self.assertEqual(result["segments"]["segment_001"]["request_fulfillment_delta"], 1.0)
        self.assertEqual(result["segments"]["segment_001"]["coverage_delta"], -1.0)
        self.assertNotIn("unknown", result["segments"])


if __name__ == "__main__":
    unittest.main()
