"""RuntimeManager 任务目录租户隔离单元测试。

覆盖：
- _job_dir_for：有属主 → JOBS_DIR/<owner>/<job>；无属主 → 扁平（本地单机模式不变）
- _load_existing_jobs：同时恢复旧版扁平目录与新属主子目录两种布局
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backend import runtime_manager
from app.backend.models import JobRecord
from app.backend.runtime_manager import RuntimeManager


def _summary_payload(job_id: str, owner_id: str, job_dir: Path) -> dict:
    record = JobRecord(job_id=job_id, owner_id=owner_id, task="tenant dir test", mode="demo", job_dir=str(job_dir))
    payload = record.model_dump()
    payload["owner_id"] = owner_id  # 与 _write_summary 一致：显式补写 excluded 字段
    return payload


class JobDirForTests(unittest.TestCase):
    def test_owner_jobs_nested_under_owner_dir(self) -> None:
        path = RuntimeManager._job_dir_for("ownerToken123", "job_x")
        self.assertEqual(path, runtime_manager.JOBS_DIR / "ownerToken123" / "job_x")

    def test_anonymous_jobs_stay_flat(self) -> None:
        path = RuntimeManager._job_dir_for("", "job_x")
        self.assertEqual(path, runtime_manager.JOBS_DIR / "job_x")


class LoadExistingJobsLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tenant-load-test-"))
        self.orig_jobs_dir = runtime_manager.JOBS_DIR
        runtime_manager.JOBS_DIR = self.tmp
        self.manager = RuntimeManager.__new__(RuntimeManager)
        self.manager._jobs = {}
        self.manager._lock = threading.RLock()

    def tearDown(self) -> None:
        runtime_manager.JOBS_DIR = self.orig_jobs_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_job(self, job_dir: Path, job_id: str, owner_id: str) -> None:
        job_dir.mkdir(parents=True)
        (job_dir / "summary.json").write_text(
            json.dumps(_summary_payload(job_id, owner_id, job_dir)), encoding="utf-8"
        )

    def test_loads_both_flat_and_owner_nested_layouts(self) -> None:
        # 旧版扁平布局
        self._write_job(self.tmp / "job_legacy", "job_legacy", "ownerA")
        # 新属主子目录布局
        self._write_job(self.tmp / "ownerA" / "job_nested", "job_nested", "ownerA")
        self._write_job(self.tmp / "ownerB" / "job_other", "job_other", "ownerB")
        # 无关目录（无 summary.json）应被跳过
        (self.tmp / "random_dir").mkdir()

        self.manager._load_existing_jobs()

        self.assertEqual(sorted(self.manager._jobs), ["job_legacy", "job_nested", "job_other"])
        self.assertEqual(self.manager._jobs["job_legacy"].record.owner_id, "ownerA")
        self.assertEqual(self.manager._jobs["job_nested"].job_dir, self.tmp / "ownerA" / "job_nested")
        self.assertEqual(self.manager._jobs["job_other"].record.owner_id, "ownerB")


if __name__ == "__main__":
    unittest.main()
