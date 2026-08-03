import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

analyze_video_module = importlib.import_module("tools.analyze_video")


class DashScopeVideoAnalysisRetryTests(unittest.TestCase):
    def test_long_video_prompt_requests_bounded_full_timeline(self) -> None:
        guidance = analyze_video_module._analysis_segment_guidance(604.0)

        self.assertIn("24~36", guidance)
        self.assertIn("20 秒", guidance)
        self.assertIn("视频结尾", guidance)
        self.assertIn("5~15", analyze_video_module._analysis_segment_guidance(120.0))

    def test_proxy_timestamp_is_drawn_after_timeline_speedup(self) -> None:
        filters = analyze_video_module._shared._analysis_video_filters(13.4, True)

        setpts_index = next(
            index for index, value in enumerate(filters) if value.startswith("setpts=")
        )
        drawtext_index = next(
            index for index, value in enumerate(filters) if value.startswith("drawtext=")
        )
        self.assertLess(setpts_index, drawtext_index)

    def test_expected_network_error_types_are_retryable(self) -> None:
        retryable_errors = [
            type("SSLError", (Exception,), {})("ssl failure"),
            type("SSLEOFError", (Exception,), {})("unexpected eof"),
            RuntimeError("HTTPSConnectionPool: Max retries exceeded"),
            ConnectionError("connection failed"),
            type("ReadTimeout", (Exception,), {})("read timed out"),
        ]

        for error in retryable_errors:
            with self.subTest(error=error.__class__.__name__):
                self.assertTrue(
                    analyze_video_module._is_retryable_dashscope_error(error)
                )

        self.assertFalse(
            analyze_video_module._is_retryable_dashscope_error(
                RuntimeError("status=403; Access denied")
            )
        )

    def test_retryable_network_errors_use_expected_backoff(self) -> None:
        attempts = 0

        def call():
            nonlocal attempts
            attempts += 1
            if attempts <= 3:
                raise ConnectionError("HTTPSConnectionPool: Max retries exceeded")
            return "ok"

        with patch.object(analyze_video_module.time, "sleep") as sleep:
            result = analyze_video_module._call_dashscope_with_retry(
                call,
                model_name="qwen-vl-max",
                video_input="file://video.mp4",
            )

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 4)
        self.assertEqual(
            [item.args[0] for item in sleep.call_args_list],
            [5.0, 15.0, 30.0],
        )

    def test_non_retryable_error_is_raised_immediately(self) -> None:
        attempts = 0

        def call():
            nonlocal attempts
            attempts += 1
            raise ValueError("invalid request")

        with patch.object(analyze_video_module.time, "sleep") as sleep:
            with self.assertRaises(ValueError):
                analyze_video_module._call_dashscope_with_retry(
                    call,
                    model_name="qwen-vl-max",
                    video_input="file://video.mp4",
                )

        self.assertEqual(attempts, 1)
        sleep.assert_not_called()

    def test_denied_latest_model_fallback_is_reused_for_later_calls(self) -> None:
        analyze_video_module.reset_analysis_model_fallbacks()

        self.assertEqual(
            analyze_video_module._dashscope_model_candidates("qwen-vl-max-latest"),
            ["qwen-vl-max-latest", "qwen-vl-max"],
        )

        analyze_video_module._remember_dashscope_model_fallback(
            "qwen-vl-max-latest",
            "qwen-vl-max",
        )

        self.assertEqual(
            analyze_video_module._dashscope_model_candidates("qwen-vl-max-latest"),
            ["qwen-vl-max"],
        )


if __name__ == "__main__":
    unittest.main()
