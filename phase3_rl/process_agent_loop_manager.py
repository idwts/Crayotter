from __future__ import annotations

from typing import Any

try:
    from verl.experimental.agent_loop import AgentLoopManager
except Exception as exc:  # pragma: no cover - optional dependency
    AgentLoopManager = object
    _VERL_IMPORT_ERROR = exc
else:
    _VERL_IMPORT_ERROR = None

from .process_reward_manager import (
    CrayotterProcessRewardManager,
    _finite_float,
    emit_process_trainer_metrics,
)


class CrayotterProcessRewardAgentLoopManager(AgentLoopManager):
    """Attach token-level Crayotter process rewards to rollout batches."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if _VERL_IMPORT_ERROR is not None:  # pragma: no cover
            raise ImportError(f"verl is required: {_VERL_IMPORT_ERROR}")
        kwargs["reward_loop_worker_handles"] = None
        super().__init__(*args, **kwargs)

    def generate_sequences(self, prompts):
        global_step_raw = (getattr(prompts, "meta_info", {}) or {}).get("global_steps")
        global_step = int(global_step_raw) if global_step_raw is not None else None
        output = super().generate_sequences(prompts)
        if "rm_scores" not in output.batch.keys():
            reward_summaries = output.non_tensor_batch.get("phase3_episode_reward")
            if reward_summaries is None:
                scores = [0.0] * len(output)
            else:
                scores = []
                for item in reward_summaries:
                    if isinstance(item, dict):
                        scores.append(_finite_float(item.get("total_reward")))
                    else:
                        scores.append(0.0)

            output.batch["rm_scores"] = CrayotterProcessRewardManager.assemble_rm_scores(output, scores)
        emit_process_trainer_metrics(output, global_step=global_step)
        return output
