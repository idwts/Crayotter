from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from script.orchestration import (
    ArtifactRegistry,
    ExecutionPlan,
    ResourcePoolConfig,
    ResourceScheduler,
    SchedulerError,
    TaskExecutionResult,
    TaskSpec,
    create_processing_budget,
    material_budget_for_duration,
)


class ProcessingBudgetTests(unittest.TestCase):
    def test_material_budget_duration_table(self) -> None:
        expected = {15: 2, 30: 3, 60: 4, 120: 7, 300: 8}
        for duration, source_target in expected.items():
            with self.subTest(duration=duration):
                budget = material_budget_for_duration(duration)
                self.assertEqual(budget.source_target, source_target)
                self.assertLessEqual(budget.source_min, budget.source_target)
                self.assertLessEqual(budget.candidate_cap, 80)

    def test_coverage_ratio_reduces_for_longer_outputs(self) -> None:
        self.assertEqual(material_budget_for_duration(30).coverage_ratio, 2.0)
        self.assertEqual(material_budget_for_duration(60).coverage_ratio, 1.6)
        self.assertEqual(material_budget_for_duration(120).coverage_ratio, 1.35)

    def test_authorization_wait_does_not_consume_processing_budget(self) -> None:
        budget = create_processing_budget(30, created_at_epoch=100.0)
        budget.authorization_wait_seconds = 50.0
        self.assertEqual(budget.processing_elapsed_seconds(now=170.0), 20.0)
        self.assertEqual(budget.remaining_seconds(now=170.0), 580.0)


class DeadlineSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _scheduler(self) -> ResourceScheduler:
        return ResourceScheduler(
            pools=ResourcePoolConfig(),
            workspace=self.workspace,
            artifact_registry=ArtifactRegistry(self.workspace),
        )

    def test_optional_task_is_skipped_when_estimate_exceeds_budget(self) -> None:
        calls: list[str] = []
        plan = ExecutionPlan(
            plan_id="deadline-optional",
            phase="test",
            deadline_at_epoch=time.time() + 0.05,
            tasks=[
                TaskSpec(
                    id="optional",
                    phase="test",
                    kind="research",
                    optional=True,
                    estimated_seconds=2.0,
                )
            ],
        )
        states = self._scheduler().run(
            plan,
            lambda task, deps: calls.append(task.id) or TaskExecutionResult(),
        )
        self.assertEqual(calls, [])
        self.assertEqual(states["optional"].status, "skipped")

    def test_required_task_continues_after_soft_deadline(self) -> None:
        plan = ExecutionPlan(
            plan_id="deadline-required",
            phase="test",
            deadline_at_epoch=time.time() - 1,
            tasks=[TaskSpec(id="required", phase="test", kind="export")],
        )
        states = self._scheduler().run(
            plan,
            lambda task, deps: TaskExecutionResult(data={"completed": True}),
        )
        self.assertEqual(states["required"].status, "completed")
        self.assertTrue(states["required"].result["completed"])


if __name__ == "__main__":
    unittest.main()
