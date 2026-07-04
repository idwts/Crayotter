from __future__ import annotations

import json
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


PlanStatus = Literal[
    "DRAFT",
    "VALIDATED",
    "WAITING_FOR_USER_REVIEW",
    "REVISING",
    "APPROVED",
    "FROZEN",
    "REJECTED",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EditingScene(BaseModel):
    scene_id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    narrative_purpose: str = ""
    source_path: str = ""
    source_start: float = Field(default=0.0, ge=0)
    source_end: float = Field(default=0.0, ge=0)
    crop: str = ""
    transition: str = ""
    subtitle: str = ""
    narration: str = ""
    alternatives: list[str] = Field(default_factory=list)
    locked: bool = False

    @field_validator(
        "scene_id",
        "narrative_purpose",
        "source_path",
        "crop",
        "transition",
        "subtitle",
        "narration",
        mode="before",
    )
    @classmethod
    def _coerce_optional_text(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    @field_validator("alternatives", mode="before")
    @classmethod
    def _coerce_alternatives(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if item is not None]
        return [str(value)]


class EditingPlan(BaseModel):
    schema_version: str = "editing-plan-v1"
    version: str = "v001"
    status: PlanStatus = "DRAFT"
    user_request: str = ""
    target_duration_seconds: float = Field(default=0.0, ge=0)
    aspect_ratio: str = "16:9"
    style: str = ""
    pacing: str = ""
    narration_strategy: str = ""
    subtitle_strategy: str = ""
    bgm_strategy: str = ""
    scenes: list[EditingScene] = Field(default_factory=list)
    source_analysis_paths: list[str] = Field(default_factory=list)
    source_video_paths: list[str] = Field(default_factory=list)
    blueprint_markdown: str = ""
    previous_version: str = ""
    diff_from_previous: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class PlanPatchOperation(BaseModel):
    op: Literal["update_global", "update_scene", "delete_scene", "add_scene", "reorder_scenes"]
    scene_id: str = ""
    field: str = ""
    value: Any = None
    position: int | None = None


class PlanPatch(BaseModel):
    patch_id: str = Field(default_factory=lambda: f"patch_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}")
    base_version: str
    feedback: str = ""
    summary: str = ""
    operations: list[PlanPatchOperation] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class PlanValidationIssue(BaseModel):
    severity: Literal["error", "warning"] = "error"
    code: str
    message: str
    scene_id: str = ""


class PlanValidationReport(BaseModel):
    ok: bool
    version: str = ""
    issues: list[PlanValidationIssue] = Field(default_factory=list)


class PlanDiff(BaseModel):
    from_version: str
    to_version: str
    summary: list[str] = Field(default_factory=list)
    changed_globals: dict[str, dict[str, Any]] = Field(default_factory=dict)
    added_scenes: list[EditingScene] = Field(default_factory=list)
    removed_scenes: list[EditingScene] = Field(default_factory=list)
    changed_scenes: list[dict[str, Any]] = Field(default_factory=list)


GLOBAL_FIELDS = {
    "target_duration_seconds",
    "aspect_ratio",
    "style",
    "pacing",
    "narration_strategy",
    "subtitle_strategy",
    "bgm_strategy",
}

SCENE_FIELDS = {
    "start",
    "end",
    "narrative_purpose",
    "source_path",
    "source_start",
    "source_end",
    "crop",
    "transition",
    "subtitle",
    "narration",
    "alternatives",
    "locked",
}


def next_version(version: str) -> str:
    text = str(version or "").strip().lower().lstrip("v")
    try:
        number = int(text)
    except ValueError:
        number = 0
    return f"v{number + 1:03d}"


def normalize_plan_timeline(plan: EditingPlan) -> EditingPlan:
    cursor = 0.0
    normalized: list[EditingScene] = []
    for index, scene in enumerate(plan.scenes, start=1):
        duration = max(0.5, float(scene.end) - float(scene.start))
        updated = scene.model_copy(
            update={
                "scene_id": scene.scene_id or f"scene_{index:02d}",
                "start": round(cursor, 3),
                "end": round(cursor + duration, 3),
            }
        )
        if updated.source_end <= updated.source_start:
            updated.source_end = updated.source_start + duration
        normalized.append(updated)
        cursor += duration
    plan.scenes = normalized
    plan.target_duration_seconds = plan.target_duration_seconds or round(cursor, 3)
    plan.updated_at = utc_now_iso()
    return plan


def validate_editing_plan(
    plan: EditingPlan,
    *,
    allowed_source_paths: list[str] | None = None,
    require_sources_exist: bool = True,
) -> PlanValidationReport:
    issues: list[PlanValidationIssue] = []
    allowed = {
        str(Path(path).resolve(strict=False))
        for path in (allowed_source_paths or plan.source_video_paths)
        if path
    }
    previous_end = 0.0
    seen_ids: set[str] = set()
    for scene in plan.scenes:
        if scene.scene_id in seen_ids:
            issues.append(PlanValidationIssue(code="duplicate_scene_id", message="分镜 ID 重复。", scene_id=scene.scene_id))
        seen_ids.add(scene.scene_id)
        if scene.end <= scene.start:
            issues.append(PlanValidationIssue(code="invalid_scene_time", message="分镜结束时间必须大于开始时间。", scene_id=scene.scene_id))
        if abs(scene.start - previous_end) > 0.25:
            issues.append(PlanValidationIssue(severity="warning", code="timeline_gap", message="分镜时间线不连续。", scene_id=scene.scene_id))
        previous_end = scene.end
        if scene.source_path:
            resolved = str(Path(scene.source_path).resolve(strict=False))
            if allowed and resolved not in allowed:
                issues.append(PlanValidationIssue(code="unknown_source", message="分镜引用了未登记素材。", scene_id=scene.scene_id))
            if require_sources_exist and not Path(resolved).is_file():
                issues.append(PlanValidationIssue(code="missing_source", message="分镜引用的素材文件不存在。", scene_id=scene.scene_id))
        if scene.source_end <= scene.source_start:
            issues.append(PlanValidationIssue(code="invalid_source_time", message="素材入点/出点不合法。", scene_id=scene.scene_id))
        if scene.source_end - scene.source_start < 0.5:
            issues.append(PlanValidationIssue(code="source_too_short", message="素材片段过短。", scene_id=scene.scene_id))
    if not plan.scenes:
        issues.append(PlanValidationIssue(code="empty_plan", message="剪辑计划必须至少包含一个分镜。"))
    total = plan.scenes[-1].end if plan.scenes else 0.0
    target = plan.target_duration_seconds or total
    if target > 0 and total > 0 and abs(total - target) > max(2.0, target * 0.25):
        issues.append(PlanValidationIssue(severity="warning", code="duration_drift", message="计划总时长与目标时长偏差较大。"))
    return PlanValidationReport(
        ok=not any(issue.severity == "error" for issue in issues),
        version=plan.version,
        issues=issues,
    )


def diff_plans(before: EditingPlan, after: EditingPlan) -> PlanDiff:
    changed_globals: dict[str, dict[str, Any]] = {}
    for field in sorted(GLOBAL_FIELDS):
        old = getattr(before, field)
        new = getattr(after, field)
        if old != new:
            changed_globals[field] = {"from": old, "to": new}

    before_map = {scene.scene_id: scene for scene in before.scenes}
    after_map = {scene.scene_id: scene for scene in after.scenes}
    added = [scene for scene_id, scene in after_map.items() if scene_id not in before_map]
    removed = [scene for scene_id, scene in before_map.items() if scene_id not in after_map]
    changed_scenes: list[dict[str, Any]] = []
    for scene_id in before_map.keys() & after_map.keys():
        old_scene = before_map[scene_id].model_dump()
        new_scene = after_map[scene_id].model_dump()
        fields = {
            key: {"from": old_scene[key], "to": new_scene[key]}
            for key in old_scene
            if old_scene.get(key) != new_scene.get(key)
        }
        if fields:
            changed_scenes.append({"scene_id": scene_id, "fields": fields})
    summary: list[str] = []
    if changed_globals:
        summary.append(f"更新 {len(changed_globals)} 个全局策略字段")
    if added:
        summary.append(f"新增 {len(added)} 个分镜")
    if removed:
        summary.append(f"删除 {len(removed)} 个分镜")
    if changed_scenes:
        summary.append(f"修改 {len(changed_scenes)} 个分镜")
    if not summary:
        summary.append("计划内容未发生结构化变化")
    return PlanDiff(
        from_version=before.version,
        to_version=after.version,
        summary=summary,
        changed_globals=changed_globals,
        added_scenes=added,
        removed_scenes=removed,
        changed_scenes=changed_scenes,
    )


def apply_plan_patch(plan: EditingPlan, patch: PlanPatch) -> tuple[EditingPlan, PlanDiff]:
    updated = plan.model_copy(deep=True)
    updated.previous_version = plan.version
    updated.version = next_version(plan.version)
    updated.status = "REVISING"
    scenes = {scene.scene_id: scene for scene in updated.scenes}

    for operation in patch.operations:
        if operation.op == "update_global":
            if operation.field in GLOBAL_FIELDS:
                setattr(updated, operation.field, operation.value)
            continue
        if operation.op == "update_scene":
            scene = scenes.get(operation.scene_id)
            if scene is not None and operation.field in SCENE_FIELDS:
                scenes[operation.scene_id] = scene.model_copy(update={operation.field: operation.value})
            continue
        if operation.op == "delete_scene":
            scenes.pop(operation.scene_id, None)
            continue
        if operation.op == "add_scene":
            value = operation.value if isinstance(operation.value, dict) else {}
            try:
                scene = EditingScene.model_validate(value)
            except ValidationError:
                continue
            scenes[scene.scene_id] = scene
            continue
        if operation.op == "reorder_scenes":
            ordered = [str(item) for item in (operation.value or [])]
            existing = list(scenes.values())
            by_id = {scene.scene_id: scene for scene in existing}
            reordered = [by_id.pop(scene_id) for scene_id in ordered if scene_id in by_id]
            updated.scenes = [*reordered, *by_id.values()]
            scenes = {scene.scene_id: scene for scene in updated.scenes}

    if not any(operation.op == "reorder_scenes" for operation in patch.operations):
        updated.scenes = list(scenes.values())
    normalize_plan_timeline(updated)
    plan_diff = diff_plans(plan, updated)
    updated.diff_from_previous = plan_diff.model_dump()
    return updated, plan_diff


def heuristic_patch_from_feedback(plan: EditingPlan, feedback: str) -> PlanPatch:
    text = " ".join(str(feedback or "").split()).strip()
    operations: list[PlanPatchOperation] = []
    lowered = text.lower()
    if any(marker in lowered for marker in ("字幕", "subtitle", "文案")):
        for scene in plan.scenes:
            if not scene.locked:
                operations.append(
                    PlanPatchOperation(
                        op="update_scene",
                        scene_id=scene.scene_id,
                        field="subtitle",
                        value=scene.subtitle or text[:80],
                    )
                )
                break
    if any(marker in lowered for marker in ("旁白", "配音", "narration", "解说")):
        for scene in plan.scenes:
            if not scene.locked:
                operations.append(
                    PlanPatchOperation(
                        op="update_scene",
                        scene_id=scene.scene_id,
                        field="narration",
                        value=text[:160],
                    )
                )
                break
    if any(marker in lowered for marker in ("节奏", "快", "慢", "pacing")):
        operations.append(PlanPatchOperation(op="update_global", field="pacing", value=text[:160]))
    if any(marker in lowered for marker in ("风格", "style", "正式", "清新", "科技")):
        operations.append(PlanPatchOperation(op="update_global", field="style", value=text[:160]))
    if not operations:
        operations.append(PlanPatchOperation(op="update_global", field="style", value=text[:160]))
    return PlanPatch(
        base_version=plan.version,
        feedback=text,
        summary="根据用户自然语言反馈更新计划。",
        operations=operations,
    )


class EditingPlanStore:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve(strict=False)
        self.root = self.workspace / "plans"
        self.root.mkdir(parents=True, exist_ok=True)
        self.current_path = self.root / "current_plan.json"
        self.approved_path = self.root / "approved_editing_plan.json"
        self._lock = threading.RLock()

    def save_plan(self, plan: EditingPlan) -> EditingPlan:
        with self._lock:
            plan.updated_at = utc_now_iso()
            path = self.version_path(plan.version)
            self._atomic_write(path, plan.model_dump())
            self._atomic_write(self.current_path, plan.model_dump())
            return plan

    def current(self) -> EditingPlan | None:
        if self.current_path.exists():
            return self._read_plan(self.current_path)
        versions = self.list_versions()
        return self.get_plan(versions[-1]) if versions else None

    def approved(self) -> EditingPlan | None:
        return self._read_plan(self.approved_path) if self.approved_path.exists() else None

    def get_plan(self, version: str) -> EditingPlan | None:
        path = self.version_path(version)
        return self._read_plan(path) if path.exists() else None

    def list_versions(self) -> list[str]:
        versions: list[str] = []
        for path in sorted(self.root.glob("editing_plan_v*.json")):
            stem = path.stem.removeprefix("editing_plan_")
            versions.append(stem)
        return versions

    def approve(self, version: str) -> EditingPlan:
        plan = self.get_plan(version)
        if plan is None:
            raise FileNotFoundError(f"Editing plan was not found: {version}")
        plan.status = "FROZEN"
        self.save_plan(plan)
        self._atomic_write(self.approved_path, plan.model_dump())
        return plan

    def reject(self, version: str) -> EditingPlan:
        plan = self.get_plan(version)
        if plan is None:
            raise FileNotFoundError(f"Editing plan was not found: {version}")
        plan.status = "REJECTED"
        return self.save_plan(plan)

    def diff(self, from_version: str, to_version: str) -> PlanDiff:
        before = self.get_plan(from_version)
        after = self.get_plan(to_version)
        if before is None or after is None:
            raise FileNotFoundError("Plan diff version was not found.")
        return diff_plans(before, after)

    def version_path(self, version: str) -> Path:
        version_text = str(version or "v001").strip().lower()
        return self.root / f"editing_plan_{version_text}.json"

    def _read_plan(self, path: Path) -> EditingPlan:
        return EditingPlan.model_validate(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        attempts = 8 if os.name == "nt" else 1
        try:
            for attempt in range(attempts):
                try:
                    os.replace(temp_path, path)
                    return
                except PermissionError:
                    if attempt + 1 >= attempts:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)


def copy_plan_artifact(store: EditingPlanStore, plan: EditingPlan) -> Path:
    path = store.version_path(plan.version)
    artifact_path = store.root / f"{path.stem}_artifact.json"
    if path.exists():
        shutil.copyfile(path, artifact_path)
    return artifact_path if artifact_path.exists() else path
