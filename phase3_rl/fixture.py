from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@dataclass(slots=True)
class SeedFile:
    source: str = ""
    target: str = ""
    generator: str = ""
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScriptedToolCall:
    tool_name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class ScriptedTurn:
    assistant_content: str = ""
    tool_calls: list[ScriptedToolCall] = field(default_factory=list)


@dataclass(slots=True)
class Phase3Fixture:
    fixture_id: str
    description: str
    user_request: str
    target_duration_seconds: float
    editing_blueprint: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    runtime_seed: list[SeedFile] = field(default_factory=list)
    scripted_turns: list[ScriptedTurn] = field(default_factory=list)
    source_path: Path | None = None


def fixture_dir(fixture_id: str) -> Path:
    return _project_root() / "phase3_rl" / "fixtures" / fixture_id


def resolve_fixture_path(identifier_or_path: str | Path) -> Path:
    raw = Path(identifier_or_path)
    if raw.exists():
        return raw.resolve()

    candidate = fixture_dir(str(identifier_or_path)) / "fixture.json"
    if candidate.exists():
        return candidate.resolve()

    raise FileNotFoundError(f"Fixture not found: {identifier_or_path}")


def _parse_scripted_turn(raw: dict[str, Any]) -> ScriptedTurn:
    calls = [
        ScriptedToolCall(
            tool_name=str(item["tool_name"]),
            arguments=dict(item.get("arguments", {})),
        )
        for item in raw.get("tool_calls", [])
    ]
    return ScriptedTurn(
        assistant_content=str(raw.get("assistant_content", "")),
        tool_calls=calls,
    )


def load_fixture(identifier_or_path: str | Path) -> Phase3Fixture:
    path = resolve_fixture_path(identifier_or_path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    return Phase3Fixture(
        fixture_id=str(payload["fixture_id"]),
        description=str(payload.get("description", "")),
        user_request=str(payload["user_request"]),
        target_duration_seconds=float(payload.get("target_duration_seconds", 0.0) or 0.0),
        editing_blueprint=str(payload.get("editing_blueprint", "")),
        allowed_tools=[str(item) for item in payload.get("allowed_tools", [])],
        runtime_seed=[
            SeedFile(
                source=str(item.get("source", "")),
                target=str(item["target"]),
                generator=str(item.get("generator", "")),
                options=dict(item.get("options", {})),
            )
            for item in payload.get("runtime_seed", [])
        ],
        scripted_turns=[_parse_scripted_turn(item) for item in payload.get("scripted_turns", [])],
        source_path=path,
    )


def list_fixtures() -> list[str]:
    root = _project_root() / "phase3_rl" / "fixtures"
    if not root.exists():
        return []
    return sorted([item.name for item in root.iterdir() if (item / "fixture.json").exists()])


def build_episode_root(base_dir: str | Path, fixture_id: str) -> Path:
    base = Path(base_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in fixture_id).strip("_") or "fixture"
    existing = sorted(base.glob(f"{safe_id}_*"))
    next_index = len(existing) + 1
    root = base / f"{safe_id}_{next_index:03d}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def materialize_fixture(
    fixture: Phase3Fixture,
    episode_root: str | Path,
    *,
    project_root: str | Path | None = None,
) -> Path:
    repo_root = Path(project_root).resolve() if project_root else _project_root()
    root = Path(episode_root).resolve()
    (root / "temp").mkdir(parents=True, exist_ok=True)
    (root / "user_temp").mkdir(parents=True, exist_ok=True)
    (root / "memory_experience").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)

    for seed in fixture.runtime_seed:
        dst = (root / seed.target).resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)
        if seed.generator:
            _materialize_generated_seed(dst, seed.generator, seed.options)
            continue

        src = (repo_root / seed.source).resolve()
        shutil.copy2(src, dst)

    source_memory = repo_root / "memory_experience" / "latest_skills.md"
    target_memory = root / "memory_experience" / "latest_skills.md"
    if source_memory.exists() and not target_memory.exists():
        shutil.copy2(source_memory, target_memory)

    meta_path = root / "fixture_meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "fixture_id": fixture.fixture_id,
                "description": fixture.description,
                "source_fixture": str(fixture.source_path) if fixture.source_path else "",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return root


def _materialize_generated_seed(target: Path, generator: str, options: dict[str, Any]) -> None:
    if generator == "synthetic_video":
        _generate_synthetic_video(target, options)
        return
    raise ValueError(f"Unsupported runtime seed generator: {generator}")


def _generate_synthetic_video(target: Path, options: dict[str, Any]) -> None:
    from moviepy import ColorClip

    duration = float(options.get("duration_seconds", 6.0) or 6.0)
    width = int(options.get("width", 640) or 640)
    height = int(options.get("height", 360) or 360)
    fps = int(options.get("fps", 24) or 24)
    color = options.get("color", [40, 120, 220])
    if not isinstance(color, list) or len(color) != 3:
        color = [40, 120, 220]

    clip = ColorClip(
        size=(max(16, width), max(16, height)),
        color=tuple(int(max(0, min(255, item))) for item in color),
        duration=max(0.5, duration),
    )
    try:
        clip.write_videofile(
            str(target),
            fps=max(1, fps),
            codec="libx264",
            audio=False,
            logger=None,
        )
    finally:
        clip.close()
