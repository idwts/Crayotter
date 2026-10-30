"""Prepare an ICNS icon and the pinned standalone macOS downloader."""
from __future__ import annotations

import hashlib
from pathlib import Path
import urllib.request

from PIL import Image
from prepare_windows_assets import build_square_icon_image

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "packaging" / "generated"
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/download/2026.03.13/yt-dlp_macos"
YTDLP_SHA256 = "d27dbb7fd7d8d6af195c8fbf4dce8e3d25e5b474bf088589962468d823e2ee9f"


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with Image.open(ROOT / "logo.png") as source:
        build_square_icon_image(source, size=1024).save(OUTPUT / "Crayotter.icns", format="ICNS")
    downloader = OUTPUT / "yt-dlp"
    if not downloader.exists() or hashlib.sha256(downloader.read_bytes()).hexdigest() != YTDLP_SHA256:
        partial = downloader.with_suffix(".part")
        try:
            with urllib.request.urlopen(YTDLP_URL, timeout=120) as response, partial.open("wb") as target:
                while chunk := response.read(1024 * 1024):
                    target.write(chunk)
            if hashlib.sha256(partial.read_bytes()).hexdigest() != YTDLP_SHA256:
                raise RuntimeError("yt-dlp download checksum mismatch")
            partial.replace(downloader)
        finally:
            partial.unlink(missing_ok=True)
    downloader.chmod(0o755)
    print(OUTPUT)


if __name__ == "__main__":
    main()
