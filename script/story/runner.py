from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from script.orchestration.artifacts import ArtifactRegistry
from script.orchestration.models import utc_now_iso
from . import gateway as model_gateway

from .export import write_story_exports
from .generation import StoryGenerator
from .ingestion import ingest_sources
from .models import StoryDocument, StoryJobConfig, StoryProvenance
from .selection import select_relevant_direction
from .similarity import build_similarity_report
from .store import StoryDocumentStore

RuntimeEventCallback = Callable[[dict[str, Any]], None]


def run_story_task(
    task: str,
    config: dict[str, Any],
    *,
    event_callback: RuntimeEventCallback | None = None,
) -> tuple[str, list[str]]:
    workspace = Path(
        os.environ.get("CRAYOTTER_TASK_WORKSPACE", "") or Path.cwd() / "temp"
    ).resolve(strict=False)
    user_workspace = Path(
        os.environ.get("CRAYOTTER_USER_WORKSPACE", "") or Path.cwd() / "user_temp"
    ).resolve(strict=False)
    workspace.mkdir(parents=True, exist_ok=True)
    user_workspace.mkdir(parents=True, exist_ok=True)
    job_id = str(config.get("job_id") or os.environ.get("CRAYOTTER_JOB_ID", ""))
    project_id = str(config.get("project_id") or "")
    revision = int(config.get("revision", 1) or 1)
    story_config = StoryJobConfig.model_validate(config.get("story_config") or {})

    def emit(event_type: str, payload: dict[str, Any]) -> None:
        if callable(event_callback):
            event_callback(
                {
                    "type": event_type,
                    "timestamp": utc_now_iso(),
                    "payload": dict(payload),
                }
            )

    emit(
        "story_workflow_started",
        {
            "job_id": job_id,
            "content_type": story_config.content_type,
            "target_languages": story_config.target_languages,
            "checkpoint": "story/intake",
        },
    )
    if not story_config.reference_rights_confirmed:
        emit(
            "story_rights_confirmation_missing",
            {
                "message": (
                    "Reference-rights confirmation was not provided. Generated output remains "
                    "a development draft and similarity report is not legal clearance."
                )
            },
        )

    sources = ingest_sources(
        task,
        story_config,
        workspace=workspace,
        user_workspace=user_workspace,
    )
    emit(
        "story_sources_ingested",
        {
            "count": len(sources),
            "characters": sum(item.character_count for item in sources),
            "source_ids": [item.source_id for item in sources],
            "checkpoint": "story/sources",
        },
    )

    generator = StoryGenerator(event_sink=emit)
    dna = generator.extract_dna(sources, story_config)
    directions = generator.generate_directions(dna, story_config)
    selected, overridden, relevance_score = select_relevant_direction(
        dna, directions, story_config
    )
    if overridden:
        for item in directions:
            item.recommended = False
        if selected.direction_id == "direction_direct_brief":
            directions[0] = selected
        else:
            selected.recommended = True
        emit(
            "story_direction_overridden",
            {
                "selected_direction_id": selected.direction_id,
                "relevance_score": round(relevance_score, 4),
                "reason": "recommended direction did not satisfy concrete brief anchors",
            },
        )
    package = generator.generate_package(dna, selected, story_config)

    localizations = []
    if story_config.generate_localizations:
        markets = story_config.target_markets or ["global"]
        for index, language in enumerate(story_config.target_languages):
            market = markets[min(index, len(markets) - 1)]
            localizations.append(
                generator.localize(
                    package,
                    language=language,
                    market=market,
                    config=story_config,
                )
            )

    similarity_report = build_similarity_report(
        package,
        sources=sources,
        reference_dna=dna,
        corpus_scope=story_config.similarity_scope,
    )
    emit(
        "story_similarity_completed",
        {
            "overall_risk": similarity_report.overall_risk,
            "overall_score": similarity_report.overall_score,
            "finding_count": len(similarity_report.findings),
            "checkpoint": "story/similarity",
        },
    )

    endpoint = model_gateway.resolve("text")
    document = StoryDocument(
        status="GENERATED",
        job_id=job_id,
        project_id=project_id,
        request=story_config,
        sources=sources,
        dna=dna,
        directions=directions,
        selected_direction_id=selected.direction_id,
        package=package,
        localizations=localizations,
        similarity_report=similarity_report,
        provenance=StoryProvenance(
            source_hashes={item.source_id: item.sha256 for item in sources},
            model_name=endpoint.model_name,
        ),
    )
    store = StoryDocumentStore(workspace)
    current = store.get_current(required=False)
    document = store.save(document, advance=current is not None)

    output_dir = workspace / "output"
    output_paths = write_story_exports(document, output_dir)
    registry = ArtifactRegistry(workspace)
    for path in output_paths:
        kind = _artifact_kind(path)
        artifact = registry.register(
            kind=kind,
            producer_task_id="story_export",
            phase="story_development",
            path=path,
            metadata={
                "revision": revision,
                "story_version": document.version,
                "status": document.status,
                "accepted": False,
                "language": _artifact_language(path, document),
            },
        )
        emit(
            "artifact_created",
            {
                "artifact_id": artifact.id,
                "kind": kind,
                "path": str(path),
                "story_version": document.version,
            },
        )

    emit(
        "story_workflow_completed",
        {
            "story_version": document.version,
            "title": package.title,
            "overall_risk": similarity_report.overall_risk,
            "outputs": [str(path) for path in output_paths],
            "checkpoint": "story/completed",
        },
    )
    summary = (
        f"Story package '{package.title}' generated as {document.version}. "
        f"{len(package.episodes)} complete episode(s), {len(localizations)} localized variant(s), "
        f"similarity risk {similarity_report.overall_risk} "
        f"({similarity_report.overall_score:.3f}). Review and approve before production use."
    )
    return summary, [str(path) for path in output_paths]


def _artifact_kind(path: Path) -> str:
    name = path.name.lower()
    if "similarity_report" in name:
        return "story_similarity_report"
    if path.suffix.lower() in {".fountain", ".fdx", ".html"}:
        return "screenplay"
    if path.suffix.lower() in {".docx", ".pdf", ".md"}:
        return "story_delivery_package"
    return "story_document"


def _artifact_language(path: Path, document: StoryDocument) -> str:
    name = path.name.lower()
    for variant in document.localizations:
        if f"_{variant.language}_" in name:
            return variant.language
    return ""
