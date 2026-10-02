from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


_MODULE_PATH = Path(__file__).resolve().parents[2] / "script" / "tools" / "_native_ffmpeg.py"
_SPEC = importlib.util.spec_from_file_location("crayotter_native_ffmpeg_test", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_native_ffmpeg = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _native_ffmpeg
_SPEC.loader.exec_module(_native_ffmpeg)
VideoProbe = _native_ffmpeg.VideoProbe


class NativeFfmpegTests(unittest.TestCase):
    def test_native_pipeline_is_opt_in(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_native_ffmpeg.native_ffmpeg_enabled())
        with patch.dict(os.environ, {"CRAYOTTER_NATIVE_FFMPEG_PIPELINE": "1"}):
            self.assertTrue(_native_ffmpeg.native_ffmpeg_enabled())

    def test_export_uses_direct_multithreaded_filter_pipeline(self) -> None:
        captured: list[str] = []
        probe = VideoProbe(1918, 1080, 30.0, 12.5, True)
        with (
            patch.object(_native_ffmpeg, "probe_video", return_value=probe),
            patch.object(_native_ffmpeg, "_base_command", return_value=["ffmpeg"]),
            patch.object(
                _native_ffmpeg,
                "_run",
                side_effect=lambda command, **_: captured.extend(command),
            ),
            patch.dict(
                os.environ,
                {
                    "CRAYOTTER_FFMPEG_THREADS": "8",
                    "CRAYOTTER_FFMPEG_FILTER_THREADS": "4",
                },
            ),
        ):
            duration = _native_ffmpeg.export_video_native(
                Path("input.mp4"),
                Path("output.mp4"),
                target_size=(1920, 1080),
            )

        self.assertEqual(duration, 12.5)
        self.assertIn("-threads", captured)
        self.assertEqual(captured[captured.index("-threads") + 1], "8")
        self.assertIn("scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,format=yuv420p", captured)
        self.assertNotIn("rawvideo", captured)

    def test_merge_normalizes_streams_and_synthesizes_missing_audio(self) -> None:
        probes = {
            "one.mp4": VideoProbe(1920, 1080, 30.0, 5.0, True),
            "two.mp4": VideoProbe(1280, 720, 25.0, 7.0, False),
        }
        captured: list[str] = []
        with (
            patch.object(
                _native_ffmpeg,
                "probe_video",
                side_effect=lambda path: probes[path.name],
            ),
            patch.object(_native_ffmpeg, "_base_command", return_value=["ffmpeg"]),
            patch.object(
                _native_ffmpeg,
                "_run",
                side_effect=lambda command, **_: captured.extend(command),
            ),
        ):
            duration, count, canvas = _native_ffmpeg.merge_videos_native(
                [Path("one.mp4"), Path("two.mp4")],
                Path("merged.mp4"),
                target_duration=None,
                tolerance=0.15,
            )

        self.assertEqual(duration, 12.0)
        self.assertEqual(count, 2)
        self.assertEqual(canvas, (1920, 1080))
        filter_graph = captured[captured.index("-filter_complex") + 1]
        self.assertIn("anullsrc=channel_layout=stereo", filter_graph)
        self.assertIn("concat=n=2:v=1:a=1[vout][aout]", filter_graph)
        self.assertIn("fps=30.000000", filter_graph)

    def test_merge_preserves_target_duration_selection(self) -> None:
        probes = {
            "one.mp4": VideoProbe(1920, 1080, 30.0, 8.0, False),
            "two.mp4": VideoProbe(1920, 1080, 30.0, 8.0, False),
        }
        with (
            patch.object(
                _native_ffmpeg,
                "probe_video",
                side_effect=lambda path: probes[path.name],
            ),
            patch.object(_native_ffmpeg, "_base_command", return_value=["ffmpeg"]),
            patch.object(_native_ffmpeg, "_run"),
        ):
            duration, count, _ = _native_ffmpeg.merge_videos_native(
                [Path("one.mp4"), Path("two.mp4")],
                Path("merged.mp4"),
                target_duration=10.0,
                tolerance=0.15,
            )

        self.assertEqual(duration, 10.0)
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
