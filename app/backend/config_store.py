from __future__ import annotations
from typing import Any

from .models import AppConfig, ServiceProfile
from app.runtime_paths import (
    configure_runtime_environment,
    read_runtime_env_file,
    runtime_env_path,
    runtime_path,
    write_runtime_env_file,
)


configure_runtime_environment()

APP_STATE_DIR = runtime_path("app_state")
LEGACY_CONFIG_PATH = APP_STATE_DIR / "config.json"
JOBS_DIR = APP_STATE_DIR / "jobs"

PROFILE_ENV_VARS = {
    "api_key": "CRAYOTTER_API_KEY",
    "base_url": "CRAYOTTER_BASE_URL",
    "model_name": "CRAYOTTER_MODEL_NAME",
    "video_api_key": "CRAYOTTER_VIDEO_API_KEY",
    "video_base_url": "CRAYOTTER_VIDEO_BASE_URL",
    "video_model_name": "CRAYOTTER_VIDEO_MODEL_NAME",
    "tts_api_key": "CRAYOTTER_TTS_API_KEY",
    "tts_base_url": "CRAYOTTER_TTS_BASE_URL",
    "tts_model_name": "CRAYOTTER_TTS_MODEL_NAME",
}

APP_ENV_VARS = {
    "enable_phase2_research": "CRAYOTTER_ENABLE_PHASE2_RESEARCH",
    "direct_phase3_execution": "CRAYOTTER_DIRECT_PHASE3_EXECUTION",
    "prefer_local_materials": "CRAYOTTER_PREFER_LOCAL_MATERIALS",
    "agent_stall_timeout_seconds": "CRAYOTTER_AGENT_STALL_TIMEOUT_SECONDS",
}

BOOL_FIELDS = {
    "enable_phase2_research",
    "direct_phase3_execution",
    "prefer_local_materials",
}


def _coerce_env_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _coerce_env_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _merge_config(current: AppConfig, payload: dict[str, Any]) -> AppConfig:
    merged = current.model_dump()
    merged.update(payload)

    profiles = payload.get("profiles")
    if isinstance(profiles, dict):
        merged_profiles: dict[str, ServiceProfile] = {}
        base_profiles = current.profiles
        for name, profile_payload in {**base_profiles, **profiles}.items():
            if isinstance(profile_payload, ServiceProfile):
                merged_profiles[name] = profile_payload
                continue
            existing = base_profiles.get(name, ServiceProfile(name=name))
            raw = existing.model_dump()
            if isinstance(profile_payload, dict):
                raw.update(profile_payload)
            raw["name"] = name
            merged_profiles[name] = ServiceProfile.model_validate(raw)
        merged["profiles"] = {name: profile.model_dump() for name, profile in merged_profiles.items()}

    return AppConfig.model_validate(merged)


class ConfigStore:
    def __init__(self) -> None:
        APP_STATE_DIR.mkdir(parents=True, exist_ok=True)
        JOBS_DIR.mkdir(parents=True, exist_ok=True)

    def load(self) -> AppConfig:
        config = AppConfig()
        should_sync = not runtime_env_path().exists()

        env_payload = self._load_env_payload()
        if env_payload:
            try:
                config = _merge_config(config, env_payload)
            except Exception:
                should_sync = True

        if should_sync:
            self.save(config)
        else:
            self._remove_legacy_config()
        return config

    def save(self, config: AppConfig) -> AppConfig:
        active_profile_name = config.active_profile if config.active_profile in config.profiles else "default"
        profile = config.profiles.get(active_profile_name) or config.profiles.get("default") or ServiceProfile(name="default")
        env_updates = {
            **{env_name: getattr(profile, field_name) for field_name, env_name in PROFILE_ENV_VARS.items()},
            **{
                env_name: ("true" if getattr(config, field_name) else "false")
                if field_name in BOOL_FIELDS
                else str(getattr(config, field_name))
                for field_name, env_name in APP_ENV_VARS.items()
            },
        }
        write_runtime_env_file(env_updates)
        self._remove_legacy_config()
        return config

    def update(self, payload: dict) -> AppConfig:
        current = self.load()
        config = _merge_config(current, payload)
        return self.save(config)

    def _load_env_payload(self) -> dict[str, Any]:
        raw = read_runtime_env_file()
        if not raw:
            return {}

        payload: dict[str, Any] = {}
        default_profile: dict[str, Any] = {}
        for field_name, env_name in PROFILE_ENV_VARS.items():
            if env_name in raw:
                default_profile[field_name] = raw[env_name]
        if default_profile:
            payload["profiles"] = {"default": default_profile}

        for field_name, env_name in APP_ENV_VARS.items():
            if env_name not in raw:
                continue
            default_value = AppConfig.model_fields[field_name].default
            if field_name in BOOL_FIELDS:
                payload[field_name] = _coerce_env_bool(raw[env_name], default_value)
            else:
                payload[field_name] = _coerce_env_int(raw[env_name], default_value)

        return payload

    def _remove_legacy_config(self) -> None:
        LEGACY_CONFIG_PATH.unlink(missing_ok=True)
