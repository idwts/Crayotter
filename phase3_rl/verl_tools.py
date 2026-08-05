from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from .fixture import load_fixture, materialize_fixture
from .reward import build_tool_signature, classify_tool_stage, compute_step_reward
from .tool_runtime import execute_tool_subprocess_async, load_api_config_from_env

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

    def _append_tool_event(episode_root: Path, event: dict[str, Any]) -> None:
        events_path = episode_root / "phase3_tool_events.jsonl"
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

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

        def _prepare_episode(
            self,
            instance_id: str,
            agent_data: Any,
        ) -> tuple[Path, list[dict[str, Any]]]:
            create_kwargs = self._instance_kwargs.get(instance_id, {})
            fixture_path = str(create_kwargs.get("fixture_path", "")).strip()
            if not fixture_path:
                raise ValueError("Missing fixture_path in Crayotter tool create_kwargs")

            episode_base_dir = Path(str(create_kwargs.get("episode_base_dir", Path("phase3_rl/runs/verl")))).resolve()
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
                    agent_data.extra_fields["phase3_fixture_path"] = fixture_path
                    agent_data.extra_fields["phase3_target_duration_seconds"] = fixture.target_duration_seconds
                episode_root = Path(state["episode_root"])
                prior_events = list(state.get("tool_events", []))
            else:  # pragma: no cover
                fixture = load_fixture(fixture_path)
                episode_root = episode_base_dir / f"{fixture.fixture_id}_{uuid4().hex[:8]}"
                materialize_fixture(fixture, episode_root)
                prior_events = []
            return episode_root, prior_events

        def _record_execution(
            self,
            *,
            parameters: dict[str, Any],
            execution: Any,
            episode_root: Path,
            prior_events: list[dict[str, Any]],
            agent_data: Any,
        ):
            reward = compute_step_reward(tool_name=self.tool_name, execution=execution, prior_events=prior_events)
            event = {
                "tool_name": self.tool_name,
                "stage": classify_tool_stage(self.tool_name),
                "arguments": parameters,
                "success": execution.success,
                "raw_result": execution.raw_result,
                "parsed_result": execution.parsed_result,
                "output_paths": execution.output_paths,
                "duration_seconds": execution.duration_seconds,
                "returncode": execution.returncode,
                "step_reward": reward.total,
                "step_reward_components": reward.components,
                "signature": build_tool_signature(self.tool_name, parameters),
            }

            if agent_data is not None:
                state = agent_data.extra_fields["phase3_episode_state"]
                state.setdefault("tool_events", []).append(event)
                agent_data.extra_fields["phase3_tool_trace"] = list(state["tool_events"])
                agent_data.extra_fields["phase3_episode_root"] = str(episode_root)
                _append_tool_event(episode_root, event)

            return ToolResponse(text=execution.raw_result), reward.total, {"success": execution.success}

        async def execute(self, instance_id: str, parameters: dict[str, Any], **kwargs):
            agent_data = kwargs.get("agent_data")
            episode_root, prior_events = self._prepare_episode(instance_id, agent_data)

            execution = await execute_tool_subprocess_async(
                tool_name=self.tool_name,
                arguments=parameters,
                runtime_root=episode_root,
                api_config=load_api_config_from_env(),
            )
            return self._record_execution(
                parameters=parameters,
                execution=execution,
                episode_root=episode_root,
                prior_events=prior_events,
                agent_data=agent_data,
            )

        async def execute_with_handler(
            self,
            instance_id: str,
            parameters: dict[str, Any],
            *,
            handler: Callable[[Path, dict[str, Any]], Awaitable[Any]],
            agent_data: Any,
        ):
            episode_root, prior_events = self._prepare_episode(instance_id, agent_data)
            execution = await handler(episode_root, parameters)
            return self._record_execution(
                parameters=parameters,
                execution=execution,
                episode_root=episode_root,
                prior_events=prior_events,
                agent_data=agent_data,
            )

        async def release(self, instance_id: str, **kwargs) -> None:
            self._instance_kwargs.pop(instance_id, None)

else:

    class CrayotterSubprocessTool:  # pragma: no cover
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "verl is required to instantiate CrayotterSubprocessTool. "
                f"Original import error: {_VERL_IMPORT_ERROR}"
            )
