from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from app.backend.models import AppConfig, ServiceProfile
from app.model_defaults import (
    DEFAULT_MODEL_NAME, DEFAULT_TTS_MODEL_NAME, DEFAULT_VIDEO_MODEL_NAME,
    OUTPUT_TOKEN_DEFAULTS, model_request_timeout, output_tokens,
)
from script.runtime.settings import RuntimeSettings


class ModelDefaultTests(unittest.TestCase):
    def test_video_request_timeout_follows_agent_waiting_setting(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(model_request_timeout(), 1200)
        with patch.dict(os.environ, {"CRAYOTTER_AGENT_STALL_TIMEOUT_SECONDS": "1800"}):
            self.assertEqual(model_request_timeout(), 1800)

    def test_runtime_and_backend_use_the_tested_flash_models(self):
        for config in [RuntimeSettings(), ServiceProfile()]:
            self.assertEqual(config.model_name, "qwen3.8-flash")
            self.assertEqual(config.video_model_name, "qwen3.8-omni-flash")
            self.assertEqual(config.tts_model_name, "qwen3-tts-flash")
        self.assertEqual(RuntimeSettings().agent_stall_timeout_seconds, 1200)
        self.assertEqual(AppConfig().agent_stall_timeout_seconds, 1200)

    def test_explicit_service_model_choices_are_preserved(self):
        config = ServiceProfile(model_name="custom-text", video_model_name="qwen3.8-flash")
        self.assertEqual(config.to_runtime_config()["model_name"], "custom-text")
        self.assertEqual(config.video_model_name, "qwen3.8-flash")

    def test_stage_budgets_leave_room_for_reasoning_and_structured_output(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(output_tokens("phase1"), 32768)
            self.assertEqual(output_tokens("phase2"), 65536)
            self.assertEqual(output_tokens("phase3"), 32768)
            self.assertEqual(output_tokens("video_analysis"), 32768)
            self.assertTrue(all(value <= 131072 for value in OUTPUT_TOKEN_DEFAULTS.values()))

    def test_env_override_and_invalid_model_output_limits(self):
        with patch.dict(os.environ, {"CRAYOTTER_PHASE2_MAX_OUTPUT_TOKENS": "98304"}):
            self.assertEqual(output_tokens("phase2"), 98304)
        for invalid in ["0", "-1", "131073", "abc"]:
            with self.subTest(invalid=invalid), patch.dict(os.environ, {"CRAYOTTER_PHASE2_MAX_OUTPUT_TOKENS": invalid}):
                with self.assertRaises(ValueError):
                    output_tokens("phase2")

    def test_langchain_sends_the_full_budget_without_disabling_thinking(self):
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage
        llm = ChatOpenAI(api_key="test-only", model=DEFAULT_MODEL_NAME, max_tokens=output_tokens("phase2"))
        payload = llm._get_request_payload([HumanMessage(content="test")])
        self.assertEqual(payload["max_completion_tokens"], 65536)
        self.assertNotIn("enable_thinking", payload)

    def test_truncated_nonempty_output_is_not_accepted_as_a_complete_plan(self):
        import script.graph as graph
        llm = SimpleNamespace(
            kwargs={"max_tokens": 65536},
            invoke=lambda _: SimpleNamespace(content='{"clips": [', response_metadata={"finish_reason": "length"}),
        )
        with patch.object(graph, "fail_fast_model_errors", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "refusing truncated output"):
                graph._invoke_llm(llm, [], "phase2_research")


if __name__ == "__main__":
    unittest.main()
