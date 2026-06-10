from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "packaging" / "generated"
ICON_PATH = OUTPUT_DIR / "Crayotter.ico"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with Image.open(ROOT / "logo.png") as image:
        image.convert("RGBA").save(
            ICON_PATH,
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
    print(ICON_PATH)


if __name__ == "__main__":
    main()
