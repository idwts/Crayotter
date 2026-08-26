from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.backend.config_store import ConfigStore
from script.model_runtime import sanitize_error
from script.run_agent_worker import MARKER

from .models import RunOutcome, ScenarioSpec
from .runner import HarnessContext


def _copy_fixture_media(spec: ScenarioSpec, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for raw in spec.fixtures.get("paths", []) or []:
        source = Path(str(raw)).resolve(strict=True)
        if source.is_file():
            shutil.copy2(source, destination / source.name)


def _runtime_config(spec: ScenarioSpec) -> dict[str, Any]:
    app_config = ConfigStore().load()
    profile_name = str(spec.request.get("profile") or app_config.active_profile)
    profile = app_config.get_profile(profile_name)
    config = profile.to_runtime_config()
    config.update(
        {
            "job_kind": spec.job_kind,
            "story_config": spec.request.get("story_config"),
            "enable_phase2_research": spec.request.get(
                "enable_phase2_research", app_config.enable_phase2_research
            ),
            "enable_plan_review": spec.request.get(
                "enable_plan_review", app_config.enable_plan_review
            ),
            "direct_phase3_execution": spec.request.get(
                "direct_phase3_execution", app_config.direct_phase3_execution
            ),
            "prefer_local_materials": spec.request.get(
                "prefer_local_materials", app_config.prefer_local_materials
            ),
            "target_duration_seconds": spec.request.get("target_duration_seconds", 0),
            "default_deadline_seconds": spec.request.get(
                "deadline_seconds", app_config.default_deadline_seconds
            ),
            "enabled_material_platforms": spec.request.get(
                "enabled_material_platforms", app_config.enabled_material_platforms
            ),
            "post_task_review_mode": "off",
        }
    )
    return config


def execute_production_scenario(
    spec: ScenarioSpec, context: HarnessContext
) -> RunOutcome:
    """Run the normal Crayotter worker protocol in an isolated runtime."""

    task = str(spec.request.get("task") or "").strip()
    if not task:
        raise ValueError("A production scenario requires request.task")
    workspace = context.runtime_root / "workspace"
    user_workspace = context.runtime_root / "user_temp"
    workspace.mkdir(parents=True, exist_ok=True)
    _copy_fixture_media(spec, user_workspace)
    task_path = context.runtime_root / "task.txt"
    config_path = context.runtime_root / "runtime_profile.json"
    task_path.write_text(task, encoding="utf-8")
    config_path.write_text(
        json.dumps(_runtime_config(spec), ensure_ascii=False), encoding="utf-8"
    )
    env = os.environ.copy()
    env.update(
        {
            "CRAYOTTER_RUNTIME_ROOT": str(context.runtime_root),
            "CRAYOTTER_TASK_WORKSPACE": str(workspace),
            "CRAYOTTER_USER_WORKSPACE": str(user_workspace),
            "CRAYOTTER_PERSIST_WORKSPACE": "true",
            "CRAYOTTER_BENCHMARK_MODE": "true",
            "CRAYOTTER_BENCHMARK_EVENTS_PATH": str(
                context.runtime_root / "benchmark.jsonl"
            ),
        }
    )
    timeout = float(
        spec.assertions.max_wall_seconds or spec.request.get("deadline_seconds") or 600
    )
    root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        str(root / "script" / "run_agent_worker.py"),
        "--task-file",
        str(task_path),
        "--config-file",
        str(config_path),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(10.0, timeout),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return RunOutcome(
            terminal_status="failed",
            events=[],
            metadata={
                "error": f"worker_timeout:{exc.timeout}",
                "provider_retry_seconds": 0,
            },
        )
    finally:
        config_path.unlink(missing_ok=True)
    result: dict[str, Any] = {}
    errors: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.startswith(MARKER):
            continue
        try:
            payload = json.loads(line[len(MARKER) :])
        except json.JSONDecodeError:
            continue
        if payload.get("kind") == "event":
            context.events.append(
                {
                    "type": payload.get("type", "runtime_event"),
                    "timestamp": payload.get("timestamp"),
                    "payload": payload.get("payload") or {},
                }
            )
        elif payload.get("kind") == "result":
            result = payload
        elif payload.get("kind") == "error":
            errors.append(sanitize_error(payload.get("error"), 1200))
    artifacts: list[dict[str, Any]] = []
    manifest = workspace / ".crayotter" / "artifact_manifest.json"
    if manifest.exists():
        try:
            artifacts = json.loads(manifest.read_text(encoding="utf-8")).get(
                "artifacts", []
            )
        except Exception:
            artifacts = []
    story_document = HarnessContextLoader.load_json(
        workspace / "story" / "current.json"
    )
    editing_plan = None
    for path in (
        workspace / "plans" / "approved_editing_plan.json",
        workspace / "plans" / "current_plan.json",
        workspace / "phase3_execution_plan.json",
    ):
        editing_plan = HarnessContextLoader.load_json(path)
        if editing_plan is not None:
            break
    return RunOutcome(
        terminal_status="completed"
        if completed.returncode == 0 and result
        else "failed",
        story_document=story_document,
        editing_plan=editing_plan,
        text_outputs=[task, str(result.get("final_output") or "")],
        artifacts=artifacts,
        events=[],
        metadata={
            "returncode": completed.returncode,
            "error": "; ".join(errors) or sanitize_error(completed.stderr, 1200),
            "output_files": result.get("output_files") or [],
        },
    )


class HarnessContextLoader:
    @staticmethod
    def load_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None
