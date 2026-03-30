from __future__ import annotations

import json
from pathlib import Path

from .models import AppConfig, ServiceProfile
from app.runtime_paths import configure_runtime_environment, runtime_path


configure_runtime_environment()

APP_STATE_DIR = runtime_path("app_state")
CONFIG_PATH = APP_STATE_DIR / "config.json"
JOBS_DIR = APP_STATE_DIR / "jobs"


class ConfigStore:
    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self.config_path = config_path
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        JOBS_DIR.mkdir(parents=True, exist_ok=True)

    def load(self) -> AppConfig:
        if not self.config_path.exists():
            config = AppConfig()
            self.save(config)
            return config

        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            return AppConfig.model_validate(payload)
        except Exception:
            config = AppConfig()
            self.save(config)
            return config

    def save(self, config: AppConfig) -> AppConfig:
        self.config_path.write_text(
            json.dumps(config.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return config

    def update(self, payload: dict) -> AppConfig:
        current = self.load()
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

        config = AppConfig.model_validate(merged)
        return self.save(config)
