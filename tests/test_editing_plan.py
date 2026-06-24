from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
