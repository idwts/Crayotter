"""Deterministic guardrails around the LLM material-gap evaluator."""

from __future__ import annotations

from typing import Any


def deterministic_material_sufficient(metrics: dict[str, Any]) -> bool:
    return (
        metrics["source_count"] >= metrics["required_sources"]
        and metrics["analysis_complete_ratio"] >= 2 / 3
        and metrics["duration_coverage_ratio"]
        >= metrics["required_duration_coverage_ratio"]
        and metrics["topic_coverage_ratio"] >= 0.15
        and metrics["orientation_match_ratio"] >= 0.5
        and metrics["quality_floor_met"]
        and metrics["duplicate_ratio"] <= 0.75
    )


def normalize_gap_report(
    report: dict[str, Any],
    *,
    metrics: dict[str, Any],
    round_index: int,
    supplement_limit: int,
    direct_phase3_execution: bool,
    evaluated_at: float,
) -> dict[str, Any]:
    normalized = dict(report)
    sufficient = deterministic_material_sufficient(metrics)
    decision = str(normalized.get("decision") or "").lower()
    if metrics["analyzed_count"] == 0:
        decision = "fail"
    elif sufficient and decision != "fail":
        decision = "proceed"
    elif decision not in {"proceed", "supplement", "fail"}:
        decision = "supplement"
    if direct_phase3_execution and metrics["analyzed_count"] > 0:
        decision = "proceed"
    if round_index >= supplement_limit and decision == "supplement":
        decision = "proceed" if sufficient else "fail"
    normalized.update(
        {
            "decision": decision,
            "metrics": metrics,
            "round": round_index,
            "evaluated_at": evaluated_at,
        }
    )
    return normalized
