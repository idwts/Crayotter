from __future__ import annotations

import unittest

from app.backend.models import AppConfig
from script.orchestration.models import ResourcePoolConfig


class RuntimeConfigDefaultsTests(unittest.TestCase):
    def test_default_resource_pools_are_sized_for_typical_editing_jobs(self) -> None:
        config = AppConfig()
        pools = ResourcePoolConfig()

        self.assertGreaterEqual(config.search_pool_size, 4)
        self.assertGreaterEqual(config.download_pool_size, 3)
        self.assertGreaterEqual(config.video_analysis_pool_size, 3)
        self.assertGreaterEqual(config.llm_pool_size, 4)
        self.assertGreaterEqual(config.ffmpeg_pool_size, 3)
        self.assertGreaterEqual(config.tts_pool_size, 3)
        self.assertEqual(config.export_pool_size, 1)
        self.assertEqual(config.search_pool_size, pools.search_pool)
        self.assertEqual(config.download_pool_size, pools.download_pool)
        self.assertEqual(config.video_analysis_pool_size, pools.video_analysis_pool)
        self.assertEqual(config.llm_pool_size, pools.llm_pool)
        self.assertEqual(config.ffmpeg_pool_size, pools.ffmpeg_pool)
        self.assertEqual(config.tts_pool_size, pools.tts_pool)
        self.assertEqual(config.export_pool_size, pools.export_pool)


if __name__ == "__main__":
    unittest.main()
