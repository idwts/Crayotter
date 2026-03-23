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

   This phase can be disabled with `ENABLE_PHASE2_RESEARCH = False` in `script\agent.py` to save tokens.
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

### 3) Configure API Endpoints and Keys

Edit the API configuration block in `script\agent.py` (model API, video API, and TTS API settings).

You can also control whether Phase 2 runs:

```python
ENABLE_PHASE2_RESEARCH = True  # True: run Phase 2, False: skip to Phase 3
```

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
