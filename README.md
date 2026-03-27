# Crayotter

<p align="center">
  <a href="./README.md">English</a> | <a href="./README_CN.md">中文</a>
</p>

<p align="center">
  <img src="./logo.png" alt="Crayotter Logo" width="180" />
</p>

Crayotter is a multimodal, agent-driven video editing system that turns a single text request into a complete edited video.

It combines **planning**, **deep editing research**, and **tool-based execution** into a three-phase workflow, with full logs and visual trace analysis for debugging and iteration.


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
- **`memory_experience\`**: Persisted post-task skills/memory summaries.
- **`website\`**: Static launch site and GitHub Pages assets.

### Added in This Branch

This branch adds a local backend service and a graphical workbench UI on top of the original project, so the agent can be used through a browser-based desktop-style interface.

- **`app\backend\`**: Local HTTP service, job manager, config store, and event streaming layer.
- **`app\frontend\`**: Workbench UI for task creation, task history, logs, configuration, and artifact preview.
- **`script\run_backend.py`**: Backend launcher for the local GUI/runtime service.
- The original CLI workflow remains available through `script\agent.py`.

---

## Workflow

Crayotter uses a three-phase architecture:

1. **Phase 1 - Material Preparation (Planner + Executor)**
   - Search candidate videos
   - Rank/select high-quality candidates
   - Download selected videos
   - Analyze each source video multimodally

2. **Phase 2 - Editing Research**
   - Read all analysis outputs
   - Build a structured editing blueprint (narrative, rhythm, transitions, narration strategy)
   - No editing tools are called in this phase

3. **Phase 3 - ReAct Editing Execution**
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

### 3) Configure API Endpoints and Keys

Edit the API configuration block in `script\agent.py` (model API, video API, and TTS API settings).

> Security note: never commit real API keys to version control.

### 4) Run the Agent

Interactive mode:

```bash
python script\agent.py
```

Single task mode:

```bash
python script\agent.py "Create a 3-minute campus-themed promo video"
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
- local configuration management
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

> The GUI stores local runtime state under `app_state/`. Do not commit `app_state/config.json`.

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
├─ app\
│  ├─ backend\
│  │  ├─ config_store.py
│  │  ├─ event_bus.py
│  │  ├─ models.py
│  │  ├─ runtime_manager.py
│  │  └─ server.py
│  └─ frontend\
│     ├─ app.js
│     ├─ index.html
│     └─ styles.css
├─ script\
│  ├─ agent.py
│  ├─ graph.py
│  ├─ run_backend.py
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
