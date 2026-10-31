from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.runtime_paths import configure_runtime_environment
from script.media_consistency import probe_media


def video_duration_seconds(video_path: Path) -> float | None:
    configure_runtime_environment()
    try:
        duration = probe_media(video_path, ffprobe_bin=os.environ.get("FFPROBE_BIN", "ffprobe")).duration_seconds
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return None
    return duration if duration > 0 else None


def video_has_audio(video_path: Path) -> bool:
    configure_runtime_environment()
    return probe_media(video_path, ffprobe_bin=os.environ.get("FFPROBE_BIN", "ffprobe")).has_audio
