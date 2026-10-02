import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from phase3_rl.tool_runner import _configure_episode_environment
from phase3_rl.tool_runtime import (
    ToolExecutionResult,
    execute_tool_subprocess_async,
    parse_tool_result_text,
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


if __name__ == "__main__":
    unittest.main()
