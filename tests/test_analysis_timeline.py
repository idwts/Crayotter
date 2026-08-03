from __future__ import annotations

import unittest

from script.analysis_timeline import (
    bounded_analysis_detail,
    normalize_analysis_payload,
    sample_timeline_segments,
    timeline_bucket_coverage_ratio,
    timeline_covered_duration_seconds,
)


class AnalysisTimelineTests(unittest.TestCase):
    def test_segments_are_clamped_to_real_duration_and_past_eof_is_dropped(self) -> None:
        payload = {
            "segments": [
                {"start": 0, "end": 2},
                {"start": 7, "end": 9},
                {"start": 9, "end": 11},
            ],
            "semantic_segments": [
                {"start": 0, "end": 2, "duration": 2, "semantic_text": "有效开场"},
                {"start": 7, "end": 9, "duration": 2, "semantic_text": "临近结尾"},
                {"start": 9, "end": 11, "duration": 2, "semantic_text": "不存在的画面"},
            ],
            "semantic_index": {"segment_count": 3},
            "analysis_text": "模型原始输出保留用于审计",
        }

        normalized, report = normalize_analysis_payload(payload, 7.45)

        self.assertEqual(normalized["segments"][-1], {"start": 7.0, "end": 7.45})
        self.assertEqual(normalized["semantic_segments"][-1]["duration"], 0.45)
        self.assertEqual(normalized["semantic_index"]["segment_count"], 2)
        self.assertEqual(report["clamped_count"], 2)
        self.assertEqual(report["dropped_count"], 2)
        detail = bounded_analysis_detail(normalized)
        self.assertIn("临近结尾", detail)
        self.assertNotIn("不存在的画面", detail)

    def test_long_timeline_sampling_keeps_head_middle_and_tail(self) -> None:
        segments = [
            {"start": index * 10, "end": (index + 1) * 10, "semantic_text": f"片段{index}"}
            for index in range(100)
        ]

        sampled = sample_timeline_segments(segments, 5, 1000)

        self.assertEqual(sampled[0]["start"], 0)
        self.assertEqual(sampled[-1]["end"], 1000)
        self.assertTrue(any(450 <= item["start"] <= 550 for item in sampled))

    def test_bucket_coverage_detects_prefix_only_analysis(self) -> None:
        prefix_only = [{"start": 0, "end": 200}]
        distributed = [
            {"start": index * 100, "end": (index + 1) * 100}
            for index in range(8)
        ]

        self.assertEqual(timeline_bucket_coverage_ratio(prefix_only, 800), 0.25)
        self.assertEqual(timeline_bucket_coverage_ratio(distributed, 800), 1.0)

    def test_covered_duration_does_not_double_count_highlights(self) -> None:
        segments = [
            {"start": 0, "end": 300},
            {"start": 300, "end": 600},
            {"start": 100, "end": 200},
            {"start": 420, "end": 520},
        ]

        self.assertEqual(timeline_covered_duration_seconds(segments, 600), 600.0)


if __name__ == "__main__":
    unittest.main()
