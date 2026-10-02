from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from .exploration import append_profile_to_messages, next_counterfactual_profile, sample_rollout_profile
from .fixture import load_fixture, materialize_fixture
from .judge import judge_episode
from .local_rollout_video import analyze_video_with_rollout, local_rollout_analysis_enabled
from .reward import compute_episode_reward, find_final_video_path
from .semantic_artifact import evaluate_semantic_artifact_deltas


def _materialize_terminal_episode(fixture: Any, extra_info: dict[str, Any]) -> dict[str, Any]:
    """Create the artifact workspace for a rollout that executed no tools."""
    tools_kwargs = extra_info.get("tools_kwargs")
    if not isinstance(tools_kwargs, dict) or not tools_kwargs:
        raise ValueError("Phase 3 RL rollout has no tool configuration for episode materialization")

    episode_base_dirs: set[str] = set()
    for tool_kwargs in tools_kwargs.values():
        if not isinstance(tool_kwargs, dict):
            continue
        create_kwargs = tool_kwargs.get("create_kwargs")
        if not isinstance(create_kwargs, dict):
            continue
        episode_base_dir = str(create_kwargs.get("episode_base_dir") or "").strip()
        if episode_base_dir:
            episode_base_dirs.add(episode_base_dir)

    if len(episode_base_dirs) != 1:
        raise ValueError(
            "Phase 3 RL rollout must provide exactly one episode_base_dir; "
            f"got {sorted(episode_base_dirs)}"
        )

    episode_base_dir = Path(next(iter(episode_base_dirs))).resolve()
    episode_root = episode_base_dir / f"{fixture.fixture_id}_no_tool_{uuid4().hex[:12]}"
    materialize_fixture(fixture, episode_root)
    return {
        "fixture_id": fixture.fixture_id,
        "fixture_path": str(fixture.source_path),
        "episode_root": str(episode_root),
        "tool_events": [],
        "termination_reason": "no_tool_call",
    }

try:
    from verl.experimental.agent_loop.agent_loop import register
    from verl.experimental.agent_loop.tool_agent_loop import ToolAgentLoop
    from verl.tools.schemas import ToolResponse
except Exception as exc:  # pragma: no cover - optional dependency
    ToolAgentLoop = None
    register = None
    ToolResponse = None
    _VERL_IMPORT_ERROR = exc
else:
    _VERL_IMPORT_ERROR = None


if ToolAgentLoop is not None and register is not None:

    _PHASE3_EXTRA_FIELD_DEFAULTS: dict[str, Any] = {
        "phase3_tool_trace": [],
        "phase3_episode_state": {},
        "phase3_fixture_path": "",
        "phase3_episode_root": "",
        "phase3_target_duration_seconds": 0.0,
        "phase3_episode_reward": {},
        "phase3_fixture_error": "",
        "phase3_reward_write_error": "",
        "phase3_exploration_profile": {},
    }

    def _normalize_phase3_extra_fields(extra_fields: dict[str, Any]) -> None:
        for key, default_value in _PHASE3_EXTRA_FIELD_DEFAULTS.items():
            if key not in extra_fields:
                if isinstance(default_value, list):
                    extra_fields[key] = []
                elif isinstance(default_value, dict):
                    extra_fields[key] = {}
                else:
                    extra_fields[key] = default_value

    def _write_tool_events(episode_root: str, tool_events: list[dict[str, Any]]) -> None:
        if not episode_root:
            raise ValueError("Missing episode root while persisting Phase 3 tool events")
        events_path = Path(episode_root) / "phase3_tool_events.jsonl"
        with events_path.open("w", encoding="utf-8") as handle:
            for event in tool_events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    @register("crayotter_phase3_tool_agent")
    class CrayotterPhase3ToolAgentLoop(ToolAgentLoop):
        """verl AgentLoop adapter for Crayotter Phase 3."""

        async def _call_tool(self, tool_call, tools_kwargs, agent_data):
            if tool_call.name != "analyze_video" or not local_rollout_analysis_enabled():
                return await super()._call_tool(tool_call, tools_kwargs, agent_data)

            active_tools = getattr(agent_data, "_active_tools", self.tools)
            tool = active_tools.get(tool_call.name)
            if tool is None:
                message = "Local rollout video analysis requested, but analyze_video is not active"
                return ToolResponse(text=message), 0.0, {"success": False}
            if not hasattr(tool, "execute_with_handler"):
                message = "analyze_video tool does not support the local rollout backend"
                return ToolResponse(text=message), 0.0, {"success": False}
            try:
                parameters = json.loads(tool_call.arguments)
            except (json.JSONDecodeError, TypeError) as exc:
                return ToolResponse(text=f"Invalid JSON in arguments for 'analyze_video': {exc}"), 0.0, {
                    "success": False
                }

            instance_id = None
            try:
                kwargs = tools_kwargs.get(tool_call.name, {})
                instance_id, _ = await tool.create(create_kwargs=kwargs.get("create_kwargs", {}))
                response, reward, metadata = await tool.execute_with_handler(
                    instance_id,
                    parameters,
                    handler=lambda episode_root, args: analyze_video_with_rollout(
                        self,
                        episode_root,
                        args,
                    ),
                    agent_data=agent_data,
                )
            except Exception as exc:
                return ToolResponse(text=f"Error executing local analyze_video: {exc}"), 0.0, {
                    "success": False
                }
            finally:
                if instance_id is not None:
                    await tool.release(instance_id)

            text = response.text or ""
            if len(text) > self.max_tool_response_length:
                if self.tool_response_truncate_side == "left":
                    text = "(truncated)..." + text[-self.max_tool_response_length :]
                elif self.tool_response_truncate_side == "right":
                    text = text[: self.max_tool_response_length] + "...(truncated)"
                else:
                    half = self.max_tool_response_length // 2
                    text = text[:half] + "...(truncated)..." + text[-half:]
            return ToolResponse(text=text), reward, metadata

        async def run(self, sampling_params: dict[str, Any], **kwargs):
            extra_info = kwargs.get("extra_info")
            if not isinstance(extra_info, dict):
                extra_info = {}
            task_metadata = extra_info.get("task_metadata")
            if not isinstance(task_metadata, dict):
                task_metadata = {}
            exploration_profile: dict[str, Any] = {}
            per_rollout_exploration = os.environ.get(
                "CRAYOTTER_RL_PER_ROLLOUT_STRATEGY",
                "1",
            ).strip().lower() not in {"0", "false", "no", "off"}
            if per_rollout_exploration and (
                task_metadata.get("long_horizon_task")
                or task_metadata.get("enable_diverse_rollout")
                or task_metadata.get("horizon_suite")
            ):
                counterfactual = os.environ.get(
                    "CRAYOTTER_RL_COUNTERFACTUAL_BRANCHING",
                    "0",
                ).strip().lower() in {"1", "true", "yes", "on"}
                task_key = str(
                    extra_info.get("fixture_id")
                    or extra_info.get("fixture_path")
                    or kwargs.get("user_request")
                    or "unknown_task"
                )
                exploration_profile = (
                    next_counterfactual_profile(task_key)
                    if counterfactual
                    else sample_rollout_profile()
                )
                raw_prompt = kwargs.get("raw_prompt")
                if isinstance(raw_prompt, list):
                    kwargs = dict(kwargs)
                    kwargs["raw_prompt"] = append_profile_to_messages(
                        raw_prompt,
                        exploration_profile,
                    )
            output = await super().run(sampling_params, **kwargs)
            _normalize_phase3_extra_fields(output.extra_fields)
            output.extra_fields["phase3_exploration_profile"] = exploration_profile
            tool_events = list(output.extra_fields.get("phase3_tool_trace", []))
            episode_state = dict(output.extra_fields.get("phase3_episode_state", {}))
            fixture_path = str(
                episode_state.get("fixture_path")
                or output.extra_fields.get("phase3_fixture_path")
                or extra_info.get("fixture_path")
                or ""
            ).strip()
            target_duration = float(
                output.extra_fields.get(
                    "phase3_target_duration_seconds",
                    kwargs.get(
                        "target_duration_seconds",
                        extra_info.get("target_duration_seconds", 0.0),
                    ),
                )
                or 0.0
            )
            user_request = str(
                kwargs.get("user_request")
                or extra_info.get("user_request")
                or ""
            )
            editing_blueprint = ""
            episode_metadata: dict[str, Any] = {}
            if not fixture_path:
                raise ValueError("Phase 3 RL rollout is missing fixture_path")
            try:
                fixture = load_fixture(fixture_path)
            except Exception as exc:
                raise RuntimeError(f"Failed to load Phase 3 fixture: {fixture_path}") from exc
            user_request = fixture.user_request
            editing_blueprint = fixture.editing_blueprint
            target_duration = fixture.target_duration_seconds
            episode_metadata = fixture.metadata
            if exploration_profile.get("counterfactual"):
                episode_metadata = dict(episode_metadata)
                episode_metadata["counterfactual_prefix"] = {
                    key: exploration_profile[key]
                    for key in (
                        "prefix_id",
                        "branch_index",
                        "branch_count",
                        "branch_point_event_index",
                        "branch_point_stage",
                        "shared_prefix",
                        "id",
                        "name",
                    )
                }
            output.extra_fields["phase3_fixture_path"] = fixture_path
            output.extra_fields["phase3_target_duration_seconds"] = target_duration

            final_video_path = find_final_video_path(tool_events)
            episode_root = str(
                episode_state.get("episode_root")
                or output.extra_fields.get("phase3_episode_root")
                or ""
            ).strip()
            if not episode_root:
                episode_state = _materialize_terminal_episode(fixture, extra_info)
                episode_root = str(episode_state["episode_root"])
            judge_result, semantic_delta = await asyncio.gather(
                judge_episode(
                    user_request=user_request,
                    target_duration_seconds=target_duration,
                    editing_blueprint=editing_blueprint,
                    tool_events=tool_events,
                    final_output="completed" if output.response_ids else "",
                    final_video_path=final_video_path,
                ),
                evaluate_semantic_artifact_deltas(
                    user_request=user_request,
                    tool_events=tool_events,
                    episode_root=episode_root,
                    episode_metadata=episode_metadata,
                ),
            )
            reward_summary = compute_episode_reward(
                tool_events=tool_events,
                target_duration_seconds=target_duration,
                final_output="completed" if output.response_ids else "",
                judge_result=judge_result,
                episode_metadata=episode_metadata,
            )
            reward_summary["user_request"] = user_request[:4000]
            reward_summary["editing_blueprint_excerpt"] = editing_blueprint[:4000]
            reward_summary["exploration_profile"] = exploration_profile
            reward_summary["semantic_artifact_delta"] = semantic_delta
            if os.environ.get("CRAYOTTER_RL_PROCESS_REWARD", "0").lower() not in {"1", "true", "yes"}:
                output.reward_score = reward_summary["total_reward"]
            output.extra_fields["phase3_episode_reward"] = reward_summary
            output.extra_fields["phase3_episode_root"] = episode_root
            output.extra_fields["phase3_tool_trace"] = tool_events
            output.extra_fields["phase3_episode_state"] = episode_state
            reward_path = Path(episode_root) / "phase3_episode_reward.json"
            reward_path.write_text(
                json.dumps(reward_summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _write_tool_events(episode_root, tool_events)
            _normalize_phase3_extra_fields(output.extra_fields)
            return output

else:

    class CrayotterPhase3ToolAgentLoop:  # pragma: no cover
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "verl is required to instantiate CrayotterPhase3ToolAgentLoop. "
                f"Original import error: {_VERL_IMPORT_ERROR}"
            )
