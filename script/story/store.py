from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from script.orchestration.models import utc_now_iso

from .models import StoryDocument


def next_story_version(version: str) -> str:
    match = re.search(r"(\d+)$", str(version or ""))
    number = int(match.group(1)) if match else 0
    return f"v{number + 1:03d}"


class StoryDocumentStore:
    """Append-only story versions inside one job workspace.

    `current.json` is only a pointer-shaped copy for fast reads; immutable
    `versions/vNNN.json` files are the source of truth.
    """

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve(strict=False)
        self.root = self.workspace / "story"
        self.versions_dir = self.root / "versions"
        self.current_path = self.root / "current.json"
        self.approved_path = self.root / "approved.json"
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def save(self, document: StoryDocument, *, advance: bool = False) -> StoryDocument:
        with self._lock:
            current = self.get_current(required=False)
            updated = document.model_copy(deep=True)
            if advance:
                updated.previous_version = (
                    current.version if current else updated.previous_version
                )
                updated.version = next_story_version(
                    current.version if current else updated.version
                )
            elif current is not None and updated.version == current.version:
                # Never mutate an existing immutable version. Initial writes may
                # replace v001 only while the document is still being assembled.
                existing = self.version_path(updated.version)
                if existing.exists() and existing.read_text("utf-8") != self._json(
                    updated
                ):
                    updated.previous_version = current.version
                    updated.version = next_story_version(current.version)
            updated.updated_at = utc_now_iso()
            self._write_atomic(self.version_path(updated.version), self._json(updated))
            self._write_atomic(self.current_path, self._json(updated))
            return updated

    def revise(self, document: StoryDocument) -> StoryDocument:
        return self.save(document, advance=True)

    def approve(self, version: str) -> StoryDocument:
        with self._lock:
            document = self.get(version)
            approved = document.model_copy(
                update={"status": "APPROVED", "updated_at": utc_now_iso()}
            )
            self._write_atomic(self.version_path(version), self._json(approved))
            self._write_atomic(self.current_path, self._json(approved))
            self._write_atomic(self.approved_path, self._json(approved))
            return approved

    def get_current(self, *, required: bool = True) -> StoryDocument | None:
        if not self.current_path.is_file():
            if required:
                raise KeyError("current")
            return None
        return StoryDocument.model_validate_json(self.current_path.read_text("utf-8"))

    def get(self, version: str) -> StoryDocument:
        path = self.version_path(version)
        if not path.is_file():
            raise KeyError(version)
        return StoryDocument.model_validate_json(path.read_text("utf-8"))

    def list_versions(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.versions_dir.glob("v*.json"), reverse=True):
            try:
                document = StoryDocument.model_validate_json(path.read_text("utf-8"))
            except Exception:
                continue
            items.append(
                {
                    "version": document.version,
                    "status": document.status,
                    "previous_version": document.previous_version,
                    "created_at": document.created_at,
                    "updated_at": document.updated_at,
                    "title": document.package.title
                    if document.package
                    else document.dna.title,
                }
            )
        return items

    def version_path(self, version: str) -> Path:
        normalized = str(version or "").strip().lower()
        if not re.fullmatch(r"v\d{3,6}", normalized):
            raise ValueError("Story versions must look like v001.")
        return self.versions_dir / f"{normalized}.json"

    @staticmethod
    def _json(document: StoryDocument) -> str:
        return json.dumps(document.model_dump(), ensure_ascii=False, indent=2)

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
