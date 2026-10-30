from __future__ import annotations

from pathlib import Path
import hashlib
import shutil
import urllib.request
import zipfile

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "packaging" / "generated"
ICON_PATH = OUTPUT_DIR / "Crayotter.ico"
FFMPEG_URL = "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip"
FFMPEG_SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"


def prepare_media_binaries() -> None:
    archive = OUTPUT_DIR / "ffmpeg-9.0.2-windows.zip"
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != FFMPEG_SHA256:
        partial = archive.with_suffix(".part")
        try:
            with urllib.request.urlopen(FFMPEG_URL, timeout=120) as response, partial.open("wb") as target:
                shutil.copyfileobj(response, target)
            if hashlib.sha256(partial.read_bytes()).hexdigest() != FFMPEG_SHA256:
                raise RuntimeError("Windows FFmpeg archive checksum mismatch")
            partial.replace(archive)
        finally:
            partial.unlink(missing_ok=True)
    destination = OUTPUT_DIR / "windows"
    destination.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            member = next(item for item in bundle.namelist() if item.endswith("/bin/" + name))
            with bundle.open(member) as source, (destination / name).open("wb") as target:
                shutil.copyfileobj(source, target)


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
    prepare_media_binaries()


if __name__ == "__main__":
    main()
