from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from .fixture import load_fixture, materialize_fixture
from .reward import compute_step_reward
from .tool_runtime import execute_tool_subprocess, load_api_config_from_env

try:
    from verl.tools.base_tool import BaseTool
    from verl.tools.schemas import OpenAIFunctionToolSchema, ToolResponse
except Exception as exc:  # pragma: no cover - optional dependency
    BaseTool = None
    OpenAIFunctionToolSchema = Any
    ToolResponse = Any
    _VERL_IMPORT_ERROR = exc
else:
    _VERL_IMPORT_ERROR = None


if BaseTool is not None:

    class CrayotterSubprocessTool(BaseTool):
        """Wrap a Crayotter Phase 3 tool as a verl native tool."""

        def __init__(self, config: dict, tool_schema: OpenAIFunctionToolSchema):
            super().__init__(config, tool_schema)
            self.tool_name = str(config.get("tool_name") or self.name)
            self._instance_kwargs: dict[str, dict[str, Any]] = {}

        async def create(self, instance_id: str | None = None, **kwargs):
            instance_id = instance_id or uuid4().hex
            self._instance_kwargs[instance_id] = dict(kwargs.get("create_kwargs", {}))
            return instance_id, ToolResponse(text="")

        async def execute(self, instance_id: str, parameters: dict[str, Any], **kwargs):
            create_kwargs = self._instance_kwargs.get(instance_id, {})
            fixture_path = str(create_kwargs.get("fixture_path", "")).strip()
            if not fixture_path:
                return ToolResponse(text="Missing fixture_path in create_kwargs."), -0.5, {"success": False}

            episode_base_dir = Path(str(create_kwargs.get("episode_base_dir", Path("phase3_rl/runs/verl")))).resolve()
            agent_data = kwargs.get("agent_data")
            if agent_data is not None:
                state = agent_data.extra_fields.get("phase3_episode_state")
                if not state:
                    fixture = load_fixture(fixture_path)
                    episode_root = episode_base_dir / f"{fixture.fixture_id}_{agent_data.request_id}"
                    materialize_fixture(fixture, episode_root)
                    state = {
                        "fixture_id": fixture.fixture_id,
                        "fixture_path": fixture_path,
                        "episode_root": str(episode_root),
                        "tool_events": [],
                    }
                    agent_data.extra_fields["phase3_episode_state"] = state
                    agent_data.extra_fields["phase3_target_duration_seconds"] = fixture.target_duration_seconds
                episode_root = Path(state["episode_root"])
                prior_events = list(state.get("tool_events", []))
            else:  # pragma: no cover
                fixture = load_fixture(fixture_path)
                episode_root = episode_base_dir / f"{fixture.fixture_id}_{uuid4().hex[:8]}"
                materialize_fixture(fixture, episode_root)
                prior_events = []

            execution = execute_tool_subprocess(
                tool_name=self.tool_name,
                arguments=parameters,
                runtime_root=episode_root,
                api_config=load_api_config_from_env(),
            )

            reward = compute_step_reward(tool_name=self.tool_name, execution=execution, prior_events=prior_events)
            event = {
                "tool_name": self.tool_name,
                "arguments": parameters,
                "success": execution.success,
                "raw_result": execution.raw_result,
                "parsed_result": execution.parsed_result,
                "output_paths": execution.output_paths,
                "duration_seconds": execution.duration_seconds,
                "step_reward": reward.total,
                "step_reward_components": reward.components,
                "signature": f"{self.tool_name}:{parameters}",
            }

            if agent_data is not None:
                state = agent_data.extra_fields["phase3_episode_state"]
                state.setdefault("tool_events", []).append(event)
                agent_data.extra_fields["phase3_tool_trace"] = list(state["tool_events"])
                agent_data.extra_fields["phase3_episode_root"] = str(episode_root)

            return ToolResponse(text=execution.raw_result), reward.total, {"success": execution.success}

        async def release(self, instance_id: str, **kwargs) -> None:
            self._instance_kwargs.pop(instance_id, None)

else:

    class CrayotterSubprocessTool:  # pragma: no cover
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "verl is required to instantiate CrayotterSubprocessTool. "
                f"Original import error: {_VERL_IMPORT_ERROR}"
            )
