from __future__ import annotations

import json
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from script.model_runtime import sanitize_error

from .faults import FaultInjector
from .models import HarnessReport, OracleFinding, RunOutcome, ScenarioSpec
from .oracles import evaluate
from .replay import ReplayStore

Executor = Callable[[ScenarioSpec, "HarnessContext"], RunOutcome]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class HarnessContext:
    runtime_root: Path
    fault_injector: FaultInjector
    replay_store: ReplayStore
    events: list[dict[str, Any]] = field(default_factory=list)

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self.events.append(
            {"type": event_type, "timestamp": _now(), "payload": payload or {}}
        )


class HarnessRunner:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def run(self, spec: ScenarioSpec, executor: Executor) -> HarnessReport:
        started_at = _now()
        started = time.monotonic()
        temp: tempfile.TemporaryDirectory[str] | None = None
        if self.root is None:
            temp = tempfile.TemporaryDirectory(prefix=f"crayotter_harness_{spec.id}_")
            runtime_root = Path(temp.name)
        else:
            runtime_root = self.root / spec.id
            runtime_root.mkdir(parents=True, exist_ok=True)
        context = HarnessContext(
            runtime_root=runtime_root,
            fault_injector=FaultInjector(spec.faults),
            replay_store=ReplayStore(runtime_root / "replay.json"),
        )
        findings: list[OracleFinding] = []
        try:
            outcome = executor(spec, context)
        except Exception as exc:
            outcome = RunOutcome(
                terminal_status="failed",
                events=list(context.events),
                metadata={"exception": sanitize_error(exc, 1200)},
            )
            findings.append(
                OracleFinding(
                    oracle="execution",
                    code="exception",
                    passed=False,
                    message="Scenario executor raised an exception.",
                    actual=sanitize_error(exc, 1200),
                )
            )
        if context.events:
            outcome.events = [*outcome.events, *context.events]
        wall = time.monotonic() - started
        findings.extend(evaluate(spec, outcome, wall))
        passed = not any(
            not item.passed and item.severity == "error" for item in findings
        )
        report = HarnessReport(
            scenario_id=spec.id,
            mode=spec.mode,
            passed=passed,
            started_at=started_at,
            completed_at=_now(),
            wall_seconds=round(wall, 3),
            outcome=outcome,
            findings=findings,
            event_summary=dict(
                Counter(str(item.get("type") or "") for item in outcome.events)
            ),
            metadata={"runtime_root": str(runtime_root)},
        )
        if temp is not None:
            temp.cleanup()
            report.metadata["runtime_root"] = "<temporary>"
        return report

    def incident_outcome(self, job_dir: Path) -> RunOutcome:
        summary = json.loads((job_dir / "summary.json").read_text(encoding="utf-8"))
        events: list[dict[str, Any]] = []
        events_path = job_dir / "events.jsonl"
        if events_path.exists():
            for line in events_path.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        artifacts: list[dict[str, Any]] = []
        manifest = job_dir / "workspace" / ".crayotter" / "artifact_manifest.json"
        if manifest.exists():
            try:
                artifacts = json.loads(manifest.read_text(encoding="utf-8")).get(
                    "artifacts", []
                )
            except Exception:
                artifacts = []
        story_document = self._load_json(
            job_dir / "workspace" / "story" / "current.json"
        )
        editing_plan = None
        for path in (
            job_dir / "workspace" / "plans" / "approved_editing_plan.json",
            job_dir / "workspace" / "plans" / "current_plan.json",
            job_dir / "workspace" / "phase3_execution_plan.json",
        ):
            editing_plan = self._load_json(path)
            if editing_plan is not None:
                break
        return RunOutcome(
            terminal_status=str(summary.get("status") or ""),
            story_document=story_document,
            editing_plan=editing_plan,
            text_outputs=[
                str(summary.get("task") or ""),
                str(summary.get("final_output") or ""),
            ],
            artifacts=artifacts,
            events=events,
            metadata={
                "duration_seconds": next(
                    (
                        item.get("metadata", {}).get("duration_seconds")
                        for item in artifacts
                        if item.get("kind") == "final_video"
                    ),
                    None,
                ),
                "processing_elapsed_seconds": summary.get(
                    "processing_elapsed_seconds", 0.0
                ),
                "total_wall_seconds": summary.get("total_wall_seconds", 0.0),
            },
        )

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None
