"""Dependency context injected into the workflow compatibility layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .settings import RuntimeSettings


EventSink = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class WorkflowContext:
    """Static dependencies for one workflow run.

    Mutable progress remains in ``AgentState``.  This context deliberately
    contains only configuration, registries, paths, and infrastructure hooks.
    """

    settings: RuntimeSettings
    tools: Any
    workspace: Path
    user_workspace: Path
    memory_experience_dir: Path
    skills: Any = None
    event_sink: EventSink | None = None

    def with_event_sink(self, sink: EventSink | None) -> "WorkflowContext":
        return WorkflowContext(
            settings=self.settings,
            tools=self.tools,
            workspace=self.workspace,
            user_workspace=self.user_workspace,
            memory_experience_dir=self.memory_experience_dir,
            skills=self.skills,
            event_sink=sink,
        )
