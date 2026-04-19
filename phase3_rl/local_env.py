from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .fixture import Phase3Fixture, build_episode_root, materialize_fixture
from .policies import PolicyResponse
from .prompt_builder import build_phase3_messages
from .reward import compute_episode_reward, compute_step_reward
from .tool_catalog import get_openai_tool_schemas, get_phase3_tool_names
from .tool_runtime import ToolExecutionResult, execute_tool_subprocess, load_api_config_from_env


class Phase3RolloutEnv:
    def __init__(
        self,
        *,
        fixture: Phase3Fixture,
        policy: Any,
        episode_base_dir: str | Path,
        python_executable: str | None = None,
        tool_timeout_seconds: int = 900,
        api_config: dict[str, str] | None = None,
    ) -> None:
        self.fixture = fixture
        self.policy = policy
        self.episode_root = build_episode_root(episode_base_dir, fixture.fixture_id)
        materialize_fixture(fixture, self.episode_root)
        self.tool_names = fixture.allowed_tools or get_phase3_tool_names()
        self.tool_schemas = get_openai_tool_schemas(self.tool_names)
        self.messages = build_phase3_messages(
            user_request=fixture.user_request,
            target_duration_seconds=fixture.target_duration_seconds,
            editing_blueprint=fixture.editing_blueprint,
            runtime_root=self.episode_root,
            tool_names=self.tool_names,
        )
        self.python_executable = python_executable
        self.tool_timeout_seconds = tool_timeout_seconds
        self.api_config = api_config or load_api_config_from_env()
        self.tool_events: list[dict[str, Any]] = []
        self.turns: list[dict[str, Any]] = []
        self.final_output = ""

    def _tool_message(self, tool_call_id: str, content: str) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }

    def _assistant_message(self, response: PolicyResponse) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": response.assistant_content,
        }
        if response.tool_calls:
            message["tool_calls"] = response.tool_calls
        return message

    def _record_tool_event(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        execution: ToolExecutionResult,
        tool_call_id: str,
    ) -> dict[str, Any]:
        reward = compute_step_reward(tool_name=tool_name, execution=execution, prior_events=self.tool_events)
        event = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
            "success": execution.success,
            "raw_result": execution.raw_result,
            "parsed_result": execution.parsed_result,
            "output_paths": execution.output_paths,
            "duration_seconds": execution.duration_seconds,
            "returncode": execution.returncode,
            "stdout_tail": execution.stdout[-1200:],
            "stderr_tail": execution.stderr[-1200:],
            "signature": f"{tool_name}:{json.dumps(arguments, ensure_ascii=False, sort_keys=True)}",
            "step_reward": reward.total,
            "step_reward_components": reward.components,
        }
        self.tool_events.append(event)
        return event

    def run(self, max_turns: int = 20) -> dict[str, Any]:
        for turn_index in range(max_turns):
            response: PolicyResponse = self.policy.generate(self.messages, self.tool_schemas)
            assistant_message = self._assistant_message(response)
            self.messages.append(assistant_message)

            turn_record: dict[str, Any] = {
                "turn_index": turn_index,
                "assistant_content": response.assistant_content,
                "tool_calls": [],
            }

            if not response.tool_calls:
                self.final_output = response.assistant_content.strip()
                self.turns.append(turn_record)
                break

            for call in response.tool_calls:
                tool_name = str(call["function"]["name"])
                arguments = json.loads(call["function"]["arguments"])
                execution = execute_tool_subprocess(
                    tool_name=tool_name,
                    arguments=arguments,
                    runtime_root=self.episode_root,
                    api_config=self.api_config,
                    python_executable=self.python_executable,
                    timeout_seconds=self.tool_timeout_seconds,
                )
                event = self._record_tool_event(
                    tool_name=tool_name,
                    arguments=arguments,
                    execution=execution,
                    tool_call_id=str(call["id"]),
                )
                turn_record["tool_calls"].append(event)
                self.messages.append(self._tool_message(str(call["id"]), execution.raw_result))

            self.turns.append(turn_record)
        else:
            self.final_output = self.final_output or "Episode stopped because max_turns was reached."

        reward_summary = compute_episode_reward(
            tool_events=self.tool_events,
            target_duration_seconds=self.fixture.target_duration_seconds,
            final_output=self.final_output,
        )
        trace = {
            "fixture_id": self.fixture.fixture_id,
            "episode_root": str(self.episode_root),
            "tool_names": self.tool_names,
            "turns": self.turns,
            "tool_events": self.tool_events,
            "final_output": self.final_output,
            "reward_summary": reward_summary,
        }

        trace_path = self.episode_root / "phase3_episode_trace.json"
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")

        jsonl_path = self.episode_root / "phase3_tool_events.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as handle:
            for event in self.tool_events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

        transcript_path = self.episode_root / "phase3_messages.json"
        transcript_path.write_text(json.dumps(self.messages, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "episode_root": str(self.episode_root),
            "trace_path": str(trace_path),
            "tool_events_path": str(jsonl_path),
            "messages_path": str(transcript_path),
            "reward_summary": reward_summary,
            "final_output": self.final_output,
        }
