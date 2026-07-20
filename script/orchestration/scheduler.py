from __future__ import annotations

import json
import hashlib
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable

from .artifacts import ArtifactRegistry
from .models import (
    ExecutionPlan,
    ResourcePoolConfig,
    TaskExecutionResult,
    TaskSpec,
    TaskState,
    utc_now_iso,
)


class SchedulerError(RuntimeError):
    pass


TaskExecutor = Callable[[TaskSpec, dict[str, TaskState]], TaskExecutionResult | dict[str, Any]]
EventSink = Callable[[str, dict[str, Any]], None]
SafePointCallback = Callable[[dict[str, Any]], None]


class ResourceScheduler:
    RESOURCE_ORDER = (
        "search_pool",
        "download_pool",
        "video_analysis_pool",
        "llm_pool",
        "ffmpeg_pool",
        "tts_pool",
        "export_pool",
    )

    def __init__(
        self,
        *,
        pools: ResourcePoolConfig,
        workspace: Path,
        artifact_registry: ArtifactRegistry,
        event_sink: EventSink | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        safe_point: SafePointCallback | None = None,
    ) -> None:
        self.pools = pools.as_dict()
        self.workspace = workspace.resolve(strict=False)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.registry = artifact_registry
        self.event_sink = event_sink
        self.cancel_requested = cancel_requested or (lambda: False)
        self.safe_point = safe_point
        self.state_dir = self.workspace / ".crayotter"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._state_lock = threading.RLock()
        self._resource_in_use = {name: 0 for name in self.pools}
        self._conflicts_in_use: set[str] = set()

    def run(
        self,
        plan: ExecutionPlan,
        executor: TaskExecutor,
        *,
        resume: bool = True,
        allow_partial_failure: bool = False,
    ) -> dict[str, TaskState]:
        self.validate_plan(plan)
        plan_path = self.state_dir / f"{plan.plan_id}.plan.json"
        state_path = self.state_dir / f"{plan.plan_id}.state.json"
        self._atomic_json_write(plan_path, plan.model_dump())
        states = self._load_states(plan, state_path) if resume else self._new_states(plan)
        completed: set[str] = set()
        for task in plan.tasks:
            task_id = task.id
            state = states[task_id]
            if state.status == "skipped":
                completed.add(task_id)
            elif (
                state.status == "completed"
                and all(dependency in completed for dependency in task.depends_on)
                and self._reuse_completed_task(
                    task,
                    state,
                    states,
                )
            ):
                completed.add(task_id)
            elif state.status == "completed":
                state.status = "pending"
                state.error = "A dependency changed or is no longer reusable."
        failed: set[str] = {
            task_id for task_id, state in states.items() if state.status == "failed"
        }
        running: dict[Future[Any], TaskSpec] = {}
        ready_emitted: set[str] = set()
        max_workers = max(1, sum(self.pools.values()))

        self._emit(
            "task_plan_started",
            {"plan_id": plan.plan_id, "phase": plan.phase, "task_count": len(plan.tasks)},
        )
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"{plan.phase}-task") as pool:
            while len(completed) + len(failed) < len(plan.tasks):
                if self.cancel_requested():
                    for future in running:
                        future.cancel()
                    raise SchedulerError(f"Execution plan cancelled: {plan.plan_id}")

                made_progress = False
                for task in sorted(plan.tasks, key=lambda item: item.priority, reverse=True):
                    state = states[task.id]
                    if task.id in completed or task.id in failed or state.status == "running":
                        continue
                    if any(dep in failed for dep in task.depends_on):
                        state.status = "failed"
                        state.error = "Dependency failed."
                        state.completed_at = utc_now_iso()
                        failed.add(task.id)
                        self._checkpoint(state_path, states)
                        continue
                    if not all(dep in completed for dep in task.depends_on):
                        continue
                    remaining = self._remaining_seconds(plan)
                    if task.optional and remaining is not None and (
                        remaining <= 0 or task.estimated_seconds > remaining
                    ):
                        state.status = "skipped"
                        state.error = "Skipped because the execution budget is exhausted."
                        state.completed_at = utc_now_iso()
                        completed.add(task.id)
                        self._checkpoint(state_path, states)
                        self._emit(
                            "optional_task_skipped",
                            {
                                "plan_id": plan.plan_id,
                                "task_id": task.id,
                                "estimated_seconds": task.estimated_seconds,
                                "remaining_seconds": max(0.0, remaining),
                            },
                        )
                        made_progress = True
                        continue
                    if not task.optional and remaining is not None and remaining <= 0:
                        self._emit(
                            "budget_threshold_reached",
                            {
                                "plan_id": plan.plan_id,
                                "task_id": task.id,
                                "required": True,
                                "remaining_seconds": 0.0,
                                "action": "continue_required_task_and_record_sla_miss",
                            },
                        )
                    if task.id not in ready_emitted:
                        ready_emitted.add(task.id)
                        self._emit(
                            "task_ready",
                            {
                                "plan_id": plan.plan_id,
                                "task_id": task.id,
                                "resources": task.resources,
                            },
                        )
                    if self._reuse_completed_task(task, state, states):
                        completed.add(task.id)
                        self._checkpoint(state_path, states)
                        self._emit(
                            "task_reused",
                            {"plan_id": plan.plan_id, "task_id": task.id},
                        )
                        made_progress = True
                        continue
                    if not self._try_acquire(task):
                        continue

                    state.status = "running"
                    state.task_fingerprint = self._task_fingerprint(task)
                    state.dependency_fingerprints = {
                        dependency: states[dependency].task_fingerprint
                        for dependency in task.depends_on
                    }
                    state.attempts += 1
                    state.started_at = utc_now_iso()
                    state.error = ""
                    self._checkpoint(state_path, states)
                    self._emit(
                        "task_started",
                        {
                            "plan_id": plan.plan_id,
                            "task_id": task.id,
                            "phase": task.phase,
                            "kind": task.kind,
                            "resources": task.resources,
                        },
                    )
                    dependency_states = {
                        dep: states[dep].model_copy(deep=True) for dep in task.depends_on
                    }
                    future = pool.submit(executor, task, dependency_states)
                    setattr(future, "_crayotter_started_monotonic", time.monotonic())
                    running[future] = task
                    made_progress = True

                if not running:
                    self._run_safe_point(plan, completed, states)
                    if len(completed) + len(failed) >= len(plan.tasks):
                        break
                    if not made_progress:
                        pending = [
                            task.id
                            for task in plan.tasks
                            if task.id not in completed and task.id not in failed
                        ]
                        raise SchedulerError(
                            f"No schedulable tasks remain in {plan.plan_id}: {pending}"
                        )
                    continue

                done, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
                for future in done:
                    task = running.pop(future)
                    state = states[task.id]
                    self._release(task)
                    started_monotonic = getattr(future, "_crayotter_started_monotonic", None)
                    if started_monotonic is not None:
                        state.elapsed_seconds = max(
                            0.0, time.monotonic() - float(started_monotonic)
                        )
                    try:
                        raw_result = future.result()
                        result = (
                            raw_result
                            if isinstance(raw_result, TaskExecutionResult)
                            else TaskExecutionResult.model_validate(raw_result)
                        )
                        if task.timeout_seconds and state.elapsed_seconds > task.timeout_seconds:
                            raise TimeoutError(
                                f"Task timeout after {state.elapsed_seconds:.3f}s "
                                f"(limit {task.timeout_seconds:.3f}s)"
                            )
                        state.status = "completed"
                        state.result = result.data
                        state.completed_at = utc_now_iso()
                        state.artifact_ids = []
                        for artifact in result.artifacts:
                            registered = self.registry.register(
                                artifact_id=artifact.id,
                                kind=artifact.kind,
                                producer_task_id=task.id,
                                phase=task.phase,
                                path=artifact.path,
                                metadata=artifact.metadata,
                            )
                            if registered.path and not registered.valid:
                                raise SchedulerError(
                                    f"Task {task.id} declared a missing artifact: "
                                    f"{registered.path}"
                                )
                            state.artifact_ids.append(registered.id)
                            self._emit(
                                "artifact_registered",
                                {
                                    "task_id": task.id,
                                    "artifact_id": registered.id,
                                    "kind": registered.kind,
                                    "path": registered.path,
                                },
                            )
                        completed.add(task.id)
                        self._emit(
                            "task_completed",
                            {
                                "plan_id": plan.plan_id,
                                "task_id": task.id,
                                "attempts": state.attempts,
                                "artifact_ids": state.artifact_ids,
                                "elapsed_seconds": round(state.elapsed_seconds, 3),
                                "estimated_seconds": task.estimated_seconds,
                            },
                        )
                    except Exception as exc:
                        message = str(exc)
                        if self._should_retry(task, state, message):
                            state.status = "pending"
                            state.error = message
                            self._emit(
                                "task_retrying",
                                {
                                    "plan_id": plan.plan_id,
                                    "task_id": task.id,
                                    "attempt": state.attempts,
                                    "error": message[:500],
                                },
                            )
                            if task.retry.backoff_seconds:
                                time.sleep(task.retry.backoff_seconds * state.attempts)
                        else:
                            state.status = "failed"
                            state.error = message
                            state.completed_at = utc_now_iso()
                            failed.add(task.id)
                            self._emit(
                                "task_failed",
                                {
                                    "plan_id": plan.plan_id,
                                    "task_id": task.id,
                                    "attempts": state.attempts,
                                    "error": message[:500],
                                },
                            )
                    self._checkpoint(state_path, states)

                if not running:
                    self._run_safe_point(plan, completed, states)

        if failed and not allow_partial_failure:
            details = "; ".join(
                f"{task_id}: {states[task_id].error}" for task_id in sorted(failed)
            )
            raise SchedulerError(f"Execution plan failed: {details}")
        if failed:
            self._emit(
                "task_plan_degraded",
                {
                    "plan_id": plan.plan_id,
                    "phase": plan.phase,
                    "completed_count": len(completed),
                    "failed_count": len(failed),
                    "failed_task_ids": sorted(failed),
                },
            )
        self._emit(
            "task_plan_completed",
            {"plan_id": plan.plan_id, "phase": plan.phase, "task_count": len(plan.tasks)},
        )
        return states

    def _run_safe_point(
        self,
        plan: ExecutionPlan,
        completed: set[str],
        states: dict[str, TaskState],
    ) -> None:
        if self.safe_point is None:
            return
        pending = [
            task.id
            for task in plan.tasks
            if task.id not in completed and states[task.id].status not in {"failed", "skipped"}
        ]
        self.safe_point(
            {
                "plan_id": plan.plan_id,
                "phase": plan.phase,
                "completed_task_ids": sorted(completed),
                "pending_task_ids": pending,
            }
        )

    def validate_plan(self, plan: ExecutionPlan) -> None:
        task_ids = [task.id for task in plan.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise SchedulerError("Execution plan contains duplicate task ids.")
        known = set(task_ids)
        for task in plan.tasks:
            unknown_dependencies = [dep for dep in task.depends_on if dep not in known]
            if unknown_dependencies:
                raise SchedulerError(
                    f"Task {task.id} has unknown dependencies: {unknown_dependencies}"
                )
            for resource, amount in task.resources.items():
                if resource not in self.pools:
                    raise SchedulerError(f"Task {task.id} requests unknown resource: {resource}")
                if amount > self.pools[resource]:
                    raise SchedulerError(
                        f"Task {task.id} requests {amount} {resource}, capacity is {self.pools[resource]}"
                    )

        visiting: set[str] = set()
        visited: set[str] = set()
        dependencies = {task.id: task.depends_on for task in plan.tasks}

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise SchedulerError(f"Execution plan contains a dependency cycle at {task_id}.")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in dependencies[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in task_ids:
            visit(task_id)

    def resource_snapshot(self) -> dict[str, dict[str, int]]:
        with self._state_lock:
            return {
                name: {"capacity": self.pools[name], "in_use": self._resource_in_use[name]}
                for name in self.RESOURCE_ORDER
            }

    @staticmethod
    def _remaining_seconds(plan: ExecutionPlan) -> float | None:
        if plan.deadline_at_epoch is None:
            return None
        return float(plan.deadline_at_epoch) - time.time()

    def _try_acquire(self, task: TaskSpec) -> bool:
        with self._state_lock:
            if any(key in self._conflicts_in_use for key in task.conflict_keys):
                return False
            for resource in self.RESOURCE_ORDER:
                requested = int(task.resources.get(resource, 0))
                if requested and self._resource_in_use[resource] + requested > self.pools[resource]:
                    return False
            for resource in self.RESOURCE_ORDER:
                requested = int(task.resources.get(resource, 0))
                self._resource_in_use[resource] += requested
            self._conflicts_in_use.update(task.conflict_keys)
            self._emit(
                "resource_acquired",
                {
                    "task_id": task.id,
                    "resources": task.resources,
                    "conflict_keys": task.conflict_keys,
                    "pools": self.resource_snapshot(),
                },
            )
            return True

    def _release(self, task: TaskSpec) -> None:
        with self._state_lock:
            for resource in self.RESOURCE_ORDER:
                requested = int(task.resources.get(resource, 0))
                self._resource_in_use[resource] = max(
                    0, self._resource_in_use[resource] - requested
                )
            for key in task.conflict_keys:
                self._conflicts_in_use.discard(key)
            self._emit(
                "resource_released",
                {
                    "task_id": task.id,
                    "resources": task.resources,
                    "pools": self.resource_snapshot(),
                },
            )

    def _reuse_completed_task(
        self,
        task: TaskSpec,
        state: TaskState,
        states: dict[str, TaskState],
    ) -> bool:
        if state.status != "completed":
            return False
        if state.task_fingerprint != self._task_fingerprint(task):
            state.status = "pending"
            state.error = "Task definition changed; scheduled for re-execution."
            state.artifact_ids = []
            return False
        expected_dependencies = {
            dependency: states[dependency].task_fingerprint
            for dependency in task.depends_on
        }
        if state.dependency_fingerprints != expected_dependencies:
            state.status = "pending"
            state.error = "Dependency output changed; scheduled for re-execution."
            state.artifact_ids = []
            return False
        artifacts = [self.registry.get(artifact_id) for artifact_id in state.artifact_ids]
        if state.artifact_ids and not all(item is not None and item.valid for item in artifacts):
            state.status = "pending"
            state.error = "A previously completed artifact is missing or invalid."
            return False
        return True

    @staticmethod
    def _should_retry(task: TaskSpec, state: TaskState, message: str) -> bool:
        if state.attempts >= task.retry.max_attempts:
            return False
        lowered = message.lower()
        return any(marker.lower() in lowered for marker in task.retry.retryable_errors)

    @staticmethod
    def _task_fingerprint(task: TaskSpec) -> str:
        payload = task.model_dump()
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _load_states(
        self,
        plan: ExecutionPlan,
        state_path: Path,
    ) -> dict[str, TaskState]:
        states = self._new_states(plan)
        if not state_path.exists():
            return states
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            for task_id, raw in payload.get("tasks", {}).items():
                if task_id not in states:
                    continue
                state = TaskState.model_validate(raw)
                if state.status == "running":
                    state.status = "pending"
                    state.error = "Interrupted while running; scheduled for retry."
                states[task_id] = state
        except Exception:
            return self._new_states(plan)
        return states

    @staticmethod
    def _new_states(plan: ExecutionPlan) -> dict[str, TaskState]:
        return {task.id: TaskState(task_id=task.id) for task in plan.tasks}

    def _checkpoint(self, path: Path, states: dict[str, TaskState]) -> None:
        self._atomic_json_write(
            path,
            {
                "version": 1,
                "updated_at": utc_now_iso(),
                "tasks": {task_id: state.model_dump() for task_id, state in states.items()},
            },
        )
        self._emit("checkpoint_saved", {"path": str(path)})

    @staticmethod
    def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        last_error: OSError | None = None
        for attempt in range(6):
            try:
                os.replace(temp_path, path)
                return
            except OSError as exc:
                last_error = exc
                if getattr(exc, "winerror", None) not in {5, 32, 33} and not isinstance(exc, PermissionError):
                    raise
                time.sleep(0.05 * (2 ** attempt))
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        if last_error is not None:
            raise last_error

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink is not None:
            self.event_sink(event_type, payload)
