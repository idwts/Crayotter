from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .models import OracleFinding, RunOutcome, ScenarioSpec


def _finding(
    oracle: str,
    code: str,
    passed: bool,
    message: str,
    *,
    expected: Any = None,
    actual: Any = None,
) -> OracleFinding:
    return OracleFinding(
        oracle=oracle,
        code=code,
        passed=passed,
        message=message,
        expected=expected,
        actual=actual,
    )


def _searchable_text(outcome: RunOutcome) -> str:
    return json.dumps(
        {
            "story": outcome.story_document,
            "plan": outcome.editing_plan,
            "text": outcome.text_outputs,
            "metadata": outcome.metadata,
        },
        ensure_ascii=False,
        default=str,
    ).lower()


def _plan_duration(plan: dict[str, Any] | None) -> float | None:
    if not plan:
        return None
    scenes = plan.get("scenes") or []
    if scenes:
        try:
            return float(scenes[-1].get("end", 0.0))
        except (TypeError, ValueError, AttributeError):
            pass
    try:
        return float(plan.get("target_duration_seconds"))
    except (TypeError, ValueError):
        return None


def evaluate(
    spec: ScenarioSpec, outcome: RunOutcome, wall_seconds: float
) -> list[OracleFinding]:
    assertions = spec.assertions
    findings: list[OracleFinding] = []
    text = _searchable_text(outcome)
    for term in assertions.required_terms:
        passed = term.lower() in text
        findings.append(
            _finding(
                "requirements",
                "required_term",
                passed,
                f"Required term {'present' if passed else 'missing'}: {term}",
                expected=term,
            )
        )
    for term in assertions.forbidden_terms:
        passed = term.lower() not in text
        findings.append(
            _finding(
                "requirements",
                "forbidden_term",
                passed,
                f"Forbidden term {'absent' if passed else 'present'}: {term}",
                expected=f"not {term}",
            )
        )
    findings.append(
        _finding(
            "runtime",
            "terminal_status",
            outcome.terminal_status == assertions.terminal_status,
            "Terminal status matches scenario contract.",
            expected=assertions.terminal_status,
            actual=outcome.terminal_status,
        )
    )
    if assertions.duration is not None:
        actual = outcome.metadata.get("duration_seconds")
        if actual is None:
            actual = _plan_duration(outcome.editing_plan)
        passed = (
            actual is not None
            and abs(float(actual) - assertions.duration.target)
            <= assertions.duration.tolerance
        )
        findings.append(
            _finding(
                "media",
                "duration",
                passed,
                "Output duration is within tolerance.",
                expected=assertions.duration.model_dump(),
                actual=actual,
            )
        )
    if assertions.required_voice:
        voice_text = " ".join(
            [
                str((outcome.editing_plan or {}).get("voice", "")),
                str((outcome.editing_plan or {}).get("narration_strategy", "")),
                str(outcome.metadata.get("voice", "")),
            ]
        ).lower()
        aliases = {
            "female": (
                "female",
                "woman",
                "cherry",
                "serena",
                "chelsie",
                "女性",
                "女声",
            ),
            "male": ("male", "man", "ethan", "dylan", "男性", "男声"),
        }
        wanted = assertions.required_voice.lower()
        passed = any(item in voice_text for item in aliases.get(wanted, (wanted,)))
        findings.append(
            _finding(
                "narration",
                "voice",
                passed,
                "Narration voice matches the requested class.",
                expected=assertions.required_voice,
                actual=voice_text.strip(),
            )
        )
    if assertions.expected_narration:
        actual_lines = [
            str(scene.get("narration") or "").strip()
            for scene in ((outcome.editing_plan or {}).get("scenes") or [])
            if str(scene.get("narration") or "").strip()
        ]
        passed = (
            actual_lines == assertions.expected_narration
            if assertions.narration_exact
            else all(line in actual_lines for line in assertions.expected_narration)
        )
        findings.append(
            _finding(
                "narration",
                "approved_text",
                passed,
                "Narration preserves the approved screenplay text.",
                expected=assertions.expected_narration,
                actual=actual_lines,
            )
        )
    event_counts = Counter(str(event.get("type") or "") for event in outcome.events)
    if assertions.minimum_download_successes:
        completed = sum(
            1
            for event in outcome.events
            if event.get("type") == "task_completed"
            and (event.get("payload") or {}).get("kind") == "material_download"
        )
        completed = max(
            completed, int(outcome.metadata.get("download_successes", 0) or 0)
        )
        findings.append(
            _finding(
                "materials",
                "download_quorum",
                completed >= assertions.minimum_download_successes,
                "Material download quorum is satisfied.",
                expected=assertions.minimum_download_successes,
                actual=completed,
            )
        )
    if assertions.maximum_fallbacks is not None:
        count = sum(
            value for event, value in event_counts.items() if "fallback" in event
        )
        findings.append(
            _finding(
                "runtime",
                "fallback_budget",
                count <= assertions.maximum_fallbacks,
                "Fallback count stays within budget.",
                expected=assertions.maximum_fallbacks,
                actual=count,
            )
        )
    if assertions.max_provider_retry_seconds is not None:
        actual = float(outcome.metadata.get("provider_retry_seconds", 0.0) or 0.0)
        findings.append(
            _finding(
                "runtime",
                "provider_retry_budget",
                actual <= assertions.max_provider_retry_seconds,
                "Provider retry time stays within budget.",
                expected=assertions.max_provider_retry_seconds,
                actual=actual,
            )
        )
    if assertions.max_wall_seconds is not None:
        findings.append(
            _finding(
                "runtime",
                "wall_budget",
                wall_seconds <= assertions.max_wall_seconds,
                "Scenario wall time stays within budget.",
                expected=assertions.max_wall_seconds,
                actual=round(wall_seconds, 3),
            )
        )
    return findings
