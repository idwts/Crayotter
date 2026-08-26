# Script Development and Video Composition

Crayotter supports a first-class `story_development` job alongside the existing
`video_editing` workflow. Both use the same configured text-model endpoint and
the same persisted job/event/artifact infrastructure.

## Web workflow

1. Open `/ui/` and switch the composer from **Video Editing** to **Script Development**.
2. Enter a brief, optionally upload `.txt`, `.md`, `.fountain`, `.fdx`, `.docx`,
   or `.pdf` references, and configure genre, episode count, and duration.
3. Submit the job. Crayotter ingests sources, extracts story DNA, generates three
   directions, writes episodes and scene-level video prompts, and calculates a
   similarity-risk report.
4. Review scenes in the workbench. Saving an edit creates a new immutable version.
5. Approve the desired version explicitly.
6. Choose an episode and click **Generate video from approved script**. Crayotter
   creates a normal video-editing child job with the screenplay locked into the
   request, so the existing material, editing, narration, subtitle, and export
   pipeline produces the MP4.

The composition endpoint rejects unapproved versions. Only the selected episode
is promoted to `user_temp`; alternate directions and other episodes are excluded
from the production handoff.

## API

- `POST /jobs` with `job_kind: "story_development"` and `story_config`
- `GET /jobs/{job_id}/story/current`
- `GET /jobs/{job_id}/story/versions`
- `GET /jobs/{job_id}/story/{version}`
- `PATCH /jobs/{job_id}/story/{version}` with `{ "changes": { ... } }`
- `POST /jobs/{job_id}/story/{version}/approve`
- `POST /jobs/{job_id}/story/{version}/compose-video`

Generated outputs include the canonical JSON document, Markdown, Fountain, FDX,
HTML, DOCX, PDF, localization exports when enabled, and the similarity report.
Story versions and approvals live inside the job workspace under `story/`; all
registered artifacts use `.crayotter/artifact_manifest.json`.

Similarity output is a development-risk indicator, not legal clearance. Source
rights confirmation is recorded in the request but does not replace a rights or
legal review.
