from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .build_case_eval_fixtures import (
    VIDEO_SUFFIXES,
    _case_dirs,
    _collect_seed_files,
    _load_prompt_map,
    _target_duration_from_prompt,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value).strip("_")


def _first_video_seed(seeds: list[dict[str, str]]) -> dict[str, str] | None:
    for seed in seeds:
        target = str(seed.get("target") or "")
        source = str(seed.get("source") or "")
        if Path(target).suffix.lower() in VIDEO_SUFFIXES or Path(source).suffix.lower() in VIDEO_SUFFIXES:
            return seed
    return None


def _prompt_for_case(prompt_map: dict[str, str], case_id: str) -> str:
    return prompt_map.get(case_id) or prompt_map.get(str(int(case_id)) if case_id.isdigit() else case_id) or (
        f"请基于评测样本 {case_id} 的已有素材完成视频剪辑。"
    )


def build_payload(
    *,
    case_eval_root: Path,
    case_dir: Path,
    prompt_map: dict[str, str],
    prefix: str,
    system: str,
    raw_cases_root: Path | None,
    default_duration_seconds: float,
) -> dict[str, Any]:
    case_id = case_dir.name
    user_request = _prompt_for_case(prompt_map, case_id)
    seeds = _collect_seed_files(
        case_eval_root,
        case_dir,
        system,
        False,
        raw_cases_root,
    )
    video_seed = _first_video_seed(seeds)
    if video_seed is None:
        raise FileNotFoundError(f"No video seed found for case {case_id}")

    video_target = str(video_seed["target"])
    fixture_id = f"{prefix}_{_safe_id(case_id)}_inspect"
    editing_blueprint = (
        "Tool-call bootstrap task for Crayotter Phase 3 RL. The only objective is to emit one valid "
        "hermes-format tool call that inspects an existing local video. Do not export a final video. "
        "Do not write a long plan before the first tool call. Correct first action:\n"
        f"<tool_call>{{\"name\":\"inspect_video_duration\",\"arguments\":{{\"video_path\":\"{video_target}\"}}}}</tool_call>"
    )
    return {
        "fixture_id": fixture_id,
        "description": f"Tool-call format bootstrap fixture for case {case_id}.",
        "user_request": (
            f"{user_request}\n\n"
            "本轮是工具调用格式冷启动任务：请直接用 inspect_video_duration 检查一个已有本地素材视频。"
        ),
        "target_duration_seconds": _target_duration_from_prompt(user_request, default_duration_seconds),
        "editing_blueprint": editing_blueprint,
        "allowed_tools": ["inspect_video_duration"],
        "runtime_seed": seeds,
        "metadata": {
            "case_eval_root": str(case_eval_root),
            "case_id": case_id,
            "seed_system": system,
            "seed_file_count": len(seeds),
            "tool_call_bootstrap": True,
            "bootstrap_tool_name": "inspect_video_duration",
            "bootstrap_video_target": video_target,
            "bootstrap_video_source": str(video_seed.get("source") or ""),
            "long_horizon_task": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build short-horizon tool-call bootstrap fixtures from Crayotter_case_eval."
    )
    parser.add_argument("--case-eval-root", required=True)
    parser.add_argument("--output-root", default=str(_project_root() / "phase3_rl" / "fixtures"))
    parser.add_argument("--cases", nargs="*", default=[])
    parser.add_argument("--prefix", default="case_eval_tool_bootstrap")
    parser.add_argument("--system", default="ours")
    parser.add_argument("--raw-cases-root", default="")
    parser.add_argument("--default-duration-seconds", type=float, default=60.0)
    parser.add_argument("--print-fixtures", action="store_true")
    args = parser.parse_args()

    case_eval_root = Path(args.case_eval_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    raw_cases_root = Path(args.raw_cases_root).expanduser().resolve() if args.raw_cases_root else None
    prompt_map = _load_prompt_map(case_eval_root)
    fixture_ids: list[str] = []
    written: list[dict[str, Any]] = []

    for case_dir in _case_dirs(case_eval_root, args.cases):
        payload = build_payload(
            case_eval_root=case_eval_root,
            case_dir=case_dir,
            prompt_map=prompt_map,
            prefix=args.prefix,
            system=args.system,
            raw_cases_root=raw_cases_root,
            default_duration_seconds=args.default_duration_seconds,
        )
        fixture_ids.append(payload["fixture_id"])
        fixture_dir = output_root / payload["fixture_id"]
        fixture_dir.mkdir(parents=True, exist_ok=True)
        fixture_path = fixture_dir / "fixture.json"
        fixture_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(
            {
                "fixture_id": payload["fixture_id"],
                "case_id": payload["metadata"]["case_id"],
                "bootstrap_video_target": payload["metadata"]["bootstrap_video_target"],
                "seed_file_count": payload["metadata"]["seed_file_count"],
                "path": str(fixture_path),
            }
        )

    if args.print_fixtures:
        print(" ".join(fixture_ids))
    else:
        print(json.dumps({"fixture_count": len(written), "fixtures": written}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
