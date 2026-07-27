"""Phase 2 mode selection and deterministic research DAG construction."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

try:
    from ...orchestration import ExecutionPlan, RetryPolicy, TaskSpec
except ImportError:
    from orchestration import ExecutionPlan, RetryPolicy, TaskSpec


ResearchMode = Literal["compact", "parallel"]


def select_research_mode(
    *,
    target_duration_seconds: float,
    remaining_seconds: float,
    short_form_optimizations: bool,
) -> ResearchMode:
    if (
        short_form_optimizations
        and 0 < target_duration_seconds <= 45
    ) or remaining_seconds < 60:
        return "compact"
    return "parallel"


def build_research_execution_plan(
    *,
    analysis_files: list[Path],
    user_request: str,
    target_duration_seconds: float,
    deadline_at_epoch: float | None,
) -> ExecutionPlan:
    source_tasks: list[TaskSpec] = []
    for path in analysis_files:
        source_id = uuid.uuid5(uuid.NAMESPACE_URL, str(path.resolve())).hex[:12]
        source_tasks.append(
            TaskSpec(
                id=f"phase2_source_{source_id}",
                phase="phase2",
                kind="source_research",
                description=f"研究素材 {path.name}",
                arguments={
                    "analysis_path": str(path.resolve()),
                    "analysis_size": path.stat().st_size,
                    "analysis_mtime_ns": path.stat().st_mtime_ns,
                    "user_request": user_request,
                    "target_duration_seconds": target_duration_seconds,
                },
                resources={"llm_pool": 1},
                output_kinds=["source_research"],
                retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
            )
        )

    source_ids = [task.id for task in source_tasks]
    topic_prompts = (
        {
            "narrative": "设计叙事结构、开场钩子、高潮和结尾记忆点。",
            "visual_pacing": "合并分析视觉连续性、镜头顺序、色调、转场和节奏曲线。",
        }
        if target_duration_seconds <= 90
        else {
            "narrative": "设计叙事结构、开场钩子、高潮和结尾记忆点。",
            "visual": "分析跨素材视觉连续性、镜头顺序、色调和转场逻辑。",
            "pacing": "设计目标时长内的片段分配、信息密度和节奏曲线。",
            "narration": "设计严格贴合画面的分段旁白、留白和字幕策略。",
        }
    )
    topic_tasks = [
        TaskSpec(
            id=f"phase2_topic_{name}",
            phase="phase2",
            kind="topic_research",
            description=instruction,
            arguments={
                "topic": name,
                "instruction": instruction,
                "user_request": user_request,
                "target_duration_seconds": target_duration_seconds,
                "source_task_ids": source_ids,
            },
            depends_on=source_ids,
            resources={"llm_pool": 1},
            output_kinds=["topic_research"],
            estimated_seconds=15.0,
            optional=name != "narrative",
            priority=20 if name == "narrative" else 10,
            retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
        )
        for name, instruction in topic_prompts.items()
    ]
    integrator = TaskSpec(
        id="phase2_blueprint_integrator",
        phase="phase2",
        kind="blueprint_integrator",
        description="整合全部素材研究和专题策略",
        arguments={
            "user_request": user_request,
            "target_duration_seconds": target_duration_seconds,
            "topic_task_ids": [task.id for task in topic_tasks],
        },
        depends_on=[task.id for task in topic_tasks],
        resources={"llm_pool": 1},
        output_kinds=["editing_blueprint", "editing_blueprint_json"],
        retry=RetryPolicy(max_attempts=2, backoff_seconds=0.5),
    )
    return ExecutionPlan(
        plan_id="phase2_parallel_research",
        phase="phase2",
        goal=user_request,
        tasks=[*source_tasks, *topic_tasks, integrator],
        deadline_at_epoch=deadline_at_epoch,
    )
