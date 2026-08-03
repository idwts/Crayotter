from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import script.graph as graph
from script.phases.material_preparation.gap_policy import deterministic_material_sufficient
from script.tools.recall_semantic_segments import _select_temporally_diverse


class LongVideoCoverageTests(unittest.TestCase):
    def test_analysis_context_samples_across_entire_single_source(self) -> None:
        segments = [
            {
                "start": index * 6,
                "end": (index + 1) * 6,
                "duration": 6,
                "semantic_text": f"时间段{index}",
            }
            for index in range(100)
        ]
        payload = {
            "source_video": "single_long.webm",
            "source_duration_seconds": 600,
            "segments": [{"start": item["start"], "end": item["end"]} for item in segments],
            "semantic_segments": segments,
            "analysis_text": "\n".join(
                f"t={item['start']}s-t={item['end']}s 时间段{index}"
                for index, item in enumerate(segments)
            ),
        }

        with tempfile.TemporaryDirectory() as tmp:
            analysis_path = Path(tmp) / "single_long_analysis.json"
            analysis_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with patch.object(graph, "_iter_analysis_json_files", return_value=[analysis_path]):
                context = graph._build_full_analysis_context()

        self.assertIn("t=0.0s ~ t=6.0s", context)
        self.assertIn("t=594.0s ~ t=600.0s", context)
        self.assertIn("时间段0", context)
        self.assertIn("时间段99", context)
        self.assertIn("时间均匀抽样 40/100", context)

    def test_single_source_fallback_spreads_clips_over_source_duration(self) -> None:
        source = Path("single_long.webm").resolve()
        state = graph.AgentState(
            user_request="将单个长视频剪成 30 秒摘要",
            target_duration_seconds=30,
        )
        with patch.object(graph, "_iter_source_videos", return_value=[source]), patch.object(
            graph, "_iter_analysis_json_files", return_value=[]
        ), patch.object(
            graph,
            "_probe_source_durations",
            return_value={str(source): 600.0},
        ):
            plan = graph._fallback_editing_plan(state)

        self.assertEqual(len(plan.scenes), 6)
        self.assertEqual(plan.scenes[0].source_start, 0.0)
        self.assertGreater(plan.scenes[-1].source_start, 500.0)
        self.assertEqual(plan.scenes[-1].source_end, 600.0)

    def test_material_gate_rejects_prefix_only_timeline(self) -> None:
        metrics = {
            "source_count": 1,
            "required_sources": 1,
            "analysis_complete_ratio": 1.0,
            "duration_coverage_ratio": 2.0,
            "required_duration_coverage_ratio": 1.0,
            "timeline_coverage_ratio": 0.25,
            "required_timeline_coverage_ratio": 0.75,
            "topic_coverage_ratio": 1.0,
            "orientation_match_ratio": 1.0,
            "quality_floor_met": True,
            "duplicate_ratio": 0.0,
        }

        self.assertFalse(deterministic_material_sufficient(metrics))
        metrics["timeline_coverage_ratio"] = 1.0
        self.assertTrue(deterministic_material_sufficient(metrics))

    def test_stale_analysis_is_refreshed_after_proxy_fix(self) -> None:
        video = Path("single_long.webm")
        with tempfile.TemporaryDirectory() as tmp:
            analysis_path = Path(tmp) / "single_long_analysis.json"
            index = {"single_long": [analysis_path]}

            analysis_path.write_text("{}", encoding="utf-8")
            self.assertIsNone(graph._current_analysis_for(video, index))

            analysis_path.write_text(
                json.dumps({"analysis_version": graph.VIDEO_ANALYSIS_VERSION}),
                encoding="utf-8",
            )
            self.assertEqual(graph._current_analysis_for(video, index), analysis_path)

    def test_semantic_selection_spreads_near_equal_matches_over_time(self) -> None:
        candidates = [
            {
                "score": 1.0,
                "source_video": "single_long.webm",
                "start": index * 100.0,
                "end": index * 100.0 + 10.0,
                "_source_duration": 600.0,
            }
            for index in range(6)
        ]

        selected = _select_temporally_diverse(candidates, 3)
        starts = [item["start"] for item in selected]

        self.assertIn(0.0, starts)
        self.assertIn(500.0, starts)
        self.assertTrue(any(200.0 <= start <= 300.0 for start in starts))


if __name__ == "__main__":
    unittest.main()
