"""Persistence boundary for backend job summaries and event history."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..models import JobRecord


@dataclass(frozen=True)
class PersistedJob:
    record: JobRecord
    job_dir: Path
    events: list[dict]


class JobRepository:
    def __init__(self, jobs_dir: Path) -> None:
        self.jobs_dir = jobs_dir

    def save(self, record: JobRecord, job_dir: Path) -> None:
        payload = record.model_dump()
        payload["job_dir"] = str(job_dir)
        (job_dir / "summary.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_all(self) -> list[PersistedJob]:
        if not self.jobs_dir.exists():
            return []
        results: list[PersistedJob] = []
        for job_dir in sorted(self.jobs_dir.iterdir()):
            summary_path = job_dir / "summary.json"
            if not job_dir.is_dir() or not summary_path.exists():
                continue
            try:
                record = JobRecord.model_validate(
                    json.loads(summary_path.read_text(encoding="utf-8"))
                )
                events_path = job_dir / "events.jsonl"
                events = []
                if events_path.exists():
                    events = [
                        json.loads(line)
                        for line in events_path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
                results.append(PersistedJob(record=record, job_dir=job_dir, events=events))
            except Exception:
                continue
        return results
