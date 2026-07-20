from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
from typing import Any, Callable, Mapping


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rate(value: Any) -> float:
    text = str(value or "0/0")
    try:
        numerator, denominator = text.split("/", 1)
        denominator_value = float(denominator)
        return float(numerator) / denominator_value if denominator_value else 0.0
    except (TypeError, ValueError):
        return _number(value)


@dataclass(frozen=True)
class MediaProbe:
    path: str
    duration_seconds: float
    size_bytes: int
    video_codec: str
    width: int
    height: int
    pixel_format: str
    average_fps: float
    nominal_fps: float
    sample_aspect_ratio: str
    color_primaries: str
    color_transfer: str
    color_space: str
    rotation_degrees: int
    video_bit_rate: int
    has_audio: bool
    audio_codec: str | None
    audio_sample_rate: int | None
    audio_channels: int | None
    audio_channel_layout: str | None
    audio_bit_rate: int | None

    @property
    def is_variable_frame_rate(self) -> bool:
        return (
            self.average_fps > 0
            and self.nominal_fps > 0
            and abs(self.average_fps - self.nominal_fps) > 0.01
        )

    @property
    def is_hdr(self) -> bool:
        transfer = self.color_transfer.lower()
        primaries = self.color_primaries.lower()
        return transfer in {"smpte2084", "arib-std-b67"} or primaries == "bt2020"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["is_variable_frame_rate"] = self.is_variable_frame_rate
        result["is_hdr"] = self.is_hdr
        return result


def build_ffprobe_command(path: str | Path, ffprobe_bin: str = "ffprobe") -> list[str]:
    return [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration,size,bit_rate:stream=index,codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,r_frame_rate,sample_aspect_ratio,color_primaries,color_transfer,color_space,bit_rate,sample_rate,channels,channel_layout:stream_tags=rotate:stream_side_data=rotation",
        "-of",
        "json",
        str(path),
    ]


def _rotation(video: Mapping[str, Any]) -> int:
    tags = video.get("tags") or {}
    if "rotate" in tags:
        return int(round(_number(tags.get("rotate")))) % 360
    for item in video.get("side_data_list") or []:
        if "rotation" in item:
            return int(round(_number(item.get("rotation")))) % 360
    return 0


def parse_ffprobe_payload(payload: str | Mapping[str, Any], *, path: str = "") -> MediaProbe:
    data = json.loads(payload) if isinstance(payload, str) else dict(payload)
    streams = list(data.get("streams") or [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("ffprobe payload has no video stream")
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    format_data = data.get("format") or {}
    return MediaProbe(
        path=path,
        duration_seconds=_number(format_data.get("duration")),
        size_bytes=int(_number(format_data.get("size"))),
        video_codec=str(video.get("codec_name") or "unknown").lower(),
        width=int(_number(video.get("width"))),
        height=int(_number(video.get("height"))),
        pixel_format=str(video.get("pix_fmt") or "unknown").lower(),
        average_fps=_rate(video.get("avg_frame_rate")),
        nominal_fps=_rate(video.get("r_frame_rate")),
        sample_aspect_ratio=str(video.get("sample_aspect_ratio") or "unknown"),
        color_primaries=str(video.get("color_primaries") or "unknown").lower(),
        color_transfer=str(video.get("color_transfer") or "unknown").lower(),
        color_space=str(video.get("color_space") or "unknown").lower(),
        rotation_degrees=_rotation(video),
        video_bit_rate=int(_number(video.get("bit_rate"))),
        has_audio=audio is not None,
        audio_codec=str(audio.get("codec_name") or "unknown").lower() if audio else None,
        audio_sample_rate=int(_number(audio.get("sample_rate"))) if audio else None,
        audio_channels=int(_number(audio.get("channels"))) if audio else None,
        audio_channel_layout=str(audio.get("channel_layout") or "unknown") if audio else None,
        audio_bit_rate=int(_number(audio.get("bit_rate"))) if audio else None,
    )


Runner = Callable[..., subprocess.CompletedProcess[str]]


def probe_media(
    path: str | Path,
    *,
    ffprobe_bin: str = "ffprobe",
    runner: Runner = subprocess.run,
    timeout_seconds: float = 30.0,
) -> MediaProbe:
    source = Path(path).resolve(strict=False)
    if not source.is_file():
        raise FileNotFoundError(source)
    command = build_ffprobe_command(source, ffprobe_bin)
    completed = runner(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown ffprobe error")[-1200:]
        raise RuntimeError(f"ffprobe failed: {detail}")
    return parse_ffprobe_payload(completed.stdout, path=str(source))
