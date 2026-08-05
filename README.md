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
  <a href="https://github.com/idwts/Crayotter/stargazers" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/github/stars/idwts/Crayotter?style=for-the-badge&logo=github&label=Star%20Crayotter&color=ffb347" alt="Star Crayotter on GitHub">
  </a>
  <a href="https://idwts.github.io/Crayotter/paper/" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/Blog-Paper%20Page-6f7cff?style=for-the-badge" alt="Research Blog">
  </a>
  <a href="https://arxiv.org/abs/2606.07636" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/arXiv-2606.07636-b31b1b?style=for-the-badge&logo=arxiv&logoColor=white" alt="arXiv Paper">
  </a>
  <a href="./LICENSE" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-blue?style=for-the-badge" alt="License">
  </a>
</p>

<p align="center">
  If Crayotter helps your research or demos, please consider giving the repo a Star on GitHub.
</p>

Crayotter is a multimodal, agent-driven video editing system that turns a single text request into a complete edited video. It combines **planning**, **deep editing research**, and **tool-based execution** into a three-phase workflow, with full logs and visual trace analysis for debugging and iteration.

---

## 📽️ Demo

<p align="center">
  <a href="https://idwts.github.io/Crayotter/#demo">
    <img src="./demo/showcase-poster.jpg" alt="Crayotter demo video preview" width="100%" />
  </a>
</p>

<p align="center">
  <a href="https://idwts.github.io/Crayotter/#demo">Watch the operation demo online</a>
</p>

A full end-to-end run: from a one-line text request, Crayotter prepares materials, researches an editing blueprint, and produces the final edited video. The online player also includes three finished case videos.

---

## Table of Contents

- [News](#news)
- [Highlights](#highlights)
- [Overview](#overview)
- [Workflow](#workflow)
- [Quick Start](#quick-start)
  - [Windows Standalone Release](#windows-standalone-release)
  - [Prerequisites](#prerequisites)
  - [1) Environment](#1-environment)
  - [2) Install Dependencies](#2-install-dependencies)
  - [3) Configure API Endpoints and Runtime Options](#3-configure-api-endpoints-and-runtime-options)
  - [4) Run the Agent](#4-run-the-agent)
  - [5) Run the Workbench GUI](#5-run-the-workbench-gui)
- [Log Trace Visualization](#log-trace-visualization)
- [Citation](#citation)
- [Repository Layout](#repository-layout)

---

## News

- **2026.6.27** — Crayotter 1.0.0 adds multi-source material import, unified download cleanup, and refreshed Windows release packaging.
- **2026.6.15** — Asynchronous scheduling upgrade accelerated the overall video generation workflow by ~1.6×.
- **2026.5.31** — Our paper is now available: [Crayotter: Traceable Multi-Agent Workflows for Long-Form Video Editing](https://arxiv.org/abs/2606.07636).
- **2026.5.23** — 100 Stars achieved!
- **2026.5.11** — The paper page is now live at [Crayotter Paper Page](https://idwts.github.io/Crayotter/paper/).
- **2026.4.10** — The release has been updated.
- **2026.3.30** — First release version available. See [v0.1.0-demo](https://github.com/idwts/Crayotter/releases/tag/v0.1.0-demo).

---

## Highlights

- **One request to one video** — A single natural-language prompt drives the whole pipeline from sourcing to export.
- **Three-phase, traceable workflow** — Explicit planning, pure-reasoning editing research, and controlled tool execution, all logged for replay.
- **Multimodal material understanding** — Each source video is analyzed by a multimodal model to inform narrative and pacing.
- **Resource-aware DAG scheduling** — Bounded concurrency for search, download, analysis, LLM, FFmpeg, TTS, and export, with retries, conflict keys, and checkpoint/resume.
- **Multi-source material import** — Bilibili, Douyin, Xiaohongshu/Rednote, Kuaishou, YouTube, or generic URLs (via `yt-dlp`).
- **Built-in narration, subtitles, and audio mixing** — Segmented TTS, loudness normalization, background-ducking, and subtitle burn-in.
- **Local workbench + desktop mode** — Web UI for task management, `.env`-synced config, structured logs, artifact preview, and interrupted-job resume.
- **Visual trace analysis** — Local server + static HTML export for inspecting phase progress and tool-call trajectories.

---

## Overview

<p align="center">
  <img src="./crayottor_framework.jpg" alt="Crayotter framework overview">
</p>

This repository centers around four core components:

- **`script/agent.py`** — Main entrypoint. Initializes runtime, runs tasks (interactive or single request), performs workspace cleanup, and writes logs/experience memory.
- **`script/graph.py`** — Orchestration layer (LangGraph `StateGraph`). Defines the three-phase workflow and routing.
- **`script/tools/`** — Modular toolset for search, download, analysis, cutting, transitions, narration, subtitles, and export.
- **`script/visualize.py`** — Log parser + local trace server for inspecting phase progress and tool calls.

Supporting folders:

- **`temp/`** — Intermediate and output artifacts during execution.
- **`user_temp/`** — User-provided local source assets.
- **`logs/`** — Runtime logs (`video_agent_*.log`).
- **`memory_experience/`** — Concise historical-case notes kept for reference only; they must not override the current task goal.
- **`website/`** — Static launch site and GitHub Pages assets.

---

## Workflow

Crayotter uses a three-phase architecture:

```text
START -> planner -> phase1_scheduler -> material_gap_evaluator
material_gap_evaluator -> planner (supplement) | editing_research | react_editor
editing_research -> react_editor -> END
```

1. **Phase 1 — Material Preparation (Planner + Executor)**
   - Planner emits an explicit dependency DAG.
   - A deterministic scheduler validates dependencies, resource pools, retries, and write conflicts.
   - Search, per-video download, and per-video analysis tasks execute concurrently when resources are available.
   - A Material Gap Evaluator decides whether to proceed or run an incremental sourcing round (at most two supplement rounds, reusing already-successful tasks).
   - Search candidate videos through the platform-agnostic material-source layer.
   - Import user-provided URLs from Bilibili, Douyin, Xiaohongshu/Rednote, Kuaishou, YouTube, or generic URLs when supported by `yt-dlp`.
   - Rank/select high-quality candidates (target orientation is a scoring factor: landscape by default, portrait when requested).
   - Download selected videos and standardize them into editing-compatible MP4/H.264/AAC assets.
   - Analyze each source video multimodally.

2. **Phase 2 — Editing Research**
   - Research source analyses concurrently.
   - Build narrative, visual, pacing, and narration strategies concurrently.
   - Integrate them into one structured editing blueprint (JSON + compatibility Markdown).
   - No editing tools are called in this phase — it is pure reasoning.

   This phase can be disabled with `CRAYOTTER_ENABLE_PHASE2_RESEARCH=false` in the runtime `.env` to save tokens. When disabled, the workflow becomes: Phase 1 → Phase 3.

3. **Phase 3 — ReAct Editing Execution**
   - Prefer a controlled editing DAG with parallel clip cutting and segmented TTS.
   - Keep timeline merge, mixing, subtitles, quality evaluation, and export serial.
   - Fall back to the existing ReAct editor when structured planning or validation fails.
   - Log full tool-call trajectory for later trace visualization.

---

## Quick Start

### Windows Standalone Release

Windows 10/11 x64 users can download `Crayotter-Windows-x64.zip` from the release page:

1. Extract the complete archive.
2. Double-click `Crayotter.exe`.
3. Enter the API key and model settings in the workbench.

The release includes Python, FFmpeg, and yt-dlp, so users do not need to install Python. It opens in a native desktop window when Microsoft Edge WebView2 is available and falls back to the default browser otherwise.

Runtime data is stored next to the executable when that directory is writable, or under `%LOCALAPPDATA%\Crayotter` otherwise. The release never includes `.env`, API keys, uploaded media, logs, or generated videos.

To build the Windows x64 release with Python 3.12:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

The build produces:

- `dist/Crayotter/`
- `Crayotter-Windows-x64.zip`

### Prerequisites

Ensure `ffmpeg` is reachable from your `PATH` so it can be called directly in a terminal. Download it from <https://ffmpeg.org/download.html> and verify with `ffmpeg -version`. On Windows, the bundled binaries under `script/dep/windows` (`ffmpeg.exe`, `ffprobe.cmd`, `yt-dlp.exe`) are also prepended to `PATH` automatically.

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

To enable browser-rendered Douyin/Xiaohongshu extraction and user-authorized
retry, install the optional browser dependencies instead:

```bash
pip install -r requirements-browser.txt
```

This mode reuses a local Chrome/Edge installation and does not automate or
bypass CAPTCHAs. Authorization state is temporary and job-scoped.

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
CRAYOTTER_ENABLE_PLAN_REVIEW=true
CRAYOTTER_DIRECT_PHASE3_EXECUTION=false
CRAYOTTER_PREFER_LOCAL_MATERIALS=false
CRAYOTTER_SEARCH_POOL_SIZE=4
CRAYOTTER_DOWNLOAD_POOL_SIZE=2
CRAYOTTER_VIDEO_ANALYSIS_POOL_SIZE=2
CRAYOTTER_LLM_POOL_SIZE=2
CRAYOTTER_FFMPEG_POOL_SIZE=2
CRAYOTTER_TTS_POOL_SIZE=2
CRAYOTTER_EXPORT_POOL_SIZE=1
CRAYOTTER_STANDARDIZE_TARGET_FPS=30
CRAYOTTER_AUDIO_LOUDNORM_TARGET=0
CRAYOTTER_AGENT_STALL_TIMEOUT_SECONDS=150
```

Notes:

- `CRAYOTTER_DIRECT_PHASE3_EXECUTION=true` skips material search/download and goes straight into the existing-material analysis + Phase 3 execution path.
- `CRAYOTTER_ENABLE_PLAN_REVIEW=true` generates a visual EditingPlan before editing and waits for user approval or natural-language revisions before Phase 3.
- `CRAYOTTER_PREFER_LOCAL_MATERIALS=true` analyzes local materials first and only searches online when the current materials are not enough.
- Resource-pool variables bound search, download, video analysis, LLM, FFmpeg, TTS, and final export work.
- Material downloads are routed through `download_material_video`; Bilibili remains the default keyword-search source, while supported third-party URLs can be imported and cleaned through the same pipeline.
- `CRAYOTTER_STANDARDIZE_TARGET_FPS` controls download cleanup frame rate. `CRAYOTTER_AUDIO_LOUDNORM_TARGET=0` disables loudness normalization; set a negative LUFS value such as `-16` to enable two-pass EBU R128 loudnorm.
- `CRAYOTTER_AGENT_STALL_TIMEOUT_SECONDS` controls the “no new progress” watchdog threshold for running jobs.
- The workbench UI writes API settings, Phase 2, direct Phase 3, local-first mode, and timeout changes back to the same `.env`.
- Candidate ranking treats target orientation as a scoring factor: landscape by default, portrait when the user explicitly asks for it. Merge/export use scale-to-cover plus centered crop instead of direct stretching.
- For videos under `user_temp`, Crayotter writes the matching `*_analysis.json` back into `user_temp`, reuses it on later runs, and removes the paired JSON when that upload is deleted from the workbench.
- `memory_experience/latest_skills.md` is automatically compacted into bounded, reference-only case notes so it does not grow indefinitely or redefine future task goals.

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

Desktop mode starts the backend and opens the standalone window:

```bash
python script\run_desktop.py
```

Alternatively, start only the local backend service:

```bash
python script\run_backend.py --host 127.0.0.1 --port 8765
```

Then open the local workbench in your browser:

```text
http://127.0.0.1:8765/ui/
```

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

## Citation

If you find Crayotter useful for your research or work, please cite:

```bibtex
@misc{yan2026crayottertraceablemultiagentworkflows,
      title={Crayotter: Traceable Multi-Agent Workflows for Long-Form Video Editing}, 
      author={Lecheng Yan and Yichong Zhang and Xiantao Xu and Jianze Lin and Ben Pan and Xiaoyu Zheng and Jiawei Qian and Anqi Wu and Jiahui Geng and Ruizhe Li and Fengyu Cai and Jingcheng Niu and Raymond Li and Wenxi Li and Chenyang Lyu},
      year={2026},
      eprint={2606.07636},
      archivePrefix={arXiv},
      primaryClass={cs.CV},
      url={https://arxiv.org/abs/2606.07636}, 
}

@misc{yan2026crayotterlearninglonghorizonvideo,
      title={Crayotter: Learning Long-Horizon Video Editing Agents via Group-Relative Preference Backpropagation}, 
      author={Lecheng Yan and Jianze Lin and Yichong Zhang and Ben Pan and Wenxi Li and Chenyang Lyu and Liting Zhou and Cathal Gurrin},
      year={2026},
      eprint={2608.02694},
      archivePrefix={arXiv},
      primaryClass={cs.CL},
      url={https://arxiv.org/abs/2608.02694}, 
}
```

---

## Repository Layout

```text
Crayotter/
├─ script/
│  ├─ agent.py
│  ├─ graph.py
│  ├─ visualize.py
│  └─ tools/
├─ app/                # backend server, frontend, runtime paths
├─ packaging/          # Windows release build scripts
├─ phase3_rl/          # experimental RL smoke pipeline (not the main runtime)
├─ demo/               # showcase media
├─ logs/
├─ temp/
├─ user_temp/
├─ memory_experience/
├─ website/            # static site + GitHub Pages assets
├─ logo.png
├─ crayottor_framework.jpg
└─ requirements.txt
```
