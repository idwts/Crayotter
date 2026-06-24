from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

import app.backend.runtime_manager as runtime_manager_module
from app.backend.models import AppConfig, JobRecord, JobRequest
from app.steering import SteeringCoordinator, SteeringStore, classify_guidance


class SteeringStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = SteeringStore(self.root / "steering")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_messages_are_ordered_and_persisted(self) -> None:
        first = self.store.append_message("节奏快一点", revision=1)
        second = self.store.append_message("旁白更正式", revision=1)

        reloaded = SteeringStore(self.root / "steering").list_messages()
        self.assertEqual([item["sequence"] for item in reloaded], [1, 2])
        self.assertEqual(first["classification"]["required_phase"], "phase2")
        self.assertEqual(second["classification"]["category"], "narration")

    def test_unsupported_bgm_is_explicit(self) -> None:
        classification = classify_guidance("换一首更有力量的背景音乐")
        self.assertEqual(classification["category"], "unsupported")
        self.assertEqual(classification["impact"], "unsupported")

    def test_pause_words_are_treated_as_guidance_text(self) -> None:
        classification = classify_guidance("等一下，字幕少一点，节奏更快")
        self.assertEqual(classification["category"], "subtitle")
        self.assertNotIn("pause_mode", classification)

    def test_guidance_is_routed_to_expected_phase(self) -> None:
        cases = [
            ("重新搜索成都素材", "phase1"),
            ("叙事结构更正式一点", "phase2"),
            ("旁白改成介绍成都", "phase3"),
        ]
        for content, expected_phase in cases:
            with self.subTest(content=content):
                self.assertEqual(classify_guidance(content)["required_phase"], expected_phase)

    def test_pause_requires_matching_token(self) -> None:
        pause = self.store.request_pause("before_export")
        with self.assertRaises(ValueError):
            self.store.approve("wrong-token")
        approved = self.store.approve(pause["token"])
        self.assertEqual(approved["status"], "approved")


class SteeringCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = SteeringStore(self.root / "steering")
        self.events: list[tuple[str, dict]] = []
        self.coordinator = SteeringCoordinator(
            workspace=self.root / "workspace",
            steering_dir=self.root / "steering",
            revision=1,
            event_sink=lambda event_type, payload: self.events.append((event_type, payload)),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_messages_are_consumed_once_and_latest_category_wins(self) -> None:
        self.store.append_message("旁白更活泼", revision=1)
        self.store.append_message("旁白改成正式语气", revision=1)

        first = self.coordinator.apply_pending("before_narration_generation", "phase3")
        second = self.coordinator.apply_pending("before_subtitle_generation", "phase3")

        self.assertEqual(len(first["applied"]), 2)
        self.assertEqual(second["applied"], [])
        guidance = self.coordinator.guidance_text()
        self.assertIn("旁白改成正式语气", guidance)
        self.assertNotIn("旁白更活泼", guidance)

    def test_wait_if_paused_unblocks_after_approval(self) -> None:
        pause = self.store.request_pause("next_safe_point")
        completed = threading.Event()

        def wait() -> None:
            self.coordinator.wait_if_paused("after_tool")
            completed.set()

        thread = threading.Thread(target=wait)
        thread.start()
        time.sleep(0.1)
        self.assertFalse(completed.is_set())
        self.store.approve(pause["token"])
        thread.join(timeout=2)

        self.assertTrue(completed.is_set())
        event_types = [event_type for event_type, _ in self.events]
        self.assertIn("steering_waiting_user", event_types)
        self.assertIn("steering_approved", event_types)

    def test_scheduler_safe_point_defers_without_consuming_guidance(self) -> None:
        self.store.append_message("重新搜索成都素材", revision=1)

        self.coordinator.scheduler_safe_point("phase1_material_plan:idle", "phase1")

        self.assertEqual(len(self.coordinator.pending_messages()), 1)
        event_types = [event_type for event_type, _ in self.events]
        self.assertIn("guidance_deferred", event_types)

    def test_react_tool_end_applies_guidance_and_requests_replan(self) -> None:
        self.store.append_message("希望介绍一下成都", revision=1)
        import os
        import sys
        script_dir = Path(__file__).resolve().parents[1] / "script"
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import script.graph as graph_module

        original_env = os.environ.get("CRAYOTTER_STEERING_DIR")
        original_coordinator = graph_module._STEERING_COORDINATOR
        original_sink = graph_module.RUNTIME_EVENT_SINK
        try:
            os.environ["CRAYOTTER_STEERING_DIR"] = str(self.root / "steering")
            graph_module._STEERING_COORDINATOR = self.coordinator
            graph_module.RUNTIME_EVENT_SINK = lambda event_type, payload: self.events.append((event_type, payload))
            handler = graph_module._RealtimeToolTraceHandler()
            run_id = "react-tool-test"
            handler._started[run_id] = ("add_narration_segments", time.perf_counter())

            with self.assertRaises(graph_module.SteeringReplanRequested) as raised:
                handler.on_tool_end("ok", run_id=run_id)

            self.assertEqual(raised.exception.required_phase, "phase3")
            self.assertEqual(self.coordinator.pending_messages(), [])
            event_types = [event_type for event_type, _ in self.events]
            self.assertIn("guidance_applied", event_types)
            self.assertIn("steering_replan_started", event_types)
        finally:
            graph_module._STEERING_COORDINATOR = original_coordinator
            graph_module.RUNTIME_EVENT_SINK = original_sink
            if original_env is None:
                os.environ.pop("CRAYOTTER_STEERING_DIR", None)
            else:
                os.environ["CRAYOTTER_STEERING_DIR"] = original_env


class RuntimeManagerRevisionTests(unittest.TestCase):
    def test_guidance_message_does_not_create_pause_request(self) -> None:
        class FakeConfigStore:
            def load(self) -> AppConfig:
                return AppConfig()

        with tempfile.TemporaryDirectory() as temp_dir:
            original_jobs_dir = runtime_manager_module.JOBS_DIR
            runtime_manager_module.JOBS_DIR = Path(temp_dir) / "jobs"
            try:
                manager = runtime_manager_module.RuntimeManager(FakeConfigStore())
                job_id = "job_guidance_no_pause_test"
                job_dir = runtime_manager_module.JOBS_DIR / job_id
                job_dir.mkdir(parents=True, exist_ok=True)
                job = runtime_manager_module.ManagedJob(
                    JobRecord(
                        job_id=job_id,
                        task="guidance no pause test",
                        mode="demo",
                        status="running",
                        steering_status="idle",
                    ),
                    job_dir,
                )
                manager._jobs[job_id] = job

                manager.add_message(job_id, "等一下，暂停一下字幕密度，节奏更快")

                self.assertEqual(job.record.steering_status, "pending")
                self.assertEqual(job.steering_store.read_control(), {})
                self.assertEqual(len(manager.list_messages(job_id)), 1)
                event_types = [event["type"] for event in job.bus.list_from()]
                self.assertIn("guidance_received", event_types)
            finally:
                runtime_manager_module.JOBS_DIR = original_jobs_dir

    def test_guidance_does_not_hide_waiting_user_state(self) -> None:
        class FakeConfigStore:
            def load(self) -> AppConfig:
                return AppConfig()

        with tempfile.TemporaryDirectory() as temp_dir:
            original_jobs_dir = runtime_manager_module.JOBS_DIR
            runtime_manager_module.JOBS_DIR = Path(temp_dir) / "jobs"
            try:
                manager = runtime_manager_module.RuntimeManager(FakeConfigStore())
                job_id = "job_waiting_guidance_test"
                job_dir = runtime_manager_module.JOBS_DIR / job_id
                job_dir.mkdir(parents=True, exist_ok=True)
                job = runtime_manager_module.ManagedJob(
                    JobRecord(
                        job_id=job_id,
                        task="waiting guidance test",
                        mode="demo",
                        status="running",
                        steering_status="waiting_user",
                    ),
                    job_dir,
                )
                manager._jobs[job_id] = job
                job.steering_store.request_pause("next_safe_point")

                manager.add_message(job_id, "节奏更紧凑")

                self.assertEqual(job.record.steering_status, "waiting_user")
                self.assertEqual(len(manager.list_messages(job_id)), 1)
            finally:
                runtime_manager_module.JOBS_DIR = original_jobs_dir

    def test_completed_demo_message_starts_new_revision_and_keeps_outputs(self) -> None:
        class FakeConfigStore:
            def load(self) -> AppConfig:
                return AppConfig()

        with tempfile.TemporaryDirectory() as temp_dir:
            original_jobs_dir = runtime_manager_module.JOBS_DIR
            runtime_manager_module.JOBS_DIR = Path(temp_dir) / "jobs"
            try:
                manager = runtime_manager_module.RuntimeManager(FakeConfigStore())
                created = manager.create_job(
                    JobRequest(task="demo revision test", mode="demo")
                )
                job_id = created["job_id"]
                self._wait_for_status(manager, job_id, "completed")

                manager.add_message(job_id, "节奏更快一些")
                self._wait_for_status(manager, job_id, "completed")
                detail = manager.get_job_detail(job_id)

                self.assertEqual(detail["revision"], 2)
                self.assertEqual(len(detail["output_files"]), 2)
                self.assertTrue(any("r001" in path for path in detail["output_files"]))
                self.assertTrue(any("r002" in path for path in detail["output_files"]))
                self.assertEqual(len(manager.list_messages(job_id)), 1)
            finally:
                runtime_manager_module.JOBS_DIR = original_jobs_dir

    @staticmethod
    def _wait_for_status(
        manager: runtime_manager_module.RuntimeManager,
        job_id: str,
        expected: str,
    ) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = manager.get_job(job_id)
            if job is not None and job.record.status == expected:
                return
            time.sleep(0.05)
        current = manager.get_job(job_id)
        raise AssertionError(
            f"Timed out waiting for {expected}; current={current.record.status if current else None}"
        )


if __name__ == "__main__":
    unittest.main()
