"""Compatibility probe for Windows bundles that only contain ffmpeg.exe.

Prefer a real ffprobe binary. This fallback reads FFmpeg's input stream headers
without decoding frames, and supports the queries used by Crayotter.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys


def probe_with_ffmpeg(video_path: Path) -> dict:
    bundled = Path(__file__).parent / "dep/windows/ffmpeg.exe"
    executable = os.environ.get("FFMPEG_BIN") or (str(bundled) if os.name == "nt" and bundled.is_file() else "ffmpeg")
    completed = subprocess.run(
        [executable, "-hide_banner", "-i", str(video_path)], capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    text = completed.stderr
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    duration = sum(float(value) * scale for value, scale in zip(match.groups(), (3600, 60, 1))) if match else 0.0
    streams = []
    for line in text.splitlines():
        stream = re.search(r"Stream #\d+:(\d+).*?: (Video|Audio): (\w+)", line)
        if not stream:
            continue
        index, kind, codec = stream.groups()
        item = {"index": int(index), "codec_type": kind.lower(), "codec_name": codec, "duration": str(duration)}
        if kind == "Video":
            size = re.search(r"(?:,\s*)(\d{2,6})x(\d{2,6})(?:\s|,|\[)", line)
            fps = re.search(r"([\d.]+) fps", line)
            pixel = re.search(r"Video:.*?,\s*(\w+)", line)
            if size:
                item.update(width=int(size[1]), height=int(size[2]))
            rate = f"{round(float(fps[1]) * 1000)}/1000" if fps else "0/1"
            item.update(avg_frame_rate=rate, r_frame_rate=rate, pix_fmt=pixel[1] if pixel else "unknown", sample_aspect_ratio="1:1")
            color = re.search(r"\b(bt709|bt2020)\b", line)
            if color:
                item.update(color_primaries=color[1], color_transfer=color[1], color_space=color[1])
        else:
            sample = re.search(r"(\d+) Hz", line)
            item.update(sample_rate=sample[1] if sample else "48000", channels=1 if "mono" in line else 2,
                        channel_layout="mono" if "mono" in line else "stereo")
        streams.append(item)
    if not streams:
        raise RuntimeError(text[-1200:] or "FFmpeg could not read media streams")
    return {"format": {"duration": str(duration), "size": str(video_path.stat().st_size)}, "streams": streams}


def main(argv: list[str]) -> int:
    if not argv or not Path(argv[-1]).is_file():
        print("ffprobe shim: missing or invalid input file", file=sys.stderr)
        return 1
    try:
        payload = probe_with_ffmpeg(Path(argv[-1]))
        if "-select_streams" in argv:
            selector = argv[argv.index("-select_streams") + 1]
            kind = {"a": "audio", "v": "video"}.get(selector.split(":")[0])
            payload["streams"] = [s for s in payload["streams"] if s["codec_type"] == kind]
            if ":" in selector:
                index = int(selector.split(":")[1])
                payload["streams"] = payload["streams"][index:index + 1]
        if "json" in argv:
            print(json.dumps(payload))
        elif any("stream=index" in arg for arg in argv):
            for stream in payload["streams"]:
                print(stream["index"])
        elif any("format=duration" in arg for arg in argv):
            print(payload["format"]["duration"])
        elif any("stream=" in arg for arg in argv):
            entries = argv[argv.index("-show_entries") + 1]
            fields = entries.partition("stream=")[2].split(":")[0].split(",")
            for stream in payload["streams"]:
                for field in fields:
                    if field in stream:
                        print(stream[field])
        else:
            raise ValueError("unsupported probe query; install a real ffprobe binary")
        return 0
    except Exception as exc:
        print(f"ffprobe shim: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
