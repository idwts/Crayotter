from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import threading
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config_store import JOBS_DIR, ConfigStore
from .event_bus import EventBus
from .models import AppConfig, JobRecord, JobRequest, RuntimeEvent, TERMINAL_JOB_STATUSES, utc_now_iso
from .task_titles import summarize_task_title
from app.media_metadata import video_duration_seconds
from app.runtime_paths import configure_runtime_environment, get_bundle_root, get_runtime_root, is_frozen
from app.steering import SteeringCoordinator, SteeringStore, classify_guidance
from script.editing_plan import (
    EditingPlanStore,
    PlanPatch,
    apply_plan_patch,
    heuristic_patch_from_feedback,
    validate_editing_plan,
)


def format_events_as_log(events: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for event in events:
        timestamp = str(event.get("timestamp") or "")
        event_type = str(event.get("type") or "event")
        payload = event.get("payload") or {}
        if isinstance(payload, dict) and "message" in payload:
            body = str(payload.get("message") or "")
        else:
            body = json.dumps(payload, ensure_ascii=False, indent=2)
        lines.append(f"{timestamp}\n{event_type}\n{body}".strip())
    return "\n\n".join(lines) + ("\n" if lines else "")


class ManagedJob:
    def __init__(self, record: JobRecord, job_dir: Path, stall_timeout_seconds: int = 150) -> None:
        self.record = record
        self.job_dir = job_dir
        self.bus = EventBus()
        self.cancel_requested = threading.Event()
        self.thread: threading.Thread | None = None
        self.process: subprocess.Popen[str] | None = None
        self.events_path = job_dir / "events.jsonl"
        self.summary_path = job_dir / "summary.json"
        self.steering_dir = job_dir / "steering"
        self.steering_store = SteeringStore(self.steering_dir)
        self.lock = threading.RLock()
        self.last_activity_monotonic = time.monotonic()
        self.stall_timeout_seconds = max(10, int(stall_timeout_seconds))
        self.request: JobRequest | None = None
        self.config: AppConfig | None = None
        self.resume_requested = False
        self.has_ephemeral_credentials = False
        self.materials_dir: Path | None = None


class RuntimeManager:
    AGENT_STALL_TIMEOUT_SECONDS = 600
    VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v", ".mpeg", ".mpg"}
    TEXT_SUFFIXES = {".txt", ".md", ".json", ".jsonl", ".log"}
    _media_metadata_cache: dict[tuple[str, int, int], float | None] = {}
    _media_metadata_lock = threading.RLock()

    # 任务产物保留策略：终态/中断任务超过保留天数后由 janitor 整体清除（含 workspace
    # 原始媒体），防止公开试用磁盘只增不减。<=0 表示关闭清扫。
    JOB_RETENTION_DAYS = float(os.environ.get("CRAYOTTER_JOB_RETENTION_DAYS", "7") or 7)
    JOB_JANITOR_INTERVAL_SECONDS = float(os.environ.get("CRAYOTTER_JOB_JANITOR_INTERVAL_SECONDS", "21600") or 21600)
    # 磁盘水位 LRU：分区使用率超过阈值（%）时，按最近使用时间从旧到新清除终态任务目录，
    # 直到回落到目标水位或无任务可清。interrupted 最后清除，running/queued 永不清除。<=0 关闭。
    DISK_LRU_THRESHOLD_PERCENT = float(os.environ.get("CRAYOTTER_DISK_LRU_THRESHOLD_PERCENT", "70") or 70)
    DISK_LRU_TARGET_PERCENT = float(os.environ.get("CRAYOTTER_DISK_LRU_TARGET_PERCENT", "60") or 60)

    def __init__(self, config_store: ConfigStore) -> None:
        self.config_store = config_store
        self._jobs: dict[str, ManagedJob] = {}
        self._lock = threading.RLock()
        self._janitor_stop = threading.Event()
        self._janitor_thread: threading.Thread | None = None
        self._shutting_down = threading.Event()
        self._load_existing_jobs()

    # ------------------------------------------------------------------
    # 产物保留 janitor
    # ------------------------------------------------------------------
    def start_janitor(self) -> None:
        """启动后台清扫线程（幂等）；保留天数 <=0 时不启动。"""
        if self.JOB_RETENTION_DAYS <= 0 or self._janitor_thread is not None:
            return
        self._janitor_thread = threading.Thread(
            target=self._janitor_loop, name="job-janitor", daemon=True
        )
        self._janitor_thread.start()

    def stop_janitor(self) -> None:
        self._janitor_stop.set()

    def _janitor_loop(self) -> None:
        while not self._janitor_stop.wait(self.JOB_JANITOR_INTERVAL_SECONDS):
            try:
                removed = self.sweep_expired_jobs()
                if removed:
                    logging.getLogger(__name__).info(
                        "job janitor removed %d expired jobs: %s", len(removed), removed
                    )
            except Exception:
                logging.getLogger(__name__).exception("job janitor sweep failed")
            try:
                evicted = self.evict_lru_jobs()
                if evicted:
                    logging.getLogger(__name__).info(
                        "disk LRU evicted %d jobs above watermark: %s", len(evicted), evicted
                    )
            except Exception:
                logging.getLogger(__name__).exception("disk LRU eviction failed")

    @staticmethod
    def _job_reference_time(job: ManagedJob) -> datetime | None:
        raw = job.record.completed_at or job.record.created_at or ""
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def sweep_expired_jobs(self, *, now: datetime | None = None) -> list[str]:
        """删除超过保留期的终态/interrupted 任务（目录+内存记录），返回删除的 job_id。

        running/queued 永不删除；时间戳无法解析的保守保留。
        """
        if self.JOB_RETENTION_DAYS <= 0:
            return []
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=self.JOB_RETENTION_DAYS)
        removed: list[str] = []
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                status = job.record.status
                if status not in TERMINAL_JOB_STATUSES and status != "interrupted":
                    continue
                reference = self._job_reference_time(job)
                if reference is None or reference > cutoff:
                    continue
                shutil.rmtree(job.job_dir, ignore_errors=True)
                del self._jobs[job_id]
                removed.append(job_id)
        return removed

    def evict_lru_jobs(self, *, usage_fn=None) -> list[str]:
        """磁盘水位 LRU 清除：分区使用率超阈值时，按最近使用时间从旧到新删除终态任务。

        终态（completed/failed/cancelled）优先，interrupted 最后参与；running/queued 永不删除；
        时间戳无法解析的保守保留。usage_fn 可注入（默认 shutil.disk_usage）便于测试。
        """
        if self.DISK_LRU_THRESHOLD_PERCENT <= 0:
            return []
        usage_fn = usage_fn or shutil.disk_usage

        def usage_percent() -> float:
            usage = usage_fn(str(JOBS_DIR))
            return usage.used / usage.total * 100 if usage.total else 0.0

        if usage_percent() <= self.DISK_LRU_THRESHOLD_PERCENT:
            return []
        removed: list[str] = []
        with self._lock:
            candidates = []
            for job_id, job in self._jobs.items():
                status = job.record.status
                if status in TERMINAL_JOB_STATUSES:
                    tier = 0
                elif status == "interrupted":
                    tier = 1
                else:
                    continue
                reference = self._job_reference_time(job)
                if reference is None:
                    continue
                candidates.append((tier, reference, job_id, job))
            candidates.sort(key=lambda item: (item[0], item[1]))
            for _, _, job_id, job in candidates:
                if usage_percent() <= self.DISK_LRU_TARGET_PERCENT:
                    break
                shutil.rmtree(job.job_dir, ignore_errors=True)
                del self._jobs[job_id]
                removed.append(job_id)
        return removed

    def begin_shutdown(self) -> None:
        """收到停机信号的第一时间调用：置停机标志，阻断 worker 失败路径误标 failed。"""
        self._shutting_down.set()

    def shutdown(self) -> None:
        """优雅停机：停 janitor，并把未完成任务显式落盘为 interrupted。

        使 SIGTERM 停机与进程被杀后的重启恢复语义一致（重启加载时也会兜底标记），
        同时保证 summary.json 在进程退出前是最新的。
        """
        self.stop_janitor()
        self._shutting_down.set()
        with self._lock:
            for job in self._jobs.values():
                if job.record.status in {"queued", "running"}:
                    job.record.status = "interrupted"
                    job.record.completed_at = None
                    job.record.error = job.record.error or "Backend stopped before the task finished."
                    self._write_summary(job)

    def list_jobs(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda item: item.record.created_at,
                reverse=True,
            )
            if owner_id is not None:
                jobs = [job for job in jobs if job.record.owner_id == owner_id]
            return [job.record.model_dump() for job in jobs]

    def get_job(self, job_id: str, owner_id: str | None = None) -> ManagedJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or (owner_id is not None and job.record.owner_id != owner_id):
                return None
            return job

    def get_job_detail(self, job_id: str, owner_id: str | None = None) -> dict[str, Any]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        with job.lock:
            detail = job.record.model_dump()
            detail["job_dir"] = str(job.job_dir)
            detail["events_path"] = str(job.events_path)
            detail["summary_path"] = str(job.summary_path)
            detail["artifacts"] = self._collect_artifacts(job)
            return detail

    def list_job_artifacts(self, job_id: str, owner_id: str | None = None) -> list[dict[str, Any]]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        return self._collect_artifacts(job)

    def get_current_plan(self, job_id: str, owner_id: str | None = None) -> dict[str, Any]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        plan = self._plan_store(job).current()
        if plan is None:
            # 无计划是正常状态（任务尚未进入 Phase 2）：返回 200 空载体，
            # 避免前端每轮轮询产生 404 噪音。
            return {"plan": None, "versions": [], "approved": None}
        return {
            "plan": plan.model_dump(),
            "versions": self._plan_store(job).list_versions(),
            "approved": self._plan_store(job).approved().model_dump()
            if self._plan_store(job).approved() is not None
            else None,
        }

    def get_plan(self, job_id: str, version: str, owner_id: str | None = None) -> dict[str, Any]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        plan = self._plan_store(job).get_plan(version)
        if plan is None:
            raise KeyError(f"{job_id}/plans/{version}")
        return {"plan": plan.model_dump()}

    def get_plan_diff(self, job_id: str, from_version: str, to_version: str, owner_id: str | None = None) -> dict[str, Any]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        return {"diff": self._plan_store(job).diff(from_version, to_version).model_dump()}

    def apply_plan_feedback(self, job_id: str, version: str, feedback: str, owner_id: str | None = None) -> dict[str, Any]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        text = str(feedback or "").strip()
        if not text:
            raise ValueError("Plan feedback cannot be empty.")
        store = self._plan_store(job)
        base = store.get_plan(version)
        if base is None:
            raise KeyError(f"{job_id}/plans/{version}")

        patch = self._generate_plan_patch(job, base.model_dump(), text)
        if patch.base_version != base.version:
            patch.base_version = base.version
        updated, plan_diff = apply_plan_patch(base, patch)
        if (
            text
            and not plan_diff.changed_globals
            and not plan_diff.added_scenes
            and not plan_diff.removed_scenes
            and not plan_diff.changed_scenes
        ):
            self._publish(
                job,
                "plan_patch_fallback",
                {"version": base.version, "reason": "generated patch made no structural changes"},
            )
            patch = heuristic_patch_from_feedback(base, text)
            updated, plan_diff = apply_plan_patch(base, patch)
        report = validate_editing_plan(updated, allowed_source_paths=updated.source_video_paths)
        if not report.ok:
            self._publish(
                job,
                "plan_validation_failed",
                {
                    "version": updated.version,
                    "base_version": base.version,
                    "issues": [item.model_dump() for item in report.issues],
                },
            )
            raise ValueError(
                "Plan feedback produced an invalid plan: "
                + "; ".join(item.message for item in report.issues if item.severity == "error")
            )
        updated.status = "WAITING_FOR_USER_REVIEW"
        store.save_plan(updated)
        patch_path = store.root / f"plan_patch_{base.version}_to_{updated.version}.json"
        patch_path.write_text(
            json.dumps(patch.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        diff_path = store.root / f"plan_diff_{base.version}_to_{updated.version}.json"
        diff_path.write_text(
            json.dumps(plan_diff.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._publish(
            job,
            "plan_revised",
            {
                "from_version": base.version,
                "to_version": updated.version,
                "summary": patch.summary,
                "diff_summary": plan_diff.summary,
            },
        )
        return {
            "plan": updated.model_dump(),
            "patch": patch.model_dump(),
            "diff": plan_diff.model_dump(),
            "validation": report.model_dump(),
        }

    def approve_plan(self, job_id: str, version: str, owner_id: str | None = None) -> dict[str, Any]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        store = self._plan_store(job)
        plan = store.approve(version)
        control = job.steering_store.read_control()
        if control.get("status") == "requested" and control.get("mode") == "plan_review":
            token = str(control.get("token") or "")
            if token:
                job.steering_store.approve(token)
                self._publish(
                    job,
                    "steering_approval_received",
                    {"pause_token": token, "revision": job.record.revision},
                )
        self._publish(
            job,
            "plan_approved",
            {"version": plan.version, "path": str(store.approved_path)},
        )
        return {"plan": plan.model_dump(), "approved_path": str(store.approved_path)}

    def reject_plan(self, job_id: str, version: str, owner_id: str | None = None) -> dict[str, Any]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        plan = self._plan_store(job).reject(version)
        self._publish(job, "plan_rejected", {"version": plan.version})
        if job.record.status == "running":
            self.cancel_job(job_id)
        return {"plan": plan.model_dump(), "status": job.record.status}

    def create_job(
        self,
        request: JobRequest,
        owner_id: str = "",
        runtime_overrides: dict[str, str] | None = None,
        material_root: Path | None = None,
    ) -> dict[str, Any]:
        config = self.config_store.load()
        if runtime_overrides:
            # Public BYOK credentials are job-scoped memory only.  Do not call
            # ConfigStore.save and never serialize these values into a record,
            # summary, event, or runtime profile file.
            config = config.model_copy(deep=True)
            profile = config.get_profile(request.profile)
            for field, value in runtime_overrides.items():
                if hasattr(profile, field) and value:
                    setattr(profile, field, value)
        if request.mode == "demo" and not config.allow_demo_jobs:
            raise ValueError("Demo jobs are disabled in configuration.")

        enable_phase2_research = (
            config.enable_phase2_research
            if request.enable_phase2_research is None
            else request.enable_phase2_research
        )
        enable_plan_review = (
            config.enable_plan_review
            if request.enable_plan_review is None
            else request.enable_plan_review
        )
        direct_phase3_execution = (
            config.direct_phase3_execution
            if request.direct_phase3_execution is None
            else request.direct_phase3_execution
        )
        prefer_local_materials = (
            config.prefer_local_materials
            if request.prefer_local_materials is None
            else request.prefer_local_materials
        )
        stall_timeout_seconds = max(10, int(config.agent_stall_timeout_seconds or self.AGENT_STALL_TIMEOUT_SECONDS))

        with self._lock:
            job_id = self._new_job_id()
            job_dir = JOBS_DIR / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            record = JobRecord(
                job_id=job_id,
                owner_id=owner_id,
                task=request.task,
                title=summarize_task_title(request.task),
                mode=request.mode,
                enable_phase2_research=enable_phase2_research,
                enable_plan_review=enable_plan_review,
                direct_phase3_execution=direct_phase3_execution,
                prefer_local_materials=prefer_local_materials,
                profile=request.profile or config.active_profile,
                job_dir=str(job_dir),
                target_duration_seconds=request.target_duration_seconds,
                deadline_seconds=request.deadline_seconds or config.default_deadline_seconds,
                processing_mode=request.processing_mode or config.processing_mode,
                output_profile=request.output_profile or config.output_profile,
                enabled_material_platforms=(
                    request.enabled_material_platforms
                    if request.enabled_material_platforms is not None
                    else config.enabled_material_platforms
                ),
                browser_auth_browser=request.browser_auth_browser or config.browser_auth_browser,
                browser_auth_profile=request.browser_auth_profile or config.browser_auth_profile,
            )
            job = ManagedJob(
                record=record,
                job_dir=job_dir,
                stall_timeout_seconds=stall_timeout_seconds,
            )
            job.request = request
            job.config = config
            job.has_ephemeral_credentials = bool(runtime_overrides)
            if material_root is not None:
                source_root = material_root.resolve(strict=False)
                # The upstream worker clears CRAYOTTER_TASK_WORKSPACE before
                # it starts.  Keep uploaded user material beside that
                # workspace, not inside it, so task preparation cannot delete
                # the just-staged session files.
                target_root = job_dir / "user_temp"
                for source in source_root.rglob("*"):
                    if not source.is_file() or source.is_symlink():
                        continue
                    resolved = source.resolve(strict=False)
                    try:
                        relative = resolved.relative_to(source_root)
                    except ValueError:
                        continue
                    destination = target_root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(resolved, destination)
                job.materials_dir = target_root
            self._jobs[job_id] = job

        self._write_summary(job)
        self._publish(
            job,
            "job_created",
            {"task": request.task, "title": record.title, "mode": request.mode},
        )

        self._publish(job, "job_queued", {"message": "Job accepted and waiting for a worker."})
        self._start_next_job()
        return record.model_dump()

    def cancel_job(self, job_id: str, owner_id: str | None = None) -> dict[str, Any]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)

        job.cancel_requested.set()
        self._publish(job, "cancel_requested", {"message": "Cancellation was requested."})

        if job.record.status == "queued":
            self._mark_cancelled(job)
            self._start_next_job()
        elif job.record.mode == "agent":
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

    def resume_job(self, job_id: str, owner_id: str | None = None, *, strategy: str = "resume") -> dict[str, Any]:
        """恢复失败/中断任务。

        strategy="resume"：从最近 checkpoint 断点续跑（保留进度）。
        strategy="restart"：重新开始（revision+1，从 Phase 1 重跑；workspace 中
        已下载素材保留可复用，但执行进度不继承）。
        """
        if strategy not in {"resume", "restart"}:
            raise ValueError(f"Unknown resume strategy: {strategy}")
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        with self._lock:
            running = [
                item.record.job_id
                for item in self._jobs.values()
                if item.record.status in {"queued", "running"}
            ]
            if running:
                raise RuntimeError(f"Another job is already running: {running[0]}.")
            if job.record.status not in {"interrupted", "failed"}:
                raise RuntimeError("Only interrupted or failed jobs can be resumed.")

            config = self.config_store.load()
            job.record.browser_auth_browser = config.browser_auth_browser or job.record.browser_auth_browser
            job.record.browser_auth_profile = config.browser_auth_profile
            request = JobRequest(
                task=job.record.task,
                mode=job.record.mode,
                profile=job.record.profile,
                enable_phase2_research=job.record.enable_phase2_research,
                enable_plan_review=job.record.enable_plan_review,
                direct_phase3_execution=job.record.direct_phase3_execution,
                prefer_local_materials=job.record.prefer_local_materials,
                target_duration_seconds=job.record.target_duration_seconds,
                deadline_seconds=job.record.deadline_seconds,
                processing_mode=job.record.processing_mode,
                output_profile=job.record.output_profile,
                enabled_material_platforms=job.record.enabled_material_platforms,
                browser_auth_browser=job.record.browser_auth_browser,
                browser_auth_profile=config.browser_auth_profile,
            )
            job.record.status = "queued"
            job.record.error = None
            job.record.completed_at = None
            if strategy == "restart":
                job.record.revision += 1
                job.record.final_output = ""
                job.record.output_files = []
            job.cancel_requested.clear()
            self._write_summary(job)

        self._publish(
            job,
            "job_restart_requested" if strategy == "restart" else "job_resume_requested",
            {"job_id": job_id, "revision": job.record.revision},
        )
        job.request = request
        job.config = config
        job.resume_requested = strategy == "resume"
        self._start_next_job()
        return job.record.model_dump()

    def delete_job(self, job_id: str, owner_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or (owner_id is not None and job.record.owner_id != owner_id):
                raise KeyError(job_id)
            if job.record.status in {"queued", "running"}:
                raise RuntimeError("Queued or running jobs cannot be deleted. Stop the job first.")
            self._jobs.pop(job_id, None)

        shutil.rmtree(job.job_dir, ignore_errors=False)
        return {"job_id": job_id, "deleted": True}

    def list_events(self, job_id: str, after_sequence: int = 0, owner_id: str | None = None) -> list[dict[str, Any]]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        return job.bus.list_from(after_sequence=after_sequence)

    def events_log_text(self, job_id: str, owner_id: str | None = None) -> str:
        return format_events_as_log(self.list_events(job_id, owner_id=owner_id))

    def list_messages(self, job_id: str, owner_id: str | None = None) -> list[dict[str, Any]]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        return job.steering_store.list_messages()

    def add_message(self, job_id: str, content: str, owner_id: str | None = None) -> dict[str, Any]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        if job.record.status in {"failed", "cancelled"}:
            raise RuntimeError("Failed or cancelled jobs do not accept guidance.")

        classification = classify_guidance(content)
        restart_completed = job.record.status == "completed"
        if restart_completed:
            with self._lock:
                running = [
                    item.record.job_id
                    for item in self._jobs.values()
                    if item.record.status in {"queued", "running"}
                ]
                if running:
                    raise RuntimeError(f"Another job is already running: {running[0]}.")
                job.record.revision += 1
                job.record.status = "queued"
                job.record.completed_at = None
                job.record.error = None
                job.record.steering_status = "pending"
                job.cancel_requested.clear()
                self._write_summary(job)

        message = job.steering_store.append_message(content, job.record.revision)
        with job.lock:
            if job.record.steering_status != "waiting_user":
                job.record.steering_status = "pending"
            self._write_summary(job)
        self._publish(
            job,
            "guidance_received",
            {
                "message_id": message["message_id"],
                "sequence": message["sequence"],
                "content": message["content"],
                "revision": job.record.revision,
                **classification,
            },
        )

        if restart_completed:
            config = self.config_store.load()
            request = JobRequest(
                task=job.record.task,
                mode=job.record.mode,
                profile=job.record.profile,
                enable_phase2_research=job.record.enable_phase2_research,
                enable_plan_review=job.record.enable_plan_review,
                direct_phase3_execution=job.record.direct_phase3_execution,
                prefer_local_materials=job.record.prefer_local_materials,
                target_duration_seconds=job.record.target_duration_seconds,
                deadline_seconds=job.record.deadline_seconds,
                processing_mode=job.record.processing_mode,
                output_profile=job.record.output_profile,
                enabled_material_platforms=job.record.enabled_material_platforms,
                browser_auth_browser=job.record.browser_auth_browser,
                browser_auth_profile=job.record.browser_auth_profile,
            )
            self._publish(
                job,
                "revision_started",
                {"revision": job.record.revision, "trigger_message_id": message["message_id"]},
            )
            worker = threading.Thread(
                target=self._run_job,
                args=(job, request, config, True),
                name=f"job-revision-{job_id}-{job.record.revision}",
                daemon=True,
            )
            job.thread = worker
            worker.start()
        return message

    def pause_job(self, job_id: str, mode: str = "next_safe_point", owner_id: str | None = None) -> dict[str, Any]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        if job.record.status != "running":
            raise RuntimeError("Only running jobs can be paused.")
        control = job.steering_store.request_pause(mode)
        with job.lock:
            job.record.steering_status = "pending"
            self._write_summary(job)
        self._publish(job, "pause_requested", control)
        return control

    def approve_job(self, job_id: str, token: str, owner_id: str | None = None) -> dict[str, Any]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        control = job.steering_store.approve(token)
        self._publish(
            job,
            "steering_approval_received",
            {"pause_token": token, "revision": job.record.revision},
        )
        return control

    def wait_for_events(self, job_id: str, after_sequence: int = 0, timeout: float = 1.0, owner_id: str | None = None) -> list[dict[str, Any]]:
        job = self.get_job(job_id, owner_id)
        if job is None:
            raise KeyError(job_id)
        return job.bus.wait_for_events(after_sequence=after_sequence, timeout=timeout)

    def _run_job(
        self,
        job: ManagedJob,
        request: JobRequest,
        config: AppConfig,
        resume: bool = False,
    ) -> None:
        try:
            if request.mode == "demo":
                self._run_demo_job(job, request)
            else:
                self._run_agent_job(job, request, config, resume=resume)
        except Exception as exc:
            # 停机竞态：SIGTERM 优雅停机已把 running 任务落盘为 interrupted，
            # worker 因进程退出拿到的异常不得覆盖为 failed（否则用户无法 resume）。
            if self._shutting_down.is_set() and job.record.status == "interrupted":
                pass
            else:
                self._mark_failed(job, str(exc))
        finally:
            if job.has_ephemeral_credentials:
                (job.job_dir / "runtime_profile.json").unlink(missing_ok=True)
            self._start_next_job()

    def _start_next_job(self) -> None:
        """Run exactly one public-trial job at a time in creation order."""
        with self._lock:
            if any(job.record.status == "running" for job in self._jobs.values()):
                return
            queued = sorted(
                (job for job in self._jobs.values() if job.record.status == "queued" and job.request and job.config),
                key=lambda job: job.record.created_at,
            )
            if not queued:
                return
            job = queued[0]
            request = job.request
            config = job.config
            resume = job.resume_requested
            job.resume_requested = False
            self._mark_running(job)
            worker = threading.Thread(
                target=self._run_job,
                args=(job, request, config, resume),
                name=f"job-{job.record.job_id}",
                daemon=True,
            )
            job.thread = worker
            worker.start()

    def _run_demo_job(self, job: ManagedJob, request: JobRequest) -> None:
        steering = SteeringCoordinator(
            workspace=job.job_dir / "workspace",
            steering_dir=job.steering_dir,
            revision=job.record.revision,
            event_sink=lambda event_type, payload: self._publish(job, event_type, payload),
        )
        if job.record.direct_phase3_execution:
            phase_steps = [
                ("phase1", "executor", "复用现有本地素材并补齐多模态分析", "analyze_video", "已完成本地素材分析，直接进入创作阶段"),
                ("phase3", "react_editor", "裁剪、合并并添加旁白", "add_narration_segments", "已完成转场与分段配音"),
            ]
        elif job.record.prefer_local_materials:
            phase_steps = [
                ("phase1", "executor", "优先分析本地素材并评估覆盖度", "analyze_video", "已完成本地素材分析，发现主体内容已具备"),
                ("phase1", "planner", "仅在本地素材不足时补充搜索", "search_bilibili_video", "已检索到少量补充素材"),
                ("phase1", "executor", "筛选最合适的补充素材", "rank_video_candidates", "已筛出 3 条高匹配补充候选"),
                ("phase3", "react_editor", "裁剪、合并并添加旁白", "add_narration_segments", "已完成转场与分段配音"),
            ]
        else:
            phase_steps = [
                ("phase1", "planner", "拆解任务并估算素材需求", "search_bilibili_video", "已整理出 4 个素材检索方向"),
                ("phase1", "executor", "筛选最合适的候选素材", "rank_video_candidates", "已筛出 6 条高匹配候选"),
                ("phase3", "react_editor", "裁剪、合并并添加旁白", "add_narration_segments", "已完成转场与分段配音"),
            ]
        if job.record.enable_phase2_research and not job.record.direct_phase3_execution:
            phase_steps.insert(
                2,
                ("phase2", "editing_research", "生成剪辑蓝图", "", "蓝图已生成，包含 5 段叙事结构"),
            )
        seen_phases: set[str] = set()

        for index, (phase_id, node_name, summary, tool_name, result_text) in enumerate(phase_steps, start=1):
            if job.cancel_requested.is_set():
                self._mark_cancelled(job)
                return
            steering.apply_pending(f"demo_step_{index}", phase_id)
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
        artifact_path = output_dir / f"demo_final_summary_r{job.record.revision:03d}.txt"
        artifact_path.write_text(
            f"Demo job completed for task:\n{request.task}\n",
            encoding="utf-8",
        )
        self._publish(job, "artifact_created", {"path": str(artifact_path), "kind": "demo_output"})
        self._mark_completed(
            job,
            final_output="Demo job completed. Backend service, event bus, and job persistence are working.",
            output_files=[
                *[path for path in job.record.output_files if path != str(artifact_path)],
                str(artifact_path),
            ],
        )

    def _run_agent_job(
        self,
        job: ManagedJob,
        request: JobRequest,
        config: AppConfig,
        *,
        resume: bool = False,
    ) -> None:
        configure_runtime_environment()
        bundle_root = get_bundle_root()
        runtime_root = get_runtime_root()
        profile = config.get_profile(request.profile)
        if not profile.api_key:
            raise RuntimeError(
                "The selected profile does not have an API key. Update runtime .env or call PUT /config first."
            )

        config_path = job.job_dir / "runtime_profile.json"
        task_path = job.job_dir / "task.txt"
        runtime_config = profile.to_runtime_config()
        runtime_config["enable_phase2_research"] = job.record.enable_phase2_research
        runtime_config["enable_plan_review"] = job.record.enable_plan_review
        runtime_config["direct_phase3_execution"] = job.record.direct_phase3_execution
        runtime_config["prefer_local_materials"] = job.record.prefer_local_materials
        runtime_config["search_pool_size"] = config.search_pool_size
        runtime_config["download_pool_size"] = config.download_pool_size
        runtime_config["video_analysis_pool_size"] = config.video_analysis_pool_size
        runtime_config["llm_pool_size"] = config.llm_pool_size
        runtime_config["ffmpeg_pool_size"] = config.ffmpeg_pool_size
        runtime_config["tts_pool_size"] = config.tts_pool_size
        runtime_config["export_pool_size"] = config.export_pool_size
        runtime_config["short_form_optimizations"] = config.short_form_optimizations
        runtime_config["short_form_max_sources"] = config.short_form_max_sources
        runtime_config["video_analysis_proxy_max_seconds"] = config.video_analysis_proxy_max_seconds
        runtime_config["download_max_height"] = config.download_max_height
        runtime_config["standardize_target_fps"] = config.standardize_target_fps
        runtime_config["audio_loudnorm_target"] = config.audio_loudnorm_target
        runtime_config["post_task_review_mode"] = config.post_task_review_mode
        runtime_config["agent_stall_timeout_seconds"] = job.stall_timeout_seconds
        runtime_config["youtube_mode"] = config.youtube_mode
        runtime_config["default_deadline_seconds"] = job.record.deadline_seconds
        runtime_config["processing_mode"] = job.record.processing_mode
        runtime_config["phase1_max_seconds"] = config.phase1_max_seconds
        runtime_config["output_profile"] = job.record.output_profile
        runtime_config["enabled_material_platforms"] = job.record.enabled_material_platforms
        runtime_config["browser_auth_browser"] = job.record.browser_auth_browser
        runtime_config["browser_auth_profile"] = job.record.browser_auth_profile
        runtime_config["target_duration_seconds"] = job.record.target_duration_seconds
        runtime_config["resume_execution"] = resume
        runtime_config["revision"] = job.record.revision
        config_path.write_text(
            json.dumps(runtime_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        task_path.write_text(request.task, encoding="utf-8")

        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
        child_env["CRAYOTTER_RUNTIME_ROOT"] = str(runtime_root)
        child_env["CRAYOTTER_BUNDLE_ROOT"] = str(bundle_root)
        child_env["CRAYOTTER_TASK_WORKSPACE"] = str(job.job_dir / "workspace")
        child_env["CRAYOTTER_USER_WORKSPACE"] = str(job.materials_dir or (runtime_root / "user_temp"))
        child_env["CRAYOTTER_PERSIST_WORKSPACE"] = "true"
        child_env["CRAYOTTER_JOB_ID"] = job.record.job_id
        child_env["CRAYOTTER_REVISION"] = str(job.record.revision)
        child_env["CRAYOTTER_STEERING_DIR"] = str(job.steering_dir)

        if is_frozen():
            command = [
                sys.executable,
                "--crayotter-worker",
                "--task-file",
                str(task_path),
                "--config-file",
                str(config_path),
            ]
        else:
            worker_script = bundle_root / "script" / "run_agent_worker.py"
            command = [
                sys.executable,
                str(worker_script),
                "--task-file",
                str(task_path),
                "--config-file",
                str(config_path),
            ]

        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        process = subprocess.Popen(
            command,
            cwd=str(runtime_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=child_env,
            creationflags=creation_flags,
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
                if job.record.status not in TERMINAL_JOB_STATUSES:
                    self._mark_completed(
                        job,
                        final_output=final_output,
                        output_files=output_files,
                    )
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
            if job.record.status not in TERMINAL_JOB_STATUSES:
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
            if idle_seconds < job.stall_timeout_seconds:
                time.sleep(2)
                continue

            timeout_seconds = job.stall_timeout_seconds
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
            job.record.steering_status = "idle"
            self._write_summary(job)
        self._publish(
            job,
            "job_completed",
            {
                "completed_at": job.record.completed_at,
                "final_output": final_output,
                "output_files": output_files,
                "revision": job.record.revision,
            },
        )
        self._publish(job, "revision_completed", {"revision": job.record.revision})

    def _mark_failed(self, job: ManagedJob, error_message: str) -> None:
        with job.lock:
            if job.record.status in TERMINAL_JOB_STATUSES:
                return
            # 停机中的 worker 异常（如 agent 子进程同收 SIGTERM）不得覆盖为 failed，
            # 保持 interrupted 以便用户 resume；shutdown() 会统一落盘。
            if self._shutting_down.is_set():
                job.record.status = "interrupted"
                job.record.completed_at = None
                job.record.error = job.record.error or "Backend stopped before the task finished."
                self._write_summary(job)
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
            if event_type in {"guidance_received", "guidance_deferred", "pause_requested"}:
                if job.record.steering_status != "waiting_user":
                    job.record.steering_status = "pending"
            elif event_type in {"guidance_applying", "guidance_classified"}:
                job.record.steering_status = "applying"
            elif event_type == "steering_replan_started":
                job.record.steering_status = "replanning"
            elif event_type == "steering_waiting_user":
                job.record.steering_status = "waiting_user"
            elif event_type == "material_source_authorization_required":
                job.record.steering_status = "waiting_user"
            elif event_type in {
                "guidance_applied",
                "guidance_unsupported",
                "steering_approved",
                "steering_replan_completed",
            }:
                job.record.steering_status = "idle"
            if "processing_elapsed_seconds" in payload:
                job.record.processing_elapsed_seconds = float(
                    payload.get("processing_elapsed_seconds") or 0.0
                )
            if "authorization_wait_seconds" in payload:
                job.record.authorization_wait_seconds = float(
                    payload.get("authorization_wait_seconds") or 0.0
                )
            if "total_wall_seconds" in payload:
                job.record.total_wall_seconds = float(payload.get("total_wall_seconds") or 0.0)
            if "degradation_level" in payload:
                job.record.degradation_level = max(
                    0, min(4, int(payload.get("degradation_level") or 0))
                )
            if event_type == "sla_completed":
                job.record.sla_status = "completed"
            elif event_type == "sla_missed":
                job.record.sla_status = "missed"
            if payload.get("checkpoint"):
                job.record.current_checkpoint = str(payload["checkpoint"])
            job.record.events_count = stored["sequence"]
            job.last_activity_monotonic = time.monotonic()
            self._append_event(job.events_path, stored)
            self._write_summary(job)
        return stored

    @staticmethod
    def _plan_store(job: ManagedJob) -> EditingPlanStore:
        return EditingPlanStore(job.job_dir / "workspace")

    def _generate_plan_patch(
        self,
        job: ManagedJob,
        plan_payload: dict[str, Any],
        feedback: str,
    ) -> PlanPatch:
        plan_version = str(plan_payload.get("version") or "v001")
        config_path = job.job_dir / "runtime_profile.json"
        try:
            runtime_config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            runtime_config = {}
        api_key = str(runtime_config.get("api_key") or "").strip()
        base_url = str(runtime_config.get("base_url") or "").strip()
        model_name = str(runtime_config.get("model_name") or "qwen-plus").strip() or "qwen-plus"
        if not api_key:
            current = self._plan_store(job).get_plan(plan_version)
            if current is None:
                raise ValueError("Current plan was not found.")
            return heuristic_patch_from_feedback(current, feedback)

        prompt = (
            "你是剪辑计划编辑 Agent。只返回 JSON，不要 Markdown。"
            "你必须把用户自然语言反馈转成 PlanPatch。"
            "允许的 operation.op 只有 update_global、update_scene、delete_scene、add_scene、reorder_scenes。"
            "update_global 的 field 只能是 target_duration_seconds、aspect_ratio、style、pacing、"
            "narration_strategy、subtitle_strategy、bgm_strategy。"
            "update_scene 的 field 只能是 start、end、narrative_purpose、source_path、source_start、"
            "source_end、crop、transition、subtitle、narration、alternatives、locked。"
            "替换素材只能使用原计划 source_video_paths 中存在的路径。"
            "不要删除 locked=true 的分镜。输出结构："
            '{"base_version":"","feedback":"","summary":"","operations":[{"op":"update_scene","scene_id":"","field":"","value":null}]}'
        )
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=base_url or None)
            response = client.chat.completions.create(
                model=model_name,
                temperature=0.1,
                max_tokens=2200,
                messages=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"plan": plan_payload, "feedback": feedback},
                            ensure_ascii=False,
                        )[:50000],
                    },
                ],
            )
            content = response.choices[0].message.content or ""
            parsed = self._parse_json_object(content)
            parsed["base_version"] = plan_version
            parsed["feedback"] = feedback
            return PlanPatch.model_validate(parsed)
        except Exception as exc:
            self._publish(
                job,
                "plan_patch_fallback",
                {"version": plan_version, "reason": str(exc)[:300]},
            )
            current = self._plan_store(job).get_plan(plan_version)
            if current is None:
                raise ValueError("Current plan was not found.") from exc
            return heuristic_patch_from_feedback(current, feedback)

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any]:
        text = str(content or "").strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        return json.loads(text)

    @staticmethod
    def _append_event(path: Path, event: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    @staticmethod
    def _write_summary(job: ManagedJob) -> None:
        payload = job.record.model_dump()
        # owner_id 在 JobRecord 上 exclude=True（不进 API 响应），但必须持久化，
        # 否则后端重启后 _load_existing_jobs 恢复出的 record.owner_id 为空，
        # 所有者的任务历史为空且 interrupted 任务无法 resume。
        payload["owner_id"] = job.record.owner_id
        payload["job_dir"] = str(job.job_dir)
        job.summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _new_job_id() -> str:
        return f"job_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    @staticmethod
    def _artifact_kind(suffix: str) -> str:
        if suffix in RuntimeManager.VIDEO_SUFFIXES:
            return "video"
        if suffix in RuntimeManager.TEXT_SUFFIXES:
            return "text"
        return "file"

    @classmethod
    def _video_duration_seconds(cls, path: Path, stat: os.stat_result) -> float | None:
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        with cls._media_metadata_lock:
            if key in cls._media_metadata_cache:
                return cls._media_metadata_cache[key]

        try:
            parsed = video_duration_seconds(path)
            duration = round(parsed, 2) if parsed is not None else None
        except (OSError, TypeError, ValueError):
            duration = None

        with cls._media_metadata_lock:
            stale_keys = [cached for cached in cls._media_metadata_cache if cached[0] == str(path)]
            for stale_key in stale_keys:
                cls._media_metadata_cache.pop(stale_key, None)
            cls._media_metadata_cache[key] = duration
        return duration

    @classmethod
    def _collect_artifacts(cls, job: ManagedJob) -> list[dict[str, Any]]:
        runtime_root = get_runtime_root()
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        registry_metadata: dict[str, dict[str, Any]] = {}

        candidate_paths: list[Path] = []
        for path_str in job.record.output_files:
            if path_str:
                candidate_paths.append(Path(path_str))
        output_dir = job.job_dir / "output"
        if output_dir.exists():
            for path in sorted(output_dir.rglob("*")):
                if path.is_file():
                    candidate_paths.append(path)
        manifest_path = job.job_dir / "workspace" / ".crayotter" / "artifact_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for artifact in manifest.get("artifacts", []):
                    raw_path = str(artifact.get("path") or "")
                    if not raw_path:
                        continue
                    registry_metadata[str(Path(raw_path).resolve(strict=False))] = dict(artifact)
                    candidate_paths.append(Path(raw_path))
            except Exception:
                pass

        for path in candidate_paths:
            try:
                resolved = path.resolve(strict=False)
            except Exception:
                resolved = path
            key = str(resolved)
            registry_item = registry_metadata.get(key, {})
            if key in seen:
                continue
            seen.add(key)
            exists = resolved.exists() and resolved.is_file()
            if not exists:
                if registry_item:
                    metadata = dict(registry_item.get("metadata", {}))
                    artifact_revision = int(metadata.get("revision", 1) or 1)
                    metadata["revision"] = artifact_revision
                    metadata["current"] = artifact_revision == job.record.revision
                    results.append(
                        {
                            "path": str(resolved),
                            "display_path": str(resolved),
                            "name": resolved.name,
                            "suffix": resolved.suffix.lower(),
                            "kind": registry_item.get("kind", "file"),
                            "size_bytes": 0,
                            "duration_seconds": None,
                            "artifact_id": registry_item.get("id", ""),
                            "producer_task_id": registry_item.get("producer_task_id", ""),
                            "phase": registry_item.get("phase", ""),
                            "valid": False,
                            "metadata": metadata,
                            "revision": artifact_revision,
                            "is_current": metadata["current"],
                        }
                    )
                continue
            try:
                relative = resolved.relative_to(runtime_root)
                display_path = str(relative)
            except Exception:
                display_path = str(resolved)
            suffix = resolved.suffix.lower()
            stat = resolved.stat()
            kind = str(registry_item.get("kind") or cls._artifact_kind(suffix))
            metadata = dict(registry_item.get("metadata", {}))
            artifact_revision = int(metadata.get("revision", 1) or 1)
            metadata["revision"] = artifact_revision
            metadata["current"] = artifact_revision == job.record.revision
            results.append(
                {
                    "path": str(resolved),
                    "display_path": display_path,
                    "name": resolved.name,
                    "suffix": suffix,
                    "kind": kind,
                    "size_bytes": stat.st_size,
                    "duration_seconds": (
                        cls._video_duration_seconds(resolved, stat)
                        if suffix in cls.VIDEO_SUFFIXES
                        else None
                    ),
                    "artifact_id": registry_item.get("id", ""),
                    "producer_task_id": registry_item.get("producer_task_id", ""),
                    "phase": registry_item.get("phase", ""),
                    "valid": bool(registry_item.get("valid", True)),
                    "metadata": metadata,
                    "revision": artifact_revision,
                    "is_current": metadata["current"],
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
                if record.status not in TERMINAL_JOB_STATUSES and record.status != "interrupted":
                    record.status = "interrupted"
                    record.completed_at = None
                    record.error = record.error or "Backend restarted before the task finished."
                    self._write_summary(job)
                self._jobs[record.job_id] = job
            except Exception:
                continue
