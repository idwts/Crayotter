from __future__ import annotations

import unittest

from script.analysis_timeline import bounded_analysis_detail, normalize_analysis_payload


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


if __name__ == "__main__":
    unittest.main()
