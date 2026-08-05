from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .build_case_eval_fixtures import (
    _case_dirs,
    _load_prompt_map,
    build_fixture_payload,
)
from .build_long_horizon_fixtures import (
    _build_revision_payload,
    _find_previous_outputs,
)
from .horizon_metrics import annotate_fixture_payload, write_manifest


DEFAULT_NORMAL_CASES = "001 002 003 004 005 006 007 008 009 010 011 012"
DEFAULT_MEDIUM_CASES = "001 002 003 004 005 006 007 008 009 010 011 012"
DEFAULT_LONG_CASES = "001 002 003 004 005 006 007 008 009 010 011 012 013 014 015 016 017 018 019 020 021 022 023"
DEFAULT_EVAL_CASES = "001 006 011 016 021"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value).strip("_")


def _case_list(raw: list[str] | str) -> list[str]:
    if isinstance(raw, str):
        items = raw.split()
    else:
        items = []
        for value in raw:
            items.extend(str(value).split())
    return [item.zfill(3) if item.isdigit() else item for item in items if item.strip()]


def _write_fixture(output_root: Path, payload: dict[str, Any]) -> Path:
    fixture_dir = output_root / payload["fixture_id"]
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / "fixture.json"
    fixture_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return fixture_path


def _annotate_suite_payload(
    payload: dict[str, Any],
    *,
    suite: str,
    split: str,
    case_id: str,
    family: str,
) -> dict[str, Any]:
    metadata = dict(payload.get("metadata") or {})
    metadata.update(
        {
            "horizon_suite": suite,
            "horizon_suite_split": split,
            "benchmark_split": split,
            "task_family": family,
            "case_id": case_id,
            "enable_diverse_rollout": True,
            "credit_assignment_target": (
                "Use deterministic execution milestones as direct process reward. "
                "Use group-relative final product preference only to calibrate stage-level credit."
            ),
        }
    )
    payload["metadata"] = metadata
    return annotate_fixture_payload(payload)


def _record_for_payload(
    payload: dict[str, Any],
    *,
    fixture_path: Path,
    suite: str,
    split: str,
    family: str,
) -> dict[str, Any]:
    metadata = payload.get("metadata") or {}
    metrics = metadata.get("horizon_metrics") or {}
    return {
        "suite": suite,
        "split": split,
        "family": family,
        "fixture_id": payload["fixture_id"],
        "case_id": metadata.get("case_id", ""),
        "task_type": metadata.get("task_type") or metrics.get("task_type", ""),
        "task_horizon_score": metadata.get("task_horizon_score") or metrics.get("task_horizon_score", 0.0),
        "horizon_metrics": metrics,
        "path": str(fixture_path),
    }


def _normal_payload(
    *,
    base_payload: dict[str, Any],
    case_id: str,
    prefix: str,
) -> dict[str, Any]:
    payload = dict(base_payload)
    payload["fixture_id"] = f"{prefix}_normal_{_safe_id(case_id)}"
    payload["description"] = f"Normal single-pass editing fixture for case {case_id}."
    payload["editing_blueprint"] = (
        "Single-pass local Phase 3 editing task. Use provided local materials directly. "
        "A good trajectory inspects one or two source clips, cuts useful segments, builds or merges "
        "a simple timeline, exports a final video, and checks the exported artifact. "
        "This is the ordinary-task baseline for long-horizon comparison."
    )
    metadata = dict(payload.get("metadata") or {})
    metadata.update(
        {
            "long_horizon_task": False,
            "normal_baseline_task": True,
            "task_complexity": "normal",
        }
    )
    payload["metadata"] = metadata
    return payload


def _medium_payload(
    *,
    base_payload: dict[str, Any],
    case_id: str,
    prefix: str,
) -> dict[str, Any]:
    payload = dict(base_payload)
    payload["fixture_id"] = f"{prefix}_medium_{_safe_id(case_id)}"
    payload["description"] = f"Medium-horizon multi-constraint editing fixture for case {case_id}."
    payload["user_request"] = (
        f"{base_payload['user_request']}\n\n"
        "请额外满足：开头更快进入主题，镜头顺序要有基本叙事推进，避免重复弱相关素材，"
        "导出后必须复检成片时长和文件可播放性。"
    )
    payload["editing_blueprint"] = (
        "Medium-horizon multi-constraint editing task. The agent should balance material coverage, "
        "timeline ordering, pacing, and final export validation. The task is harder than a normal "
        "single-pass edit but does not include a previous final video or explicit user feedback loop."
    )
    metadata = dict(payload.get("metadata") or {})
    metadata.update(
        {
            "long_horizon_task": False,
            "multi_constraint_task": True,
            "semantic_material_grounding_required": True,
            "task_complexity": "medium",
            "preserve_requirements": ["保留主题最相关素材"],
            "change_requirements": ["压缩重复素材", "优化开头节奏", "复检导出成片"],
        }
    )
    payload["metadata"] = metadata
    return payload


def _write_fixture_list(path: Path, fixture_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(" ".join(fixture_ids) + "\n", encoding="utf-8")


def _build_base_payloads(
    *,
    case_eval_root: Path,
    output_root: Path,
    cases: list[str],
    prompt_map: dict[str, str],
    prefix: str,
    system: str,
    raw_cases_root: Path | None,
    default_duration_seconds: float,
    variant: str,
    split_cases: set[str],
    records: list[dict[str, Any]],
    fixture_ids_by_key: dict[str, list[str]],
) -> None:
    for case_dir in _case_dirs(case_eval_root, cases):
        if not case_dir.is_dir():
            raise FileNotFoundError(f"Missing case directory: {case_dir}")
        case_id = case_dir.name
        base_payload = build_fixture_payload(
            case_eval_root=case_eval_root,
            case_dir=case_dir,
            prompt_map=prompt_map,
            prefix=f"{prefix}_{variant}",
            system=system,
            default_duration_seconds=default_duration_seconds,
            include_result_videos=False,
            raw_cases_root=raw_cases_root,
        )
        if variant == "normal":
            payload = _normal_payload(base_payload=base_payload, case_id=case_id, prefix=prefix)
        elif variant == "medium":
            payload = _medium_payload(base_payload=base_payload, case_id=case_id, prefix=prefix)
        else:
            raise ValueError(f"Unknown variant: {variant}")

        split = "eval" if case_id in split_cases else "train"
        payload = _annotate_suite_payload(
            payload,
            suite=prefix,
            split=split,
            case_id=case_id,
            family=variant,
        )
        fixture_path = _write_fixture(output_root, payload)
        fixture_id = payload["fixture_id"]
        fixture_ids_by_key.setdefault(variant, []).append(fixture_id)
        fixture_ids_by_key.setdefault(split, []).append(fixture_id)
        fixture_ids_by_key.setdefault(f"{split}_{variant}", []).append(fixture_id)
        records.append(
            _record_for_payload(
                payload,
                fixture_path=fixture_path,
                suite=prefix,
                split=split,
                family=variant,
            )
        )


def _build_long_payloads(
    *,
    case_eval_root: Path,
    output_root: Path,
    cases: list[str],
    prompt_map: dict[str, str],
    prefix: str,
    system: str,
    raw_cases_root: Path | None,
    default_duration_seconds: float,
    revision_rounds: int,
    previous_output_limit: int,
    split_cases: set[str],
    records: list[dict[str, Any]],
    fixture_ids_by_key: dict[str, list[str]],
) -> None:
    for case_dir in _case_dirs(case_eval_root, cases):
        if not case_dir.is_dir():
            raise FileNotFoundError(f"Missing case directory: {case_dir}")
        case_id = case_dir.name
        base_payload = build_fixture_payload(
            case_eval_root=case_eval_root,
            case_dir=case_dir,
            prompt_map=prompt_map,
            prefix=f"{prefix}_long_base",
            system=system,
            default_duration_seconds=default_duration_seconds,
            include_result_videos=False,
            raw_cases_root=raw_cases_root,
        )
        previous_outputs = _find_previous_outputs(case_dir, system, previous_output_limit)
        for revision_round in range(1, revision_rounds + 1):
            payload = _build_revision_payload(
                base_payload=base_payload,
                case_id=case_id,
                prefix=f"{prefix}_long",
                revision_round=revision_round,
                previous_outputs=previous_outputs,
            )
            split = "eval" if case_id in split_cases else "train"
            payload = _annotate_suite_payload(
                payload,
                suite=prefix,
                split=split,
                case_id=case_id,
                family="long",
            )
            fixture_path = _write_fixture(output_root, payload)
            fixture_id = payload["fixture_id"]
            fixture_ids_by_key.setdefault("long", []).append(fixture_id)
            fixture_ids_by_key.setdefault(split, []).append(fixture_id)
            fixture_ids_by_key.setdefault(f"{split}_long", []).append(fixture_id)
            records.append(
                _record_for_payload(
                    payload,
                    fixture_path=fixture_path,
                    suite=prefix,
                    split=split,
                    family="long",
                )
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a normal/medium/long-horizon Crayotter Phase 3 RL benchmark suite. "
            "The long split is feedback-revision oriented and carries horizon metrics for evaluation."
        )
    )
    parser.add_argument("--case-eval-root", required=True)
    parser.add_argument("--output-root", default=str(_project_root() / "phase3_rl" / "fixtures"))
    parser.add_argument("--prefix", default="horizon_suite")
    parser.add_argument("--system", default="ours")
    parser.add_argument("--raw-cases-root", default="")
    parser.add_argument("--default-duration-seconds", type=float, default=60.0)
    parser.add_argument("--normal-cases", nargs="*", default=_case_list(DEFAULT_NORMAL_CASES))
    parser.add_argument("--medium-cases", nargs="*", default=_case_list(DEFAULT_MEDIUM_CASES))
    parser.add_argument("--long-cases", nargs="*", default=_case_list(DEFAULT_LONG_CASES))
    parser.add_argument("--eval-cases", nargs="*", default=_case_list(DEFAULT_EVAL_CASES))
    parser.add_argument("--revision-rounds", type=int, default=2)
    parser.add_argument("--previous-output-limit", type=int, default=3)
    parser.add_argument("--manifest", default=str(_project_root() / "phase3_rl" / "generated" / "horizon_suite_manifest.json"))
    parser.add_argument("--lists-dir", default=str(_project_root() / "phase3_rl" / "generated" / "horizon_suite_lists"))
    parser.add_argument("--print-fixtures", action="store_true")
    args = parser.parse_args()

    if args.revision_rounds < 1:
        parser.error("--revision-rounds must be at least 1")

    case_eval_root = Path(args.case_eval_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    raw_cases_root = Path(args.raw_cases_root).expanduser().resolve() if args.raw_cases_root else None
    manifest_path = Path(args.manifest).expanduser().resolve()
    lists_dir = Path(args.lists_dir).expanduser().resolve()
    prompt_map = _load_prompt_map(case_eval_root)
    split_cases = set(_case_list(args.eval_cases))

    records: list[dict[str, Any]] = []
    fixture_ids_by_key: dict[str, list[str]] = {}

    _build_base_payloads(
        case_eval_root=case_eval_root,
        output_root=output_root,
        cases=_case_list(args.normal_cases),
        prompt_map=prompt_map,
        prefix=args.prefix,
        system=args.system,
        raw_cases_root=raw_cases_root,
        default_duration_seconds=args.default_duration_seconds,
        variant="normal",
        split_cases=split_cases,
        records=records,
        fixture_ids_by_key=fixture_ids_by_key,
    )
    _build_base_payloads(
        case_eval_root=case_eval_root,
        output_root=output_root,
        cases=_case_list(args.medium_cases),
        prompt_map=prompt_map,
        prefix=args.prefix,
        system=args.system,
        raw_cases_root=raw_cases_root,
        default_duration_seconds=args.default_duration_seconds,
        variant="medium",
        split_cases=split_cases,
        records=records,
        fixture_ids_by_key=fixture_ids_by_key,
    )
    _build_long_payloads(
        case_eval_root=case_eval_root,
        output_root=output_root,
        cases=_case_list(args.long_cases),
        prompt_map=prompt_map,
        prefix=args.prefix,
        system=args.system,
        raw_cases_root=raw_cases_root,
        default_duration_seconds=args.default_duration_seconds,
        revision_rounds=args.revision_rounds,
        previous_output_limit=args.previous_output_limit,
        split_cases=split_cases,
        records=records,
        fixture_ids_by_key=fixture_ids_by_key,
    )

    train_ids = fixture_ids_by_key.get("train_normal", []) + fixture_ids_by_key.get("train_medium", []) + fixture_ids_by_key.get("train_long", [])
    eval_ids = fixture_ids_by_key.get("eval_normal", []) + fixture_ids_by_key.get("eval_medium", []) + fixture_ids_by_key.get("eval_long", [])
    if not eval_ids:
        eval_ids = fixture_ids_by_key.get("normal", [])[:2] + fixture_ids_by_key.get("medium", [])[:2] + fixture_ids_by_key.get("long", [])[:4]
    fixture_ids_by_key["train_all"] = train_ids
    fixture_ids_by_key["eval_all"] = eval_ids
    fixture_ids_by_key["all"] = [record["fixture_id"] for record in records]

    lists_dir.mkdir(parents=True, exist_ok=True)
    for key, fixture_ids in sorted(fixture_ids_by_key.items()):
        _write_fixture_list(lists_dir / f"{key}.txt", fixture_ids)

    write_manifest(manifest_path, records)
    summary = {
        "manifest": str(manifest_path),
        "lists_dir": str(lists_dir),
        "record_count": len(records),
        "fixture_counts": {key: len(value) for key, value in sorted(fixture_ids_by_key.items())},
        "train_fixtures_file": str(lists_dir / "train_all.txt"),
        "eval_fixtures_file": str(lists_dir / "eval_all.txt"),
    }
    if args.print_fixtures:
        print(" ".join(train_ids))
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
