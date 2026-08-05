import unittest

from script.tools._shared import (
    _extract_semantic_segments_from_analysis,
    _extract_time_segments_from_analysis,
)


class AnalysisTimeParsingTests(unittest.TestCase):
    def test_parses_seconds_and_clock_ranges(self) -> None:
        text = (
            "1. t=0s-t=4s 开场全景。\n"
            "2. **00:05 - 00:13** 人物进入室内。\n"
            "3. 14秒至22秒 展示工作区。"
        )

        segments = _extract_time_segments_from_analysis(text)

        self.assertEqual(
            segments,
            [
                {"start": 0.0, "end": 4.0},
                {"start": 5.0, "end": 13.0},
                {"start": 14.0, "end": 22.0},
            ],
        )

    def test_clock_ranges_produce_semantic_segments(self) -> None:
        text = (
            "1. **00:00 - 00:04** 建筑外景，固定广角镜头。\n"
            "2. **00:05 - 00:13** 人物进入大厅，镜头跟随。"
        )

        segments = _extract_semantic_segments_from_analysis(text)

        self.assertEqual(len(segments), 2)
        self.assertEqual((segments[0]["start"], segments[0]["end"]), (0.0, 4.0))
        self.assertIn("建筑外景", segments[0]["semantic_text"])


if __name__ == "__main__":
    unittest.main()
