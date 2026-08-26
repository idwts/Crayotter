from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from script.harness.faults import FaultInjector, InjectedFault
from script.harness.models import RunOutcome, ScenarioSpec
from script.harness.preflight import CapabilityPreflight
from script.harness.replay import ReplayStore
from script.harness.runner import HarnessRunner


class HarnessFrameworkTests(unittest.TestCase):
    def test_faults_fire_on_declared_occurrence(self) -> None:
        spec = ScenarioSpec.model_validate(
            {
                "id": "fault",
                "faults": [{"stage": "video", "effect": "status_403", "occurrence": 2}],
            }
        )
        injector = FaultInjector(spec.faults)
        self.assertEqual(injector.invoke("video", lambda: "ok"), "ok")
        with self.assertRaises(InjectedFault) as raised:
            injector.invoke("video", lambda: "never")
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(injector.invoke("video", lambda: "recovered"), "recovered")

    def test_preflight_cache_excludes_credentials(self) -> None:
        calls = 0

        def probe():
            nonlocal calls
            calls += 1
            return {"available": True, "voice": "Cherry"}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capabilities.json"
            cache = CapabilityPreflight(path, ttl_seconds=60)
            first = cache.probe(
                capability="tts",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen-tts-latest",
                probe=probe,
            )
            second = cache.probe(
                capability="tts",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen-tts-latest",
                probe=probe,
            )
            self.assertTrue(first.available)
            self.assertEqual(second.metadata["voice"], "Cherry")
            self.assertEqual(calls, 1)
            self.assertNotIn("api_key", path.read_text(encoding="utf-8"))

    def test_replay_store_redacts_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "replay.json"
            store = ReplayStore(path)
            request = {"prompt": "hello", "api_key": "sk-supersecret123"}
            store.record("text", request, response={"text": "ok sk-supersecret123"})
            self.assertEqual(store.lookup("text", request)["text"], "ok <redacted>")
            persisted = path.read_text(encoding="utf-8")
            self.assertNotIn("sk-supersecret123", persisted)
            self.assertNotIn("api_key", persisted)

    def test_runner_applies_hard_oracles(self) -> None:
        spec = ScenarioSpec.model_validate(
            {
                "id": "beijing",
                "assertions": {
                    "required_terms": ["1920", "2026"],
                    "duration": {"target": 60, "tolerance": 1},
                    "required_voice": "female",
                },
            }
        )

        def execute(_spec, context):
            context.emit("job_completed")
            return RunOutcome(
                terminal_status="completed",
                text_outputs=["1920 to 2026"],
                editing_plan={"voice": "Cherry", "scenes": [{"end": 60}]},
                metadata={"duration_seconds": 60},
            )

        report = HarnessRunner().run(spec, execute)
        self.assertTrue(report.passed, report.model_dump_json(indent=2))
        self.assertEqual(report.event_summary["job_completed"], 1)

    def test_incident_loader_reads_crayotter_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            manifest = job / "workspace" / ".crayotter" / "artifact_manifest.json"
            manifest.parent.mkdir(parents=True)
            (job / "summary.json").write_text(
                json.dumps({"status": "failed"}), encoding="utf-8"
            )
            (job / "events.jsonl").write_text(
                json.dumps({"type": "job_failed", "payload": {}}) + "\n",
                encoding="utf-8",
            )
            manifest.write_text(json.dumps({"artifacts": []}), encoding="utf-8")
            outcome = HarnessRunner().incident_outcome(job)
            self.assertEqual(outcome.terminal_status, "failed")
            self.assertEqual(outcome.events[0]["type"], "job_failed")


if __name__ == "__main__":
    unittest.main()
