"""磁盘水位 LRU（RuntimeManager.evict_lru_jobs）单元测试。

不依赖 ConfigStore/JOBS_DIR：__new__ 构造 manager 并注入桩任务与假 usage_fn。
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


def _make_manager(threshold: float = 70, target: float = 60) -> RuntimeManager:
    manager = RuntimeManager.__new__(RuntimeManager)
    manager._jobs = {}
    manager._lock = threading.RLock()
    manager.DISK_LRU_THRESHOLD_PERCENT = threshold
    manager.DISK_LRU_TARGET_PERCENT = target
    return manager


class FakeUsage:
    """模拟分区使用率：每次删除回调后按 step 递减，验证清到目标水位即停。"""

    def __init__(self, percent: float, step: float = 0.0) -> None:
        self.percent = percent
        self.step = step

    def __call__(self, _path: str) -> SimpleNamespace:
        current = self.percent
        self.percent -= self.step
        return SimpleNamespace(used=current, total=100, free=100 - current)


class EvictLruJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="disk-lru-test-"))
        self.now = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)
        self.manager = _make_manager()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add_job(self, job_id: str, status: str, *, days: float) -> Path:
        job_dir = self.tmp / job_id
        job_dir.mkdir(parents=True)
        (job_dir / "payload.bin").write_text("x" * 64)
        record = SimpleNamespace(
            status=status,
            created_at=(self.now - timedelta(days=days)).isoformat(),
            completed_at=(self.now - timedelta(days=days)).isoformat(),
        )
        self.manager._jobs[job_id] = SimpleNamespace(record=record, job_dir=job_dir)
        return job_dir

    def test_below_threshold_is_noop(self) -> None:
        self._add_job("j1", "completed", days=30)
        removed = self.manager.evict_lru_jobs(usage_fn=FakeUsage(69.9))
        self.assertEqual(removed, [])
        self.assertIn("j1", self.manager._jobs)

    def test_disabled_is_noop(self) -> None:
        self.manager.DISK_LRU_THRESHOLD_PERCENT = 0
        self._add_job("j1", "completed", days=30)
        removed = self.manager.evict_lru_jobs(usage_fn=FakeUsage(99))
        self.assertEqual(removed, [])

    def test_oldest_terminal_evicted_first_until_target(self) -> None:
        for job_id, days in [("old", 30), ("mid", 20), ("new", 5)]:
            self._add_job(job_id, "completed", days=days)
        # usage_fn 每次调用递减 8：79(初始判定>70) → 71(>60 清 old) → 63(>60 清 mid) → 55(≤60 停)
        usage = FakeUsage(79, step=8)
        removed = self.manager.evict_lru_jobs(usage_fn=usage)
        self.assertEqual(removed, ["old", "mid"])
        self.assertIn("new", self.manager._jobs)
        self.assertTrue((self.tmp / "new").is_dir())
        self.assertFalse((self.tmp / "old").exists())

    def test_running_queued_never_evicted_and_bad_timestamp_kept(self) -> None:
        for job_id, status in [("run", "running"), ("queue", "queued")]:
            self._add_job(job_id, status, days=90)
        self.manager._jobs["broken"] = SimpleNamespace(
            record=SimpleNamespace(status="completed", created_at="bad", completed_at=None),
            job_dir=self.tmp / "broken",
        )
        removed = self.manager.evict_lru_jobs(usage_fn=FakeUsage(99))
        self.assertEqual(removed, [])
        self.assertEqual(sorted(self.manager._jobs), ["broken", "queue", "run"])

    def test_interrupted_evicted_only_after_terminals(self) -> None:
        self._add_job("done", "completed", days=1)
        self._add_job("paused", "interrupted", days=60)
        # 90 → 85(清 done，终态优先) → 80(清 paused) → 候选耗尽
        usage = FakeUsage(90, step=5)
        removed = self.manager.evict_lru_jobs(usage_fn=usage)
        self.assertEqual(removed, ["done", "paused"])

    def test_terminal_only_when_enough(self) -> None:
        self._add_job("done", "completed", days=1)
        self._add_job("paused", "interrupted", days=60)
        # 71 → 65(清 done) → 59(≤60 停)，interrupted 不被波及
        usage = FakeUsage(71, step=6)
        removed = self.manager.evict_lru_jobs(usage_fn=usage)
        self.assertEqual(removed, ["done"])
        self.assertIn("paused", self.manager._jobs)


if __name__ == "__main__":
    unittest.main()
