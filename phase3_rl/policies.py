from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from openai import OpenAI


@dataclass(slots=True)
class PolicyResponse:
    assistant_content: str
    tool_calls: list[dict[str, Any]]
    raw_response: dict[str, Any] | None = None


class ScriptedPolicy:
    def __init__(self, scripted_turns: list[Any]) -> None:
        self._turns = list(scripted_turns)
        self._cursor = 0

    def generate(self, messages: list[dict[str, Any]], tool_schemas: list[dict[str, Any]]) -> PolicyResponse:
        if self._cursor >= len(self._turns):
            return PolicyResponse(
                assistant_content="本轮 scripted policy 已经没有额外动作，结束执行。",
                tool_calls=[],
            )

        turn = self._turns[self._cursor]
        self._cursor += 1
        tool_calls: list[dict[str, Any]] = []
        for call in turn.tool_calls:
            tool_calls.append(
                {
                    "id": f"call_{uuid4().hex}",
                    "type": "function",
                    "function": {
                        "name": call.tool_name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
            )
        return PolicyResponse(
            assistant_content=turn.assistant_content,
            tool_calls=tool_calls,
        )


class OpenAIToolPolicy:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_name: str,
        temperature: float = 0.2,
        max_tokens: int = 1800,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAIToolPolicy requires a non-empty api_key.")
        if not model_name:
            raise ValueError("OpenAIToolPolicy requires a non-empty model_name.")
        self.client = OpenAI(api_key=api_key, base_url=base_url or None)
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate(self, messages: list[dict[str, Any]], tool_schemas: list[dict[str, Any]]) -> PolicyResponse:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            tools=tool_schemas,
            tool_choice="auto",
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        choice = response.choices[0]
        message = choice.message
        content = message.content or ""
        tool_calls: list[dict[str, Any]] = []
        for tool_call in getattr(message, "tool_calls", []) or []:
            tool_calls.append(
                {
                    "id": getattr(tool_call, "id", None) or f"call_{uuid4().hex}",
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
            )
        return PolicyResponse(
            assistant_content=content,
            tool_calls=tool_calls,
            raw_response=response.model_dump() if hasattr(response, "model_dump") else None,
        )
