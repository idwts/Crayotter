"""Render subtitle text once with Pillow; FFmpeg composites the static image."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def wrap_subtitle_text(text: str, font_path: str, font_size: int, max_width_px: int) -> str:
    """Wrap text using rendered pixel width, keeping punctuation with text."""
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


def render_subtitle_image(
    *, text: str, font_path: str, video_size: tuple[int, int],
) -> tuple[Any, int, int, int, int]:
    """Return an RGBA image, y coordinate, font size, margin and baseline lift."""
    from PIL import Image, ImageDraw, ImageFont

    width, height = map(int, video_size)
    box_width = max(1, width - min(120, int(width * 0.10)))
    max_height = max(1, int(height * 0.35))
    bottom_margin = min(max(8, int(height * 0.06)), max(0, height // 4))
    baseline_lift = min(max(2, int(height * 0.01)), max(0, height // 8))
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    for font_size in range(min(44, max(12, int(height * 0.10))), 5, -2):
        font = ImageFont.truetype(font_path, size=font_size)
        display = wrap_subtitle_text(text, font_path, font_size, max(1, box_width - 8))
        bbox = measure.multiline_textbbox((0, 0), display, font=font, spacing=6, stroke_width=2, align="center")
        text_height = bbox[3] - bbox[1] + 8
        if text_height <= max_height:
            image = Image.new("RGBA", (box_width, text_height), (0, 0, 0, 0))
            ImageDraw.Draw(image).multiline_text(
                ((box_width - (bbox[2] - bbox[0])) / 2 - bbox[0], 4 - bbox[1]),
                display, font=font, fill="white", stroke_fill="black", stroke_width=2,
                spacing=6, align="center",
            )
            y = max(0, height - bottom_margin - text_height - baseline_lift)
            return image, y, font_size, bottom_margin, baseline_lift
    raise ValueError("subtitle text is too long to fit inside the video safe area")


def resolve_subtitle_font(custom_font: str | None = None) -> str | None:
    from ._shared import BUNDLE_DIR, WORKSPACE

    candidates = []
    if custom_font:
        custom = Path(custom_font)
        candidates.extend([custom] if custom.is_absolute() else [BUNDLE_DIR / custom, WORKSPACE / custom])
    candidates.extend([
        BUNDLE_DIR / "AlibabaPuHuiTi-3-55-Regular/AlibabaPuHuiTi-3-55-Regular.ttf",
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf"),
    ])
    return next((str(path) for path in candidates if path.is_file()), None)


def burn_subtitles(
    video_path: Path, output_path: Path, subtitles: list[dict], font_path: str,
) -> int:
    import math
    import tempfile
    from ._native_ffmpeg import overlay_subtitles_native, probe_video

    probe = probe_video(video_path)
    with tempfile.TemporaryDirectory(prefix=".subtitles_", dir=output_path.parent) as directory:
        overlays = []
        for index, sub in enumerate(subtitles):
            text = str(sub.get("text") or "").strip()
            start, end = float(sub.get("start", 0)), float(sub.get("end", 0))
            if not all(math.isfinite(v) for v in (start, end)):
                raise ValueError("subtitle times must be finite")
            start, end = max(0.0, start), min(probe.duration, end)
            if not text or end <= start:
                continue
            image, y, _, _, _ = render_subtitle_image(
                text=text, font_path=font_path, video_size=(probe.width, probe.height),
            )
            path = Path(directory) / f"{index:04d}.png"
            try:
                image.save(path)
            finally:
                image.close()
            overlays.append((path, start, end, y))
        overlay_subtitles_native(video_path, output_path, overlays)
        return len(overlays)
