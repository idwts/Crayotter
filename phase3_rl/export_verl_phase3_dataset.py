from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .fixture import list_fixtures, load_fixture, materialize_fixture
from .prompt_builder import build_phase3_messages


DEFAULT_AGENT_NAME = "crayotter_phase3_tool_agent"


def _build_record(fixture_id: str, index: int, episode_base_dir: str) -> dict:
    fixture = load_fixture(fixture_id)
    with tempfile.TemporaryDirectory(prefix=f"crayotter_{fixture.fixture_id}_") as temp_dir:
        runtime_root = materialize_fixture(fixture, temp_dir)
        prompt = build_phase3_messages(
            user_request=fixture.user_request,
            target_duration_seconds=fixture.target_duration_seconds,
            editing_blueprint=fixture.editing_blueprint,
            runtime_root=runtime_root,
            tool_names=fixture.allowed_tools,
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
        "data_source": "crayotter_phase3",
        "agent_name": DEFAULT_AGENT_NAME,
        "prompt": prompt,
        "ability": "video_editing",
        "reward_model": {"style": "rule", "ground_truth": fixture.fixture_id},
        "target_duration_seconds": fixture.target_duration_seconds,
        "extra_info": {
            "index": index,
            "fixture_id": fixture.fixture_id,
            "need_tools_kwargs": True,
            "tools_kwargs": tools_kwargs,
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
    args = parser.parse_args()

    fixture_ids = args.fixtures or list_fixtures()
    records = [_build_record(fixture_id, idx, args.episode_base_dir) for idx, fixture_id in enumerate(fixture_ids)]

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
