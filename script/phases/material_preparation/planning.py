"""Pure Phase 1 planning and normalization policies."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

try:
    from ...orchestration import material_budget_for_duration
    from ...workflow import Plan, Step
except ImportError:
    from orchestration import material_budget_for_duration
    from workflow import Plan, Step


def recommend_material_counts(target_duration_seconds: float) -> dict[str, int]:
    material = material_budget_for_duration(target_duration_seconds or 30.0)
    per_search = max(6, min(8, math.ceil(material.candidate_cap / material.search_task_cap)))
    return {
        "search_per_source": per_search,
        "search_pages": 1 if target_duration_seconds <= 90 else 2,
        "max_candidates": material.candidate_cap,
        "mllm_review": material.candidate_cap,
        "top_k_min": material.source_min,
        "top_k_max": material.source_target,
        "search_task_cap": material.search_task_cap,
        "coverage_ratio_percent": int(round(material.coverage_ratio * 100)),
    }


def build_fallback_plan(
    user_request: str,
    counts: dict[str, int],
    *,
    enabled_platforms: list[str],
) -> Plan:
    top_k = max(1, (counts["top_k_min"] + counts["top_k_max"]) // 2)
    return Plan(
        goal=user_request,
        analysis="自动生成的素材准备 DAG",
        steps=[
            Step(
                id=1,
                description="搜索相关视频素材",
                tool_hint="search_material_sources",
                arguments={
                    "query": user_request,
                    "platforms": enabled_platforms,
                    "max_results": counts["search_per_source"],
                    "pages": counts["search_pages"],
                    "expand_variants": 2 if counts["search_pages"] == 1 else 3,
                    "max_total_results": counts["max_candidates"],
                },
            ),
            Step(
                id=2,
                description="筛选候选视频",
                tool_hint="rank_video_candidates",
                depends_on=[1],
                arguments={
                    "candidates_json": "[]",
                    "top_k": top_k,
                    "max_review": counts["mllm_review"],
                    "selection_goal": user_request,
                },
            ),
            Step(
                id=3,
                description="下载最佳素材",
                tool_hint="download_material_video",
                depends_on=[2],
            ),
            Step(
                id=4,
                description="分析所有已下载视频",
                tool_hint="analyze_video",
                depends_on=[3],
                arguments={"analysis_goal": user_request},
            ),
        ],
    )


def lightweight_material_collection(
    counts: dict[str, int],
    *,
    enabled: bool,
) -> bool:
    return (
        enabled
        and counts.get("search_pages") == 1
        and counts.get("max_candidates", 0) <= 32
    )


def lightweight_search_step_limit(
    counts: dict[str, int],
    *,
    enabled: bool,
) -> int:
    if not lightweight_material_collection(counts, enabled=enabled):
        return 0
    return int(
        counts.get("search_task_cap")
        or (2 if counts.get("max_candidates", 0) <= 24 else 3)
    )


def plan_has_cycle(steps: list[Step]) -> bool:
    ids = {step.id for step in steps}
    indegree = {step.id: 0 for step in steps}
    children: dict[int, list[int]] = {step.id: [] for step in steps}
    for step in steps:
        for dependency in step.depends_on:
            if dependency not in ids:
                continue
            indegree[step.id] += 1
            children[dependency].append(step.id)
    ready = [step_id for step_id, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        step_id = ready.pop()
        visited += 1
        for child_id in children[step_id]:
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                ready.append(child_id)
    return visited != len(steps)


def validate_and_normalize_plan(
    plan: Plan,
    user_request: str,
    counts: dict[str, int],
    *,
    normalize_tool_hint: Callable[[Step], str],
    enabled_platforms: list[str],
    short_form_optimizations: bool,
    direct_phase3_execution: bool,
    remote_tool_names: set[str],
    warning: Callable[[str], None] | None = None,
) -> Plan:
    if not plan.steps:
        return plan

    fallback = lambda: build_fallback_plan(
        user_request,
        counts,
        enabled_platforms=enabled_platforms,
    )
    raw_ids = [step.id for step in plan.steps]
    if any(step_id <= 0 for step_id in raw_ids) or len(raw_ids) != len(set(raw_ids)):
        if warning:
            warning("Planner DAG 步骤 ID 非法，回退为标准 DAG")
        return fallback()

    valid_ids = set(raw_ids)
    normalized: list[Step] = []
    for original in plan.steps:
        step = original.model_copy(deep=True)
        step.tool_hint = normalize_tool_hint(step)
        step.depends_on = sorted(
            {
                int(dependency)
                for dependency in step.depends_on
                if int(dependency) in valid_ids and int(dependency) != step.id
            }
        )
        step.arguments = dict(step.arguments or {})
        normalized.append(step)

    search_step_limit = lightweight_search_step_limit(
        counts,
        enabled=short_form_optimizations,
    )
    if search_step_limit:
        kept_search_ids = {
            step.id
            for step in [
                item
                for item in normalized
                if item.tool_hint == "search_material_sources"
            ][:search_step_limit]
        }
        normalized = [
            step
            for step in normalized
            if step.tool_hint != "search_material_sources" or step.id in kept_search_ids
        ]
        kept_ids = {step.id for step in normalized}
        for step in normalized:
            step.depends_on = [
                dependency for dependency in step.depends_on if dependency in kept_ids
            ]
            if step.tool_hint == "search_material_sources":
                step.arguments["max_results"] = min(
                    int(
                        step.arguments.get("max_results", counts["search_per_source"])
                        or counts["search_per_source"]
                    ),
                    counts["search_per_source"],
                )
                step.arguments["pages"] = 1
                step.arguments["expand_variants"] = min(
                    int(step.arguments.get("expand_variants", 2) or 2),
                    2,
                )
                step.arguments["max_total_results"] = min(
                    int(
                        step.arguments.get("max_total_results", counts["max_candidates"])
                        or counts["max_candidates"]
                    ),
                    counts["max_candidates"],
                )
            elif step.tool_hint == "rank_video_candidates":
                step.arguments["top_k"] = min(
                    int(
                        step.arguments.get("top_k", counts["top_k_max"])
                        or counts["top_k_max"]
                    ),
                    counts["top_k_max"],
                )
                step.arguments["max_review"] = min(
                    int(
                        step.arguments.get("max_review", counts["mllm_review"])
                        or counts["mllm_review"]
                    ),
                    counts["mllm_review"],
                )

    if direct_phase3_execution:
        normalized = [
            step for step in normalized if step.tool_hint not in remote_tool_names
        ]
        kept_ids = {step.id for step in normalized}
        for step in normalized:
            step.depends_on = [
                dependency for dependency in step.depends_on if dependency in kept_ids
            ]

    search_ids = [
        step.id
        for step in normalized
        if step.tool_hint in {"search_material_sources", "import_material_urls"}
    ]
    rank_ids = [
        step.id for step in normalized if step.tool_hint == "rank_video_candidates"
    ]
    download_ids = [
        step.id for step in normalized if step.tool_hint == "download_material_video"
    ]
    positions = {step.id: index for index, step in enumerate(normalized)}
    for step in normalized:
        required = set(step.depends_on)
        if step.tool_hint == "rank_video_candidates":
            required.update(search_ids)
        elif step.tool_hint == "download_material_video":
            required.update(rank_ids)
        elif step.tool_hint == "analyze_video":
            required.update(
                download_id
                for download_id in download_ids
                if positions.get(download_id, -1) < positions.get(step.id, -1)
            )
        step.depends_on = sorted(required)

    if plan_has_cycle(normalized):
        if warning:
            warning("Planner DAG 存在环路，回退为标准 DAG")
        return fallback()
    return plan.model_copy(update={"steps": normalized})
