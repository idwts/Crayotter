"""Shared model defaults and per-request output limits for the main application."""
from __future__ import annotations

import os

DEFAULT_MODEL_NAME = "qwen3.8-flash"
DEFAULT_VIDEO_MODEL_NAME = "qwen3.8-omni-flash"
DEFAULT_TTS_MODEL_NAME = "qwen3-tts-flash"
DEFAULT_AGENT_STALL_TIMEOUT_SECONDS = 1200

# These are output ceilings per call, not context-window sizes or token targets.
# Both tested Qwen3.8 models support up to 131072 output tokens, including reasoning.
OUTPUT_TOKEN_DEFAULTS = {
    "phase1": 32768,
    "phase2": 65536,
    "phase3": 32768,
    "video_analysis": 32768,
    "auxiliary": 8192,
}


def model_request_timeout() -> int:
    """Keep video requests within the configured agent waiting window."""
    raw = os.environ.get("CRAYOTTER_AGENT_STALL_TIMEOUT_SECONDS", "").strip()
    return max(10, int(raw) if raw else DEFAULT_AGENT_STALL_TIMEOUT_SECONDS)


def output_tokens(stage: str) -> int:
    default = OUTPUT_TOKEN_DEFAULTS[stage]
    name = f"CRAYOTTER_{stage.upper()}_MAX_OUTPUT_TOKENS"
    raw = os.environ.get(name, "").strip()
    value = int(raw) if raw else default
    if not 1 <= value <= 131072:
        raise ValueError(f"{name} must be between 1 and 131072")
    return value
