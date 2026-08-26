from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from script.orchestration.models import utc_now_iso


class DurationExpectation(BaseModel):
    target: float = Field(gt=0)
    tolerance: float = Field(default=1.0, ge=0)


class FaultSpec(BaseModel):
    stage: str = Field(min_length=1)
    effect: Literal[
        "connection_reset",
        "timeout",
        "status_403",
        "status_429",
        "malformed_json",
        "empty_response",
    ]
    occurrence: int = Field(default=1, ge=1)
    repeat: int = Field(default=1, ge=1, le=100)
    message: str = ""


class ScenarioAssertions(BaseModel):
    required_terms: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)
    duration: DurationExpectation | None = None
    required_voice: str = ""
    expected_narration: list[str] = Field(default_factory=list)
    narration_exact: bool = False
    minimum_download_successes: int = Field(default=0, ge=0)
    terminal_status: str = "completed"
    max_provider_retry_seconds: float | None = Field(default=None, ge=0)
    max_wall_seconds: float | None = Field(default=None, ge=0)
    maximum_fallbacks: int | None = Field(default=None, ge=0)

    @field_validator(
        "required_terms", "forbidden_terms", "expected_narration", mode="before"
    )
    @classmethod
    def _coerce_text_lists(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return [str(item).strip() for item in value if str(item).strip()]


class ScenarioSpec(BaseModel):
    id: str = Field(min_length=1)
    description: str = ""
    job_kind: Literal["video_editing", "story_development"] = "video_editing"
    mode: Literal["offline_replay", "fault_matrix", "provider_canary", "full_e2e"] = (
        "offline_replay"
    )
    request: dict[str, Any] = Field(default_factory=dict)
    fixtures: dict[str, Any] = Field(default_factory=dict)
    providers: dict[str, str] = Field(default_factory=dict)
    faults: list[FaultSpec] = Field(default_factory=list)
    assertions: ScenarioAssertions = Field(default_factory=ScenarioAssertions)
    tags: list[str] = Field(default_factory=list)


class CapabilityProbeResult(BaseModel):
    capability: str
    provider: str
    model: str
    available: bool
    latency_seconds: float = Field(default=0.0, ge=0)
    error_class: str = ""
    error: str = ""
    checked_at: str = Field(default_factory=utc_now_iso)
    expires_at_epoch: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunOutcome(BaseModel):
    terminal_status: str = ""
    story_document: dict[str, Any] | None = None
    editing_plan: dict[str, Any] | None = None
    text_outputs: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OracleFinding(BaseModel):
    oracle: str
    code: str
    severity: Literal["error", "warning", "info"] = "error"
    passed: bool
    message: str
    expected: Any = None
    actual: Any = None


class HarnessReport(BaseModel):
    scenario_id: str
    mode: str
    passed: bool
    started_at: str
    completed_at: str
    wall_seconds: float = Field(ge=0)
    outcome: RunOutcome
    findings: list[OracleFinding] = Field(default_factory=list)
    event_summary: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
