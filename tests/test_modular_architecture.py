from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.backend.models import JobRecord
from app.backend.services import ArtifactQueryService, JobRepository
from script.phases.editing_execution import (
    build_react_budget,
    should_try_short_form_fallback,
)
from script.phases.editing_research import (
    build_research_execution_plan,
    select_research_mode,
)
from script.phases.material_preparation import (
    deterministic_material_sufficient,
    normalize_gap_report,
    recommend_material_counts,
)
from script.runtime import RuntimeSettings
from script.workflow import LoopController, LoopPolicy, SkillRegistry, build_tool_catalog


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


class _Job:
    def __init__(self, record: JobRecord, job_dir: Path) -> None:
        self.record = record
        self.job_dir = job_dir


class RuntimeArchitectureTests(unittest.TestCase):
    def test_runtime_settings_exposes_exact_resource_pool_names(self) -> None:
        settings = RuntimeSettings(search_pool_size=7, ffmpeg_pool_size=2)
        self.assertEqual(settings.resource_pools()["search_pool"], 7)
        self.assertEqual(settings.resource_pools()["ffmpeg_pool"], 2)
        self.assertEqual(settings.resource_pools()["export_pool"], 1)

    def test_tool_skills_resolve_through_authoritative_catalog(self) -> None:
        search = _Tool("search_material_sources")
        rank = _Tool("rank_video_candidates")
        catalog = build_tool_catalog([search, rank])
        resolved = SkillRegistry.defaults().resolve_tools("material_acquisition", catalog)
        self.assertEqual(resolved, [search, rank])

    def test_loop_controller_is_bounded_and_progress_aware(self) -> None:
        policy = LoopPolicy(name="quality_revision", max_iterations=2)
        self.assertEqual(
            LoopController.decide(
                policy,
                iteration=0,
                completed=False,
                progress_changed=True,
                fallback_available=True,
            ).action,
            "continue",
        )
        self.assertEqual(
            LoopController.decide(
                policy,
                iteration=1,
                completed=False,
                progress_changed=False,
                fallback_available=True,
            ).action,
            "fallback",
        )


class PhasePolicyTests(unittest.TestCase):
    def test_material_policy_keeps_duration_budget_monotonic(self) -> None:
        short = recommend_material_counts(30)
        long = recommend_material_counts(300)
        self.assertLessEqual(short["max_candidates"], long["max_candidates"])
        self.assertLessEqual(short["top_k_max"], long["top_k_max"])

    def test_gap_policy_overrides_llm_when_no_analysis_exists(self) -> None:
        metrics = {
            "source_count": 0,
            "required_sources": 1,
            "analysis_complete_ratio": 0.0,
            "duration_coverage_ratio": 0.0,
            "required_duration_coverage_ratio": 1.0,
            "topic_coverage_ratio": 0.0,
            "orientation_match_ratio": 0.0,
            "quality_floor_met": False,
            "duplicate_ratio": 1.0,
            "analyzed_count": 0,
        }
        self.assertFalse(deterministic_material_sufficient(metrics))
        report = normalize_gap_report(
            {"decision": "proceed"},
            metrics=metrics,
            round_index=0,
            supplement_limit=1,
            direct_phase3_execution=False,
            evaluated_at=1.0,
        )
        self.assertEqual(report["decision"], "fail")

    def test_research_factory_preserves_checkpoint_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            analysis = Path(temp_dir) / "source_analysis.json"
            analysis.write_text("{}", encoding="utf-8")
            plan = build_research_execution_plan(
                analysis_files=[analysis],
                user_request="校园宣传片",
                target_duration_seconds=60,
                deadline_at_epoch=None,
            )
        self.assertEqual(plan.plan_id, "phase2_parallel_research")
        self.assertEqual(plan.tasks[-1].id, "phase2_blueprint_integrator")
        self.assertEqual(select_research_mode(
            target_duration_seconds=20,
            remaining_seconds=300,
            short_form_optimizations=True,
        ), "compact")

    def test_phase3_fallback_and_react_budget_are_bounded(self) -> None:
        self.assertTrue(
            should_try_short_form_fallback(
                enabled=True,
                target_duration_seconds=15,
                has_blueprint=True,
            )
        )
        budget = build_react_budget(1000)
        self.assertEqual(budget.max_tool_calls, 20)
        self.assertEqual(budget.max_encoding_calls, 6)
        self.assertLessEqual(budget.recursion_limit, 48)


class BackendServiceBoundaryTests(unittest.TestCase):
    def test_job_repository_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir)
            job_dir = jobs_dir / "job_test"
            job_dir.mkdir()
            repository = JobRepository(jobs_dir)
            record = JobRecord(job_id="job_test", task="test", mode="demo")
            repository.save(record, job_dir)
            (job_dir / "events.jsonl").write_text(
                json.dumps({"type": "created"}) + "\n",
                encoding="utf-8",
            )
            loaded = repository.load_all()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].record.job_id, "job_test")
        self.assertEqual(loaded[0].events[0]["type"], "created")

    def test_artifact_query_reads_registry_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "job"
            workspace = job_dir / "workspace"
            manifest_dir = workspace / ".crayotter"
            manifest_dir.mkdir(parents=True)
            artifact = workspace / "blueprint.md"
            artifact.write_text("blueprint", encoding="utf-8")
            (manifest_dir / "artifact_manifest.json").write_text(
                json.dumps(
                    {
                        "artifacts": [
                            {
                                "id": "blueprint",
                                "kind": "editing_blueprint",
                                "path": str(artifact),
                                "producer_task_id": "phase2_blueprint_integrator",
                                "phase": "phase2",
                                "valid": True,
                                "metadata": {"revision": 1},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            record = JobRecord(
                job_id="job",
                task="test",
                mode="demo",
                revision=1,
            )
            results = ArtifactQueryService(root).collect(_Job(record, job_dir))
        self.assertEqual(results[0]["kind"], "editing_blueprint")
        self.assertTrue(results[0]["is_current"])


if __name__ == "__main__":
    unittest.main()
