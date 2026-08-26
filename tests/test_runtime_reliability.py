from __future__ import annotations

import importlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.backend.models import JobRecord
from app.backend.runtime_manager import RuntimeManager
from script.orchestration.models import TaskState
from script.phases.editing_execution.voice_policy import resolve_narration_voice

graph = importlib.import_module("script.graph")
analyze_video = importlib.import_module("script.tools.analyze_video")


class _Scheduler:
    def __init__(self, states: dict[str, TaskState]) -> None:
        self.states = states
        self.allow_partial_failure = False

    def run(self, plan, execute, **kwargs):
        self.allow_partial_failure = bool(kwargs.get("allow_partial_failure"))
        return self.states


class RuntimeReliabilityTests(unittest.TestCase):
    def test_voice_policy_preserves_female_request(self) -> None:
        self.assertEqual(resolve_narration_voice("成年女性普通话"), "Cherry")
        self.assertEqual(resolve_narration_voice("温柔女声"), "Serena")
        self.assertEqual(resolve_narration_voice("no preference"), "Ethan")

    def test_download_batch_allows_one_of_three_to_fail(self) -> None:
        states = {
            "one": TaskState(
                task_id="one", status="completed", result={"path": "one.mp4"}
            ),
            "two": TaskState(task_id="two", status="failed", error="connection reset"),
            "three": TaskState(
                task_id="three", status="completed", result={"path": "three.mp4"}
            ),
        }
        scheduler = _Scheduler(states)
        selected = [
            {"bvid": f"BV00000000{i}", "source": "bilibili", "title": str(i)}
            for i in range(1, 4)
        ]
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(graph, "WORKSPACE", Path(tmp)),
            patch.object(graph, "_emit_orchestration_event") as emit,
        ):
            result = graph._run_phase1_downloads(
                type(
                    "State",
                    (),
                    {"gap_round": 0, "user_request": "test", "processing_budget": None},
                )(),
                scheduler,
                selected,
            )
        self.assertTrue(scheduler.allow_partial_failure)
        self.assertEqual(result, ["one.mp4", "three.mp4"])
        emit.assert_called_once()

    def test_dashscope_retry_budget_suppresses_excess_backoff(self) -> None:
        analyze_video.reset_analysis_failure_circuit()
        with (
            patch.dict("os.environ", {"CRAYOTTER_VIDEO_RETRY_BUDGET_SECONDS": "4"}),
            patch.object(analyze_video.time, "sleep") as sleep,
            self.assertRaisesRegex(RuntimeError, "retry budget exhausted"),
        ):
            analyze_video._call_dashscope_with_retry(
                lambda: (_ for _ in ()).throw(ConnectionError("connection reset")),
                model_name="qwen-vl-max",
                video_input="file://video.mp4",
            )
        sleep.assert_not_called()

    def test_terminal_timing_is_recovered(self) -> None:
        started = datetime.now(timezone.utc) - timedelta(seconds=12)
        completed = datetime.now(timezone.utc)
        record = JobRecord(
            job_id="j",
            task="t",
            mode="agent",
            status="cancelled",
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
        )
        RuntimeManager._finalize_terminal_timing(record)
        self.assertGreaterEqual(record.total_wall_seconds, 11.9)
        self.assertGreaterEqual(record.processing_elapsed_seconds, 11.9)


if __name__ == "__main__":
    unittest.main()
