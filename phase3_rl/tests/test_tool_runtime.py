import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from phase3_rl.tool_runner import (
    _LATCH_RESETTERS,
    _configure_episode_environment,
    _reset_known_latches,
)
from phase3_rl.tool_runtime import (
    ToolExecutionResult,
    execute_tool_subprocess_async,
    parse_tool_result_text,
    result_indicates_failure,
)


class ToolRunnerEnvironmentTests(unittest.TestCase):
    def test_episode_paths_override_inherited_worker_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            episode_root = Path(temp_dir) / "episode"
            stale_root = Path(temp_dir) / "project-level-worker"
            stale_env = {
                "CRAYOTTER_RUNTIME_ROOT": str(stale_root),
                "CRAYOTTER_TASK_WORKSPACE": str(stale_root / "temp"),
                "CRAYOTTER_USER_WORKSPACE": str(stale_root / "user_temp"),
            }

            with patch.dict(os.environ, stale_env, clear=False):
                configured_root = _configure_episode_environment(episode_root)

                self.assertEqual(configured_root, episode_root.resolve())
                self.assertEqual(os.environ["CRAYOTTER_RUNTIME_ROOT"], str(episode_root.resolve()))
                self.assertEqual(
                    os.environ["CRAYOTTER_TASK_WORKSPACE"],
                    str((episode_root / "temp").resolve()),
                )
                self.assertEqual(
                    os.environ["CRAYOTTER_USER_WORKSPACE"],
                    str((episode_root / "user_temp").resolve()),
                )
                self.assertTrue((episode_root / "temp").is_dir())
                self.assertTrue((episode_root / "user_temp").is_dir())


class ToolResultParsingTests(unittest.TestCase):
    def test_multiline_analysis_result_collects_declared_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            analysis_path = runtime_root / "clip_analysis.json"
            analysis_path.write_text("{}", encoding="utf-8")
            raw = (
                "视频分析完成。\n"
                f"analysis_json: {analysis_path}\n"
                f"source_video: {runtime_root / 'clip.mp4'}\n"
                + ("analysis detail " * 500)
            )

            _, success, output_paths, _ = parse_tool_result_text(raw, runtime_root)

            self.assertTrue(success)
            self.assertEqual(output_paths, [str(analysis_path.resolve())])

    def test_successful_analysis_may_describe_failed_actions(self) -> None:
        raw = "视频分析完成（本地 rollout vLLM）:\n画面中的人物尝试跳跃但失败。"

        _, success, _, _ = parse_tool_result_text(raw, ".")

        self.assertTrue(success)

    def test_explicit_error_header_is_still_an_error(self) -> None:
        _, success, _, _ = parse_tool_result_text(
            "视频分析出错: 本地 rollout vLLM 调用失败",
            ".",
        )

        self.assertFalse(success)


class AsyncToolRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_subprocess_execution_is_offloaded_from_event_loop(self) -> None:
        expected = ToolExecutionResult(
            tool_name="inspect_video_duration",
            arguments={"video_path": "input.mp4"},
            raw_result="{}",
            parsed_result={},
            success=True,
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch("phase3_rl.tool_runtime.asyncio.to_thread", new_callable=AsyncMock) as to_thread:
            to_thread.return_value = expected
            result = await execute_tool_subprocess_async(
                tool_name="inspect_video_duration",
                arguments={"video_path": "input.mp4"},
                runtime_root=".",
            )

        self.assertIs(result, expected)
        function, = to_thread.await_args.args
        self.assertEqual(function.__name__, "execute_tool_subprocess")
        self.assertEqual(to_thread.await_args.kwargs["tool_name"], "inspect_video_duration")

    async def test_subprocess_concurrency_is_bounded(self) -> None:
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_execute(**kwargs) -> ToolExecutionResult:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return ToolExecutionResult(
                tool_name=kwargs["tool_name"],
                arguments={},
                raw_result="{}",
                parsed_result={},
                success=True,
                returncode=0,
                stdout="",
                stderr="",
            )

        old_value = os.environ.get("CRAYOTTER_RL_TOOL_PROCESS_CONCURRENCY")
        os.environ["CRAYOTTER_RL_TOOL_PROCESS_CONCURRENCY"] = "2"
        try:
            with patch("phase3_rl.tool_runtime.execute_tool_subprocess", side_effect=fake_execute):
                import asyncio

                await asyncio.gather(
                    *[
                        execute_tool_subprocess_async(
                            tool_name=f"tool_{index}",
                            arguments={},
                            runtime_root=".",
                        )
                        for index in range(5)
                    ]
                )
        finally:
            if old_value is None:
                os.environ.pop("CRAYOTTER_RL_TOOL_PROCESS_CONCURRENCY", None)
            else:
                os.environ["CRAYOTTER_RL_TOOL_PROCESS_CONCURRENCY"] = old_value

        self.assertEqual(max_active, 2)


class ResultIndicatesFailureTests(unittest.TestCase):
    """S1-1: canonical verdict shared by graph.py and parse_tool_result_text."""

    CASES = (
        # (payload, expected)
        ('{"status": "success", "path": "/tmp/a.mp4"}', False),
        ('{"status": "error", "message": "boom"}', True),
        ("```json\n{\"status\": \"success\"}\n```", False),
        ('[{"path": "/tmp/a.mp4"}]', False),
        ('下载失败: 网络超时', True),
        ('处理完成，文件已保存', False),
    )

    def test_verdicts_match_expectations(self) -> None:
        for payload, expected in self.CASES:
            with self.subTest(payload=payload):
                self.assertIs(result_indicates_failure(payload), expected)

    def test_parse_tool_result_text_agrees_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for payload, expected in self.CASES:
                with self.subTest(payload=payload):
                    _parsed, success, _paths, _dur = parse_tool_result_text(
                        payload, temp_dir
                    )
                    self.assertIs(success, not expected)

    def test_graph_marker_vocabulary_is_caller_supplied(self) -> None:
        # graph.py passes its own narrower markers; the canonical function
        # must honor them instead of the wider default set.
        self.assertFalse(
            result_indicates_failure("处理完成", markers=("出错", "失败", "error"))
        )
        self.assertTrue(
            result_indicates_failure("下载失败: 网络超时", markers=("出错", "失败", "error"))
        )

    def test_empty_status_falls_back_to_markers(self) -> None:
        # A dict with a blank status must not mask a first-line failure.
        self.assertTrue(result_indicates_failure('{"status": ""} 失败'))

    GRAPH_MARKERS = ("出错", "失败", "error", '"status": "fail"')

    def _graph_verdict(self, text: str) -> bool:
        # Exactly how script/graph.py calls the canonical function.
        return result_indicates_failure(
            text, markers=self.GRAPH_MARKERS, strict_status=False, full_text=True
        )

    def test_graph_vocabulary_accepts_non_success_statuses(self) -> None:
        # graph.py historically did not special-case JSON status; tools emit
        # allowed/empty/blocked on success paths and must not raise.
        self.assertFalse(self._graph_verdict('{"status": "allowed"}'))
        self.assertFalse(self._graph_verdict('{"status": "empty"}'))
        self.assertFalse(self._graph_verdict('{"status": "blocked"}'))
        # The same payloads stay failures under phase3_rl's strict tier.
        self.assertTrue(result_indicates_failure('{"status": "allowed"}'))

    def test_graph_vocabulary_scans_full_text(self) -> None:
        multi_line = "处理完成\n部分片段出错: codec fallback"
        # graph.py's historical whole-text scan catches second-line failures.
        self.assertTrue(self._graph_verdict(multi_line))
        # The phase3_rl default tier is first-line only — the documented
        # strictness difference between the two callers.
        self.assertFalse(result_indicates_failure(multi_line))

    def test_graph_vocabulary_catches_json_failures(self) -> None:
        self.assertTrue(self._graph_verdict('{"status": "fail", "reason": "x"}'))
        self.assertTrue(self._graph_verdict('{"status": "error"}'))
        self.assertFalse(self._graph_verdict('{"status": "success"}'))
        # Parity quirk: the historical '"status": "fail"' marker has a closing
        # quote, so "failed" was never caught either — kept verbatim.
        self.assertFalse(self._graph_verdict('{"status": "failed"}'))


class LatchRegistryTests(unittest.TestCase):
    """S2-1: worker resets every registered cross-request latch per request."""

    def test_registry_targets_real_reset_functions(self) -> None:
        self.assertEqual(
            set(_LATCH_RESETTERS),
            {
                ("script.tools.analyze_video", "reset_analysis_failure_circuit"),
                ("script.tools.analyze_video", "reset_analysis_model_fallbacks"),
            },
        )

    def test_reset_known_latches_invokes_every_registered_resetter(self) -> None:
        calls = []
        import types as _types

        fake_module = _types.ModuleType("script.tools.analyze_video")
        for _module_name, attr in _LATCH_RESETTERS:
            setattr(fake_module, attr, lambda attr=attr: calls.append(attr))
        with patch.dict(
            "sys.modules", {"script.tools.analyze_video": fake_module}
        ):
            _reset_known_latches()
        self.assertEqual(
            sorted(calls), sorted(attr for _m, attr in _LATCH_RESETTERS)
        )


if __name__ == "__main__":
    unittest.main()
