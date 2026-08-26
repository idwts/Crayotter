"""Canonical parsing helpers for LLM output (stdlib-only).

Single home for the JSON-in-prose extraction that was previously
copy-pasted (with drifting semantics) across phases, the planner, and
the backend plan-patch generator. The semantics are the superset of all
former variants: strip a Markdown code fence if present, then trim to
the outermost ``{...}`` object before parsing.
"""

from __future__ import annotations

import json
from typing import Any


def strip_code_fences(text: str) -> str:
    """Return the content of the first Markdown code fence, if any."""
    content = str(text or "").strip()
    if "```json" in content:
        return content.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in content:
        return content.split("```", 1)[1].split("```", 1)[0].strip()
    return content


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object out of LLM prose (fences and surrounding text tolerated)."""
    content = strip_code_fences(text)
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start : end + 1]
    return json.loads(content)
