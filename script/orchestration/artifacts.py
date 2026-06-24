from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .models import ArtifactRef, utc_now_iso


class ArtifactRegistry:
    _locks_guard = threading.RLock()
    _manifest_locks: dict[str, threading.RLock] = {}

    def __init__(self, workspace: Path, manifest_path: Path | None = None) -> None:
        self.workspace = workspace.resolve(strict=False)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.manifest_path = manifest_path or (self.workspace / ".crayotter" / "artifact_manifest.json")
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = self._lock_for_manifest(self.manifest_path)
        self._artifacts: dict[str, ArtifactRef] = {}
        with self._lock:
            self._load()

    def register(
        self,
        *,
        kind: str,
        producer_task_id: str,
        phase: str,
        path: str | Path = "",
        metadata: dict[str, Any] | None = None,
        artifact_id: str | None = None,
    ) -> ArtifactRef:
        resolved_path = ""
        size_bytes = 0
        checksum = ""
        if path:
            candidate = Path(path).resolve(strict=False)
            self._assert_allowed_path(candidate)
            resolved_path = str(candidate)
            if candidate.exists() and candidate.is_file():
                stat = candidate.stat()
                size_bytes = stat.st_size
                checksum = self._fingerprint(candidate, stat.st_size)

        artifact = ArtifactRef(
            id=artifact_id or f"artifact_{uuid.uuid4().hex}",
            kind=kind,
            path=resolved_path,
            producer_task_id=producer_task_id,
            phase=phase,
            metadata=dict(metadata or {}),
            checksum=checksum,
            size_bytes=size_bytes,
            valid=not resolved_path or Path(resolved_path).is_file(),
        )
        with self._lock:
            self._load(merge=True)
            self._artifacts[artifact.id] = artifact
            self._save()
        return artifact

    def get(self, artifact_id: str) -> ArtifactRef | None:
        with self._lock:
            self.validate()
            artifact = self._artifacts.get(artifact_id)
            return artifact.model_copy(deep=True) if artifact is not None else None

    def list(self, *, kind: str | None = None, valid_only: bool = False) -> list[ArtifactRef]:
        with self._lock:
            self.validate()
            items = list(self._artifacts.values())
            if kind is not None:
                items = [item for item in items if item.kind == kind]
            if valid_only:
                items = [item for item in items if item.valid]
            return [item.model_copy(deep=True) for item in items]

    def find_by_producer(self, task_id: str) -> list[ArtifactRef]:
        return [item for item in self.list() if item.producer_task_id == task_id]

    def validate(self) -> None:
        changed = False
        for artifact in self._artifacts.values():
            valid = True
            if artifact.path:
                path = Path(artifact.path)
                valid = path.exists() and path.is_file()
                if valid and artifact.size_bytes:
                    valid = path.stat().st_size == artifact.size_bytes
                if valid and artifact.checksum:
                    valid = self._fingerprint(path, path.stat().st_size) == artifact.checksum
            if artifact.valid != valid:
                artifact.valid = valid
                changed = True
        if changed:
            self._save()

    def serialize(self) -> dict[str, Any]:
        with self._lock:
            self.validate()
            return {
                "version": 1,
                "updated_at": utc_now_iso(),
                "workspace": str(self.workspace),
                "artifacts": [item.model_dump() for item in self._artifacts.values()],
            }

    @classmethod
    def _lock_for_manifest(cls, manifest_path: Path) -> threading.RLock:
        key = str(manifest_path.resolve(strict=False)).lower()
        with cls._locks_guard:
            lock = cls._manifest_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._manifest_locks[key] = lock
            return lock

    def _load(self, *, merge: bool = False) -> None:
        if not self.manifest_path.exists():
            return
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if not merge:
                self._artifacts = {}
            for raw in payload.get("artifacts", []):
                artifact = ArtifactRef.model_validate(raw)
                self._artifacts[artifact.id] = artifact
            self.validate()
        except Exception:
            if not merge:
                self._artifacts = {}

    def _save(self) -> None:
        payload = {
            "version": 1,
            "updated_at": utc_now_iso(),
            "workspace": str(self.workspace),
            "artifacts": [item.model_dump() for item in self._artifacts.values()],
        }
        temp_path = self.manifest_path.with_name(
            f".{self.manifest_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._replace_with_retry(temp_path, self.manifest_path)

    @staticmethod
    def _replace_with_retry(temp_path: Path, target_path: Path) -> None:
        last_error: OSError | None = None
        for attempt in range(6):
            try:
                os.replace(temp_path, target_path)
                return
            except OSError as exc:
                last_error = exc
                if getattr(exc, "winerror", None) not in {5, 32, 33} and not isinstance(exc, PermissionError):
                    raise
                time.sleep(0.05 * (2 ** attempt))
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        if last_error is not None:
            raise last_error

    def _assert_allowed_path(self, path: Path) -> None:
        allowed = [self.workspace]
        user_workspace = os.environ.get("CRAYOTTER_USER_WORKSPACE", "").strip()
        if user_workspace:
            allowed.append(Path(user_workspace).resolve(strict=False))
        for root in allowed:
            try:
                path.relative_to(root)
                return
            except ValueError:
                continue
        raise ValueError(f"Artifact path is outside allowed workspaces: {path}")

    @staticmethod
    def _fingerprint(path: Path, size: int) -> str:
        digest = hashlib.sha256()
        digest.update(str(size).encode("ascii"))
        with path.open("rb") as handle:
            digest.update(handle.read(1024 * 1024))
        return digest.hexdigest()
