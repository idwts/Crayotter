from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


APP_NAME = "Crayotter"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_bundle_root() -> Path:
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def get_executable_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return get_bundle_root()


def _can_write(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".crayotter_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def get_runtime_root() -> Path:
    env_root = os.environ.get("CRAYOTTER_RUNTIME_ROOT", "").strip()
    if env_root:
        root = Path(env_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    portable_root = get_executable_dir()
    if _can_write(portable_root):
        return portable_root

    local_appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if local_appdata:
        appdata_root = (Path(local_appdata) / APP_NAME).resolve()
        if _can_write(appdata_root):
            return appdata_root

    fallback_root = (Path.home() / f".{APP_NAME.lower()}").resolve()
    fallback_root.mkdir(parents=True, exist_ok=True)
    return fallback_root


def resource_path(*parts: str) -> Path:
    return get_bundle_root().joinpath(*parts)


def runtime_path(*parts: str) -> Path:
    return get_runtime_root().joinpath(*parts)


def ensure_runtime_dirs() -> dict[str, Path]:
    directories = {
        "app_state": runtime_path("app_state"),
        "jobs": runtime_path("app_state", "jobs"),
        "logs": runtime_path("logs"),
        "runtime_logs": runtime_path("runtime_logs"),
        "temp": runtime_path("temp"),
        "user_temp": runtime_path("user_temp"),
        "memory_experience": runtime_path("memory_experience"),
    }
    for path in directories.values():
        path.mkdir(parents=True, exist_ok=True)
    return directories


def _seed_runtime_tree(relative_dir: str) -> None:
    source_dir = resource_path(relative_dir)
    target_dir = runtime_path(relative_dir)
    if not source_dir.exists() or not source_dir.is_dir():
        return

    for source in source_dir.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(source_dir)
        target = target_dir / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


def _prepend_path(entries: list[Path]) -> None:
    current = os.environ.get("PATH", "")
    existing = [item for item in current.split(os.pathsep) if item]
    normalized_existing = {str(Path(item).resolve()) for item in existing if Path(item).exists()}

    ordered: list[str] = []
    for entry in entries:
        if not entry.exists():
            continue
        resolved = str(entry.resolve())
        if resolved in normalized_existing or resolved in ordered:
            continue
        ordered.append(resolved)

    if ordered:
        os.environ["PATH"] = os.pathsep.join([*ordered, current] if current else ordered)


def _binary_search_roots() -> list[Path]:
    runtime_root = get_runtime_root()
    bundle_root = get_bundle_root()

    def _script_binary_dirs(root: Path) -> list[Path]:
        return [
            root / "script" / "dep" / "windows",
            root / "script" / "lib" / "windows",
            root / "script" / "dep",
            root / "script" / "lib",
        ]

    candidates = [
        *_script_binary_dirs(runtime_root),
        *_script_binary_dirs(bundle_root),
        runtime_root / "app" / "bin" / "windows",
        bundle_root / "app" / "bin" / "windows",
        runtime_root / "app" / "bin",
        bundle_root / "app" / "bin",
        runtime_root,
        bundle_root,
    ]
    return candidates


def resolve_binary(*names: str) -> Path | None:
    candidates: list[Path] = []
    for root in _binary_search_roots():
        for name in names:
            if not name:
                continue
            candidates.append(root / name)

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    for name in names:
        if not name:
            continue
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    return None


def configure_runtime_environment() -> None:
    ensure_runtime_dirs()
    bundle_root = get_bundle_root()
    runtime_root = get_runtime_root()

    os.environ.setdefault("CRAYOTTER_BUNDLE_ROOT", str(bundle_root))
    os.environ.setdefault("CRAYOTTER_RUNTIME_ROOT", str(runtime_root))

    _prepend_path(_binary_search_roots())

    ffmpeg_names = ("ffmpeg.exe", "ffmpeg") if os.name == "nt" else ("ffmpeg",)
    ffprobe_names = ("ffprobe.cmd", "ffprobe.exe", "ffprobe") if os.name == "nt" else ("ffprobe",)
    yt_dlp_names = ("yt-dlp.exe", "yt-dlp.cmd", "yt-dlp") if os.name == "nt" else ("yt-dlp",)

    ffmpeg_path = resolve_binary(*ffmpeg_names)
    ffprobe_path = resolve_binary(*ffprobe_names)
    yt_dlp_path = resolve_binary(*yt_dlp_names)

    if ffmpeg_path is not None:
        os.environ.setdefault("FFMPEG_BIN", str(ffmpeg_path))
    if ffprobe_path is not None:
        os.environ.setdefault("FFPROBE_BIN", str(ffprobe_path))
    if yt_dlp_path is not None:
        os.environ.setdefault("CRAYOTTER_YTDLP_BIN", str(yt_dlp_path))

    _seed_runtime_tree("memory_experience")
