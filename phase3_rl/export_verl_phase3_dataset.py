from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .exploration import profile_for_repeat
from .fixture import Phase3Fixture, list_fixtures, load_fixture
from .prompt_builder import build_phase3_messages


DEFAULT_AGENT_NAME = "crayotter_phase3_tool_agent"
PROMPT_COPY_SUFFIXES = {".json", ".md", ".txt", ".yaml", ".yml"}

def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _materialize_prompt_snapshot(fixture: Phase3Fixture, root: Path) -> Path:
    for relative_dir in ("temp", "user_temp", "memory_experience"):
        (root / relative_dir).mkdir(parents=True, exist_ok=True)

    for seed in fixture.runtime_seed:
        target = root / seed.target
        target.parent.mkdir(parents=True, exist_ok=True)
        source = _project_root() / seed.source if seed.source else None
        if (
            source is not None
            and source.is_file()
            and source.suffix.lower() in PROMPT_COPY_SUFFIXES
        ):
            shutil.copy2(source, target)
        else:
            target.touch()

    source_memory = _project_root() / "memory_experience" / "latest_skills.md"
    if source_memory.is_file():
        shutil.copy2(
            source_memory,
            root / "memory_experience" / "latest_skills.md",
        )
    return root


def _branch_profile_for_repeat(repeat_index: int) -> dict[str, Any]:
    return profile_for_repeat(repeat_index)


def _branching_enabled() -> bool:
    if os.environ.get("CRAYOTTER_RL_PER_ROLLOUT_STRATEGY", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False
    return os.environ.get("CRAYOTTER_RL_BRANCH_STRATEGY_PROMPTS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _fixture_allows_branch_profile(task_metadata: dict[str, Any]) -> bool:
    return bool(
        task_metadata.get("long_horizon_task")
        or task_metadata.get("enable_diverse_rollout")
        or task_metadata.get("horizon_suite")
    )


def _safe_data_source_part(value: Any, fallback: str) -> str:
    raw = str(value or fallback).strip().lower()
    safe = "".join(ch if ch.isalnum() else "_" for ch in raw)
    safe = "_".join(part for part in safe.split("_") if part)
    return safe or fallback


def _data_source_for_metadata(task_metadata: dict[str, Any]) -> str:
    metrics = task_metadata.get("horizon_metrics") or {}
    if not isinstance(metrics, dict):
        metrics = {}
    task_type = (
        task_metadata.get("task_type")
        or metrics.get("task_type")
        or ("long_horizon_revision" if task_metadata.get("long_horizon_task") else "phase3")
    )
    split = task_metadata.get("horizon_suite_split") or task_metadata.get("benchmark_split")
    family = task_metadata.get("task_family") or task_metadata.get("task_complexity")
    parts = ["crayotter", "phase3"]
    if split:
        parts.append(_safe_data_source_part(split, "split"))
    if family:
        parts.append(_safe_data_source_part(family, "family"))
    parts.append(_safe_data_source_part(task_type, "task"))
    return "_".join(parts)


def _build_record(
    fixture_id: str,
    index: int,
    episode_base_dir: str,
    *,
    branch_profile: dict[str, Any] | None = None,
) -> dict:
    fixture = load_fixture(fixture_id)
    task_metadata = dict(fixture.metadata)
    if branch_profile and _fixture_allows_branch_profile(task_metadata):
        task_metadata["branch_strategy"] = branch_profile["id"]
        task_metadata["branch_strategy_name"] = branch_profile["name"]
        task_metadata["branch_strategy_instruction"] = branch_profile["instruction"]
        task_metadata["branch_preferred_stages"] = list(branch_profile["preferred_stages"])
    with tempfile.TemporaryDirectory(prefix=f"crayotter_{fixture.fixture_id}_") as temp_dir:
        runtime_root = _materialize_prompt_snapshot(fixture, Path(temp_dir))
        prompt = build_phase3_messages(
            user_request=fixture.user_request,
            target_duration_seconds=fixture.target_duration_seconds,
            editing_blueprint=fixture.editing_blueprint,
            runtime_root=runtime_root,
            tool_names=fixture.allowed_tools,
            task_metadata=task_metadata,
            display_runtime_root=".",
        )

    tools_kwargs = {
        tool_name: {
            "create_kwargs": {
                "fixture_path": str(fixture.source_path),
                "episode_base_dir": episode_base_dir,
            }
        }
        for tool_name in fixture.allowed_tools
    }
    return {
        "data_source": _data_source_for_metadata(task_metadata),
        "agent_name": DEFAULT_AGENT_NAME,
        "prompt": prompt,
        "ability": "video_editing",
        "reward_model": {"style": "rule", "ground_truth": fixture.fixture_id},
        "target_duration_seconds": fixture.target_duration_seconds,
        "extra_info": {
            "index": index,
            "fixture_id": fixture.fixture_id,
            "fixture_path": str(fixture.source_path),
            "user_request": fixture.user_request,
            "target_duration_seconds": fixture.target_duration_seconds,
            "task_metadata": task_metadata,
            "branch_profile": branch_profile if task_metadata.get("long_horizon_task") else None,
            "need_tools_kwargs": True,
            "tools_kwargs": tools_kwargs,
            "tool_selection": list(fixture.allowed_tools),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Crayotter Phase 3 fixtures as a verl-friendly dataset.")
    parser.add_argument(
        "--fixtures",
        nargs="*",
        default=[],
        help="Fixture ids. Defaults to all fixtures under phase3_rl/fixtures.",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parent / "generated" / "phase3_fixtures.jsonl"),
    )
    parser.add_argument(
        "--episode-base-dir",
        default=str(Path(__file__).resolve().parent / "runs" / "verl"),
        help="Base dir passed to tool create_kwargs in verl rollouts.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeat each fixture this many times in the exported dataset.",
    )
    args = parser.parse_args()

    fixture_ids = args.fixtures or list_fixtures()
    if not fixture_ids:
        parser.error("No Phase 3 fixtures were found.")
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    records: list[dict] = []
    for fixture_id in fixture_ids:
        for repeat_index in range(args.repeat):
            branch_profile = _branch_profile_for_repeat(repeat_index) if _branching_enabled() else None
            record = _build_record(
                fixture_id,
                len(records),
                args.episode_base_dir,
                branch_profile=branch_profile,
            )
            records.append(record)

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(
        json.dumps(
            {
                "output": str(output_path),
                "record_count": len(records),
                "agent_name": DEFAULT_AGENT_NAME,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
