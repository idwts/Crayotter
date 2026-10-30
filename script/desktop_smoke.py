"""Offline check of the frozen interpreter, graph and bundled media tools."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> int:
    from app.runtime_paths import configure_runtime_environment, get_runtime_root
    from script.run_agent_worker import configure_stdio

    configure_stdio()
    configure_runtime_environment()
    from graph import build_graph
    from tools import TOOL_REGISTRY
    from tools import _native_ffmpeg as native
    from tools.subtitle_layout import burn_subtitles, resolve_subtitle_font

    result = {"frozen": bool(getattr(sys, "frozen", False)), "runtime_root": str(get_runtime_root())}
    result["graph"] = type(build_graph()).__name__
    result["tool_count"] = len(TOOL_REGISTRY.definitions())
    for name, variable in [("ffmpeg", "FFMPEG_BIN"), ("ffprobe", "FFPROBE_BIN"), ("yt-dlp", "CRAYOTTER_YTDLP_BIN")]:
        binary = os.environ[variable]
        flag = "--version" if name == "yt-dlp" else "-version"
        probe = subprocess.run([binary, flag], capture_output=True, text=True, check=True, timeout=60)
        result[name] = {"path": binary, "version": probe.stdout.splitlines()[0]}
    with tempfile.TemporaryDirectory(prefix="package-check-", dir=get_runtime_root()) as directory:
        root = Path(directory)
        source, cut, merged, subtitled, exported = [root / f"{name}.mp4" for name in ["source", "cut", "merged", "subtitled", "exported"]]
        subprocess.run([os.environ["FFMPEG_BIN"], "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], check=True, capture_output=True, timeout=60)
        native.cut_video_native(source, cut, start_time=0.25, end_time=1.25)
        native.merge_videos_native([cut, cut], merged, target_duration=None, tolerance=0.15)
        font = resolve_subtitle_font()
        if font is None:
            raise RuntimeError("Bundled subtitle font is missing")
        burn_subtitles(merged, subtitled, [{"text": "打包测试 Crayotter", "start": 0.2, "end": 1.5}], font)
        native.export_video_native(subtitled, exported, target_size=(640, 360))
        probe = native.probe_video(exported)
        if (probe.width, probe.height) != (640, 360) or abs(probe.duration - 2) > 0.1 or not probe.has_audio:
            raise RuntimeError(f"Unexpected media output: {probe}")
        result["media"] = {"cut_merge_subtitle_export": "passed", "duration": probe.duration, "width": probe.width, "height": probe.height, "audio": probe.has_audio}
    result["passed"] = True
    (get_runtime_root() / "package_self_test.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0
