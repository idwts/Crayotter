from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ScenarioSpec


def load_scenario(path: Path) -> ScenarioSpec:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload: Any = json.loads(text)
    elif path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "YAML scenarios require PyYAML; JSON works without it."
            ) from exc
        payload = yaml.safe_load(text)
    else:
        raise ValueError(f"Unsupported scenario format: {path.suffix}")
    return ScenarioSpec.model_validate(payload)
