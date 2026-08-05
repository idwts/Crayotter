from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True, slots=True)
class VideoProbe:
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool


def native_ffmpeg_enabled() -> bool:
    return os.environ.get("CRAYOTTER_NATIVE_FFMPEG_PIPELINE", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _binary(env_name: str, executable: str) -> str:
    configured = os.environ.get(env_name, "").strip()
    if configured:
        return configured
    located = shutil.which(executable)
    if located:
        return located
    raise RuntimeError(f"{executable} executable was not found on PATH")


def _run(command: Sequence[str], *, timeout: int = 1800) -> None:
    process = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout or "unknown ffmpeg error").strip()
        raise RuntimeError(detail[-2000:])


def _parse_rate(value: object) -> float:
    text = str(value or "0/1")
    numerator, _, denominator = text.partition("/")
    try:
        denominator_value = float(denominator or 1.0)
        return float(numerator) / denominator_value if denominator_value else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def probe_video(path: Path) -> VideoProbe:
    command = [
        _binary("FFPROBE_BIN", "ffprobe"),
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,width,height,avg_frame_rate,r_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout or "ffprobe failed").strip()
        raise RuntimeError(detail[-1200:])

    payload = json.loads(process.stdout or "{}")
    streams = payload.get("streams", [])
    video = next(
        (item for item in streams if item.get("codec_type") == "video"),
        None,
    )
    if not isinstance(video, dict):
        raise RuntimeError(f"no video stream found in {path}")

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = _parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    duration = float((payload.get("format") or {}).get("duration") or 0.0)
    if width <= 0 or height <= 0 or duration <= 0:
        raise RuntimeError(f"invalid video metadata for {path}")
    return VideoProbe(
        width=width,
        height=height,
        fps=fps if fps > 0 else 30.0,
        duration=duration,
        has_audio=any(item.get("codec_type") == "audio" for item in streams),
    )


def _encoder_args(*, bitrate: str | None = None) -> list[str]:
    args = [
        "-c:v",
        "libx264",
        "-preset",
        os.environ.get("CRAYOTTER_FFMPEG_PRESET", "veryfast").strip() or "veryfast",
        "-threads",
        str(_positive_int_env("CRAYOTTER_FFMPEG_THREADS", 8)),
    ]
    if bitrate:
        args.extend(["-b:v", bitrate])
    else:
        args.extend(["-crf", os.environ.get("CRAYOTTER_FFMPEG_CRF", "20")])
    args.extend(["-pix_fmt", "yuv420p"])
    return args


def _base_command() -> list[str]:
    return [
        _binary("FFMPEG_BIN", "ffmpeg"),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-filter_threads",
        str(_positive_int_env("CRAYOTTER_FFMPEG_FILTER_THREADS", 4)),
        "-filter_complex_threads",
        str(_positive_int_env("CRAYOTTER_FFMPEG_FILTER_THREADS", 4)),
    ]


def cut_video_native(
    input_path: Path,
    output_path: Path,
    *,
    start_time: float,
    end_time: float,
) -> float:
    probe = probe_video(input_path)
    start = float(start_time)
    end = float(end_time)
    if start < 0 or end <= start or end > probe.duration + 0.05:
        raise ValueError(
            f"invalid cut range {start:.3f}-{end:.3f}s for {probe.duration:.3f}s video"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [*_base_command(), "-i", str(input_path), "-ss", f"{start:.6f}", "-t", f"{end - start:.6f}"]
    command.extend(["-map", "0:v:0", "-map", "0:a:0?"])
    command.extend(_encoder_args())
    command.extend(["-c:a", "aac", "-movflags", "+faststart", str(output_path)])
    try:
        _run(command)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return end - start


def export_video_native(
    input_path: Path,
    output_path: Path,
    *,
    target_size: tuple[int, int],
    bitrate: str = "8000k",
) -> float:
    probe = probe_video(input_path)
    target_w, target_h = (int(target_size[0]), int(target_size[1]))
    if target_w <= 0 or target_h <= 0:
        raise ValueError(f"invalid export target size: {target_size}")

    video_filter = "setsar=1,format=yuv420p"
    if (probe.width, probe.height) != (target_w, target_h):
        video_filter = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},setsar=1,format=yuv420p"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [*_base_command(), "-i", str(input_path), "-map", "0:v:0", "-map", "0:a:0?"]
    command.extend(["-vf", video_filter])
    command.extend(_encoder_args(bitrate=bitrate))
    command.extend(["-c:a", "aac", "-movflags", "+faststart", str(output_path)])
    try:
        _run(command)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return probe.duration


def merge_videos_native(
    input_paths: Sequence[Path],
    output_path: Path,
    *,
    target_duration: float | None,
    tolerance: float,
) -> tuple[float, int, tuple[int, int]]:
    if not input_paths:
        raise ValueError("no videos were supplied")

    probes = [probe_video(path) for path in input_paths]
    landscape = [item for item in probes if item.width >= item.height]
    portrait = [item for item in probes if item.height > item.width]
    anchor = portrait[0] if portrait and len(portrait) > len(landscape) else landscape[0] if landscape else probes[0]
    target_w, target_h = anchor.width, anchor.height
    target_w -= target_w % 2
    target_h -= target_h % 2
    target_fps = max(item.fps for item in probes)

    selected: list[tuple[Path, VideoProbe, float]] = []
    remaining = float(target_duration) if target_duration and target_duration > 0 else None
    for path, probe in zip(input_paths, probes):
        if remaining is not None and remaining <= 0:
            break
        duration = probe.duration
        if remaining is not None and duration > remaining * (1.0 + float(tolerance)):
            duration = remaining
        selected.append((path, probe, duration))
        if remaining is not None:
            remaining -= duration

    if not selected:
        raise ValueError("no videos remained after duration selection")

    command = _base_command()
    for path, _, _ in selected:
        command.extend(["-i", str(path)])

    has_any_audio = any(probe.has_audio for _, probe, _ in selected)
    filters: list[str] = []
    concat_inputs: list[str] = []
    for index, (_, probe, duration) in enumerate(selected):
        filters.append(
            f"[{index}:v:0]trim=duration={duration:.6f},setpts=PTS-STARTPTS,"
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},fps={target_fps:.6f},setsar=1,format=yuv420p[v{index}]"
        )
        concat_inputs.append(f"[v{index}]")
        if has_any_audio:
            if probe.has_audio:
                filters.append(
                    f"[{index}:a:0]atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,"
                    "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                    f"aresample=async=1:first_pts=0[a{index}]"
                )
            else:
                filters.append(
                    "anullsrc=channel_layout=stereo:sample_rate=48000,"
                    f"atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[a{index}]"
                )
            concat_inputs.append(f"[a{index}]")

    if has_any_audio:
        filters.append(
            "".join(concat_inputs)
            + f"concat=n={len(selected)}:v=1:a=1[vout][aout]"
        )
    else:
        filters.append(
            "".join(concat_inputs)
            + f"concat=n={len(selected)}:v=1:a=0[vout]"
        )

    command.extend(["-filter_complex", ";".join(filters), "-map", "[vout]"])
    if has_any_audio:
        command.extend(["-map", "[aout]"])
    command.extend(_encoder_args())
    if has_any_audio:
        command.extend(["-c:a", "aac"])
    command.extend(["-movflags", "+faststart", str(output_path)])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run(command)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise

    return sum(item[2] for item in selected), len(selected), (target_w, target_h)
