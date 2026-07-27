"""Composable tool-skill groups built on top of the authoritative tool catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .tool_catalog import ToolCatalog


SkillPhase = Literal["phase1", "phase3", "shared"]


@dataclass(frozen=True)
class ToolSkill:
    name: str
    phase: SkillPhase
    description: str
    tool_names: tuple[str, ...]


class SkillRegistry:
    """Maps business capabilities to existing deterministic tools.

    A skill is orchestration metadata, not another hidden executor.  The
    scheduler and tool registry remain the only code paths that invoke tools.
    """

    def __init__(self, skills: list[ToolSkill] | None = None) -> None:
        self._skills = {skill.name: skill for skill in (skills or [])}

    def register(self, skill: ToolSkill, *, replace: bool = False) -> None:
        if skill.name in self._skills and not replace:
            raise ValueError(f"Tool skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> ToolSkill:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool skill: {name}") from exc

    def for_phase(self, phase: SkillPhase) -> list[ToolSkill]:
        return [
            skill
            for skill in self._skills.values()
            if skill.phase in {phase, "shared"}
        ]

    def resolve_tools(self, name: str, catalog: ToolCatalog) -> list[Any]:
        skill = self.get(name)
        return [
            catalog.by_name[tool_name]
            for tool_name in skill.tool_names
            if tool_name in catalog.by_name
        ]

    @classmethod
    def defaults(cls) -> "SkillRegistry":
        return cls(
            [
                ToolSkill(
                    name="material_acquisition",
                    phase="phase1",
                    description="搜索、导入、筛选并下载素材",
                    tool_names=(
                        "search_material_sources",
                        "import_material_urls",
                        "rank_video_candidates",
                        "download_material_video",
                    ),
                ),
                ToolSkill(
                    name="material_understanding",
                    phase="shared",
                    description="探测和分析视频素材",
                    tool_names=("inspect_video_duration", "analyze_video"),
                ),
                ToolSkill(
                    name="timeline_editing",
                    phase="phase3",
                    description="召回、裁剪、连续性规划、转场和时间线装配",
                    tool_names=(
                        "recall_semantic_segments",
                        "cut_video",
                        "batch_cut_video",
                        "score_cut_continuity",
                        "recommend_transition_for_cut",
                        "plan_transition_timeline",
                        "add_transition",
                        "merge_videos",
                    ),
                ),
                ToolSkill(
                    name="narration_and_audio",
                    phase="phase3",
                    description="旁白对齐、TTS 合成、混音、响度和字幕",
                    tool_names=(
                        "align_narration_to_timeline",
                        "validate_narration_timeline",
                        "add_narration_segments",
                        "duck_background_audio",
                        "normalize_loudness",
                        "add_subtitles",
                    ),
                ),
                ToolSkill(
                    name="delivery",
                    phase="phase3",
                    description="验证时间线并导出最终视频",
                    tool_names=("validate_timeline_constraints", "export_video"),
                ),
            ]
        )
