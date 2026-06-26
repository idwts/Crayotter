from __future__ import annotations

import json
import importlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


class MaterialSourceTests(unittest.TestCase):
    def test_detect_platform_from_url_recognizes_supported_sources(self) -> None:
        from script.tools.material_sources import detect_platform_from_url

        cases = {
            "https://www.bilibili.com/video/BV1xx411c7XZ": "bilibili",
            "https://v.douyin.com/abc123/": "douyin",
            "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9": "xiaohongshu",
            "https://www.rednote.com/explore/69ce30d3000000002100791c": "rednote",
            "https://www.kuaishou.com/f/X3t3Ee6o1L7gqHe": "kuaishou",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ": "youtube",
            "https://example.com/video/123": "unknown",
        }

        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(detect_platform_from_url(url), expected)

    def test_normalize_candidate_outputs_common_schema_and_orientation(self) -> None:
        from script.tools.material_sources import normalize_candidate

        candidate = normalize_candidate(
            {
                "aweme_id": "7336481666707229992",
                "desc": "校园宣传片 竖屏",
                "author": {"nickname": "创作者"},
                "video": {"duration": 45000, "width": 720, "height": 1280},
                "statistics": {"play_count": 12345},
                "share_url": "https://www.douyin.com/video/7336481666707229992",
            },
            source="douyin",
            query="校园宣传片",
        )

        self.assertEqual(candidate["source"], "douyin")
        self.assertEqual(candidate["platform"], "douyin")
        self.assertEqual(candidate["id"], "7336481666707229992")
        self.assertEqual(candidate["title"], "校园宣传片 竖屏")
        self.assertEqual(candidate["author"], "创作者")
        self.assertEqual(candidate["duration_seconds"], 45.0)
        self.assertEqual(candidate["play"], 12345)
        self.assertEqual(candidate["width"], 720)
        self.assertEqual(candidate["height"], 1280)
        self.assertEqual(candidate["orientation_hint"], "portrait")
        self.assertEqual(candidate["orientation_source"], "resolution")
        self.assertEqual(candidate["query"], "校园宣传片")
        self.assertIn("raw", candidate)


class DownloadMaterialVideoTests(unittest.TestCase):
    def test_normalize_downloaded_material_standardizes_fps_pixel_format_and_codecs(self) -> None:
        module = importlib.import_module("script.tools.download_material_video")
        commands: list[list[str]] = []

        def fake_run(cmd, capture_output=True, text=True, timeout=None):
            commands.append(list(cmd))
            Path(cmd[-1]).write_bytes(b"standardized-video")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "raw.mp4"
            output_path = Path(tmp) / "clean.mp4"
            input_path.write_bytes(b"raw-video")
            with patch.object(module.subprocess, "run", side_effect=fake_run):
                metadata = module.normalize_downloaded_material(
                    input_path,
                    output_path,
                    max_height=720,
                    target_fps=30,
                    loudnorm_target=0,
                )
                output_exists = output_path.exists()

        self.assertTrue(output_exists)
        self.assertTrue(metadata["standardized"])
        self.assertEqual(metadata["target_fps"], 30)
        self.assertEqual(metadata["pixel_format"], "yuv420p")
        self.assertFalse(metadata["loudnorm_applied"])
        self.assertEqual(metadata["standardization_steps"], ["video_audio_standardization"])
        ffmpeg_cmd = commands[0]
        self.assertIn("-vf", ffmpeg_cmd)
        vf = ffmpeg_cmd[ffmpeg_cmd.index("-vf") + 1]
        self.assertIn("fps=30", vf)
        self.assertIn("format=yuv420p", vf)
        self.assertIn("-c:v", ffmpeg_cmd)
        self.assertIn("libx264", ffmpeg_cmd)
        self.assertIn("-c:a", ffmpeg_cmd)
        self.assertIn("aac", ffmpeg_cmd)

    def test_normalize_downloaded_material_runs_two_pass_loudnorm_when_enabled(self) -> None:
        module = importlib.import_module("script.tools.download_material_video")
        commands: list[list[str]] = []
        loudnorm_json = """
        [Parsed_loudnorm_0 @ 000001]
        {
          "input_i": "-22.10",
          "input_tp": "-2.40",
          "input_lra": "6.20",
          "input_thresh": "-32.30",
          "target_offset": "-0.30"
        }
        """

        def fake_run(cmd, capture_output=True, text=True, timeout=None):
            commands.append(list(cmd))
            joined = " ".join(str(part) for part in cmd)
            if "print_format=json" in joined:
                return subprocess.CompletedProcess(cmd, 0, "", loudnorm_json)
            Path(cmd[-1]).write_bytes(b"video")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "raw.mp4"
            output_path = Path(tmp) / "clean.mp4"
            input_path.write_bytes(b"raw-video")
            with patch.object(module.subprocess, "run", side_effect=fake_run):
                metadata = module.normalize_downloaded_material(
                    input_path,
                    output_path,
                    max_height=720,
                    target_fps=30,
                    loudnorm_target=-16.0,
                )
                output_exists = output_path.exists()

        self.assertTrue(output_exists)
        self.assertTrue(metadata["loudnorm_applied"])
        self.assertEqual(metadata["loudnorm_target"], -16.0)
        self.assertEqual(
            metadata["standardization_steps"],
            ["video_audio_standardization", "loudnorm_analysis", "loudnorm_apply"],
        )
        self.assertEqual(len(commands), 3)
        analysis_cmd = commands[1]
        apply_cmd = commands[2]
        self.assertIn("-af", analysis_cmd)
        self.assertIn("print_format=json", analysis_cmd[analysis_cmd.index("-af") + 1])
        second_pass_filter = apply_cmd[apply_cmd.index("-af") + 1]
        self.assertIn("measured_I=-22.10", second_pass_filter)
        self.assertIn("measured_TP=-2.40", second_pass_filter)
        self.assertIn("measured_LRA=6.20", second_pass_filter)
        self.assertIn("measured_thresh=-32.30", second_pass_filter)
        self.assertIn("offset=-0.30", second_pass_filter)

    def test_normalize_downloaded_material_raises_when_ffmpeg_fails(self) -> None:
        module = importlib.import_module("script.tools.download_material_video")

        def fake_run(cmd, capture_output=True, text=True, timeout=None):
            return subprocess.CompletedProcess(cmd, 1, "", "ffmpeg broke")

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "raw.mp4"
            output_path = Path(tmp) / "clean.mp4"
            input_path.write_bytes(b"raw-video")
            with patch.object(module.subprocess, "run", side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, "ffmpeg broke"):
                    module.normalize_downloaded_material(
                        input_path,
                        output_path,
                        max_height=720,
                        target_fps=30,
                        loudnorm_target=0,
                    )

        self.assertFalse(output_path.exists())

    def test_download_material_video_uses_yt_dlp_and_normalizes_to_h264_aac(self) -> None:
        module = importlib.import_module("script.tools.download_material_video")
        download_tool = module.download_material_video

        commands: list[list[str]] = []

        def fake_run(cmd, capture_output=True, text=True, timeout=None):
            commands.append(list(cmd))
            output_arg = cmd[cmd.index("-o") + 1] if "-o" in cmd else None
            if output_arg:
                Path(output_arg).write_bytes(b"raw-video")
            elif cmd and "ffmpeg" in str(cmd[0]).lower():
                Path(cmd[-1]).write_bytes(b"normalized-video")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        probe = {
            "duration_seconds": 12.3,
            "fps": 29.97,
            "width": 720,
            "height": 1280,
            "video_codec": "hevc",
            "audio_codec": "mp3",
            "resolution": "720x1280",
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(module, "WORKSPACE", tmp_path), patch.object(
                module, "_safe_output_video_path", lambda filename, default_stem="material_video": tmp_path / f"{filename}.mp4"
            ), patch.object(module, "_probe_video", return_value=probe), patch.object(
                module.subprocess, "run", side_effect=fake_run
            ):
                raw = download_tool.invoke(
                    {
                        "url": "https://www.douyin.com/video/7336481666707229992",
                        "source": "douyin",
                        "filename": "douyin_sample",
                    }
                )
                path_exists_before_cleanup = Path(json.loads(str(raw))["path"]).exists()

        result = json.loads(str(raw))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["source"], "douyin")
        self.assertEqual(result["codec"], "h264")
        self.assertEqual(result["audio_codec"], "aac")
        self.assertTrue(result["normalized"])
        self.assertTrue(result["standardized"])
        self.assertEqual(result["target_fps"], 30)
        self.assertEqual(result["pixel_format"], "yuv420p")
        self.assertFalse(result["loudnorm_applied"])
        self.assertTrue(path_exists_before_cleanup)
        self.assertIn("yt-dlp", commands[0][0])
        self.assertIn("--merge-output-format", commands[0])
        self.assertIn("-c:v", commands[1])
        self.assertIn("libx264", commands[1])
        self.assertIn("-c:a", commands[1])
        self.assertIn("aac", commands[1])

    def test_download_material_video_returns_structured_error_on_download_failure(self) -> None:
        module = importlib.import_module("script.tools.download_material_video")
        download_tool = module.download_material_video

        def fake_run(cmd, capture_output=True, text=True, timeout=None):
            return subprocess.CompletedProcess(cmd, 1, "", "unsupported url")

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(module, "WORKSPACE", Path(tmp)), patch.object(
                module, "_safe_output_video_path", lambda filename, default_stem="material_video": Path(tmp) / f"{filename}.mp4"
            ), patch.object(module.subprocess, "run", side_effect=fake_run):
                raw = download_tool.invoke(
                    {
                        "url": "https://www.kuaishou.com/f/X3t3Ee6o1L7gqHe",
                        "source": "kuaishou",
                        "filename": "kuaishou_sample",
                    }
                )

        result = json.loads(str(raw))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["source"], "kuaishou")
        self.assertEqual(result["error_type"], "download_failed")
        self.assertIn("unsupported url", result["error"])

    def test_download_material_video_falls_back_to_bilibili_when_source_download_fails(self) -> None:
        module = importlib.import_module("script.tools.download_material_video")
        download_tool = module.download_material_video
        commands: list[list[str]] = []
        bili_candidate = {
            "title": "B站校园素材",
            "url": "https://www.bilibili.com/video/BV1xx411c7XZ",
            "bvid": "BV1xx411c7XZ",
            "duration_seconds": 30,
            "source": "bilibili",
        }

        def fake_run(cmd, capture_output=True, text=True, timeout=None):
            commands.append(list(cmd))
            if "douyin.com" in str(cmd):
                return subprocess.CompletedProcess(cmd, 1, "", "fresh cookies needed")
            output_arg = cmd[cmd.index("-o") + 1] if "-o" in cmd else None
            if output_arg:
                Path(output_arg).write_bytes(b"bilibili-video")
            elif cmd and "ffmpeg" in str(cmd[0]).lower():
                Path(cmd[-1]).write_bytes(b"normalized-video")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        probe = {
            "duration_seconds": 30.0,
            "fps": 30.0,
            "width": 1280,
            "height": 720,
            "video_codec": "h264",
            "audio_codec": "aac",
            "resolution": "1280x720",
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(module, "WORKSPACE", tmp_path), patch.object(
                module, "_safe_output_video_path", lambda filename, default_stem="material_video": tmp_path / f"{filename}.mp4"
            ), patch.object(module, "_probe_video", return_value=probe), patch.object(
                module.subprocess, "run", side_effect=fake_run
            ), patch.object(
                module,
                "search_bilibili_video",
                type(
                    "FakeSearchTool",
                    (),
                    {"invoke": lambda self, arguments: json.dumps([bili_candidate], ensure_ascii=False)},
                )(),
                create=True,
            ):
                raw = download_tool.invoke(
                    {
                        "url": "https://www.douyin.com/video/7336481666707229992",
                        "source": "douyin",
                        "filename": "fallback_sample",
                        "fallback_query": "校园宣传片",
                    }
                )

        result = json.loads(str(raw))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["source"], "bilibili")
        self.assertEqual(result["original_source"], "douyin")
        self.assertTrue(result["fallback"])
        self.assertEqual(result["fallback_query"], "校园宣传片")
        self.assertTrue(any("bilibili.com" in " ".join(command) for command in commands))


class MaterialConfigTests(unittest.TestCase):
    def test_config_store_loads_standardization_env_values(self) -> None:
        module = importlib.import_module("app.backend.config_store")
        raw_env = {
            "CRAYOTTER_STANDARDIZE_TARGET_FPS": "24",
            "CRAYOTTER_AUDIO_LOUDNORM_TARGET": "-14.5",
        }

        with patch.object(module, "read_runtime_env_file", return_value=raw_env):
            payload = module.ConfigStore()._load_env_payload()

        self.assertEqual(payload["standardize_target_fps"], 24)
        self.assertEqual(payload["audio_loudnorm_target"], -14.5)


class SearchAndRankingMaterialSourceTests(unittest.TestCase):
    def test_search_material_sources_falls_back_to_bilibili_for_unsupported_keyword_platform(self) -> None:
        module = importlib.import_module("script.tools.search_material_sources")
        bili_candidate = {
            "title": "校园宣传片",
            "url": "https://www.bilibili.com/video/BV1xx411c7XZ",
            "bvid": "BV1xx411c7XZ",
            "duration_seconds": 60,
            "source": "bilibili",
        }

        with patch.object(module, "_append_candidates_to_pool"), patch.object(
            module,
            "search_bilibili_video",
            type(
                "FakeSearchTool",
                (),
                {"invoke": lambda self, arguments: json.dumps([bili_candidate], ensure_ascii=False)},
            )(),
        ):
            raw = module.search_material_sources.invoke(
                {
                    "query": "校园宣传片",
                    "platforms": ["douyin"],
                    "max_results": 1,
                    "pages": 1,
                }
            )

        result = json.loads(str(raw))
        self.assertEqual(result["unsupported"][0]["platform"], "douyin")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["source"], "bilibili")

    def test_rank_keeps_imported_url_candidate_with_unknown_duration(self) -> None:
        module = importlib.import_module("script.tools.rank_video_candidates")
        candidate = {
            "title": "快手导入素材",
            "url": "https://www.kuaishou.com/f/X3t3Ee6o1L7gqHe",
            "source": "kuaishou",
            "allow_unknown_duration": True,
        }

        with patch.object(module, "_load_candidates_from_pool", return_value=[]), patch.object(
            module, "_append_candidates_to_pool"
        ), patch.object(module, "_get_openai_client", return_value=object()), patch.object(
            module, "fail_fast_model_errors", return_value=False
        ):
            raw = module.rank_video_candidates.invoke(
                {
                    "candidates_json": json.dumps([candidate], ensure_ascii=False),
                    "top_k": 1,
                    "max_review": 1,
                    "selection_goal": "校园宣传片",
                }
            )

        result = json.loads(str(raw))
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["selected_videos"][0]["source"], "kuaishou")


if __name__ == "__main__":
    unittest.main()
