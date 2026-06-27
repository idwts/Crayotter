from __future__ import annotations

import unittest

from app.backend.runtime_manager import format_events_as_log


class BackendLogDownloadTests(unittest.TestCase):
    def test_format_events_as_log_includes_timestamp_type_and_payload(self) -> None:
        text = format_events_as_log(
            [
                {
                    "timestamp": "2026-06-27T07:28:26Z",
                    "type": "editing_plan_created",
                    "payload": {"version": "v001", "scene_count": 6},
                }
            ]
        )

        self.assertIn("2026-06-27T07:28:26Z", text)
        self.assertIn("editing_plan_created", text)
        self.assertIn('"version": "v001"', text)
