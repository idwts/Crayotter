"""Phase 3 RL rollout scaffolding for Crayotter."""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_import_paths() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_root = repo_root / "script"
    for candidate in (repo_root, script_root):
        text = str(candidate)
        if text not in sys.path:
            sys.path.insert(0, text)


_bootstrap_import_paths()

from .fixture import Phase3Fixture, load_fixture

__all__ = [
    "Phase3Fixture",
    "load_fixture",
]
