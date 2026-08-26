from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from script.model_runtime import (
    emit_benchmark_event,
    ensure_model_calls_allowed,
    fail_fast_model_errors,
    raise_model_failure,
    sanitize_error,
)


@dataclass(frozen=True)
class ModelEndpoint:
    api_key: str
    base_url: str
    model_name: str


def resolve(capability: str = "text") -> ModelEndpoint:
    if capability != "text":
        raise ValueError(f"Unsupported story capability: {capability}")
    import script.tools._shared as shared

    return ModelEndpoint(shared.API_KEY, shared.BASE_URL, shared.MODEL_NAME)


def chat(
    stage: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> str:
    """Use Crayotter's configured text-model client for story development."""

    import script.tools._shared as shared

    ensure_model_calls_allowed()
    model_name = shared.MODEL_NAME
    started = time.perf_counter()
    emit_benchmark_event("model_call_started", {"stage": stage, "model": model_name})
    try:
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = shared._get_openai_client().chat.completions.create(**kwargs)
        content = shared._extract_chat_content(response)
        if not content:
            raise RuntimeError("Model returned empty story content.")
        emit_benchmark_event(
            "model_call_completed",
            {
                "stage": stage,
                "model": model_name,
                "duration_seconds": round(time.perf_counter() - started, 3),
            },
        )
        return content
    except Exception as exc:
        safe = sanitize_error(exc)
        emit_benchmark_event(
            "model_call_failed",
            {"stage": stage, "model": model_name, "error": safe[:300]},
        )
        if fail_fast_model_errors():
            raise_model_failure(
                stage=stage,
                model=model_name,
                message=safe,
                duration_seconds=time.perf_counter() - started,
            )
        raise RuntimeError(f"{stage}: {safe}") from exc
