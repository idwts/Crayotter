from __future__ import annotations

from typing import Any

from langchain_core.utils.function_calling import convert_to_openai_tool

from script import graph as graph_module
from script import tools as tools_module


def get_tool_map() -> dict[str, Any]:
    return {getattr(tool, "name", ""): tool for tool in tools_module.ALL_TOOLS}


def get_phase3_tool_names() -> list[str]:
    return sorted(graph_module.EDITING_TOOL_NAMES)


def get_tools_by_name(tool_names: list[str]) -> list[Any]:
    tool_map = get_tool_map()
    missing = [name for name in tool_names if name not in tool_map]
    if missing:
        raise KeyError(f"Unknown tools requested: {missing}")
    return [tool_map[name] for name in tool_names]


def get_openai_tool_schemas(tool_names: list[str]) -> list[dict[str, Any]]:
    return [convert_to_openai_tool(tool) for tool in get_tools_by_name(tool_names)]
