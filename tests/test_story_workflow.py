from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.backend.services.stories import StoryReviewService
from script.story.gateway import ModelEndpoint
from script.story.generation import (
    _normalize_episode,
    _normalize_package,
    _parse_story_json,
)
from script.story.ingestion import ingest_sources
from script.story.models import (
    ScreenplayEpisode,
    ScreenplayScene,
    SourceChunk,
    SourceWork,
    StoryDirection,
    StoryDNA,
    StoryDocument,
    StoryJobConfig,
    StoryPackage,
)
from script.story.runner import run_story_task
from script.story.similarity import build_similarity_report
from script.story.store import StoryDocumentStore


class StoryConfigTests(unittest.TestCase):
    def test_language_aliases_are_normalized_and_deduplicated(self) -> None:
        config = StoryJobConfig(target_languages="中文, English, 西语, en")
        self.assertEqual(config.target_languages, ["zh", "en", "es"])

    def test_common_llm_package_shapes_are_normalized(self) -> None:
        config = StoryJobConfig(episode_count=1)
        direction = StoryDirection(
            direction_id="dir_001",
            title="New direction",
            logline="A logline",
            protagonist_strategy="Collect evidence",
            opposition_mechanism="Reputation control",
            recurring_reward="Public reversal",
            cost="An ally is exposed",
            differentiation="Different conflict engine",
        )
        payload = {
            "title": "Generated",
            "world": {
                "setting": "A public company",
                "rule": "Every approval needs two signatures",
            },
            "character_bible": {
                "protagonist": {"name": "Lin", "evidence": ["new-story note"]},
                "antagonist": {"name": "Gao"},
            },
            "relationships": [{"from": "Lin", "to": "Gao", "type": "rivals"}],
            "episode_outline": ["Secure the witness"],
            "episodes": [],
        }
        package = StoryPackage.model_validate(
            _normalize_package(payload, StoryDNA(), direction, config)
        )
        self.assertEqual(len(package.character_bible), 2)
        self.assertEqual(package.relationships[0].relation, "rivals")
        self.assertEqual(package.episode_outline[0].number, 1)
        self.assertIn("setting", package.world)

    def test_dialogue_maps_are_normalized_to_screenplay_lines(self) -> None:
        payload = {
            "scenes": [
                {
                    "heading": "INT. OFFICE - DAY",
                    "dialogue": {"LIN": "Show them the ledger."},
                }
            ]
        }
        episode = ScreenplayEpisode.model_validate(
            _normalize_episode(payload, 1, StoryJobConfig(episode_count=1))
        )
        self.assertEqual(episode.scenes[0].dialogue[0]["character"], "LIN")

    def test_truncated_model_json_is_repaired(self) -> None:
        repaired = _parse_story_json(
            '```json\n{"title":"Test","characters":[{"name":"Lin"}],"beats":['
        )
        self.assertEqual(repaired["title"], "Test")
        self.assertEqual(repaired["characters"][0]["name"], "Lin")


class StoryIngestionTests(unittest.TestCase):
    def test_task_user_temp_reference_is_sandboxed_and_ingested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            user_workspace = root / "user_temp"
            workspace.mkdir()
            user_workspace.mkdir()
            source = user_workspace / "reference.md"
            source.write_text("主角被公开羞辱，但暗中保留关键证据。", encoding="utf-8")
            result = ingest_sources(
                "请参考 user_temp/reference.md 开发三集短剧",
                StoryJobConfig(),
                workspace=workspace,
                user_workspace=user_workspace,
            )
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].title, "reference")
            self.assertIn("关键证据", result[0].text)

    def test_missing_reference_falls_back_to_inline_brief(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = ingest_sources(
                "A courier is secretly the company chair.",
                StoryJobConfig(source_paths=["user_temp/missing.docx"]),
                workspace=root / "workspace",
                user_workspace=root / "user_temp",
            )
            self.assertEqual(result[0].source_id, "source_inline")


class StoryVersionStoreTests(unittest.TestCase):
    def test_revisions_are_append_only_and_approval_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StoryDocumentStore(tmp)
            first = store.save(StoryDocument(dna=StoryDNA(title="First")))
            revised = first.model_copy(deep=True)
            revised.dna.title = "Second"
            second = store.revise(revised)
            approved = store.approve(second.version)
            self.assertEqual(first.version, "v001")
            self.assertEqual(second.version, "v002")
            self.assertEqual(store.get("v001").dna.title, "First")
            self.assertEqual(approved.status, "APPROVED")
            self.assertEqual(len(store.list_versions()), 2)

    def test_review_revision_rescores_changed_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            user_workspace = root / "user_temp"
            user_workspace.mkdir()
            reference = user_workspace / "reference.txt"
            reference.write_text(
                "A uniquely copied line appears here.", encoding="utf-8"
            )
            package = StoryPackage(
                title="Draft",
                episodes=[
                    ScreenplayEpisode(
                        episode_id="ep_001",
                        number=1,
                        title="One",
                        scenes=[
                            ScreenplayScene(
                                scene_id="scene_001",
                                number=1,
                                heading="INT. ROOM - DAY",
                                dialogue=[
                                    {"character": "A", "text": "An unrelated sentence."}
                                ],
                            )
                        ],
                    )
                ],
            )
            config = StoryJobConfig(source_paths=["user_temp/reference.txt"])
            sources = ingest_sources(
                "Use user_temp/reference.txt",
                config,
                workspace=workspace,
                user_workspace=user_workspace,
            )
            initial = StoryDocument(request=config, sources=sources, package=package)
            saved = StoryDocumentStore(workspace).save(initial)
            changed = package.model_copy(deep=True)
            changed.episodes[0].scenes[0].dialogue[0]["text"] = (
                "A uniquely copied line appears here."
            )
            result = StoryReviewService().revise(
                workspace,
                saved.version,
                {"package": changed.model_dump()},
                job_id="job_story",
                revision=1,
                task="Use user_temp/reference.txt",
                user_workspace=user_workspace,
            )
            self.assertEqual(result["document"]["version"], "v002")
            self.assertEqual(
                result["document"]["similarity_report"]["overall_risk"], "high"
            )


class SimilarityReportTests(unittest.TestCase):
    def _source(self, text: str) -> SourceWork:
        return SourceWork(
            source_id="source_001",
            title="Reference",
            sha256="a" * 64,
            character_count=len(text),
            text=text,
            chunks=[
                SourceChunk(
                    chunk_id="source_001_chunk_001", start=0, end=len(text), text=text
                )
            ],
        )

    def test_near_verbatim_dialogue_forces_high_risk_finding(self) -> None:
        copied = "You only saw what I wanted you to see."
        package = StoryPackage(
            title="Test",
            episodes=[
                ScreenplayEpisode(
                    episode_id="ep_001",
                    number=1,
                    title="One",
                    scenes=[
                        ScreenplayScene(
                            scene_id="scene_001",
                            number=1,
                            heading="INT. ROOM - DAY",
                            dialogue=[{"character": "A", "text": copied}],
                        )
                    ],
                )
            ],
        )
        report = build_similarity_report(
            package,
            sources=[self._source(copied)],
            reference_dna=StoryDNA(),
        )
        self.assertEqual(report.overall_risk, "high")
        self.assertTrue(any(item.signal == "dialogue" for item in report.findings))


class StoryRunnerTests(unittest.TestCase):
    def test_runner_fallback_produces_versioned_artifacts_without_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            user_workspace = root / "user_temp"
            events: list[dict] = []
            config = {
                "job_id": "job_story",
                "project_id": "proj_story",
                "revision": 1,
                "story_config": {
                    "genre": "revenge",
                    "target_languages": ["zh"],
                    "target_markets": ["CN"],
                    "episode_count": 3,
                },
            }
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "CRAYOTTER_TASK_WORKSPACE": str(workspace),
                        "CRAYOTTER_USER_WORKSPACE": str(user_workspace),
                    },
                    clear=False,
                ),
                mock.patch(
                    "script.story.generation.model_gateway.chat",
                    side_effect=RuntimeError("offline"),
                ),
                mock.patch(
                    "script.story.runner.model_gateway.resolve",
                    return_value=ModelEndpoint("", "", "test-model"),
                ),
            ):
                summary, outputs = run_story_task(
                    "An underestimated courier gathers evidence against a powerful manager.",
                    config,
                    event_callback=events.append,
                )
            self.assertIn("v001", summary)
            suffixes = {Path(path).suffix for path in outputs}
            self.assertTrue({".json", ".md", ".fountain"}.issubset(suffixes))
            current = json.loads(
                (workspace / "story" / "current.json").read_text("utf-8")
            )
            self.assertEqual(current["version"], "v001")
            self.assertEqual(current["status"], "GENERATED")
            manifest = json.loads(
                (workspace / ".crayotter" / "artifact_manifest.json").read_text("utf-8")
            )
            self.assertGreaterEqual(len(manifest["artifacts"]), 3)
            self.assertTrue(
                any(event["type"] == "story_workflow_completed" for event in events)
            )


if __name__ == "__main__":
    unittest.main()
