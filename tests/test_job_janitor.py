"""任务产物保留 janitor（RuntimeManager.sweep_expired_jobs）单元测试。

不依赖 ConfigStore/JOBS_DIR：通过 __new__ 构造 manager 并注入桩任务。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backend.runtime_manager import RuntimeManager


def _make_manager(retention_days: float) -> RuntimeManager:
    manager = RuntimeManager.__new__(RuntimeManager)
    manager._jobs = {}
    manager._lock = threading.RLock()
    manager._janitor_stop = threading.Event()
    manager._janitor_thread = None
    manager.JOB_RETENTION_DAYS = retention_days
    return manager


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class SweepExpiredJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="janitor-test-"))
        self.now = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
        self.manager = _make_manager(7)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add_job(self, job_id: str, status: str, *, created_days: float, completed_days: float | None = None) -> Path:
        job_dir = self.tmp / job_id
        job_dir.mkdir(parents=True)
        (job_dir / "payload.bin").write_text("x" * 64)
        record = SimpleNamespace(
            status=status,
            created_at=_iso(self.now - timedelta(days=created_days)),
            completed_at=_iso(self.now - timedelta(days=completed_days)) if completed_days is not None else None,
        )
        self.manager._jobs[job_id] = SimpleNamespace(record=record, job_dir=job_dir)
        return job_dir

    def test_terminal_and_interrupted_older_than_retention_are_removed(self) -> None:
        for job_id, status in [("j1", "completed"), ("j2", "failed"), ("j3", "cancelled"), ("j4", "interrupted")]:
            self._add_job(job_id, status, created_days=30, completed_days=10)
        removed = self.manager.sweep_expired_jobs(now=self.now)
        self.assertEqual(sorted(removed), ["j1", "j2", "j3", "j4"])
        self.assertEqual(self.manager._jobs, {})
        self.assertEqual([p for p in self.tmp.iterdir()], [])

    def test_recent_terminal_jobs_are_kept(self) -> None:
        kept = self._add_job("recent", "completed", created_days=2, completed_days=1)
        removed = self.manager.sweep_expired_jobs(now=self.now)
        self.assertEqual(removed, [])
        self.assertIn("recent", self.manager._jobs)
        self.assertTrue(kept.is_dir())

    def test_running_and_queued_never_removed(self) -> None:
        for job_id, status in [("run", "running"), ("queue", "queued")]:
            self._add_job(job_id, status, created_days=90)
        removed = self.manager.sweep_expired_jobs(now=self.now)
        self.assertEqual(removed, [])
        self.assertEqual(sorted(self.manager._jobs), ["queue", "run"])

    def test_unparseable_timestamp_is_kept(self) -> None:
        job_dir = self.tmp / "broken"
        job_dir.mkdir()
        self.manager._jobs["broken"] = SimpleNamespace(
            record=SimpleNamespace(status="completed", created_at="not-a-date", completed_at=None),
            job_dir=job_dir,
        )
        removed = self.manager.sweep_expired_jobs(now=self.now)
        self.assertEqual(removed, [])
        self.assertTrue(job_dir.is_dir())

    def test_completed_at_takes_priority_over_created_at(self) -> None:
        # created_at 很新但 completed_at 已过保留期：按完成时间清扫
        self._add_job("stale-done", "completed", created_days=1, completed_days=30)
        removed = self.manager.sweep_expired_jobs(now=self.now)
        self.assertEqual(removed, ["stale-done"])

    def test_naive_timestamp_treated_as_utc(self) -> None:
        job_dir = self.tmp / "naive"
        job_dir.mkdir()
        naive = (self.now - timedelta(days=30)).replace(tzinfo=None)
        self.manager._jobs["naive"] = SimpleNamespace(
            record=SimpleNamespace(status="failed", created_at=naive.isoformat(), completed_at=None),
            job_dir=job_dir,
        )
        removed = self.manager.sweep_expired_jobs(now=self.now)
        self.assertEqual(removed, ["naive"])

    def test_retention_disabled_is_noop(self) -> None:
        self.manager.JOB_RETENTION_DAYS = 0
        self._add_job("old", "completed", created_days=365, completed_days=365)
        self.assertEqual(self.manager.sweep_expired_jobs(now=self.now), [])
        self.assertIn("old", self.manager._jobs)


if __name__ == "__main__":
    unittest.main()
