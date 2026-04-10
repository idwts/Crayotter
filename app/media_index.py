from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path


VIDEO_SUFFIXES = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".m4v",
    ".mpeg",
    ".mpg",
}


def is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES


def is_analysis_json(path: Path) -> bool:
    return path.is_file() and path.name.lower().endswith("_analysis.json")


def analysis_stem(path: Path) -> str:
    stem = path.stem
    return stem[: -len("_analysis")] if stem.endswith("_analysis") else stem


def iter_video_files(roots: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()

    for root in roots:
        if not root.exists():
            continue
        for candidate in root.rglob("*"):
            if not is_video_file(candidate):
                continue
            key = str(candidate.resolve(strict=False))
            if key in seen:
                continue
            seen.add(key)
            files.append(candidate)

    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files


def iter_analysis_files(roots: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()

    for root in roots:
        if not root.exists():
            continue
        for candidate in root.rglob("*_analysis.json"):
            if not is_analysis_json(candidate):
                continue
            key = str(candidate.resolve(strict=False))
            if key in seen:
                continue
            seen.add(key)
            files.append(candidate)

    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files


def build_analysis_index(roots: Iterable[Path]) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for path in iter_analysis_files(roots):
        index.setdefault(analysis_stem(path), []).append(path)
    return index


def match_analysis_files(
    video_path: Path,
    *,
    analysis_index: Mapping[str, list[Path]] | None = None,
    roots: Iterable[Path] | None = None,
) -> list[Path]:
    index = analysis_index if analysis_index is not None else build_analysis_index(roots or [video_path.parent])
    return list(index.get(video_path.stem, []))
