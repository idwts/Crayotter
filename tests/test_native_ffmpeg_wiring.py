"""Lightweight unit tests for the native ffmpeg wiring and unified tool format.

No real ffmpeg/moviepy/video files are needed: the ffprobe/ffmpeg boundary is
mocked, and the format helpers are pure functions.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from script.tools import _native_ffmpeg as native
from script.tools._shared import tool_error, tool_success


class ToolResultFormatTest(unittest.TestCase):
    def test_tool_success_is_json_with_status(self) -> None:
        payload = json.loads(tool_success(path="/workspace/out.mp4", duration=12.5))
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["path"], "/workspace/out.mp4")
        self.assertEqual(payload["duration"], 12.5)

    def test_tool_error_keeps_error_marker(self) -> None:
        text = tool_error("剪辑", ValueError("boom"))
        # graph.py / phase3_rl detect failures by the "出错" substring — keep it.
        self.assertIn("出错", text)
        self.assertIn("剪辑", text)
        self.assertIn("boom", text)


class BinariesAvailableTest(unittest.TestCase):
    def test_false_when_ffmpeg_missing(self) -> None:
        with mock.patch.object(native.shutil, "which", return_value=None):
            self.assertFalse(native.binaries_available())

    def test_probe_video_optional_swallows_errors(self) -> None:
        with mock.patch.object(native, "probe_video", side_effect=RuntimeError("no ffprobe")):
            self.assertIsNone(native.probe_video_optional(Path("x.mp4")))


class MixNarrationCommandTest(unittest.TestCase):
    def test_filtergraph_truncates_and_delays_segments(self) -> None:
        probe = native.VideoProbe(width=1920, height=1080, fps=30.0, duration=60.0, has_audio=True)
        captured: dict[str, list[str]] = {}

        def fake_run(command, **kwargs):
            captured["command"] = list(command)

            class Result:
                returncode = 0

            return Result()

        with mock.patch.object(native, "probe_video", return_value=probe), mock.patch.object(
            native, "_binary", side_effect=lambda env, exe: exe
        ), mock.patch.object(native.subprocess, "run", side_effect=fake_run):
            native.mix_narration_native(
                Path("in.mp4"),
                [(Path("a.mp3"), 2.0, 1.0, 5.0), (Path("b.mp3"), 10.0, 0.9, None)],
                Path("out.mp4"),
            )

        command = captured["command"]
        filtergraph = command[command.index("-filter_complex") + 1]
        self.assertIn("volume=0.200", filtergraph)  # original audio ducked
        self.assertIn("atrim=duration=5.000000", filtergraph)  # slot truncation
        self.assertIn("adelay=2000|2000", filtergraph)
        self.assertIn("adelay=10000|10000", filtergraph)
        self.assertIn("-c:v", command)
        self.assertEqual(command[command.index("-c:v") + 1], "copy")  # no video re-encode

    def test_no_audio_track_mixes_narration_only(self) -> None:
        probe = native.VideoProbe(width=640, height=360, fps=24.0, duration=10.0, has_audio=False)
        captured: dict[str, list[str]] = {}

        def fake_run(command, **kwargs):
            captured["command"] = list(command)

            class Result:
                returncode = 0

            return Result()

        with mock.patch.object(native, "probe_video", return_value=probe), mock.patch.object(
            native, "_binary", side_effect=lambda env, exe: exe
        ), mock.patch.object(native.subprocess, "run", side_effect=fake_run):
            native.mix_narration_native(
                Path("in.mp4"), [(Path("a.mp3"), 0.0, 1.0, None)], Path("out.mp4")
            )

        filtergraph = captured["command"][captured["command"].index("-filter_complex") + 1]
        self.assertNotIn("[0:a:0]", filtergraph)

    def test_empty_parts_rejected(self) -> None:
        with self.assertRaises(ValueError):
            native.mix_narration_native(Path("in.mp4"), [], Path("out.mp4"))


if __name__ == "__main__":
    unittest.main()


class FfprobeShimJsonContractTest(unittest.TestCase):
    """The shim must answer exactly the -of json field sets it can measure
    (duration; index/codec_type/width/height/frame rates) and fail loudly
    (rc=1) for anything beyond, e.g. media_consistency's codec/size request."""

    def _run_shim(self, entries: str, video: Path):
        import subprocess

        shim = SCRIPT_DIR / "ffprobe_shim.py"
        return subprocess.run(
            [sys.executable, str(shim), "-v", "error", "-show_entries", entries,
             "-of", "json", str(video)],
            capture_output=True, text=True, timeout=120,
        )

    def _make_clip(self) -> Path:
        import cv2
        import numpy as np

        from script.tools._shared import WORKSPACE

        clip = WORKSPACE / "shim_contract_test.mp4"
        writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (64, 48))
        frame = np.zeros((48, 64, 3), dtype="uint8")
        for _ in range(20):
            writer.write(frame)
        writer.release()
        self.addCleanup(clip.unlink, missing_ok=True)
        return clip

    def test_duration_only_query_returns_format_json(self):
        clip = self._make_clip()
        result = self._run_shim("format=duration", clip)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertAlmostEqual(float(payload["format"]["duration"]), 2.0, delta=0.3)
        self.assertNotIn("streams", payload)

    def test_stream_query_returns_measured_video_stream(self):
        clip = self._make_clip()
        result = self._run_shim(
            "format=duration:stream=index,codec_type,width,height,avg_frame_rate,r_frame_rate",
            clip,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        video = payload["streams"][0]
        self.assertEqual(video["codec_type"], "video")
        self.assertEqual((video["width"], video["height"]), (64, 48))
        # consumed by _native_ffmpeg.probe_video without raising
        probe = native.probe_video(clip)
        self.assertEqual((probe.width, probe.height), (64, 48))
        self.assertAlmostEqual(probe.duration, 2.0, delta=0.3)

    def test_unmeasurable_fields_fail_loudly(self):
        clip = self._make_clip()
        # media_consistency's probe shape: codec_name/pix_fmt/size/bit_rate are
        # beyond the shim — must NOT get silently fabricated values.
        result = self._run_shim(
            "format=duration,size,bit_rate:stream=index,codec_name,pix_fmt", clip
        )
        self.assertEqual(result.returncode, 1)
