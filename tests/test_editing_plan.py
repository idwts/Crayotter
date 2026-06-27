from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from script.editing_plan import (
    EditingPlan,
    EditingPlanStore,
    EditingScene,
    PlanPatch,
    PlanPatchOperation,
    apply_plan_patch,
    validate_editing_plan,
)


class EditingPlanTests(unittest.TestCase):
    def _sample_plan(self, source: Path) -> EditingPlan:
        return EditingPlan(
            version="v001",
            user_request="做一个短片",
            target_duration_seconds=4,
            source_video_paths=[str(source.resolve())],
            scenes=[
                EditingScene(
                    scene_id="scene_01",
                    start=0,
                    end=4,
                    source_path=str(source.resolve()),
                    source_start=0,
                    source_end=4,
                    narrative_purpose="开场",
                    subtitle="旧字幕",
                )
            ],
        )

    def test_validate_rejects_unknown_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.mp4"
            source.write_bytes(b"video")
            plan = self._sample_plan(source)
            plan.scenes[0].source_path = str(Path(tmp) / "missing.mp4")

            report = validate_editing_plan(plan, allowed_source_paths=[str(source.resolve())])

            self.assertFalse(report.ok)
            self.assertTrue(any(issue.code == "unknown_source" for issue in report.issues))

    def test_apply_patch_creates_new_version_and_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.mp4"
            source.write_bytes(b"video")
            plan = self._sample_plan(source)
            patch = PlanPatch(
                base_version="v001",
                feedback="字幕改成更有冲击力",
                operations=[
                    PlanPatchOperation(
                        op="update_scene",
                        scene_id="scene_01",
                        field="subtitle",
                        value="穿越辽阔新疆",
                    )
                ],
            )

            updated, diff = apply_plan_patch(plan, patch)

            self.assertEqual(updated.version, "v002")
            self.assertEqual(updated.scenes[0].subtitle, "穿越辽阔新疆")
            self.assertEqual(diff.changed_scenes[0]["scene_id"], "scene_01")

    def test_store_approve_freezes_current_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.mp4"
            source.write_bytes(b"video")
            store = EditingPlanStore(Path(tmp) / "workspace")
            plan = self._sample_plan(source)
            store.save_plan(plan)

            approved = store.approve("v001")

            self.assertEqual(approved.status, "FROZEN")
            self.assertIsNotNone(store.approved())
            self.assertTrue((Path(tmp) / "workspace" / "plans" / "approved_editing_plan.json").exists())

    def test_generated_plan_accepts_common_llm_scene_coercions(self) -> None:
        plan = EditingPlan.model_validate(
            {
                "version": "v001",
                "target_duration_seconds": 3,
                "scenes": [
                    {
                        "scene_id": 1,
                        "start": 0,
                        "end": 3,
                        "source_start": 0,
                        "source_end": 3,
                        "crop": None,
                        "transition": None,
                        "subtitle": None,
                        "narration": None,
                        "alternatives": None,
                    }
                ],
            }
        )

        self.assertEqual(plan.scenes[0].scene_id, "1")
        self.assertEqual(plan.scenes[0].crop, "")
        self.assertEqual(plan.scenes[0].alternatives, [])

    def test_store_retries_current_plan_replace_when_windows_locks_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.mp4"
            source.write_bytes(b"video")
            store = EditingPlanStore(Path(tmp) / "workspace")
            plan = self._sample_plan(source)
            original_replace = __import__("os").replace
            calls = 0

            def flaky_replace(src, dst):
                nonlocal calls
                calls += 1
                if Path(dst).name == "current_plan.json" and calls <= 3:
                    raise PermissionError(5, "Access denied", str(dst))
                return original_replace(src, dst)

            with patch("script.editing_plan.os.replace", side_effect=flaky_replace), patch(
                "script.editing_plan.time.sleep"
            ):
                store.save_plan(plan)

            self.assertTrue(store.current_path.exists())
            self.assertIsNotNone(store.current())


if __name__ == "__main__":
    unittest.main()
