# Crayotter Project Agent Notes

This file is project-local context for future coding agents. Claude Code users can treat it as the same project briefing even though Claude Code may look for `CLAUDE.md` by default.

## Project Purpose

Crayotter is a multimodal, agent-driven automatic video editing system. Its business goal is to turn a user's single natural-language video request into a finished edited video.

Typical user request:

- "Make a 1-minute campus promo video, fresh style, with subtitles and narration."

The system searches or reuses video materials, analyzes them with a multimodal model, builds an editing strategy, executes video editing tools, adds narration/subtitles, exports a final video, and stores logs/traces for review.

## Core Workflow

The main workflow is implemented in `script/graph.py` with LangGraph `StateGraph`.

1. Phase 1: Material Preparation
   - `planner_node` creates a structured material-preparation plan.
   - `phase1_scheduler_node` converts the plan into an `ExecutionPlan` DAG and executes it through the shared `ResourceScheduler`.
   - Search tasks may run concurrently. Candidate ranking is a barrier that waits for all searches. Downloads and per-video analyses then run as independent resource-limited tasks.
   - `material_gap_evaluator_node` evaluates material quantity, usable duration, analysis completeness, topic coverage, aspect-ratio suitability, and narrative completeness.
   - The evaluator returns `proceed`, `supplement`, or `fail`. A supplement decision creates an incremental search DAG, with at most two supplement rounds and reuse of already successful tasks.
   - `CRAYOTTER_PREFER_LOCAL_MATERIALS` uses the same evaluator rather than a separate readiness rule.

2. Phase 2: Editing Research
   - `editing_research_node` is pure reasoning and does not call tools.
   - It runs per-material research and narrative, visual-continuity, pacing, and narration-strategy research with bounded `llm_pool` concurrency.
   - A single Blueprint Integrator waits for all research artifacts and produces both structured blueprint JSON and compatibility Markdown.
   - If the DAG/integrator path fails, it falls back to `_legacy_editing_research_node`.
   - Controlled by `CRAYOTTER_ENABLE_PHASE2_RESEARCH`.

3. Phase 3: Controlled Editing With ReAct Fallback
   - `react_editor_node` first attempts a constrained Phase 3 plan and converts the blueprint into an editing DAG.
   - Independent clip cuts/preprocessing and segmented TTS may run concurrently under `ffmpeg_pool` and `tts_pool`.
   - Main timeline assembly, transitions, video re-analysis, narration mix, subtitles, quality evaluation, and final export remain ordered where output dependencies or write conflicts require serialization.
   - The export candidate is registered as the accepted `final_video` artifact only after evaluation succeeds.
   - Planning, execution, or quality-check failure falls back to the existing short-form/ReAct editor using `EDITING_TOOLS`.
   - Keep `add_narration_segments` compatible. The controlled path uses `script/tools/narration_pipeline.py` to compose pre-generated segment audio.

Graph shape:

```text
START -> planner -> phase1_scheduler -> material_gap_evaluator
material_gap_evaluator -> planner (supplement) | editing_research | react_editor
editing_research -> react_editor -> END
```

An evaluator `fail` decision raises a job failure instead of routing to a normal graph end.

The old Phase 1 `executor_node` and prep-router helpers may remain for compatibility/reference, but they are not connected by `build_graph()`. Do not infer the active graph from those legacy functions.

## DAG Scheduling And Artifacts

The shared orchestration package is `script/orchestration/`.

- `models.py`
  - `TaskSpec`: dependencies, arguments, required resources, input/output artifact kinds, conflict keys, and retry policy.
  - `ExecutionPlan`: a phase-scoped DAG.
  - `TaskState` and `TaskExecutionResult`: persisted execution state and executor output.
  - `ResourcePoolConfig`: resource-pool capacities.
- `scheduler.py`
  - `ResourceScheduler` validates dependency references and cycles.
  - It acquires multiple resources atomically in a fixed order, enforces conflict keys, runs blocking work in a bounded `ThreadPoolExecutor`, handles retries/cancellation, and checkpoints plans/states.
  - Resume reuses a completed task only when its task/dependency fingerprints and output artifacts are still valid.
- `artifacts.py`
  - `ArtifactRegistry` persists `.crayotter/artifact_manifest.json`.
  - Artifacts record kind, path, producer task, phase, metadata, checksum/size, and validity.
  - Missing or changed files invalidate reuse.

Agents/planners decide task shape and dependencies. Only deterministic executors call tools and register artifacts. New concurrent work must go through `ResourceScheduler`; do not add isolated `ThreadPoolExecutor` instances inside Phase 1/2/3 tools.

Tasks that may write the same path must share a conflict key. Resource requests must use these exact pool names:

- `search_pool`
- `download_pool`
- `video_analysis_pool`
- `llm_pool`
- `ffmpeg_pool`
- `tts_pool`
- `export_pool`

## Main Entrypoints

- `script/agent.py`
  - CLI and programmatic task runner.
  - Reads runtime config, builds the graph, initializes/cleans a fresh task workspace, preserves it on resume, writes logs, collects accepted artifacts, and updates `memory_experience`.
  - Single task: `python script\agent.py "task text"`
  - Interactive: `python script\agent.py`

- `script/run_backend.py`
  - Starts the local HTTP backend for the web workbench.
  - Common command: `python script\run_backend.py --host 127.0.0.1 --port 8765`
  - UI: `http://127.0.0.1:8765/ui/`

- `script/run_desktop.py`
  - Starts a desktop/webview wrapper if `pywebview` is installed; otherwise opens the system browser.

- `script/run_agent_worker.py`
  - Worker entry used by backend-managed real agent jobs.

- `script/visualize.py`
  - Parses logs and serves or exports trace visualizations.

## Runtime And Configuration

Runtime paths are centralized in `app/runtime_paths.py`.

- Source/bundle root is usually the repository root.
- Runtime root is selected by:
  - `CRAYOTTER_RUNTIME_ROOT`, if set
  - otherwise writable executable/source directory
  - otherwise `%LOCALAPPDATA%\Crayotter` / `%APPDATA%\Crayotter`
  - otherwise `~/.crayotter`

Important runtime directories:

- `temp`: intermediate files and final output candidates
- `user_temp`: user-uploaded local source videos and paired `*_analysis.json`
- `logs`: `agent_*.log` and `video_agent_*.log`
- `memory_experience`: historical case memory, especially `latest_skills.md`
- `app_state/jobs`: backend job records, events, summaries, and per-job workspaces
- `app_state/jobs/<job_id>/workspace`: isolated task workspace containing DAG checkpoints and the artifact manifest

Backend workers set:

- `CRAYOTTER_TASK_WORKSPACE` to the current job workspace
- `CRAYOTTER_USER_WORKSPACE` to the shared user-upload workspace

Path helpers in `script/tools/_shared.py` honor these variables. Do not replace them with repository-relative `temp` assumptions.

The web workbench uses the runtime-root `.env` as the config source of truth. `app/backend/config_store.py` maps `.env` variables to `AppConfig`.

Important env vars:

- `CRAYOTTER_API_KEY`
- `CRAYOTTER_BASE_URL`
- `CRAYOTTER_MODEL_NAME`
- `CRAYOTTER_VIDEO_API_KEY`
- `CRAYOTTER_VIDEO_BASE_URL`
- `CRAYOTTER_VIDEO_MODEL_NAME`
- `CRAYOTTER_TTS_API_KEY`
- `CRAYOTTER_TTS_BASE_URL`
- `CRAYOTTER_TTS_MODEL_NAME`
- `CRAYOTTER_ENABLE_PHASE2_RESEARCH`
- `CRAYOTTER_DIRECT_PHASE3_EXECUTION`
- `CRAYOTTER_PREFER_LOCAL_MATERIALS`
- `CRAYOTTER_AGENT_STALL_TIMEOUT_SECONDS`
- `CRAYOTTER_SEARCH_POOL_SIZE` (default `4`)
- `CRAYOTTER_DOWNLOAD_POOL_SIZE` (default `2`)
- `CRAYOTTER_VIDEO_ANALYSIS_POOL_SIZE` (default `2`)
- `CRAYOTTER_LLM_POOL_SIZE` (default `2`)
- `CRAYOTTER_FFMPEG_POOL_SIZE` (default `2`)
- `CRAYOTTER_TTS_POOL_SIZE` (default `2`)
- `CRAYOTTER_EXPORT_POOL_SIZE` (default `1`)

The resource-pool settings are a deliberate breaking configuration change. Do not read or reintroduce:

- `CRAYOTTER_PREP_MAX_CONCURRENCY`
- `CRAYOTTER_DOWNLOAD_MAX_CONCURRENCY`
- `CRAYOTTER_VIDEO_ANALYSIS_MAX_CONCURRENCY`

Never commit real `.env` values or API keys.

Bundled/located binaries:

- `app/runtime_paths.py` prepends candidate binary directories to `PATH`.
- Windows bundled binaries live under `script/dep/windows`, including `ffmpeg.exe`, `ffprobe.cmd`, and `yt-dlp.exe`.

## Tool System

Tools live under `script/tools/`.

The main tool registry is `script/tools/__init__.py`:

- `ALL_TOOLS` is the authoritative list exposed to the main graph.
- `PREP_TOOLS` and `EDITING_TOOLS` are selected in `script/graph.py`.
- Some files under `script/tools/` define tools that are not currently in `ALL_TOOLS`; do not assume every `@tool` is reachable by the agent.

Core tool responsibilities:

- Search/download/rank:
  - `search_bilibili_video`
  - `download_bilibili_video`
  - `rank_video_candidates`
  - older YouTube tools exist but are not in `ALL_TOOLS`
- Analysis:
  - `analyze_video`
  - semantic indexes and analysis persistence are handled through helpers in `_shared.py`
- Editing:
  - `cut_video`
  - `batch_cut_video`
  - `merge_videos`
  - `add_transition`
  - `list_transition_presets`
  - `plan_transition_timeline`
  - `inspect_video_duration`
- Timeline/narration/audio:
  - `recall_semantic_segments`
  - `build_edit_timeline_from_segments`
  - `align_narration_to_timeline`
  - `validate_timeline_constraints`
  - `validate_narration_timeline`
  - `add_narration_segments`
  - `add_subtitles`
  - `duck_background_audio`
  - `normalize_loudness`
- Export:
  - `export_video`

Path safety and input resolution are mostly in `script/tools/_shared.py`. Prefer those helpers when adding new file tools.

## Backend And Frontend

Backend:

- `app/backend/server.py` is a plain `http.server` based local API.
- `app/backend/runtime_manager.py` manages one running job at a time, job cancellation, job event streams, and artifact collection.
- `app/backend/event_bus.py` stores and streams runtime events.
- `app/backend/models.py` defines `AppConfig`, `JobRequest`, `JobRecord`, and `RuntimeEvent`.

Important API routes:

- `GET /health`
- `GET /config`
- `PUT /config`
- `GET /jobs`
- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/events`
- `GET /jobs/{job_id}/events/stream`
- `GET /jobs/{job_id}/artifacts`
- `POST /jobs/{job_id}/cancel`
- `POST /jobs/{job_id}/resume`
- `DELETE /jobs/{job_id}`
- `GET /uploads`
- `POST /uploads`
- `DELETE /uploads?path=user_temp/<file>`
- `GET /files?path=<absolute-or-project-relative-path>`

Frontend:

- `app/frontend_src/`: React/Vite source; edit this directory.
- `app/frontend/`: generated static build served by the backend; rebuild it after source changes.

The workbench supports:

- task history
- demo and real agent jobs
- interrupted-job resume from the latest scheduler checkpoint
- local upload management
- API/runtime settings synced to `.env`
- resource-pool settings
- toggles for Phase 2, direct Phase 3, and local-material priority
- structured scheduler/resource/retry/evaluator events and artifact previews

`JobRecord.status` includes `interrupted`. On backend restart, nonterminal persisted jobs become `interrupted`; resume is allowed only from that state. Resume must preserve the job workspace and must not perform the normal pre-run cleanup that would delete reusable checkpoints/artifacts.

The artifact API reads the registry manifest and returns intermediate artifacts as well as final output, including producer task, phase, metadata, and validity. Do not identify final output by collecting every MP4 in a directory; use the accepted `final_video` artifact.

Static website/demo assets are separate under `website/` and should not be confused with the local workbench UI.

## Memory And Logs

- `memory_experience/latest_skills.md` is historical case memory.
- Memory is reference-only. It must not override the current user's topic, style,素材 choice, target duration, or business goal.
- `script/agent.py` updates memory after a completed task by re-analyzing the final video and summarizing reusable patterns.
- Logs are used both for debugging and visualization. Keep log messages clear if changing graph/tool execution.

## Phase 3 RL Directory

`phase3_rl/` is an experimental/smoke pipeline for `verl + Qwen3.5 + Crayotter` Phase 3 RL integration.

It includes:

- fixture-based local rollout
- dataset export
- tool config export
- `CrayotterSubprocessTool`
- custom `CrayotterPhase3ToolAgentLoop`
- GRPO smoke script

This is not the main app runtime. The README in `phase3_rl/README_CN.md` documents environment-specific assumptions such as vendored `verl`, `sglang`, and AutoDL paths.

## Development Notes

- Python target is 3.10+.
- Dependencies are in `requirements.txt`.
- Focused orchestration tests live in `tests/test_orchestration.py`.
- Prefer focused smoke checks:
  - `.venv\Scripts\python.exe -m unittest discover -s tests -v`
  - compile/import changed Python modules
  - build `script.graph.build_graph()`
  - import critical modules
  - run `python script\run_backend.py --host 127.0.0.1 --port 8765`
  - hit `GET /health`
  - for frontend changes, run `npm run build` from `app/frontend_src`
  - for agent runs, use a real `.env` and expect network/API usage
- Network/API-dependent code may fail without credentials or connectivity.
- Video operations require FFmpeg/FFprobe; bundled Windows binaries are present, but PATH/runtime resolution still matters.

## Change Guidelines For Future Agents

- Keep the three-phase contract intact unless the user explicitly asks for architecture changes.
- Do not make Phase 2 call editing tools; it is intended to be pure reasoning.
- Keep planner/evaluator judgment separate from deterministic tool execution.
- Route new parallel work through the shared scheduler and declare dependencies, resources, artifacts, conflicts, and retries explicitly.
- Preserve checkpoint and artifact compatibility when changing task IDs, plan IDs, fingerprints, or workspace paths.
- Keep final timeline mutation and export serialized unless the outputs are demonstrably independent.
- Do not let historical memory rewrite the active task's requirements.
- When adding tools, register them in `script/tools/__init__.py` and confirm whether they belong in Phase 1, Phase 3, or both.
- Preserve real file paths returned by tools; prompts explicitly warn agents not to guess or rename paths.
- Be careful with cleanup logic:
  - a fresh CLI task may clean its task workspace before execution
  - a resumed backend task must retain its job workspace
  - `user_temp` contains user uploads and reusable analysis files; do not delete it casually.
  - `memory_experience` stores reusable process memory; do not overwrite it unless working on memory behavior.
- Avoid committing generated videos, logs, runtime job state, or real `.env`.
- If changing frontend behavior, verify both `app/frontend` workbench and backend API expectations.
- If changing runtime paths, consider frozen/desktop mode and `CRAYOTTER_RUNTIME_ROOT`.
