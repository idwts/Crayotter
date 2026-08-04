from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from tools._shared import BUNDLE_DIR
from tools.subtitle_layout import create_subtitle_clip, wrap_subtitle_text


class SubtitleLayoutTests(unittest.TestCase):
    def test_multiline_subtitle_stays_inside_bottom_safe_area(self) -> None:
        font_path = (
            BUNDLE_DIR
            / "AlibabaPuHuiTi-3-55-Regular"
            / "AlibabaPuHuiTi-3-55-Regular.ttf"
        )
        self.assertTrue(font_path.is_file())
        clip, y_position, _, bottom_margin, _ = create_subtitle_clip(
            text="这是一段用于验证多行字幕不会越过视频画布底边的较长字幕内容" * 3,
            font_path=str(font_path),
            video_size=(1280, 720),
            duration=1.0,
        )
        try:
            self.assertGreaterEqual(clip.h, 1)
            self.assertGreaterEqual(y_position, 0)
            self.assertLessEqual(y_position + clip.h, 720 - bottom_margin)
        finally:
            clip.close()

    def test_wrapping_does_not_leave_punctuation_on_its_own_line(self) -> None:
        font_path = (
            BUNDLE_DIR
            / "AlibabaPuHuiTi-3-55-Regular"
            / "AlibabaPuHuiTi-3-55-Regular.ttf"
        )
        wrapped = wrap_subtitle_text(
            "这是一段刚好需要自动换行的字幕内容。",
            str(font_path),
            28,
            180,
        )
        self.assertTrue(all(line not in "，。！？；：、,.!?;:" for line in wrapped.splitlines()))


if __name__ == "__main__":
    unittest.main()
