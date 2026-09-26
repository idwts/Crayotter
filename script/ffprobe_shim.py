from __future__ import annotations

import json
import sys
from pathlib import Path


def _cv2_props(video_path: Path) -> tuple[float, float, int, int]:
    """(fps, duration, width, height) via cv2; duration<=0 signals failure."""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = frame_count / fps if fps > 0 else 0.0
    finally:
        cap.release()
    return fps, duration, width, height


def _has_audio_track(video_path: Path) -> bool:
    """Audio detection without ffprobe (this shim IS ffprobe here — never recurse)."""
    from moviepy.video.io.VideoFileClip import VideoFileClip

    clip = VideoFileClip(str(video_path))
    try:
        return clip.audio is not None
    finally:
        clip.close()


def _print_duration(video_path: Path) -> int:
    fps, duration, _, _ = _cv2_props(video_path)
    if duration <= 0:
        return 1
    print(f"{duration:.6f}")
    return 0


# Field sets this shim can honestly measure (cv2 + moviepy). Anything beyond
# must fail loudly (rc=1) exactly as before, never fabricate values.
_SHIM_FORMAT_FIELDS = {"duration"}
_SHIM_STREAM_FIELDS = {"index", "codec_type", "width", "height", "avg_frame_rate", "r_frame_rate"}


def _requested_entries(argv: list[str]) -> tuple[set[str], set[str]] | None:
    """Parse `-show_entries format=a,b:stream=c,d` into (format_fields, stream_fields)."""
    if "-show_entries" not in argv:
        return None
    spec = argv[argv.index("-show_entries") + 1]
    format_fields: set[str] = set()
    stream_fields: set[str] = set()
    for group in spec.split(":"):
        side, _, fields = group.partition("=")
        target = format_fields if side == "format" else stream_fields
        target.update(f for f in fields.split(",") if f)
    return format_fields, stream_fields


def _print_json_probe(video_path: Path, argv: list[str]) -> int:
    """Honor `-of json`: emit the shape _native_ffmpeg expects.

    `format=duration`-only queries get {"format": {"duration": ...}}; queries
    that also ask for streams (format=duration:stream=...) additionally get a
    streams array with the real cv2-measured video stream and an audio entry
    when the container actually has one. Requests for fields the shim cannot
    measure (codec_name, pix_fmt, size, bit_rate, ...) fail loudly instead of
    returning silently wrong metadata.
    """
    requested = _requested_entries(argv)
    if requested is not None:
        format_fields, stream_fields = requested
        if not format_fields <= _SHIM_FORMAT_FIELDS or not stream_fields <= _SHIM_STREAM_FIELDS:
            print("ffprobe shim: requested fields beyond shim capability", file=sys.stderr)
            return 1
    fps, duration, width, height = _cv2_props(video_path)
    if duration <= 0:
        return 1
    payload: dict = {"format": {"duration": f"{duration:.6f}"}}
    if requested is None or requested[1]:
        streams: list[dict] = [
            {
                "index": 0,
                "codec_type": "video",
                "width": width,
                "height": height,
                "avg_frame_rate": f"{fps:.6f}",
                "r_frame_rate": f"{fps:.6f}",
            }
        ]
        if _has_audio_track(video_path):
            streams.append({"index": 1, "codec_type": "audio"})
        payload["streams"] = streams
    print(json.dumps(payload))
    return 0


def _print_audio_presence(video_path: Path) -> int:
    # The .CMD wrapper runs this file as a plain script, so sys.path[0] is
    # script/ and the repo root is missing — add it for the package import.
    repo_root = str(Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from script.tools._native_ffmpeg import binaries_available, probe_video_optional

    if binaries_available():
        probe = probe_video_optional(video_path)
        if probe is not None:
            if probe.has_audio:
                print("0")
            return 0

    from moviepy.video.io.VideoFileClip import VideoFileClip

    clip = VideoFileClip(str(video_path))
    try:
        if clip.audio is not None:
            print("0")
    finally:
        clip.close()
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print("ffprobe shim: missing arguments", file=sys.stderr)
        return 1

    video_path = Path(argv[-1])
    if not video_path.exists():
        print(f"ffprobe shim: file not found: {video_path}", file=sys.stderr)
        return 1

    if "-select_streams" in argv and "a" in argv and "stream=index" in " ".join(argv):
        return _print_audio_presence(video_path)

    if "-of" in argv and "json" in argv:
        return _print_json_probe(video_path, argv)

    if "format=duration" in " ".join(argv):
        return _print_duration(video_path)

    print("ffprobe shim: unsupported arguments", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
