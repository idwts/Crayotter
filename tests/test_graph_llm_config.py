from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


class GraphLlmConfigTests(unittest.TestCase):
    def _get_llm_with_env(self, value: str):
        import graph

        with mock.patch.dict(os.environ, {"CRAYOTTER_ENABLE_THINKING": value}):
            with mock.patch.object(graph, "ENABLE_THINKING", value.strip().lower()):
                with mock.patch.object(graph, "ChatOpenAI") as chat:
                    graph._get_llm(temperature=0.1)
                    return chat.call_args.kwargs

    def test_thinking_disabled_passes_extra_body(self) -> None:
        kwargs = self._get_llm_with_env("false")
        self.assertEqual(kwargs.get("extra_body"), {"enable_thinking": False})

    def test_thinking_enabled_passes_extra_body(self) -> None:
        kwargs = self._get_llm_with_env("true")
        self.assertEqual(kwargs.get("extra_body"), {"enable_thinking": True})

    def test_unset_leaves_provider_default(self) -> None:
        kwargs = self._get_llm_with_env("")
        self.assertNotIn("extra_body", kwargs)

    def test_generate_plan_retries_with_previous_error_feedback(self) -> None:
        import inspect

        import graph

        source = inspect.getsource(graph.generate_editing_plan_node)
        self.assertIn("previous_error", source)
        self.assertIn("for attempt in range(1, 3)", source)


if __name__ == "__main__":
    unittest.main()
