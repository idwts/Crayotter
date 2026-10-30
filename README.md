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
  <a href="https://arxiv.org/abs/2608.02694" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/arXiv-2608.02694-b31b1b?style=for-the-badge&logo=arxiv&logoColor=white" alt="arXiv 论文">
  </a>
  <a href="./LICENSE" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-blue?style=for-the-badge" alt="License">
  </a>
</p>

<p align="center">
  If Crayotter helps your research or demos, please consider giving the repo a Star on GitHub.
</p>

Crayotter turns a plain-language editing request into a video. Upload your footage or ask it to find material online, then let it select shots, edit, add narration and subtitles, and export.

> “Make a 30-second travel video from my uploads: landscape, calm style, with Chinese narration and subtitles.”

[Watch the demo](https://idwts.github.io/Crayotter/#demo) · [Download](https://github.com/idwts/Crayotter/releases) · [Report an issue](https://github.com/idwts/Crayotter/issues)

## What you can make

- Travel, campus, and event videos using local uploads or online material.
- Edits with your chosen duration, orientation, and style, including transitions, narration, and Chinese subtitles.
- Review progress, preview exports, revisit task history, and resume interrupted work from valid checkpoints.

Crayotter is intended for creators who review the result before publishing. Online sourcing depends on platform access; some platforms require browser sign-in. Models can misread footage, so check the narration and shots.

## Install and open

| Platform | Where to get it | How to open |
| --- | --- | --- |
| Windows 10/11 x64 | `Crayotter-Windows-x64.zip` in [Releases](https://github.com/idwts/Crayotter/releases) | Extract the complete archive and run `Crayotter.exe` |
| macOS Apple Silicon | Test builds in [Actions](https://github.com/idwts/Crayotter/actions/workflows/desktop-build.yml); check Releases for published packages | Unzip and open `Crayotter.app`, or move it to Applications |

Desktop packages include Python, FFmpeg, and the downloader. Conda is not required. The Windows native window uses Edge WebView2; browser mode is attempted if unavailable. The current Mac package declares macOS 15.0 or newer and targets arm64; test packages are not Apple-notarized.

## Your first video

1. **Set up models:** open Settings and enter your API key, endpoint, and models. Provider charges apply; Crayotter does not include API credit.
2. **Choose material:** upload local videos, describe a subject for online sourcing, or provide video links.
3. **Describe the edit:** include duration, style, orientation, narration, and subtitle preferences, then start the task.
4. **Review the result:** preview and save the final video from the task details, checking the narration, subtitles, and footage.

Defaults are `qwen3.8-flash` for planning/editing, `qwen3.8-omni-flash` for audio/video analysis, and `qwen3-tts-flash` for speech. For visual-only analysis, set the video model to `qwen3.8-flash`. Explicit model settings are preserved.

Sourcing, video analysis, and model reasoning take time. Runtime depends on footage length and provider response. A 30-second task is a useful first run.

## Common questions

**Where is the result?**
Open the task details in the workbench. Mac runtime data defaults to `~/Library/Application Support/Crayotter`; Windows uses the writable program directory or the user application-data directory.

**Why did sourcing fail?**
Check platform access. For platforms requiring login, authorize through your local browser and retry, or upload your own footage.

**Why did the model or narration fail?**
Check the API key, endpoint, model availability, and account balance. Text, video, and speech providers can be configured separately.

**Can I resume?**
The workbench can resume jobs marked interrupted. A failed job is different and is not guaranteed to resume in place.

## Run from source

Use Python 3.10+ and FFmpeg/FFprobe. Runtime paths select matching bundled tools when available; on Mac, `python script/setup_ffmpeg.py` can fetch missing binaries.

```bash
python -m pip install -r requirements.txt
python script/run_backend.py --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/ui/` and configure your API in Settings. For a native window, also install `requirements-desktop.txt` and run `python script/run_desktop.py`.

## Development and research

- [Source setup, configuration, and traces](docs/development.md)
- [Architecture](docs/architecture_cn.md)
- [Tool protocol, MCP, and FFmpeg](docs/tool_protocol_and_ffmpeg.md)
- [Windows build](packaging/build_windows.ps1) / [macOS build and signing](packaging/README_MACOS_CN.md)
- [Papers and citation](docs/citation.md); [experimental RL training](phase3_rl/README.md) is separate from everyday editing.

Licensed under [PolyForm Noncommercial 1.0.0](LICENSE). Check the terms before commercial use.
