"""Validated runtime settings shared by entrypoints and workflow nodes."""

from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field, field_validator


class RuntimeSettings(BaseModel):
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model_name: str = "qwen-plus"
    video_api_key: str = ""
    video_base_url: str = ""
    video_model_name: str = "qwen-vl-max-latest"
    tts_api_key: str = ""
    tts_base_url: str = ""
    tts_model_name: str = "qwen-tts-latest"

    enable_phase2_research: bool = True
    enable_plan_review: bool = True
    direct_phase3_execution: bool = False
    prefer_local_materials: bool = False
    resume_execution: bool = False
    revision: int = Field(default=1, ge=1)

    search_pool_size: int = Field(default=4, ge=1)
    download_pool_size: int = Field(default=3, ge=1)
    video_analysis_pool_size: int = Field(default=3, ge=1)
    llm_pool_size: int = Field(default=4, ge=1)
    ffmpeg_pool_size: int = Field(default=3, ge=1)
    tts_pool_size: int = Field(default=3, ge=1)
    export_pool_size: int = Field(default=1, ge=1)

    short_form_optimizations: bool = True
    short_form_max_sources: int = Field(default=2, ge=1, le=4)
    video_analysis_proxy_max_seconds: int = Field(default=45, ge=1)
    download_max_height: int = Field(default=720, ge=1)
    standardize_target_fps: int = Field(default=30, ge=1)
    audio_loudnorm_target: float = 0.0
    post_task_review_mode: Literal["async", "sync", "off"] = "async"
    agent_stall_timeout_seconds: int = Field(default=150, ge=10)
    youtube_mode: Literal["auto", "on", "off"] = "auto"
    default_deadline_seconds: int = Field(default=600, ge=60)
    phase1_max_seconds: int = Field(default=180, ge=30)
    processing_mode: Literal["auto", "speed", "quality"] = "auto"
    output_profile: str = "auto"
    enabled_material_platforms: list[str] = Field(
        default_factory=lambda: ["bilibili", "douyin", "xiaohongshu", "youtube"]
    )
    browser_auth_browser: str = ""
    browser_auth_profile: str = ""
    target_duration_seconds: float = Field(default=0.0, ge=0.0)

    @field_validator("enabled_material_platforms", mode="before")
    @classmethod
    def _normalize_platforms(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            value = value.split(",")
        normalized = [
            str(item).strip().lower()
            for item in (value or [])
            if str(item).strip()
        ]
        return list(dict.fromkeys(normalized)) or [
            "bilibili",
            "douyin",
            "xiaohongshu",
            "youtube",
        ]

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any],
        *,
        defaults: "RuntimeSettings | None" = None,
    ) -> "RuntimeSettings":
        payload = defaults.model_dump() if defaults is not None else {}
        payload.update(dict(values))
        return cls.model_validate(payload)

    def resource_pools(self) -> dict[str, int]:
        return {
            "search_pool": self.search_pool_size,
            "download_pool": self.download_pool_size,
            "video_analysis_pool": self.video_analysis_pool_size,
            "llm_pool": self.llm_pool_size,
            "ffmpeg_pool": self.ffmpeg_pool_size,
            "tts_pool": self.tts_pool_size,
            "export_pool": self.export_pool_size,
        }
