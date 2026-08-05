from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase3_rl.local_rollout_video import (
    _restore_cache,
    _resolve_episode_video,
    local_rollout_analysis_enabled,
)


class LocalRolloutVideoTests(unittest.TestCase):
    def test_backend_switch_is_explicit(self) -> None:
        with patch.dict(os.environ, {"CRAYOTTER_RL_ANALYZE_VIDEO_BACKEND": "local_rollout"}):
            self.assertTrue(local_rollout_analysis_enabled())
        with patch.dict(os.environ, {"CRAYOTTER_RL_ANALYZE_VIDEO_BACKEND": "api"}):
            self.assertFalse(local_rollout_analysis_enabled())

    def test_video_must_be_inside_episode_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            episode_root = Path(temp_dir) / "episode"
            episode_root.mkdir()
            video = episode_root / "user_temp" / "clip.mp4"
            video.parent.mkdir()
            video.write_bytes(b"video")

            self.assertEqual(
                _resolve_episode_video(episode_root, "user_temp/clip.mp4"),
                video.resolve(),
            )
            with self.assertRaises(ValueError):
                _resolve_episode_video(episode_root, str(Path(temp_dir) / "outside.mp4"))

    def test_cache_requires_nonempty_semantic_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "clip.mp4"
            video.write_bytes(b"video")
            cache = root / "cache.json"
            output = root / "output.json"
            cache.write_text('{"semantic_segments": []}', encoding="utf-8")

            self.assertFalse(_restore_cache(cache, output, video))
            self.assertFalse(output.exists())

    def test_launcher_does_not_override_default_video_fps(self) -> None:
        launcher = (
            Path(__file__).resolve().parents[1]
            / "start_screen_back4_api_dyncredit.sh"
        ).read_text(encoding="utf-8")
        self.assertNotIn("CRAYOTTER_RL_LOCAL_VIDEO_FPS", launcher)
        self.assertIn('ROLLOUT_MAX_MODEL_LEN="${ROLLOUT_MAX_MODEL_LEN:-65536}"', launcher)
        self.assertIn(
            'CRAYOTTER_RL_LOCAL_VIDEO_MAX_TOKENS="${CRAYOTTER_RL_LOCAL_VIDEO_MAX_TOKENS:-16384}"',
            launcher,
        )
