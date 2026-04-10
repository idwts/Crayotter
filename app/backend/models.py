from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ServiceProfile(BaseModel):
    name: str = "default"
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model_name: str = "qwen-plus"
    video_api_key: str = ""
    video_base_url: str = ""
    video_model_name: str = "qwen-vl-max-latest"
    tts_api_key: str = ""
    tts_base_url: str = ""
    tts_model_name: str = "qwen-tts-latest"

    def to_runtime_config(self) -> dict[str, str]:
        return {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model_name": self.model_name,
            "video_api_key": self.video_api_key,
            "video_base_url": self.video_base_url,
            "video_model_name": self.video_model_name,
            "tts_api_key": self.tts_api_key,
            "tts_base_url": self.tts_base_url,
            "tts_model_name": self.tts_model_name,
        }


class AppConfig(BaseModel):
    active_profile: str = "default"
    profiles: dict[str, ServiceProfile] = Field(
        default_factory=lambda: {"default": ServiceProfile()}
    )
    allow_demo_jobs: bool = True
    workspace_root: str = "app_state"
    enable_phase2_research: bool = True
    direct_phase3_execution: bool = False
    prefer_local_materials: bool = False
    agent_stall_timeout_seconds: int = Field(default=150, ge=10)

    def get_profile(self, profile_name: str | None = None) -> ServiceProfile:
        name = profile_name or self.active_profile
        profile = self.profiles.get(name)
        if profile is None:
            raise KeyError(f"Profile '{name}' was not found.")
        return profile


class JobRequest(BaseModel):
    task: str = Field(min_length=1)
    mode: Literal["agent", "demo"] = "agent"
    profile: str | None = None
    enable_phase2_research: bool | None = None
    direct_phase3_execution: bool | None = None
    prefer_local_materials: bool | None = None


class RuntimeEvent(BaseModel):
    job_id: str
    sequence: int = 0
    type: str
    timestamp: str = Field(default_factory=utc_now_iso)
    payload: dict[str, Any] = Field(default_factory=dict)


class JobRecord(BaseModel):
    job_id: str
    task: str
    mode: Literal["agent", "demo"]
    enable_phase2_research: bool = True
    direct_phase3_execution: bool = False
    prefer_local_materials: bool = False
    status: Literal["queued", "running", "completed", "failed", "cancelled"] = "queued"
    profile: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    final_output: str = ""
    output_files: list[str] = Field(default_factory=list)
    job_dir: str = ""
    events_count: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES
