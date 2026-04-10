# Crayotter

<p align="center">
  <a href="./README.md">English</a> | <a href="./README_CN.md">中文</a>
</p>

<p align="center">
  <img src="./logo.png" alt="Crayotter Logo" width="180" />
</p>

<p align="center">
  <a href="https://idwts.github.io/Crayotter" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/🚀-Interactive%20Demo-4CAF50?style=for-the-badge&logo=googlechrome&logoColor=white" alt="Interactive Demo">
  </a>
</p>

Crayotter is a multimodal, agent-driven video editing system that turns a single text request into a complete edited video.

It combines **planning**, **deep editing research**, and **tool-based execution** into a three-phase workflow, with full logs and visual trace analysis for debugging and iteration.


---

## NEWS

- 2026.4.10：The release has been updated.
- 2026.3.30: The first release version is now available. See [v0.1.0-demo](https://github.com/idwts/Crayotter/releases/tag/v0.1.0-demo).

---

## Overview

This repository centers around four core components:

- **`script\agent.py`**: Main entrypoint. Initializes runtime, runs tasks (interactive or single request), performs workspace cleanup, and writes logs/experience memory.
- **`script\graph.py`**: Orchestration layer (LangGraph StateGraph). Defines the three-phase workflow and routing.
- **`script\tools\`**: Modular toolset for search, download, analysis, cutting, transitions, narration, subtitles, and export.
- **`script\visualize.py`**: Log parser + local trace server for inspecting phase progress and tool calls.

Supporting folders:

- **`temp\`**: Intermediate and output artifacts during execution.
- **`user_temp\`**: User-provided local source assets.
- **`logs\`**: Runtime logs (`video_agent_*.log`).
- **`memory_experience\`**: Concise historical-case notes kept for reference only; they must not override the current task goal.
- **`website\`**: Static launch site and GitHub Pages assets.

---

## Workflow

Crayotter uses a three-phase architecture:

1. **Phase 1 — Material Preparation (Planner + Executor)**
   - Search candidate videos
   - Rank/select high-quality candidates
   - Download selected videos
   - Analyze each source video multimodally

2. **Phase 2 — Editing Research**
   - Read all analysis outputs
   - Build a structured editing blueprint (narrative, rhythm, transitions, narration strategy)
   - No editing tools are called in this phase

   This phase can be disabled with `CRAYOTTER_ENABLE_PHASE2_RESEARCH=false` in the runtime `.env` to save tokens.
   When disabled, the workflow becomes: Phase 1 → Phase 3.

3. **Phase 3 — ReAct Editing Execution**
   - Execute cutting, merging, transition design, narration/subtitles, and final export
   - Log full tool-call trajectory for later trace visualization

---

## Quick Start

### 1) Environment

Use Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 2) Install Dependencies

```bash
pip install -r requirements.txt
```

### 3) Configure API Endpoints and Runtime Options

Copy `.env.example` to `.env`, then edit the values there:

```bash
copy .env.example .env
```

Common options:

```env
CRAYOTTER_API_KEY=your-key
CRAYOTTER_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
CRAYOTTER_MODEL_NAME=qwen-plus
CRAYOTTER_VIDEO_MODEL_NAME=qwen-vl-max-latest
CRAYOTTER_TTS_MODEL_NAME=qwen-tts-latest

CRAYOTTER_ENABLE_PHASE2_RESEARCH=true
CRAYOTTER_DIRECT_PHASE3_EXECUTION=false
CRAYOTTER_PREFER_LOCAL_MATERIALS=false
CRAYOTTER_AGENT_STALL_TIMEOUT_SECONDS=150
```

Notes:

- `CRAYOTTER_DIRECT_PHASE3_EXECUTION=true` skips material search/download and goes straight into the existing-material analysis + Phase 3 execution path.
- `CRAYOTTER_PREFER_LOCAL_MATERIALS=true` analyzes local materials first and only searches online when the current materials are not enough.
- `CRAYOTTER_AGENT_STALL_TIMEOUT_SECONDS` controls the “no new progress” watchdog threshold for running jobs.
- The workbench UI writes API settings, Phase 2, direct Phase 3, local-first mode, and timeout changes back to the same `.env`.
- Candidate ranking now treats target orientation as a scoring factor: landscape by default, portrait when the user explicitly asks for it. Merge/export also use scale-to-cover plus centered crop instead of direct stretching.
- For videos under `user_temp`, Crayotter now writes the matching `*_analysis.json` back into `user_temp`, reuses it on later runs, and removes the paired JSON when that upload is deleted from the workbench.
- `memory_experience\latest_skills.md` is automatically compacted into bounded, reference-only case notes so it does not grow indefinitely or redefine future task goals.

> Security note: never commit real API keys to version control.

### 4) Run the Agent

Interactive mode:

```bash
python script\agent.py
```

Single task mode:

```bash
python script\agent.py "Create a 1-minute campus-themed promo video"
```

### 5) Run the Workbench GUI

Start the local backend service:

```bash
python script\run_backend.py --host 127.0.0.1 --port 8765
```

Then open the local workbench in your browser:

```text
http://127.0.0.1:8765/ui/
```

The workbench supports:

- task creation in `demo` and `agent` modes
- local configuration management with `.env` sync
- task history
- structured logs and event viewing
- artifact preview and download

The backend also exposes local runtime routes such as:

- `GET /health`
- `GET /config`
- `PUT /config`
- `GET /jobs`
- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/events`
- `POST /jobs/{job_id}/cancel`

> The GUI uses the runtime-root `.env` as the only configuration source of truth. Do not commit real `.env` values.

---

## Log Trace Visualization

Launch trace UI using the latest log:

```bash
python script\visualize.py
```

Use a specific log:

```bash
python script\visualize.py logs\video_agent_20260321_045836.log
```

Custom port:

```bash
python script\visualize.py --port 8080
```

`script\visualize.py` also exports a static trace HTML file next to the input log (e.g., `*_trace.html`).

---

## Repository Layout

```text
Crayotter\
├─ script\
│  ├─ agent.py
│  ├─ graph.py
│  ├─ visualize.py
│  └─ tools\
├─ logs\
├─ temp\
├─ user_temp\
├─ memory_experience\
├─ website\
├─ logo.png
└─ requirements.txt
```
