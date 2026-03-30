from __future__ import annotations

import sys
from pathlib import Path


def _print_duration(video_path: Path) -> int:
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        duration = frame_count / fps if fps > 0 else 0.0
    finally:
        cap.release()

    if duration <= 0:
        return 1
    print(f"{duration:.6f}")
    return 0


def _print_audio_presence(video_path: Path) -> int:
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

    if "format=duration" in " ".join(argv):
        return _print_duration(video_path)

    print("ffprobe shim: unsupported arguments", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
