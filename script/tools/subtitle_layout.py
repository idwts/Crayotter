"""Shared subtitle text measurement and safe-area placement."""

from __future__ import annotations

from typing import Any


def wrap_subtitle_text(
    text: str,
    font_path: str,
    font_size: int,
    max_width_px: int,
) -> str:
    """Wrap text using rendered pixel width."""
    from PIL import ImageFont

    font = ImageFont.truetype(font_path, size=max(1, int(font_size)))
    lines: list[str] = []
    current = ""
    for char in text:
        if char == "\n":
            if current.strip():
                lines.append(current.strip())
            current = ""
            continue
        candidate = current + char
        bbox = font.getbbox(candidate)
        if current and bbox[2] - bbox[0] > max_width_px:
            if char in "，。！？；：、,.!?;:" and len(current) > 1:
                lines.append(current[:-1].strip())
                current = current[-1] + char
            else:
                lines.append(current.strip())
                current = char
        else:
            current = candidate
    if current.strip():
        lines.append(current.strip())
    return "\n".join(lines) if lines else text


def create_subtitle_clip(
    *,
    text: str,
    font_path: str,
    video_size: tuple[int, int],
    duration: float,
) -> tuple[Any, int, int, int, int]:
    """Create a fitted TextClip and return it with its safe y position."""
    from moviepy.video.VideoClip import TextClip

    video_width, video_height = map(int, video_size)
    box_width = max(320, video_width - 120)
    max_height = max(120, int(video_height * 0.35))
    bottom_margin = max(40, int(video_height * 0.06))
    baseline_lift = max(8, int(video_height * 0.01))

    clip = None
    selected_font_size = 0
    for font_size in range(44, 11, -2):
        display_text = wrap_subtitle_text(text, font_path, font_size, box_width)
        candidate = TextClip(
            text=f"{display_text}\n ",
            font_size=font_size,
            color="white",
            stroke_color="black",
            stroke_width=2,
            font=font_path,
            text_align="center",
            size=(box_width, None),
            duration=duration,
        )
        if candidate.h <= max_height:
            clip = candidate
            selected_font_size = font_size
            break
        candidate.close()

    if clip is None:
        raise ValueError("subtitle text is too long to fit inside the video safe area")

    y_position = max(
        20,
        video_height - bottom_margin - int(clip.h) - baseline_lift,
    )
    return clip, y_position, selected_font_size, bottom_margin, baseline_lift


__all__ = ["create_subtitle_clip", "wrap_subtitle_text"]
