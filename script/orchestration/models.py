from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=1, ge=1, le=5)
    backoff_seconds: float = Field(default=0.0, ge=0.0, le=60.0)
    retryable_errors: list[str] = Field(
        default_factory=lambda: [
            "timeout",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
            "rate limit",
            "429",
            "502",
            "503",
            "504",
        ]
    )


class TaskSpec(BaseModel):
    id: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    tool_name: str = ""
    description: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    resources: dict[str, int] = Field(default_factory=dict)
    conflict_keys: list[str] = Field(default_factory=list)
    input_artifacts: list[str] = Field(default_factory=list)
    output_kinds: list[str] = Field(default_factory=list)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)

    @field_validator("depends_on", "conflict_keys", "input_artifacts", "output_kinds")
    @classmethod
    def _dedupe_strings(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))

    @field_validator("resources")
    @classmethod
    def _validate_resources(cls, value: dict[str, int]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for name, amount in value.items():
            key = str(name).strip()
            parsed = int(amount)
            if key and parsed > 0:
                normalized[key] = parsed
        return normalized


class ExecutionPlan(BaseModel):
    plan_id: str
    phase: str
    goal: str = ""
    tasks: list[TaskSpec] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class ArtifactRef(BaseModel):
    id: str
    kind: str
    path: str = ""
    producer_task_id: str
    phase: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    checksum: str = ""
    size_bytes: int = 0
    created_at: str = Field(default_factory=utc_now_iso)
    valid: bool = True

    def resolved_path(self) -> Path | None:
        return Path(self.path).resolve(strict=False) if self.path else None


TaskStatus = Literal["pending", "running", "completed", "failed", "skipped"]


class TaskState(BaseModel):
    task_id: str
    task_fingerprint: str = ""
    dependency_fingerprints: dict[str, str] = Field(default_factory=dict)
    status: TaskStatus = "pending"
    attempts: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    error: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list)


class TaskExecutionResult(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactRef] = Field(default_factory=list)


class ResourcePoolConfig(BaseModel):
    search_pool: int = Field(default=4, ge=1)
    download_pool: int = Field(default=3, ge=1)
    video_analysis_pool: int = Field(default=3, ge=1)
    llm_pool: int = Field(default=4, ge=1)
    ffmpeg_pool: int = Field(default=3, ge=1)
    tts_pool: int = Field(default=3, ge=1)
    export_pool: int = Field(default=1, ge=1)

    def as_dict(self) -> dict[str, int]:
        return {name: int(value) for name, value in self.model_dump().items()}
