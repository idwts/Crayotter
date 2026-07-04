from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PHASE_ORDER = {"phase1": 1, "phase2": 2, "phase3": 3}
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_guidance(content: str) -> dict[str, Any]:
    text = " ".join(str(content or "").split()).strip()
    lowered = text.lower()
    if not text:
        raise ValueError("Guidance content cannot be empty.")

    unsupported_markers = ("bgm", "背景音乐", "配乐", "音乐素材", "换音乐")
    if any(marker in lowered for marker in unsupported_markers):
        return {
            "category": "unsupported",
            "required_phase": "phase3",
            "impact": "unsupported",
            "normalized_guidance": text,
            "reason": "当前流程没有完整的 BGM 素材输入与选曲能力。",
        }

    global_markers = (
        "主题改",
        "完全改成",
        "重新找素材",
        "重新搜索",
        "换主题",
        "新主题",
        "不要这些素材",
    )
    material_markers = (
        "素材",
        "视频源",
        "多用",
        "少用",
        "操场",
        "本地视频",
        "横屏",
        "竖屏",
        "画幅",
    )
    duration_requested = (
        any(marker in lowered for marker in ("时长", "多久", "分半"))
        or re.search(
            r"\d+(?:\.\d+)?\s*(?:秒|分钟|分|s\b|sec(?:ond)?s?\b|min(?:ute)?s?\b)",
            lowered,
        )
        is not None
    )
    narrative_markers = (
        "叙事",
        "结构",
        "开场",
        "结尾",
        "高潮",
        "科技感",
        "清新",
        "正式",
        "风格",
        "节奏",
        "转场",
    )
    narration_markers = ("旁白", "配音", "解说", "音色", "语气", "voice", "narration")
    subtitle_markers = ("字幕", "字数", "字体", "subtitle")

    if any(marker in lowered for marker in global_markers):
        category, phase, impact = "global", "phase1", "phase"
    elif duration_requested:
        category, phase, impact = "duration", "phase1", "phase"
    elif any(marker in lowered for marker in material_markers):
        category, phase, impact = "material", "phase1", "phase"
    elif any(marker in lowered for marker in narration_markers):
        category, phase, impact = "narration", "phase3", "local"
    elif any(marker in lowered for marker in subtitle_markers):
        category, phase, impact = "subtitle", "phase3", "local"
    elif any(marker in lowered for marker in narrative_markers):
        category, phase, impact = "style", "phase2", "phase"
    else:
        category, phase, impact = "general", "phase3", "local"

    return {
        "category": category,
        "required_phase": phase,
        "impact": impact,
        "normalized_guidance": text,
    }


class SteeringStore:
    def __init__(self, steering_dir: str | Path) -> None:
        self.root = Path(steering_dir).resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True)
        self.messages_path = self.root / "messages.jsonl"
        self.control_path = self.root / "control.json"
        self._lock = threading.RLock()

    def append_message(self, content: str, revision: int) -> dict[str, Any]:
        classification = classify_guidance(content)
        with self._lock:
            sequence = 1
            messages = self.list_messages()
            if messages:
                sequence = max(int(item.get("sequence", 0)) for item in messages) + 1
            message = {
                "message_id": f"msg_{uuid.uuid4().hex}",
                "sequence": sequence,
                "revision": max(1, int(revision)),
                "content": classification["normalized_guidance"],
                "created_at": utc_now_iso(),
                "classification": classification,
            }
            with self.messages_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(message, ensure_ascii=False) + "\n")
            return message

    def list_messages(self, after_sequence: int = 0) -> list[dict[str, Any]]:
        if not self.messages_path.exists():
            return []
        items: list[dict[str, Any]] = []
        with self._lock:
            for line in self.messages_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if int(item.get("sequence", 0)) > after_sequence:
                    items.append(item)
        return sorted(items, key=lambda item: int(item.get("sequence", 0)))

    def request_pause(self, mode: str = "next_safe_point") -> dict[str, Any]:
        if mode not in {"next_safe_point", "before_export", "plan_review"}:
            raise ValueError("Pause mode must be next_safe_point, before_export, or plan_review.")
        payload = {
            "status": "requested",
            "mode": mode,
            "token": f"pause_{uuid.uuid4().hex}",
            "requested_at": utc_now_iso(),
        }
        self._atomic_write(self.control_path, payload)
        return payload

    def approve(self, token: str) -> dict[str, Any]:
        control = self.read_control()
        if control.get("status") != "requested":
            raise RuntimeError("This job is not waiting for approval.")
        if not token or token != control.get("token"):
            raise ValueError("Invalid pause token.")
        control.update({"status": "approved", "approved_at": utc_now_iso()})
        self._atomic_write(self.control_path, control)
        return control

    def read_control(self) -> dict[str, Any]:
        if not self.control_path.exists():
            return {}
        try:
            return json.loads(self.control_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def clear_control(self) -> None:
        self._atomic_write(self.control_path, {"status": "idle", "updated_at": utc_now_iso()})

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)


class SteeringCoordinator:
    def __init__(
        self,
        *,
        workspace: str | Path,
        steering_dir: str | Path,
        revision: int = 1,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        classifier: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve(strict=False)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.store = SteeringStore(steering_dir)
        self.revision = max(1, int(revision))
        self.event_sink = event_sink
        self.classifier = classifier
        self.state_path = self.workspace / ".crayotter" / "steering_state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def guidance_text(self) -> str:
        state = self._load_state()
        latest: dict[str, dict[str, Any]] = {}
        for item in state.get("guidance", []):
            latest[str(item.get("category") or "general")] = item
        ordered = sorted(latest.values(), key=lambda item: int(item.get("sequence", 0)))
        return "\n".join(
            f"- [{item.get('category', 'general')}] {item.get('content', '')}"
            for item in ordered
            if item.get("content")
        )

    def pending_messages(self) -> list[dict[str, Any]]:
        state = self._load_state()
        return self.store.list_messages(int(state.get("last_processed_sequence", 0)))

    def apply_pending(self, checkpoint: str, current_phase: str) -> dict[str, Any]:
        self.wait_if_paused(checkpoint)
        pending = self.pending_messages()
        if not pending:
            self._save_checkpoint(checkpoint)
            return {"applied": [], "required_phase": "", "categories": []}

        self._emit(
            "guidance_applying",
            {"checkpoint": checkpoint, "message_count": len(pending), "revision": self.revision},
        )
        state = self._load_state()
        applied: list[dict[str, Any]] = []
        required_phase = ""
        for message in pending:
            fallback = dict(
                message.get("classification") or classify_guidance(message["content"])
            )
            classification = fallback
            if self.classifier is not None and fallback.get("category") not in {
                "pause",
                "unsupported",
            }:
                try:
                    classification = dict(self.classifier(str(message["content"])))
                except Exception:
                    classification = fallback
            payload = {
                "message_id": message.get("message_id"),
                "sequence": message.get("sequence"),
                "content": message.get("content"),
                "checkpoint": checkpoint,
                "revision": self.revision,
                **classification,
            }
            self._emit("guidance_classified", payload)
            if classification.get("category") == "unsupported":
                self._emit("guidance_unsupported", payload)
            elif classification.get("category") == "pause":
                self._emit("guidance_deferred", payload)
            else:
                applied.append(payload)
                state.setdefault("guidance", []).append(
                    {
                        "message_id": message.get("message_id"),
                        "sequence": message.get("sequence"),
                        "revision": message.get("revision", self.revision),
                        "category": classification.get("category", "general"),
                        "required_phase": classification.get("required_phase", "phase3"),
                        "content": classification.get("normalized_guidance") or message.get("content"),
                        "applied_at": utc_now_iso(),
                        "checkpoint": checkpoint,
                    }
                )
                candidate = str(classification.get("required_phase") or "phase3")
                if not required_phase or PHASE_ORDER.get(candidate, 3) < PHASE_ORDER.get(required_phase, 3):
                    required_phase = candidate
                self._emit("guidance_applied", payload)
            state["last_processed_sequence"] = max(
                int(state.get("last_processed_sequence", 0)),
                int(message.get("sequence", 0)),
            )
        state["current_checkpoint"] = checkpoint
        state["updated_at"] = utc_now_iso()
        self._save_state(state)
        return {
            "applied": applied,
            "required_phase": required_phase,
            "categories": [item.get("category", "general") for item in applied],
            "current_phase": current_phase,
        }

    def scheduler_safe_point(self, checkpoint: str, current_phase: str) -> None:
        self.wait_if_paused(checkpoint)
        pending = self.pending_messages()
        if pending:
            self._emit(
                "guidance_deferred",
                {
                    "checkpoint": checkpoint,
                    "current_phase": current_phase,
                    "message_count": len(pending),
                    "reason": "等待当前调度批次结束后应用。",
                },
            )
        self._save_checkpoint(checkpoint)

    def wait_if_paused(self, checkpoint: str) -> None:
        control = self.store.read_control()
        if control.get("status") != "requested":
            self._save_checkpoint(checkpoint)
            return
        mode = control.get("mode", "next_safe_point")
        if mode == "before_export" and checkpoint != "before_export":
            self._save_checkpoint(checkpoint)
            return
        if mode == "plan_review" and checkpoint != "plan_review":
            self._save_checkpoint(checkpoint)
            return

        token = str(control.get("token") or "")
        self._emit(
            "steering_waiting_user",
            {
                "checkpoint": checkpoint,
                "pause_mode": mode,
                "pause_token": token,
                "revision": self.revision,
            },
        )
        last_heartbeat = 0.0
        while True:
            current = self.store.read_control()
            if current.get("status") == "approved" and current.get("token") == token:
                self.store.clear_control()
                self._emit(
                    "steering_approved",
                    {"checkpoint": checkpoint, "pause_token": token, "revision": self.revision},
                )
                self._save_checkpoint(checkpoint)
                return
            now = time.monotonic()
            if now - last_heartbeat >= 5.0:
                last_heartbeat = now
                self._emit(
                    "heartbeat",
                    {
                        "reason": "waiting_user",
                        "checkpoint": checkpoint,
                        "revision": self.revision,
                    },
                )
            time.sleep(0.25)

    def _save_checkpoint(self, checkpoint: str) -> None:
        state = self._load_state()
        state["current_checkpoint"] = checkpoint
        state["updated_at"] = utc_now_iso()
        self._save_state(state)

    def _load_state(self) -> dict[str, Any]:
        with self._lock:
            if not self.state_path.exists():
                return {
                    "version": 1,
                    "last_processed_sequence": 0,
                    "guidance": [],
                    "current_checkpoint": "",
                }
            try:
                payload = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
            except Exception:
                pass
            return {
                "version": 1,
                "last_processed_sequence": 0,
                "guidance": [],
                "current_checkpoint": "",
            }

    def _save_state(self, state: dict[str, Any]) -> None:
        with self._lock:
            SteeringStore._atomic_write(self.state_path, state)

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink is not None:
            self.event_sink(event_type, payload)
