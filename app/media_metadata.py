from __future__ import annotations

from pathlib import Path


def video_duration_seconds(video_path: Path) -> float | None:
    import cv2

    capture = cv2.VideoCapture(str(video_path))
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    finally:
        capture.release()

    if fps <= 0 or frame_count <= 0:
        return None
    duration = frame_count / fps
    return duration if duration > 0 else None


def video_has_audio(video_path: Path) -> bool:
    from moviepy.video.io.VideoFileClip import VideoFileClip

    clip = VideoFileClip(str(video_path))
    try:
        return clip.audio is not None
    finally:
        clip.close()
