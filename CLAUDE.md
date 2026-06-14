# Crayotter Claude Code Context

Read `AGENTS.md` in this directory before planning or editing. It is the canonical project-local briefing and contains the business summary, current architecture, runtime paths, tool registry, validation commands, and change guidelines. If this file and `AGENTS.md` differ, follow `AGENTS.md` and update this summary when appropriate.

## Current Architecture Snapshot

Crayotter keeps its LangGraph three-phase contract, but execution is now resource-aware and artifact-driven:

```text
START -> planner -> phase1_scheduler -> material_gap_evaluator
material_gap_evaluator -> planner (supplement) | editing_research | react_editor
editing_research -> react_editor -> END
```

- Shared orchestration lives in `script/orchestration/`.
- Planners/evaluators decide DAG shape and continuation; deterministic executors call tools.
- `ResourceScheduler` owns bounded concurrency, dependency validation, resource acquisition, conflict keys, retries, cancellation, checkpointing, and resume.
- `ArtifactRegistry` owns intermediate/final artifact metadata and validity.
- Phase 1 runs concurrent search, then a ranking barrier, then bounded per-video download and analysis.
- Material Gap evaluation can proceed, supplement for at most two rounds, or fail.
- A Material Gap `fail` decision fails the job; it is not a successful graph exit.
- Phase 2 is pure reasoning: concurrent research plus one Blueprint Integrator, with legacy fallback.
- Phase 3 uses a controlled editing DAG for independent cuts and TTS, keeps timeline mutation/export ordered, and falls back to ReAct on failure.
- Each backend job has an isolated workspace under `app_state/jobs/<job_id>/workspace`.
- Persisted nonterminal jobs become `interrupted` after backend restart and resume through `POST /jobs/{job_id}/resume`.

## Resource Configuration

Use only:

- `CRAYOTTER_SEARCH_POOL_SIZE`
- `CRAYOTTER_DOWNLOAD_POOL_SIZE`
- `CRAYOTTER_VIDEO_ANALYSIS_POOL_SIZE`
- `CRAYOTTER_LLM_POOL_SIZE`
- `CRAYOTTER_FFMPEG_POOL_SIZE`
- `CRAYOTTER_TTS_POOL_SIZE`
- `CRAYOTTER_EXPORT_POOL_SIZE`

Do not restore compatibility reads for the removed `*_MAX_CONCURRENCY` variables.

## Non-Negotiable Constraints

- Keep Phase 2 free of editing tool calls.
- Do not add standalone thread pools for workflow concurrency; declare DAG resources through `ResourceScheduler`.
- Do not let agents mutate global workflow state directly; persist outputs as artifacts.
- Preserve task workspace/checkpoints on resume.
- Use accepted `final_video` artifacts rather than directory-wide MP4 discovery.
- Do not delete `user_temp`, runtime job state, logs, generated media, or real `.env` values casually.
- Prefer minimal, reviewable changes and run the focused tests described in `AGENTS.md`.
