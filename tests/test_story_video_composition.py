from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.backend.models import JobRecord, StoryVideoComposeRequest
from app.backend.runtime_manager import ManagedJob, RuntimeManager
from app.backend.services.stories import StoryReviewService
from script.story.models import (
    ScreenplayEpisode,
    ScreenplayScene,
    StoryDocument,
    StoryJobConfig,
    StoryPackage,
)
from script.story.store import StoryDocumentStore


def _document() -> StoryDocument:
    episodes = [
        ScreenplayEpisode(
            episode_id=f"ep_{number:03d}",
            number=number,
            title=f"Episode {number}",
            target_duration_seconds=45 + number,
            scenes=[
                ScreenplayScene(
                    scene_id=f"scene_{number:03d}",
                    number=1,
                    heading=f"Scene {number}",
                    action=f"Locked action {number}",
                    dialogue=[
                        {"character": "Narrator", "text": f"Locked line {number}"}
                    ],
                )
            ],
        )
        for number in (1, 2)
    ]
    return StoryDocument(
        request=StoryJobConfig(episode_count=2, episode_duration_seconds=60),
        package=StoryPackage(title="Approved Story", episodes=episodes),
    )


class StoryCompositionTests(unittest.TestCase):
    def test_approved_episode_is_promoted_without_other_episodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            store = StoryDocumentStore(workspace)
            saved = store.save(_document())
            store.approve(saved.version)
            result = StoryReviewService().prepare_video_composition(
                workspace,
                saved.version,
                job_id="job_story",
                episode_number=2,
                materials_dir=root / "user_temp",
            )
            text = Path(result["script_item"]["path"]).read_text("utf-8")
            self.assertIn("Locked line 2", text)
            self.assertNotIn("Locked line 1", text)
            self.assertNotIn("Alternative directions", text)

    def test_compose_creates_video_job_with_locked_script_inline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "job_story"
            workspace = job_dir / "workspace"
            store = StoryDocumentStore(workspace)
            saved = store.save(_document())
            store.approve(saved.version)
            story_job = ManagedJob(
                JobRecord(
                    job_id="job_story",
                    task="Generate story",
                    mode="agent",
                    job_kind="story_development",
                    status="completed",
                    job_dir=str(job_dir),
                ),
                job_dir,
            )
            manager = RuntimeManager.__new__(RuntimeManager)
            manager._story_reviews = StoryReviewService()
            manager.get_job = MagicMock(return_value=story_job)
            manager.create_job = MagicMock(
                return_value={"job_id": "job_video", "status": "running"}
            )
            manager._publish = MagicMock()
            with patch(
                "app.backend.runtime_manager.get_runtime_root", return_value=root
            ):
                result = manager.compose_story_video(
                    "job_story",
                    saved.version,
                    StoryVideoComposeRequest(
                        episode_number=2, target_duration_seconds=60
                    ),
                )
            child_request = manager.create_job.call_args.args[0]
            self.assertEqual(child_request.job_kind, "video_editing")
            self.assertIn("Locked line 2", child_request.task)
            self.assertNotIn("Locked line 1", child_request.task)
            self.assertEqual(result["job"]["job_id"], "job_video")


if __name__ == "__main__":
    unittest.main()
