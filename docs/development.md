# Development and advanced configuration

Start with the [user guide](../README.md) if you want to create a video. This page covers source installation, model budgets, runtime configuration, and traces.

### Prerequisites

Ensure `ffmpeg` is reachable from your `PATH` so it can be called directly in a terminal. Download it from <https://ffmpeg.org/download.html> and verify with `ffmpeg -version`. The runtime automatically selects binaries for the host OS and CPU: Windows uses `script/dep/windows`; Apple Silicon and Intel Macs use `script/dep/macos/arm64` and `script/dep/macos/x86_64`. Run `python script/setup_ffmpeg.py` to download and verify missing macOS FFmpeg/FFprobe files. Explicit `FFMPEG_BIN` / `FFPROBE_BIN` overrides take precedence.

### 1) Environment

Use Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate
```

Tools share a registry with an optional standard MCP stdio server. Cutting, merging, narration, subtitles, and export now use native FFmpeg. See [tool protocol and FFmpeg migration](tool_protocol_and_ffmpeg.md) for setup and compatibility details.

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
CRAYOTTER_MODEL_NAME=qwen3.8-flash
CRAYOTTER_VIDEO_MODEL_NAME=qwen3.8-omni-flash
CRAYOTTER_TTS_MODEL_NAME=qwen3-tts-flash

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
CRAYOTTER_AGENT_STALL_TIMEOUT_SECONDS=1200
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

### Model output budgets

Defaults are `qwen3.8-flash` for text, `qwen3.8-omni-flash` for video/audio analysis,
and `qwen3-tts-flash` for speech. Set `CRAYOTTER_VIDEO_MODEL_NAME=qwen3.8-flash`
for visual-only analysis.

Per-call output ceilings, including reasoning, are defined in
`app/model_defaults.py`: Phase 1 32768, Phase 2 65536, Phase 3 32768, video
analysis 32768, and auxiliary calls 8192. Override them before launch through
`CRAYOTTER_PHASE1_MAX_OUTPUT_TOKENS`, `CRAYOTTER_PHASE2_MAX_OUTPUT_TOKENS`,
`CRAYOTTER_PHASE3_MAX_OUTPUT_TOKENS`, `CRAYOTTER_VIDEO_ANALYSIS_MAX_OUTPUT_TOKENS`,
and `CRAYOTTER_AUXILIARY_MAX_OUTPUT_TOKENS`. These are output limits, not context
windows or aggregate phase allowances. Model-default thinking stays enabled;
truncated graph responses are rejected explicitly. Existing explicit model
choices are preserved.

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

