"""failed/interrupted 任务恢复（resume/restart 策略）冲突与状态单测。

桩式构造 RuntimeManager，验证策略校验、状态门槛、owner 隔离、并发占用冲突。
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backend.runtime_manager import RuntimeManager


def _record(job_id: str, status: str, owner: str = "owner-a") -> SimpleNamespace:
    record = SimpleNamespace(
        job_id=job_id,
        owner_id=owner,
        task="测试任务",
        mode="agent",
        profile="default",
        enable_phase2_research=True,
        enable_plan_review=True,
        direct_phase3_execution=False,
        prefer_local_materials=False,
        target_duration_seconds=None,
        deadline_seconds=600,
        processing_mode="auto",
        output_profile="auto",
        enabled_material_platforms=["bilibili"],
        browser_auth_browser="",
        browser_auth_profile="",
        status=status,
        revision=1,
        error="boom" if status == "failed" else None,
        completed_at="2026-08-04T00:00:00+00:00",
        final_output="out.mp4" if status == "failed" else "",
        output_files=["out.mp4"] if status == "failed" else [],
    )
    record.model_dump = lambda: {"job_id": record.job_id, "status": record.status}
    return record


def _manager(*jobs: SimpleNamespace) -> RuntimeManager:
    manager = RuntimeManager.__new__(RuntimeManager)
    manager._lock = threading.RLock()
    manager._jobs = {
        r.job_id: SimpleNamespace(
            record=r,
            cancel_requested=threading.Event(),
            request=None,
            config=None,
            resume_requested=False,
        )
        for r in jobs
    }
    manager.config_store = SimpleNamespace(
        load=lambda: SimpleNamespace(browser_auth_browser="", browser_auth_profile="")
    )
    manager._write_summary = lambda job: None
    manager._publish = lambda *args, **kwargs: None
    manager._start_next_job = lambda: None
    return manager


class ResumeStrategyTests(unittest.TestCase):
    def test_failed_job_resume_from_checkpoint(self) -> None:
        manager = _manager(_record("j1", "failed"))
        result = manager.resume_job("j1", "owner-a", strategy="resume")
        job = manager._jobs["j1"]
        self.assertEqual(result["status"], "queued")
        self.assertTrue(job.resume_requested)
        self.assertEqual(job.record.revision, 1, "断点续跑不应增加 revision")
        self.assertIsNone(job.record.error)

    def test_failed_job_restart_bumps_revision_and_clears_outputs(self) -> None:
        manager = _manager(_record("j1", "failed"))
        manager.resume_job("j1", "owner-a", strategy="restart")
        job = manager._jobs["j1"]
        self.assertFalse(job.resume_requested, "重新开始不应走 checkpoint")
        self.assertEqual(job.record.revision, 2)
        self.assertEqual(job.record.final_output, "")
        self.assertEqual(job.record.output_files, [])

    def test_interrupted_job_can_resume(self) -> None:
        manager = _manager(_record("j1", "interrupted"))
        result = manager.resume_job("j1", "owner-a")
        self.assertEqual(result["status"], "queued")
        self.assertTrue(manager._jobs["j1"].resume_requested)

    def test_unknown_strategy_rejected(self) -> None:
        manager = _manager(_record("j1", "failed"))
        with self.assertRaises(ValueError):
            manager.resume_job("j1", "owner-a", strategy="sideways")

    def test_completed_job_cannot_resume(self) -> None:
        manager = _manager(_record("j1", "completed"))
        with self.assertRaises(RuntimeError):
            manager.resume_job("j1", "owner-a")

    def test_cancelled_job_cannot_resume(self) -> None:
        manager = _manager(_record("j1", "cancelled"))
        with self.assertRaises(RuntimeError):
            manager.resume_job("j1", "owner-a")

    def test_conflict_when_another_job_running(self) -> None:
        manager = _manager(_record("j1", "failed"), _record("j2", "running"))
        with self.assertRaisesRegex(RuntimeError, "Another job"):
            manager.resume_job("j1", "owner-a", strategy="restart")

    def test_owner_isolation(self) -> None:
        manager = _manager(_record("j1", "failed", owner="owner-a"))
        with self.assertRaises(KeyError):
            manager.resume_job("j1", "owner-b", strategy="restart")


if __name__ == "__main__":
    unittest.main()
