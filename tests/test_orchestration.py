from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from script.orchestration import (
    ArtifactRef,
    ArtifactRegistry,
    ExecutionPlan,
    ResourcePoolConfig,
    ResourceScheduler,
    SchedulerError,
    TaskExecutionResult,
    TaskSpec,
)
from app.backend.models import AppConfig


class ResourceSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = ArtifactRegistry(self.workspace)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _scheduler(self, **pool_overrides: int) -> ResourceScheduler:
        values = ResourcePoolConfig().model_dump()
        values.update(pool_overrides)
        return ResourceScheduler(
            pools=ResourcePoolConfig(**values),
            workspace=self.workspace,
            artifact_registry=self.registry,
        )

    def test_rejects_cycle(self) -> None:
        plan = ExecutionPlan(
            plan_id="cycle",
            phase="test",
            tasks=[
                TaskSpec(id="a", phase="test", kind="x", depends_on=["b"]),
                TaskSpec(id="b", phase="test", kind="x", depends_on=["a"]),
            ],
        )
        with self.assertRaises(SchedulerError):
            self._scheduler().validate_plan(plan)

    def test_dependency_order(self) -> None:
        observed: list[str] = []
        plan = ExecutionPlan(
            plan_id="order",
            phase="test",
            tasks=[
                TaskSpec(id="first", phase="test", kind="x"),
                TaskSpec(id="second", phase="test", kind="x", depends_on=["first"]),
            ],
        )

        def execute(task: TaskSpec, dependencies):
            if task.id == "second":
                self.assertEqual(dependencies["first"].status, "completed")
            observed.append(task.id)
            return TaskExecutionResult()

        self._scheduler().run(plan, execute)
        self.assertEqual(observed, ["first", "second"])

    def test_resource_pool_bounds_parallelism(self) -> None:
        active = 0
        peak = 0
        lock = threading.Lock()
        plan = ExecutionPlan(
            plan_id="pool",
            phase="test",
            tasks=[
                TaskSpec(
                    id=f"task-{index}",
                    phase="test",
                    kind="x",
                    resources={"download_pool": 1},
                )
                for index in range(4)
            ],
        )

        def execute(task: TaskSpec, dependencies):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return TaskExecutionResult()

        self._scheduler(download_pool=2).run(plan, execute)
        self.assertEqual(peak, 2)

    def test_conflict_key_serializes_writers(self) -> None:
        active = 0
        peak = 0
        lock = threading.Lock()
        plan = ExecutionPlan(
            plan_id="conflict",
            phase="test",
            tasks=[
                TaskSpec(
                    id=f"writer-{index}",
                    phase="test",
                    kind="x",
                    resources={"ffmpeg_pool": 1},
                    conflict_keys=["write:shared.mp4"],
                )
                for index in range(2)
            ],
        )

        def execute(task: TaskSpec, dependencies):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return TaskExecutionResult()

        self._scheduler(ffmpeg_pool=2).run(plan, execute)
        self.assertEqual(peak, 1)

    def test_resume_reuses_valid_artifact(self) -> None:
        calls = 0
        output = self.workspace / "result.json"
        plan = ExecutionPlan(
            plan_id="resume",
            phase="test",
            tasks=[TaskSpec(id="task", phase="test", kind="x")],
        )

        def execute(task: TaskSpec, dependencies):
            nonlocal calls
            calls += 1
            output.write_text("{}", encoding="utf-8")
            return TaskExecutionResult(
                artifacts=[
                    ArtifactRef(
                        id="result",
                        kind="json",
                        path=str(output),
                        producer_task_id=task.id,
                        phase=task.phase,
                    )
                ]
            )

        scheduler = self._scheduler()
        scheduler.run(plan, execute)
        scheduler.run(plan, execute)
        self.assertEqual(calls, 1)

    def test_concurrent_registry_instances_preserve_manifest_entries(self) -> None:
        file_count = 12
        barrier = threading.Barrier(file_count)
        errors: list[BaseException] = []

        def register(index: int) -> None:
            try:
                path = self.workspace / f"artifact-{index}.txt"
                path.write_text(str(index), encoding="utf-8")
                registry = ArtifactRegistry(self.workspace)
                barrier.wait(timeout=5)
                registry.register(
                    artifact_id=f"artifact-{index}",
                    kind="text",
                    producer_task_id=f"task-{index}",
                    phase="test",
                    path=path,
                )
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=register, args=(index,))
            for index in range(file_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(errors)
        manifest = json.loads(
            (self.workspace / ".crayotter" / "artifact_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        ids = {item["id"] for item in manifest["artifacts"]}
        self.assertTrue({f"artifact-{index}" for index in range(file_count)} <= ids)

    def test_missing_artifact_is_reexecuted(self) -> None:
        calls = 0
        output = self.workspace / "result.json"
        plan = ExecutionPlan(
            plan_id="missing-artifact",
            phase="test",
            tasks=[TaskSpec(id="task", phase="test", kind="x")],
        )

        def execute(task: TaskSpec, dependencies):
            nonlocal calls
            calls += 1
            output.write_text("{}", encoding="utf-8")
            return TaskExecutionResult(
                artifacts=[
                    ArtifactRef(
                        id="result-missing",
                        kind="json",
                        path=str(output),
                        producer_task_id=task.id,
                        phase=task.phase,
                    )
                ]
            )

        scheduler = self._scheduler()
        scheduler.run(plan, execute)
        output.unlink()
        scheduler.run(plan, execute)
        self.assertEqual(calls, 2)

    def test_same_size_artifact_change_is_reexecuted(self) -> None:
        calls = 0
        output = self.workspace / "result.txt"
        plan = ExecutionPlan(
            plan_id="changed-artifact",
            phase="test",
            tasks=[TaskSpec(id="task", phase="test", kind="x")],
        )

        def execute(task: TaskSpec, dependencies):
            nonlocal calls
            calls += 1
            output.write_text("first", encoding="utf-8")
            return TaskExecutionResult(
                artifacts=[
                    ArtifactRef(
                        id="changed-result",
                        kind="text",
                        path=str(output),
                        producer_task_id=task.id,
                        phase=task.phase,
                    )
                ]
            )

        scheduler = self._scheduler()
        scheduler.run(plan, execute)
        output.write_text("other", encoding="utf-8")
        scheduler.run(plan, execute)
        self.assertEqual(calls, 2)

    def test_missing_declared_artifact_fails_task(self) -> None:
        plan = ExecutionPlan(
            plan_id="missing-declared-artifact",
            phase="test",
            tasks=[TaskSpec(id="task", phase="test", kind="x")],
        )

        def execute(task: TaskSpec, dependencies):
            return TaskExecutionResult(
                artifacts=[
                    ArtifactRef(
                        id="missing-result",
                        kind="text",
                        path=str(self.workspace / "missing.txt"),
                        producer_task_id=task.id,
                        phase=task.phase,
                    )
                ]
            )

        with self.assertRaises(SchedulerError):
            self._scheduler().run(plan, execute)

    def test_retryable_error_retries_only_failed_task(self) -> None:
        calls = 0
        plan = ExecutionPlan(
            plan_id="retry",
            phase="test",
            tasks=[
                TaskSpec(
                    id="task",
                    phase="test",
                    kind="x",
                    retry={"max_attempts": 2, "retryable_errors": ["timeout"]},
                )
            ],
        )

        def execute(task: TaskSpec, dependencies):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary timeout")
            return TaskExecutionResult(data={"ok": True})

        states = self._scheduler().run(plan, execute)
        self.assertEqual(calls, 2)
        self.assertEqual(states["task"].status, "completed")

    def test_partial_failure_mode_returns_completed_and_failed_states(self) -> None:
        plan = ExecutionPlan(
            plan_id="partial-failure",
            phase="phase1",
            tasks=[
                TaskSpec(id="success-a", phase="phase1", kind="video_analysis"),
                TaskSpec(id="failed", phase="phase1", kind="video_analysis"),
                TaskSpec(id="success-b", phase="phase1", kind="video_analysis"),
            ],
        )

        def execute(task: TaskSpec, dependencies):
            if task.id == "failed":
                raise RuntimeError("non-retryable analysis failure")
            return TaskExecutionResult(data={"ok": True})

        states = self._scheduler().run(
            plan,
            execute,
            allow_partial_failure=True,
        )

        self.assertEqual(states["success-a"].status, "completed")
        self.assertEqual(states["success-b"].status, "completed")
        self.assertEqual(states["failed"].status, "failed")

    def test_changed_dependency_invalidates_downstream(self) -> None:
        calls: list[str] = []

        def build_plan(value: int) -> ExecutionPlan:
            return ExecutionPlan(
                plan_id="dependency-change",
                phase="test",
                tasks=[
                    TaskSpec(
                        id="source",
                        phase="test",
                        kind="source",
                        arguments={"value": value},
                    ),
                    TaskSpec(
                        id="consumer",
                        phase="test",
                        kind="consumer",
                        depends_on=["source"],
                    ),
                ],
            )

        def execute(task: TaskSpec, dependencies):
            calls.append(task.id)
            return TaskExecutionResult(data={"value": task.arguments.get("value")})

        scheduler = self._scheduler()
        scheduler.run(build_plan(1), execute)
        scheduler.run(build_plan(2), execute)
        self.assertEqual(calls, ["source", "consumer", "source", "consumer"])

    def test_resume_preserves_dependency_result_data(self) -> None:
        calls: list[str] = []
        plan = ExecutionPlan(
            plan_id="dependency-result-resume",
            phase="test",
            tasks=[
                TaskSpec(id="search", phase="test", kind="search"),
                TaskSpec(
                    id="rank",
                    phase="test",
                    kind="rank",
                    depends_on=["search"],
                ),
            ],
        )

        def execute(task: TaskSpec, dependencies):
            calls.append(task.id)
            if task.id == "search":
                return TaskExecutionResult(data={"candidates": [{"id": "candidate-1"}]})
            return TaskExecutionResult(
                data={"selected": dependencies["search"].result["candidates"]}
            )

        scheduler = self._scheduler()
        first = scheduler.run(plan, execute)
        second = scheduler.run(plan, execute)

        self.assertEqual(calls, ["search", "rank"])
        self.assertEqual(first["rank"].result, second["rank"].result)
        self.assertEqual(second["rank"].result["selected"][0]["id"], "candidate-1")

    def test_safe_point_runs_only_without_active_tasks(self) -> None:
        active = 0
        calls: list[tuple[int, list[str]]] = []
        lock = threading.Lock()
        plan = ExecutionPlan(
            plan_id="safe-point",
            phase="test",
            tasks=[
                TaskSpec(
                    id=f"task-{index}",
                    phase="test",
                    kind="x",
                    resources={"download_pool": 1},
                )
                for index in range(2)
            ],
        )

        def execute(task: TaskSpec, dependencies):
            nonlocal active
            with lock:
                active += 1
            time.sleep(0.05)
            with lock:
                active -= 1
            return TaskExecutionResult()

        scheduler = ResourceScheduler(
            pools=ResourcePoolConfig(download_pool=2),
            workspace=self.workspace,
            artifact_registry=self.registry,
            safe_point=lambda context: calls.append(
                (active, list(context["pending_task_ids"]))
            ),
        )
        scheduler.run(plan, execute)

        self.assertTrue(calls)
        self.assertTrue(all(active_count == 0 for active_count, _ in calls))


class ShortFormConfigTests(unittest.TestCase):
    def test_short_form_source_limits_accept_1_2_4(self) -> None:
        for value in (1, 2, 4):
            config = AppConfig(short_form_max_sources=value)
            self.assertEqual(config.short_form_max_sources, value)

    def test_30_second_tasks_use_lightweight_material_collection(self) -> None:
        from script import graph

        counts = graph._recommend_material_counts(30)

        self.assertLessEqual(counts["top_k_max"], 4)
        self.assertLessEqual(counts["mllm_review"], 60)

    def test_short_form_executor_no_longer_has_10s_error(self) -> None:
        graph_source = Path("script/graph.py").read_text(encoding="utf-8")
        self.assertNotIn("短片计划总时长不是 10 秒", graph_source)
        self.assertNotIn("short_merged_10s", graph_source)
        self.assertNotIn("xinjiang_10s_promo", graph_source)


if __name__ == "__main__":
    unittest.main()
