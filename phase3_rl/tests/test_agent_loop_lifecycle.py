from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from phase3_rl.verl_agent_loop import _materialize_terminal_episode
from phase3_rl.verl_tools import CrayotterSubprocessTool


class AgentLoopLifecycleTests(unittest.TestCase):
    def test_no_tool_rollout_gets_an_explicit_terminal_workspace(self) -> None:
        fixture = SimpleNamespace(fixture_id="fixture_a", source_path=Path("fixture.json"))
        with tempfile.TemporaryDirectory() as temp_dir:
            extra_info = {
                "tools_kwargs": {
                    "cut_video": {"create_kwargs": {"episode_base_dir": temp_dir}},
                    "export_video": {"create_kwargs": {"episode_base_dir": temp_dir}},
                }
            }
            with patch("phase3_rl.verl_agent_loop.materialize_fixture") as materialize:
                state = _materialize_terminal_episode(fixture, extra_info)

        self.assertEqual(state["termination_reason"], "no_tool_call")
        self.assertEqual(state["tool_events"], [])
        self.assertIn("fixture_a_no_tool_", state["episode_root"])
        materialize.assert_called_once_with(fixture, Path(state["episode_root"]))

    def test_terminal_workspace_rejects_ambiguous_episode_roots(self) -> None:
        fixture = SimpleNamespace(fixture_id="fixture_a", source_path=Path("fixture.json"))
        extra_info = {
            "tools_kwargs": {
                "cut_video": {"create_kwargs": {"episode_base_dir": "/tmp/a"}},
                "export_video": {"create_kwargs": {"episode_base_dir": "/tmp/b"}},
            }
        }
        with self.assertRaisesRegex(ValueError, "exactly one episode_base_dir"):
            _materialize_terminal_episode(fixture, extra_info)

    @unittest.skipUnless(
        hasattr(CrayotterSubprocessTool, "_prepare_episode"),
        "verl is not installed",
    )
    def test_tool_lifecycle_uses_the_provided_agent_data(self) -> None:
        fixture = SimpleNamespace(
            fixture_id="fixture_a",
            source_path=Path("fixture.json"),
            target_duration_seconds=60.0,
        )
        agent_data = SimpleNamespace(extra_fields={}, request_id="request_a")
        tool = object.__new__(CrayotterSubprocessTool)
        with tempfile.TemporaryDirectory() as temp_dir:
            tool._instance_kwargs = {
                "instance_a": {
                    "fixture_path": "fixture.json",
                    "episode_base_dir": temp_dir,
                }
            }
            with (
                patch("phase3_rl.verl_tools.load_fixture", return_value=fixture),
                patch("phase3_rl.verl_tools.materialize_fixture") as materialize,
            ):
                episode_root, prior_events = tool._prepare_episode(
                    "instance_a",
                    agent_data,
                )

        self.assertEqual(prior_events, [])
        self.assertEqual(
            agent_data.extra_fields["phase3_episode_state"]["episode_root"],
            str(episode_root),
        )
        materialize.assert_called_once_with(fixture, episode_root)


if __name__ == "__main__":
    unittest.main()
