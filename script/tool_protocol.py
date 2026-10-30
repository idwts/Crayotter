"""Transport-independent tool contracts and a compatibility adapter for LangChain.

All tool schemas are derived from the existing typed functions. New callers use
ToolRegistry.call(); existing graph callers keep their LangChain tools/results.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict


class ToolError(BaseModel):
    code: str
    message: str


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "error"]
    data: Any = None
    text: str = ""
    error: ToolError | None = None

    @property
    def is_error(self) -> bool:
        return self.status == "error"


# Match an error prefix, never error words inside valid analysis content.
_ERROR_PREFIX = re.compile(r"^(?:[^\n:：]{0,32}(?:出错|失败|异常)|error|exception|traceback)\s*[:：]", re.I)


def normalize_tool_result(raw: Any) -> ToolResult:
    if isinstance(raw, ToolResult):
        return raw
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    try:
        data = json.loads(text) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        data = None
    failed = bool(_ERROR_PREFIX.match(text.strip()))
    if isinstance(data, dict):
        failed = failed or str(data.get("status", "")).lower() in {"error", "failed", "failure"}
        failed = failed or data.get("success") is False
    if failed:
        message = str(data.get("message") or data.get("error") or text) if isinstance(data, dict) else text
        return ToolResult(status="error", data=data, text=text, error=ToolError(code="execution_error", message=message))
    return ToolResult(status="success", data=data, text=text)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    phases: tuple[str, ...] = ()

    def to_mcp(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "inputSchema": self.input_schema, "outputSchema": ToolResult.model_json_schema()}


class ToolRegistry:
    """One registry for internal structured calls, LangChain, and MCP adapters."""

    def __init__(self, tools: Iterable[Any], *, phase_names: dict[str, set[str]] | None = None) -> None:
        self._tools: dict[str, Any] = {}
        self._definitions: dict[str, ToolDefinition] = {}
        for tool in tools:
            name = tool.name
            if name in self._tools:
                raise ValueError(f"duplicate tool registration: {name}")
            schema = tool.get_input_schema().model_json_schema()
            schema["additionalProperties"] = False
            self._tools[name] = tool
            self._definitions[name] = ToolDefinition(
                name=name, description=tool.description, input_schema=schema,
                phases=tuple(phase for phase, names in (phase_names or {}).items() if name in names),
            )

    def definitions(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def langchain_tools(self) -> list[Any]:
        """Keep tool IDs, argument schemas, exceptions and legacy output intact."""
        return list(self._tools.values())

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(status="error", error=ToolError(code="unknown_tool", message=f"Unknown tool: {name}"))
        if arguments is None:
            arguments = {}
        try:
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
            schema = tool.get_input_schema()
            unknown = set(arguments) - set(schema.model_fields)
            if unknown:
                raise ValueError(f"Unknown arguments: {', '.join(sorted(unknown))}")
            validated = schema.model_validate(arguments, strict=True)
        except (ValueError, TypeError) as exc:
            return ToolResult(status="error", error=ToolError(code="invalid_arguments", message=str(exc)))
        try:
            return normalize_tool_result(tool.invoke(validated.model_dump()))
        except Exception as exc:
            return ToolResult(status="error", error=ToolError(code="execution_error", message=str(exc)))
