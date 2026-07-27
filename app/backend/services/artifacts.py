"""Read-only artifact projection used by the backend API."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from app.media_metadata import video_duration_seconds


class ArtifactQueryService:
    VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v", ".mpeg", ".mpg"}
    TEXT_SUFFIXES = {".txt", ".md", ".json", ".jsonl", ".log"}

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root
        self._media_metadata_cache: dict[tuple[str, int, int], float | None] = {}
        self._media_metadata_lock = threading.RLock()

    def collect(self, job: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        registry_metadata: dict[str, dict[str, Any]] = {}
        candidate_paths = [Path(path) for path in job.record.output_files if path]
        output_dir = job.job_dir / "output"
        if output_dir.exists():
            candidate_paths.extend(
                path for path in sorted(output_dir.rglob("*")) if path.is_file()
            )
        manifest_path = job.job_dir / "workspace" / ".crayotter" / "artifact_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for artifact in manifest.get("artifacts", []):
                    raw_path = str(artifact.get("path") or "")
                    if not raw_path:
                        continue
                    registry_metadata[str(Path(raw_path).resolve(strict=False))] = dict(artifact)
                    candidate_paths.append(Path(raw_path))
            except Exception:
                pass

        for path in candidate_paths:
            resolved = path.resolve(strict=False)
            key = str(resolved)
            registry_item = registry_metadata.get(key, {})
            if key in seen:
                continue
            seen.add(key)
            if not resolved.exists() or not resolved.is_file():
                if registry_item:
                    metadata = self._revision_metadata(registry_item, job.record.revision)
                    results.append(
                        {
                            "path": key,
                            "display_path": key,
                            "name": resolved.name,
                            "suffix": resolved.suffix.lower(),
                            "kind": registry_item.get("kind", "file"),
                            "size_bytes": 0,
                            "duration_seconds": None,
                            "artifact_id": registry_item.get("id", ""),
                            "producer_task_id": registry_item.get("producer_task_id", ""),
                            "phase": registry_item.get("phase", ""),
                            "valid": False,
                            "metadata": metadata,
                            "revision": metadata["revision"],
                            "is_current": metadata["current"],
                        }
                    )
                continue
            try:
                display_path = str(resolved.relative_to(self.runtime_root))
            except Exception:
                display_path = key
            suffix = resolved.suffix.lower()
            stat = resolved.stat()
            metadata = self._revision_metadata(registry_item, job.record.revision)
            results.append(
                {
                    "path": key,
                    "display_path": display_path,
                    "name": resolved.name,
                    "suffix": suffix,
                    "kind": str(registry_item.get("kind") or self.artifact_kind(suffix)),
                    "size_bytes": stat.st_size,
                    "duration_seconds": (
                        self.video_duration(resolved, stat)
                        if suffix in self.VIDEO_SUFFIXES
                        else None
                    ),
                    "artifact_id": registry_item.get("id", ""),
                    "producer_task_id": registry_item.get("producer_task_id", ""),
                    "phase": registry_item.get("phase", ""),
                    "valid": bool(registry_item.get("valid", True)),
                    "metadata": metadata,
                    "revision": metadata["revision"],
                    "is_current": metadata["current"],
                }
            )
        return sorted(results, key=lambda item: item["display_path"])

    @staticmethod
    def _revision_metadata(item: dict[str, Any], current_revision: int) -> dict[str, Any]:
        metadata = dict(item.get("metadata", {}))
        revision = int(metadata.get("revision", 1) or 1)
        metadata["revision"] = revision
        metadata["current"] = revision == current_revision
        return metadata

    @classmethod
    def artifact_kind(cls, suffix: str) -> str:
        if suffix in cls.VIDEO_SUFFIXES:
            return "video"
        if suffix in cls.TEXT_SUFFIXES:
            return "text"
        return "file"

    def video_duration(self, path: Path, stat: os.stat_result) -> float | None:
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        with self._media_metadata_lock:
            if key in self._media_metadata_cache:
                return self._media_metadata_cache[key]
        try:
            parsed = video_duration_seconds(path)
            duration = round(parsed, 2) if parsed is not None else None
        except (OSError, TypeError, ValueError):
            duration = None
        with self._media_metadata_lock:
            for stale_key in [
                cached for cached in self._media_metadata_cache if cached[0] == str(path)
            ]:
                self._media_metadata_cache.pop(stale_key, None)
            self._media_metadata_cache[key] = duration
        return duration
