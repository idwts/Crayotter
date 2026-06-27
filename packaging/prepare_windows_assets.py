from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "packaging" / "generated"
ICON_PATH = OUTPUT_DIR / "Crayotter.ico"


def build_square_icon_image(image: Image.Image, *, size: int = 256, padding_ratio: float = 0.08) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    source = image.convert("RGBA")
    padding = max(0, min(size // 3, round(size * padding_ratio)))
    max_side = max(1, size - padding * 2)
    source.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    x = (size - source.width) // 2
    y = (size - source.height) // 2
    canvas.alpha_composite(source, (x, y))
    return canvas


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with Image.open(ROOT / "logo.png") as image:
        icon = build_square_icon_image(image)
        icon.save(
            ICON_PATH,
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
    print(ICON_PATH)


if __name__ == "__main__":
    main()
