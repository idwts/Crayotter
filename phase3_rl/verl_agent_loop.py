from __future__ import annotations

from typing import Any

from .reward import compute_episode_reward

try:
    from verl.experimental.agent_loop.agent_loop import register
    from verl.experimental.agent_loop.tool_agent_loop import ToolAgentLoop
except Exception as exc:  # pragma: no cover - optional dependency
    ToolAgentLoop = None
    register = None
    _VERL_IMPORT_ERROR = exc
else:
    _VERL_IMPORT_ERROR = None


if ToolAgentLoop is not None and register is not None:

    @register("crayotter_phase3_tool_agent")
    class CrayotterPhase3ToolAgentLoop(ToolAgentLoop):
        """verl AgentLoop adapter for Crayotter Phase 3."""

        async def run(self, sampling_params: dict[str, Any], **kwargs):
            output = await super().run(sampling_params, **kwargs)
            tool_events = list(output.extra_fields.get("phase3_tool_trace", []))
            target_duration = float(
                output.extra_fields.get(
                    "phase3_target_duration_seconds",
                    kwargs.get("target_duration_seconds", 0.0),
                )
                or 0.0
            )
            reward_summary = compute_episode_reward(
                tool_events=tool_events,
                target_duration_seconds=target_duration,
                final_output="completed" if output.response_ids else "",
            )
            output.reward_score = reward_summary["total_reward"]
            output.extra_fields["phase3_episode_reward"] = reward_summary
            return output

else:

    class CrayotterPhase3ToolAgentLoop:  # pragma: no cover
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "verl is required to instantiate CrayotterPhase3ToolAgentLoop. "
                f"Original import error: {_VERL_IMPORT_ERROR}"
            )
