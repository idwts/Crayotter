"""Install checksum-verified standalone macOS FFmpeg/FFprobe bundles.

Run once on a new Mac: python script/setup_ffmpeg.py
Use --arch all to prepare both Apple Silicon and Intel distribution assets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import stat
import tempfile
from urllib.request import urlopen
import zipfile


SOURCE_PAGE = "https://www.osxexperts.net/"
# Published SHA256 values identify the extracted executable, not the ZIP archive.
BUILDS = {
    "arm64": {
        "ffmpeg": ("https://www.osxexperts.net/ffmpeg9arm.zip", "591260c945d0eef150e3bf82b0ef988bd36a9cecc18ff05d6679617159f0a95e"),
        "ffprobe": ("https://www.osxexperts.net/ffprobe9arm.zip", "e11c17e8200b3ee4c4c186d245e2b4053f01d56957336c1817fca0b997469106"),
    },
    "x86_64": {
        "ffmpeg": ("https://www.osxexperts.net/ffmpeg80intel.zip", "df3f1e3facdc1ae0ad0bd898cdfb072fbc9641bf47b11f172844525a05db8d11"),
        "ffprobe": ("https://www.osxexperts.net/ffprobe80intel.zip", "5228e651e2bd67bb55819b27f6138351587b16d2b87446007bf35b7cf930d891"),
    },
}


def install_binary(name: str, destination: Path, url: str, checksum: str) -> dict[str, str]:
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == checksum:
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"Already verified: {destination}", flush=True)
        return {"url": url, "sha256": checksum}
    if destination.exists():
        raise RuntimeError(f"Refusing to overwrite an unverified existing binary: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="crayotter_ffmpeg_") as temporary:
        archive = Path(temporary) / "download.zip"
        print(f"Downloading {url}", flush=True)
        with urlopen(url, timeout=60) as response, archive.open("wb") as output:
            downloaded = 0
            last_report = 0
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                downloaded += len(chunk)
                if downloaded - last_report >= 10 * 1024 * 1024:
                    print(f"  {name}: {downloaded // (1024 * 1024)} MiB", flush=True)
                    last_report = downloaded
        with zipfile.ZipFile(archive) as package:
            members = [item for item in package.infolist() if not item.is_dir()
                       and Path(item.filename).name == name and not item.filename.startswith("__MACOSX/")]
            if len(members) != 1:
                raise RuntimeError(f"Archive does not contain exactly one {name} executable")
            content = package.read(members[0])
        actual = hashlib.sha256(content).hexdigest()
        if actual != checksum:
            raise RuntimeError(f"Checksum mismatch for {name}: expected {checksum}, received {actual}")
        # Never extract arbitrary archive paths into the source tree.
        staging = destination.with_name(f".{name}.download")
        try:
            staging.write_bytes(content)
            staging.chmod(0o755)
            staging.replace(destination)
        finally:
            staging.unlink(missing_ok=True)
    print(f"Installed and verified: {destination}", flush=True)
    return {"url": url, "sha256": checksum}


def main(argv: list[str] | None = None) -> None:
    machine = platform.machine().lower()
    default_arch = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=["arm64", "x86_64", "all"], default=default_arch)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent / "dep" / "macos")
    args = parser.parse_args(argv)
    for architecture in BUILDS if args.arch == "all" else [args.arch]:
        target = args.directory / architecture
        records = {name: install_binary(name, target / name, url, checksum)
                   for name, (url, checksum) in BUILDS[architecture].items()}
        (target / "manifest.json").write_text(json.dumps({
            "platform": "macos", "architecture": architecture, "source": SOURCE_PAGE,
            "binaries": records,
        }, indent=2) + "\n")


if __name__ == "__main__":
    main()
