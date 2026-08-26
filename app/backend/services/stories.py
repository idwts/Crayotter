"""Story-document review and version lifecycle service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from script.orchestration.artifacts import ArtifactRegistry
from script.story.export import render_production_screenplay, write_story_exports
from script.story.ingestion import ingest_sources
from script.story.models import StoryDocument
from script.story.similarity import build_similarity_report
from script.story.store import StoryDocumentStore


class StoryReviewService:
    EDITABLE_FIELDS = frozenset(
        {
            "dna",
            "directions",
            "selected_direction_id",
            "package",
            "localizations",
            "similarity_report",
        }
    )

    @staticmethod
    def store_for_workspace(workspace: Path) -> StoryDocumentStore:
        return StoryDocumentStore(workspace)

    def get_current(self, workspace: Path, *, job_id: str) -> dict[str, Any]:
        document = self.store_for_workspace(workspace).get_current()
        assert document is not None
        return {"job_id": job_id, "document": document.model_dump()}

    def get(self, workspace: Path, version: str, *, job_id: str) -> dict[str, Any]:
        document = self.store_for_workspace(workspace).get(version)
        return {"job_id": job_id, "document": document.model_dump()}

    def list_versions(self, workspace: Path, *, job_id: str) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "items": self.store_for_workspace(workspace).list_versions(),
        }

    def revise(
        self,
        workspace: Path,
        version: str,
        changes: dict[str, Any],
        *,
        job_id: str,
        revision: int,
        task: str,
        user_workspace: Path,
    ) -> dict[str, Any]:
        store = self.store_for_workspace(workspace)
        document = store.get(version)
        unknown = set(changes) - self.EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"Unsupported story fields: {', '.join(sorted(unknown))}")
        payload = document.model_dump()
        payload.update(changes)
        payload["status"] = "REVIEWED"
        revised_candidate = StoryDocument.model_validate(payload)
        if revised_candidate.package is not None and {
            "package",
            "dna",
            "selected_direction_id",
        }.intersection(changes):
            sources = ingest_sources(
                task,
                revised_candidate.request,
                workspace=workspace,
                user_workspace=user_workspace,
            )
            revised_candidate.sources = sources
            revised_candidate.similarity_report = build_similarity_report(
                revised_candidate.package,
                sources=sources,
                reference_dna=revised_candidate.dna,
                corpus_scope=revised_candidate.request.similarity_scope,
            )
        revised = store.revise(revised_candidate)
        outputs = write_story_exports(revised, workspace / "output")
        registry = ArtifactRegistry(workspace)
        for path in outputs:
            registry.register(
                kind=_artifact_kind(path),
                producer_task_id="story_review_revision",
                phase="story_development",
                path=path,
                metadata={
                    "revision": revision,
                    "story_version": revised.version,
                    "status": revised.status,
                    "accepted": False,
                },
            )
        return {
            "job_id": job_id,
            "document": revised.model_dump(),
            "output_files": [str(path) for path in outputs],
        }

    def approve(
        self,
        workspace: Path,
        version: str,
        *,
        job_id: str,
        revision: int,
    ) -> dict[str, Any]:
        store = self.store_for_workspace(workspace)
        approved = store.approve(version)
        approved_export = workspace / "story" / f"approved_{approved.version}.json"
        approved_export.write_text(
            approved.model_dump_json(indent=2),
            encoding="utf-8",
        )
        ArtifactRegistry(workspace).register(
            kind="approved_story_document",
            producer_task_id="story_review_approval",
            phase="story_development",
            path=approved_export,
            metadata={
                "revision": revision,
                "story_version": approved.version,
                "status": approved.status,
                "accepted": True,
            },
        )
        return {"job_id": job_id, "document": approved.model_dump()}

    def prepare_video_composition(
        self,
        workspace: Path,
        version: str,
        *,
        job_id: str,
        episode_number: int,
        materials_dir: Path,
    ) -> dict[str, Any]:
        document = self.store_for_workspace(workspace).get(version)
        if document.status != "APPROVED":
            raise RuntimeError("Approve this script version before generating a video.")
        package = document.package
        if package is None or not package.episodes:
            raise RuntimeError("The approved script has no episodes to compose.")
        episode = next(
            (item for item in package.episodes if item.number == episode_number),
            None,
        )
        if episode is None:
            raise ValueError(f"Episode {episode_number} was not found in {version}.")

        production_document = document.model_copy(deep=True)
        assert production_document.package is not None
        production_document.package.episodes = [episode.model_copy(deep=True)]
        production_document.package.episode_outline = [
            item
            for item in production_document.package.episode_outline
            if item.number == episode_number
        ]
        scene_ids = {scene.scene_id for scene in episode.scenes}
        production_document.package.video_prompt_package = [
            item
            for item in production_document.package.video_prompt_package
            if str(item.get("scene_id") or "") in scene_ids
        ]
        production_document.request.episode_count = 1
        production_document.request.episode_duration_seconds = (
            episode.target_duration_seconds
        )
        production_document.dna.episodes = [
            item
            for item in production_document.dna.episodes
            if item.number == episode_number
        ]

        materials_dir.mkdir(parents=True, exist_ok=True)
        filename = f"approved_story_{job_id}_{version}_ep{episode_number:03d}.md"
        target = materials_dir / filename
        target.write_text(
            render_production_screenplay(production_document), encoding="utf-8"
        )
        stat = target.stat()
        item = {
            "name": target.name,
            "path": str(target.resolve()),
            "display_path": (Path("user_temp") / target.name).as_posix(),
            "size_bytes": stat.st_size,
            "modified_at": document.updated_at,
            "kind": "file",
            "has_analysis": False,
            "analysis_count": 0,
            "analysis_path": "",
            "analysis_display_path": "",
            "analysis_modified_at": "",
        }
        return {
            "job_id": job_id,
            "story_version": version,
            "episode": episode.model_dump(),
            "document": production_document.model_dump(),
            "script_item": item,
        }


def _artifact_kind(path: Path) -> str:
    if "similarity_report" in path.name.lower():
        return "story_similarity_report"
    if path.suffix.lower() in {".fountain", ".fdx", ".html"}:
        return "screenplay"
    if path.suffix.lower() in {".docx", ".pdf", ".md"}:
        return "story_delivery_package"
    return "story_document"
