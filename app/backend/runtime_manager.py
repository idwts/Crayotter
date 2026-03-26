from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .config_store import JOBS_DIR, ConfigStore
from .event_bus import EventBus
from .models import AppConfig, JobRecord, JobRequest, RuntimeEvent, TERMINAL_JOB_STATUSES, utc_now_iso


class ManagedJob:
    def __init__(self, record: JobRecord, job_dir: Path) -> None:
        self.record = record
        self.job_dir = job_dir
        self.bus = EventBus()
        self.cancel_requested = threading.Event()
        self.thread: threading.Thread | None = None
        self.process: subprocess.Popen[str] | None = None
        self.events_path = job_dir / "events.jsonl"
        self.summary_path = job_dir / "summary.json"
        self.lock = threading.RLock()
        self.last_activity_monotonic = time.monotonic()


class RuntimeManager:
    AGENT_STALL_TIMEOUT_SECONDS = 150

    def __init__(self, config_store: ConfigStore) -> None:
        self.config_store = config_store
        self._jobs: dict[str, ManagedJob] = {}
        self._lock = threading.RLock()
        self._load_existing_jobs()

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda item: item.record.created_at,
                reverse=True,
            )
            return [job.record.model_dump() for job in jobs]

    def get_job(self, job_id: str) -> ManagedJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def get_job_detail(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        with job.lock:
            detail = job.record.model_dump()
            detail["job_dir"] = str(job.job_dir)
            detail["events_path"] = str(job.events_path)
            detail["summary_path"] = str(job.summary_path)
            detail["artifacts"] = self._collect_artifacts(job)
            return detail

    def list_job_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return self._collect_artifacts(job)

    def create_job(self, request: JobRequest) -> dict[str, Any]:
        config = self.config_store.load()
        if request.mode == "demo" and not config.allow_demo_jobs:
            raise ValueError("Demo jobs are disabled in configuration.")

        with self._lock:
            running = [
                job.record.job_id
                for job in self._jobs.values()
                if job.record.status == "running"
            ]
            if running:
                raise RuntimeError(
                    f"Another job is already running: {running[0]}. Phase A only supports one running job."
                )

            job_id = self._new_job_id()
            job_dir = JOBS_DIR / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            record = JobRecord(
                job_id=job_id,
                task=request.task,
                mode=request.mode,
                profile=request.profile or config.active_profile,
                job_dir=str(job_dir),
            )
            job = ManagedJob(record=record, job_dir=job_dir)
            self._jobs[job_id] = job

        self._write_summary(job)
        self._publish(job, "job_created", {"task": request.task, "mode": request.mode})

        worker = threading.Thread(
            target=self._run_job,
            args=(job, request, config),
            name=f"job-{job_id}",
            daemon=True,
        )
        job.thread = worker
        worker.start()
        return record.model_dump()

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)

        job.cancel_requested.set()
        self._publish(job, "cancel_requested", {"message": "Cancellation was requested."})

        if job.record.mode == "agent":
            self._mark_cancelled(job)
            process = job.process
            if process is not None and process.poll() is None:
                self._terminate_process_tree(process)
        elif job.record.mode == "demo" and job.record.status == "running":
            self._mark_cancelled(job)

        return {
            "job_id": job_id,
            "status": job.record.status,
            "cancel_requested": True,
            "note": "Cancellation requested.",
        }

    def delete_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.record.status == "running":
                raise RuntimeError("Running jobs cannot be deleted. Stop the job first.")
            self._jobs.pop(job_id, None)

        shutil.rmtree(job.job_dir, ignore_errors=False)
        return {"job_id": job_id, "deleted": True}

    def list_events(self, job_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job.bus.list_from(after_sequence=after_sequence)

    def wait_for_events(self, job_id: str, after_sequence: int = 0, timeout: float = 1.0) -> list[dict[str, Any]]:
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job.bus.wait_for_events(after_sequence=after_sequence, timeout=timeout)

    def _run_job(self, job: ManagedJob, request: JobRequest, config: AppConfig) -> None:
        self._mark_running(job)
        try:
            if request.mode == "demo":
                self._run_demo_job(job, request)
            else:
                self._run_agent_job(job, request, config)
        except Exception as exc:
            self._mark_failed(job, str(exc))

    def _run_demo_job(self, job: ManagedJob, request: JobRequest) -> None:
        phase_steps = [
            ("phase1", "planner", "拆解任务并估算素材需求", "search_bilibili_video", "已整理出 4 个素材检索方向"),
            ("phase1", "executor", "筛选最合适的候选素材", "rank_video_candidates", "已筛出 6 条高匹配候选"),
            ("phase2", "editing_research", "生成剪辑蓝图", "", "蓝图已生成，包含 5 段叙事结构"),
            ("phase3", "react_editor", "裁剪、合并并添加旁白", "add_narration_segments", "已完成转场与分段配音"),
        ]
        seen_phases: set[str] = set()

        for index, (phase_id, node_name, summary, tool_name, result_text) in enumerate(phase_steps, start=1):
            if job.cancel_requested.is_set():
                self._mark_cancelled(job)
                return
            if phase_id not in seen_phases:
                seen_phases.add(phase_id)
                self._publish(job, "phase_started", {"phase": phase_id, "node": node_name})
            self._publish(job, "thinking_summary", {"phase": phase_id, "summary": summary})
            self._publish(
                job,
                "step_started",
                {"phase": phase_id, "step_index": index, "description": summary},
            )
            time.sleep(0.15)
            if tool_name:
                self._publish(
                    job,
                    "tool_called",
                    {
                        "phase": phase_id,
                        "tool_name": tool_name,
                        "args_preview": {"demo": True, "step_index": index},
                    },
                )
                time.sleep(0.15)
                self._publish(
                    job,
                    "tool_result",
                    {"phase": phase_id, "tool_name": tool_name, "summary": result_text},
                )
            self._publish(
                job,
                "step_completed",
                {"phase": phase_id, "step_index": index, "result": result_text},
            )

        output_dir = job.job_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = output_dir / "demo_final_summary.txt"
        artifact_path.write_text(
            f"Demo job completed for task:\n{request.task}\n",
            encoding="utf-8",
        )
        self._publish(job, "artifact_created", {"path": str(artifact_path), "kind": "demo_output"})
        self._mark_completed(
            job,
            final_output="Demo job completed. Backend service, event bus, and job persistence are working.",
            output_files=[str(artifact_path)],
        )

    def _run_agent_job(self, job: ManagedJob, request: JobRequest, config: AppConfig) -> None:
        project_root = Path(__file__).resolve().parents[2]
        profile = config.get_profile(request.profile)
        if not profile.api_key:
            raise RuntimeError(
                "The selected profile does not have an API key. Update app_state/config.json or call PUT /config first."
            )

        worker_script = project_root / "script" / "run_agent_worker.py"
        config_path = job.job_dir / "runtime_profile.json"
        task_path = job.job_dir / "task.txt"
        config_path.write_text(
            json.dumps(profile.to_runtime_config(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        task_path.write_text(request.task, encoding="utf-8")

        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"

        process = subprocess.Popen(
            [
                sys.executable,
                str(worker_script),
                "--task-file",
                str(task_path),
                "--config-file",
                str(config_path),
            ],
            cwd=str(project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=child_env,
        )
        job.process = process
        job.last_activity_monotonic = time.monotonic()

        watchdog = threading.Thread(
            target=self._watch_agent_process,
            args=(job, process),
            name=f"job-watchdog-{job.record.job_id}",
            daemon=True,
        )
        watchdog.start()

        marker = "__CRAYOTTER_EVENT__"
        final_output = ""
        output_files: list[str] = []
        worker_error = ""
        stderr_lines: list[str] = []

        def _drain_stderr() -> None:
            if process.stderr is None:
                return
            for raw_line in process.stderr:
                text = raw_line.rstrip()
                if text:
                    stderr_lines.append(text)

        stderr_thread = threading.Thread(
            target=_drain_stderr,
            name=f"job-stderr-{job.record.job_id}",
            daemon=True,
        )
        stderr_thread.start()

        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line or not line.startswith(marker):
                continue
            try:
                message = json.loads(line[len(marker):])
            except json.JSONDecodeError:
                continue

            message_kind = message.get("kind")
            if message_kind == "event":
                event_type = message.get("type", "runtime_event")
                payload = dict(message.get("payload", {}))
                source_timestamp = message.get("timestamp")
                if source_timestamp:
                    payload.setdefault("source_timestamp", source_timestamp)
                self._publish(job, event_type, payload)
            elif message_kind == "result":
                final_output = str(message.get("final_output", ""))
                output_files = [str(path) for path in message.get("output_files", [])]
            elif message_kind == "error":
                worker_error = str(message.get("error", "Agent worker failed."))

        return_code = process.wait()
        stderr_thread.join(timeout=1.0)
        stderr_text = "\n".join(stderr_lines[-200:]).strip()
        job.process = None

        if job.cancel_requested.is_set():
            if job.record.status not in TERMINAL_JOB_STATUSES:
                self._mark_cancelled(job)
            return

        if return_code == 0:
            self._mark_completed(job, final_output=final_output, output_files=output_files)
            return

        self._mark_failed(
            job,
            worker_error or stderr_text or f"Agent worker exited with code {return_code}.",
        )

    def _watch_agent_process(self, job: ManagedJob, process: subprocess.Popen[str]) -> None:
        while process.poll() is None:
            if job.cancel_requested.is_set() or job.record.status in TERMINAL_JOB_STATUSES:
                return

            idle_seconds = time.monotonic() - job.last_activity_monotonic
            if idle_seconds < self.AGENT_STALL_TIMEOUT_SECONDS:
                time.sleep(2)
                continue

            timeout_seconds = self.AGENT_STALL_TIMEOUT_SECONDS
            self._publish(
                job,
                "job_stalled",
                {
                    "idle_seconds": round(idle_seconds, 1),
                    "timeout_seconds": timeout_seconds,
                    "message": f"任务连续 {timeout_seconds} 秒无新进展，已判定为卡住。",
                },
            )
            self._mark_failed(
                job,
                f"任务连续 {timeout_seconds} 秒无新进展，已自动停止。常见原因是模型接口或素材搜索网络超时。",
            )
            job.cancel_requested.set()
            self._terminate_process_tree(process)
            return

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            else:
                process.terminate()
                process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=2)
            except Exception:
                pass

    def _mark_running(self, job: ManagedJob) -> None:
        with job.lock:
            job.record.status = "running"
            job.record.started_at = utc_now_iso()
            job.last_activity_monotonic = time.monotonic()
            self._write_summary(job)
        self._publish(job, "job_started", {"started_at": job.record.started_at})

    def _mark_completed(self, job: ManagedJob, final_output: str, output_files: list[str]) -> None:
        with job.lock:
            if job.record.status in TERMINAL_JOB_STATUSES:
                return
            job.record.status = "completed"
            job.record.completed_at = utc_now_iso()
            job.record.final_output = final_output
            job.record.output_files = output_files
            self._write_summary(job)
        self._publish(
            job,
            "job_completed",
            {
                "completed_at": job.record.completed_at,
                "final_output": final_output,
                "output_files": output_files,
            },
        )

    def _mark_failed(self, job: ManagedJob, error_message: str) -> None:
        with job.lock:
            if job.record.status in TERMINAL_JOB_STATUSES:
                return
            job.record.status = "failed"
            job.record.completed_at = utc_now_iso()
            job.record.error = error_message
            self._write_summary(job)
        self._publish(job, "job_failed", {"error": error_message})

    def _mark_cancelled(self, job: ManagedJob) -> None:
        with job.lock:
            if job.record.status == "cancelled":
                return
            job.record.status = "cancelled"
            job.record.completed_at = utc_now_iso()
            self._write_summary(job)
        self._publish(job, "job_cancelled", {"completed_at": job.record.completed_at})

    def _publish(self, job: ManagedJob, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        raw_event = RuntimeEvent(job_id=job.record.job_id, type=event_type, payload=payload).model_dump()
        stored = job.bus.publish(raw_event)
        with job.lock:
            job.record.events_count = stored["sequence"]
            job.last_activity_monotonic = time.monotonic()
            self._append_event(job.events_path, stored)
            self._write_summary(job)
        return stored

    @staticmethod
    def _append_event(path: Path, event: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    @staticmethod
    def _write_summary(job: ManagedJob) -> None:
        payload = job.record.model_dump()
        payload["job_dir"] = str(job.job_dir)
        job.summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _new_job_id() -> str:
        return f"job_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    @staticmethod
    def _collect_artifacts(job: ManagedJob) -> list[dict[str, Any]]:
        project_root = Path(__file__).resolve().parents[2]
        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        candidate_paths: list[Path] = []
        for path_str in job.record.output_files:
            if path_str:
                candidate_paths.append(Path(path_str))
        output_dir = job.job_dir / "output"
        if output_dir.exists():
            for path in sorted(output_dir.rglob("*")):
                if path.is_file():
                    candidate_paths.append(path)

        for path in candidate_paths:
            try:
                resolved = path.resolve(strict=False)
            except Exception:
                resolved = path
            key = str(resolved)
            if key in seen or not resolved.exists() or not resolved.is_file():
                continue
            seen.add(key)
            try:
                relative = resolved.relative_to(project_root)
                display_path = str(relative)
            except Exception:
                display_path = str(resolved)
            results.append(
                {
                    "path": str(resolved),
                    "display_path": display_path,
                    "name": resolved.name,
                    "suffix": resolved.suffix.lower(),
                    "size_bytes": resolved.stat().st_size,
                }
            )
        return sorted(results, key=lambda item: item["display_path"])

    def _load_existing_jobs(self) -> None:
        if not JOBS_DIR.exists():
            return

        for job_dir in sorted(JOBS_DIR.iterdir()):
            if not job_dir.is_dir():
                continue
            summary_path = job_dir / "summary.json"
            if not summary_path.exists():
                continue
            try:
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
                record = JobRecord.model_validate(payload)
                job = ManagedJob(record=record, job_dir=job_dir)
                if job.events_path.exists():
                    seeded_events: list[dict[str, Any]] = []
                    for line in job.events_path.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        seeded_events.append(json.loads(line))
                    job.bus.seed(seeded_events)
                if record.status not in TERMINAL_JOB_STATUSES:
                    record.status = "cancelled"
                    record.completed_at = record.completed_at or utc_now_iso()
                    record.error = record.error or "Backend restarted before the task finished."
                    self._write_summary(job)
                self._jobs[record.job_id] = job
            except Exception:
                continue
