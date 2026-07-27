"""Tool groups exposed to each workflow phase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


PREP_TOOL_NAMES = {
    "search_material_sources",
    "import_material_urls",
    "download_material_video",
    "rank_video_candidates",
    "analyze_video",
    "inspect_video_duration",
}

REMOTE_PREP_TOOL_NAMES = {
    "search_material_sources",
    "import_material_urls",
    "download_material_video",
    "rank_video_candidates",
}

EDITING_TOOL_NAMES = {
    "recall_semantic_segments",
    "analyze_video",
    "batch_cut_video",
    "cut_video",
    "merge_videos",
    "inspect_video_duration",
    "list_transition_presets",
    "plan_transition_timeline",
    "add_transition",
    "validate_narration_timeline",
    "build_edit_timeline_from_segments",
    "align_narration_to_timeline",
    "validate_timeline_constraints",
    "score_cut_continuity",
    "recommend_transition_for_cut",
    "duck_background_audio",
    "normalize_loudness",
    "add_narration",
    "add_narration_segments",
    "add_subtitles",
    "export_video",
}


@dataclass(frozen=True)
class ToolCatalog:
    """Resolved tools plus the phase-specific views used by the workflow."""

    by_name: dict[str, Any]
    preparation: list[Any]
    editing: list[Any]


def build_tool_catalog(tools: Iterable[Any]) -> ToolCatalog:
    """Resolve the authoritative tool registry into phase-specific groups."""

    all_tools = list(tools)
    by_name = {
        getattr(tool, "name", ""): tool
        for tool in all_tools
        if getattr(tool, "name", "")
    }
    return ToolCatalog(
        by_name=by_name,
        preparation=[
            tool for tool in all_tools if getattr(tool, "name", "") in PREP_TOOL_NAMES
        ],
        editing=[
            tool for tool in all_tools if getattr(tool, "name", "") in EDITING_TOOL_NAMES
        ],
    )
