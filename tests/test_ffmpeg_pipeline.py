from __future__ import annotations

import importlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from tools import _native_ffmpeg as native
from tools.subtitle_layout import burn_subtitles, resolve_subtitle_font


def available(name, variable):
    configured = os.environ.get(variable)
    return bool(configured and Path(configured).is_file() or shutil.which(name))


class AtomicRenderTests(unittest.TestCase):
    def test_failed_render_preserves_input_and_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "same.mp4"
            output.write_bytes(b"original video")
            def fail(command):
                Path(command[-1]).write_bytes(b"partial output")
                raise RuntimeError("encoder failed")
            with patch.object(native, "_run", side_effect=fail):
                with self.assertRaises(RuntimeError):
                    native._render_output(["ffmpeg", "-i", str(output)], output)
            self.assertEqual(output.read_bytes(), b"original video")
            self.assertEqual(list(Path(directory).iterdir()), [output])


@unittest.skipUnless(available("ffmpeg", "FFMPEG_BIN") and available("ffprobe", "FFPROBE_BIN"), "FFmpeg/FFprobe required")
class FfmpegPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="crayotter_ffmpeg_test_")
        cls.root = Path(cls.temp.name)
        cls.silent = cls.root / "silent.mp4"
        cls.audio = cls.root / "short_audio.mp4"
        cls.voice = cls.root / "voice.wav"
        cls.ffmpeg = os.environ.get("FFMPEG_BIN", "ffmpeg")
        cls.run_ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.silent))
        cls.run_ffmpeg("-f", "lavfi", "-i", "color=c=red:s=180x320:r=25:d=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5", "-c:v", "libx264", "-c:a", "aac", str(cls.audio))
        cls.run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=880:duration=0.5", str(cls.voice))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def run_ffmpeg(cls, *args):
        return subprocess.run([cls.ffmpeg, "-v", "error", "-y", *args], check=True, capture_output=True, timeout=60).stdout

    def test_cut_merge_export_duration_geometry_and_missing_audio(self):
        cut = self.root / "cut.mp4"
        native.cut_video_native(self.silent, cut, start_time=0.5, end_time=1.5)
        self.assertAlmostEqual(native.probe_video(cut).duration, 1.0, delta=0.1)
        merged = self.root / "merged.mp4"
        native.merge_videos_native([cut, self.audio], merged, target_duration=None, tolerance=0.15)
        probe = native.probe_video(merged)
        self.assertEqual((probe.width, probe.height), (320, 180))
        self.assertTrue(probe.has_audio)
        # A short audio stream must not shorten the corresponding video segment.
        self.assertAlmostEqual(probe.duration, 3.0, delta=0.12)
        exported = self.root / "exported.mp4"
        native.export_video_native(merged, exported, target_size=(640, 360))
        probe = native.probe_video(exported)
        self.assertEqual((probe.width, probe.height), (640, 360))
        self.assertAlmostEqual(probe.duration, 3.0, delta=0.15)

    def test_narration_preserves_video_packets_and_full_timeline(self):
        import numpy as np
        output = self.root / "narrated.mp4"
        native.mix_narration_native(self.silent, output, [(self.voice, 0.75, 1.5)])
        self.assertAlmostEqual(native.probe_video(output).duration, 2.0, delta=0.1)
        def video_hash(path):
            return self.run_ffmpeg("-i", str(path), "-map", "0:v:0", "-c:v", "copy", "-f", "hash", "-")
        self.assertEqual(video_hash(self.silent), video_hash(output))
        pcm = self.run_ffmpeg("-i", str(output), "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "-")
        samples = np.frombuffer(pcm, dtype=np.float32)
        def rms(start, end):
            values = samples[int(start * 8000):int(end * 8000)]
            return float(np.sqrt(np.mean(values * values)))
        self.assertLess(rms(0.1, 0.5), 0.001)
        self.assertGreater(rms(0.85, 1.1), 0.01)
        self.assertLess(rms(1.6, 1.9), 0.001)
        self.assertEqual(len(samples) // 8000, 2)

    def test_subtitle_appears_only_in_its_slot_and_supports_same_path(self):
        import numpy as np
        font = resolve_subtitle_font()
        self.assertIsNotNone(font)
        output = self.root / "subtitled.mp4"
        shutil.copyfile(self.silent, output)
        count = burn_subtitles(output, output, [{"text": "字幕测试 {quoted}: 100%", "start": 0.5, "end": 1.0}], font)
        self.assertEqual(count, 1)
        def frame(at):
            pixels = self.run_ffmpeg("-ss", str(at), "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-")
            return np.frombuffer(pixels, dtype=np.uint8).reshape(180, 320, 3)
        # White text must appear during the slot and disappear at its end.
        for at, expect_white in [(0.2, False), (0.75, True), (1.25, False)]:
            pixels = frame(at)
            white_count = int(np.all(pixels > 180, axis=2).sum())
            self.assertEqual(white_count > 5, expect_white)
        self.assertAlmostEqual(native.probe_video(output).duration, 2.0, delta=0.1)

    def test_invalid_cut_does_not_create_or_replace_output(self):
        target = self.root / "existing.mp4"
        target.write_bytes(b"keep me")
        for start, end in [(-1, 1), (1, 1), (0, math.nan), (0, 9)]:
            with self.assertRaises(ValueError):
                native.cut_video_native(self.silent, target, start_time=start, end_time=end)
        self.assertEqual(target.read_bytes(), b"keep me")

    def test_cut_preserves_requested_tail_content(self):
        import numpy as np
        source, tail = self.root / "head_tail.mp4", self.root / "tail.mp4"
        self.run_ffmpeg("-f", "lavfi", "-i", "color=c=red:s=160x90:r=10:d=2", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10:d=2", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-c:v", "libx264", str(source))
        native.cut_video_native(source, tail, start_time=2.5, end_time=3.5)
        frame = self.run_ffmpeg("-i", str(tail), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-")
        rgb = np.frombuffer(frame, dtype=np.uint8).reshape(-1, 3).mean(axis=0)
        self.assertGreater(rgb[2], 200)
        self.assertLess(rgb[0], 30)
        self.assertAlmostEqual(native.probe_video(tail).duration, 1.0, delta=0.12)

    def test_canonical_short_silent_clip_encodes_without_invalid_audio(self):
        from dataclasses import replace
        from script.media_consistency import CanonicalRenderRequest, probe_media, render_canonical_media, resolve_media_profile
        output = self.root / "canonical_silent.mp4"
        profile = replace(resolve_media_profile(2), width=320, height=180)
        render_canonical_media(CanonicalRenderRequest(self.silent, output, profile, probe_media(self.silent)), ffmpeg_bin=self.ffmpeg)
        probe = probe_media(output)
        self.assertTrue(probe.has_audio)
        self.assertAlmostEqual(probe.duration_seconds, 2.0, delta=0.1)
        self.run_ffmpeg("-xerror", "-i", str(output), "-f", "null", "-")

    def test_final_mix_measures_loudness_before_single_video_encode(self):
        from dataclasses import replace
        from script.media_consistency import CanonicalRenderRequest, build_quality_analysis_commands, parse_quality_analysis_outputs, probe_media, render_canonical_media, resolve_media_profile
        source, output = self.root / "dynamic_audio.mp4", self.root / "normalized_final.mp4"
        self.run_ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=15", "-f", "lavfi", "-i", "sine=frequency=440:duration=15", "-af", "volume='if(lt(t,5),0.08,if(lt(t,10),0.5,0.02))':eval=frame", "-c:v", "libx264", "-c:a", "aac", str(source))
        profile = replace(resolve_media_profile(15), width=320, height=180)
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.run(command, **kwargs)
        render_canonical_media(CanonicalRenderRequest(source, output, profile, probe_media(source), final_mix=True), ffmpeg_bin=self.ffmpeg, runner=run)
        self.assertEqual(sum("-c:v" in command for command in calls), 1)
        self.assertTrue(any("-vn" in command for command in calls))
        command = build_quality_analysis_commands(output, ffmpeg_bin=self.ffmpeg).loudness
        measured = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
        metrics = parse_quality_analysis_outputs(duration_seconds=15, loudness_output=measured.stderr, black_frames_output="", freeze_frames_output="", decode_returncode=0)
        self.assertAlmostEqual(metrics.integrated_lufs, -16, delta=1.0)
        self.assertLessEqual(metrics.true_peak_dbfs, -1.5)

    def test_registered_narration_tools_use_native_rendering(self):
        from tools import TOOL_REGISTRY, _shared
        from tools.narration_pipeline import compose_prepared_narration
        single = importlib.import_module("tools.add_narration")
        segmented = importlib.import_module("tools.add_narration_segments")

        def generate(text, voice, path):
            self.run_ffmpeg("-i", str(self.voice), str(path))
            return None

        with (
            patch.object(_shared, "WORKSPACE", self.root),
            patch.object(single, "WORKSPACE", self.root),
            patch.object(segmented, "WORKSPACE", self.root),
            patch.object(single, "_tts_generate", side_effect=generate),
            patch.object(segmented, "_tts_generate", side_effect=generate),
        ):
            result = TOOL_REGISTRY.call("add_narration", {
                "video_path": str(self.silent), "narration_text": "测试旁白", "output_name": "single",
            })
            self.assertFalse(result.is_error, result.text)
            self.assertTrue(native.probe_video(Path(result.data["path"])).has_audio)
            result = TOOL_REGISTRY.call("add_narration_segments", {
                "video_path": str(self.silent), "segments": [{"text": "字幕配音", "start": 0.5, "end": 1.5}],
                "output_name": "segmented", "add_subtitle": True,
            })
            self.assertFalse(result.is_error, result.text)
            self.assertTrue(result.data["has_subtitles"])
            self.assertEqual(result.data["success_segments"], 1)
            result = json.loads(compose_prepared_narration(str(self.silent), [
                {"audio_path": str(self.voice), "start": 1, "end": 1.5},
            ], output_name="prepared"))
            self.assertEqual(result["segment_count"], 1)
            self.assertAlmostEqual(native.probe_video(Path(result["path"])).duration, 2.0, delta=0.1)

    def test_complete_narration_and_subtitles_extend_into_free_time(self):
        import numpy as np
        from tools import TOOL_REGISTRY, _shared
        segmented = importlib.import_module("tools.add_narration_segments")
        def generate(text, voice, path):
            self.run_ffmpeg("-i", str(self.voice), str(path))
            return None
        texts = ["完整旁白", "保留末尾"]
        with patch.object(_shared, "WORKSPACE", self.root), patch.object(segmented, "WORKSPACE", self.root), patch.object(segmented, "_tts_generate", side_effect=generate) as tts:
            result = TOOL_REGISTRY.call("add_narration_segments", {
                "video_path": str(self.silent), "segments": [
                    {"text": texts[0], "start": 0.5, "end": 0.6},
                    {"text": texts[1], "start": 1.4, "end": 1.5},
                ], "output_name": "extended_speech",
            })
        self.assertFalse(result.is_error, result.text)
        self.assertEqual([call.args[0] for call in tts.call_args_list], texts)
        aligned = result.data["segments"]
        self.assertEqual([item["text"] for item in aligned], texts)
        self.assertGreater(aligned[0]["end"], 0.6)
        self.assertAlmostEqual(aligned[0]["end"] - aligned[0]["start"], native.probe_audio_duration(Path(aligned[0]["audio_path"])), places=3)
        self.assertLessEqual(aligned[0]["end"], aligned[1]["start"])
        output = Path(result.data["path"])
        pcm = self.run_ffmpeg("-i", str(output), "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "-")
        samples = np.frombuffer(pcm, dtype=np.float32)
        self.assertGreater(float(np.sqrt(np.mean(samples[6400:7600] ** 2))), 0.01)
        for at, expected in [(0.85, True), (1.2, False), (1.65, True)]:
            frame = self.run_ffmpeg("-ss", str(at), "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-")
            white = np.all(np.frombuffer(frame, dtype=np.uint8).reshape(-1, 3) > 180, axis=1).sum()
            self.assertEqual(white > 5, expected)

    def test_narration_cannot_overlap_next_start_or_overrun_video(self):
        from tools import _shared
        from tools.narration_pipeline import align_prepared_narration
        for segments in [
            [{"audio_path": str(self.voice), "start": 0.1, "end": 0.2}, {"audio_path": str(self.voice), "start": 0.4, "end": 0.5}],
            [{"audio_path": str(self.voice), "start": 1.8, "end": 1.9}],
        ]:
            with self.subTest(segments=segments), patch.object(_shared, "WORKSPACE", self.root):
                with self.assertRaisesRegex(ValueError, "不能截断台词"):
                    align_prepared_narration(segments, 2.0)

    def test_native_mixer_rejects_truncation_without_replacing_output(self):
        output = self.root / "keep_narration.mp4"
        output.write_bytes(b"existing output")
        with self.assertRaisesRegex(ValueError, "refusing to truncate"):
            native.mix_narration_native(self.silent, output, [(self.voice, 0.1, 0.2)])
        self.assertEqual(output.read_bytes(), b"existing output")


if __name__ == "__main__":
    unittest.main()
