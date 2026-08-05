from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

VIDEO_SUFFIXES = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}
TEXT_SEED_SUFFIXES = {".json", ".md", ".txt", ".yaml", ".yml"}
DEFAULT_PREFIX = "case_eval"
DEFAULT_PHASE3_TOOL_NAMES = [
    "add_narration_segments",
    "add_subtitles",
    "add_transition",
    "align_narration_to_timeline",
    "batch_cut_video",
    "build_edit_timeline_from_segments",
    "cut_video",
    "duck_background_audio",
    "export_video",
    "inspect_video_duration",
    "list_transition_presets",
    "merge_videos",
    "normalize_loudness",
    "plan_transition_timeline",
    "recall_semantic_segments",
    "validate_narration_timeline",
    "validate_timeline_constraints",
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _phase3_tool_names() -> list[str]:
    try:
        from .tool_catalog import get_phase3_tool_names

        return get_phase3_tool_names()
    except Exception:
        return list(DEFAULT_PHASE3_TOOL_NAMES)


def _safe_fixture_id(prefix: str, case_id: str) -> str:
    safe_case = re.sub(r"[^0-9A-Za-z_-]+", "_", case_id).strip("_")
    safe_prefix = re.sub(r"[^0-9A-Za-z_-]+", "_", prefix).strip("_") or DEFAULT_PREFIX
    return f"{safe_prefix}_{safe_case}"


def _load_prompt_map(case_eval_root: Path) -> dict[str, str]:
    parsed_path = case_eval_root / "meta" / "parsed_prompts.json"
    if not parsed_path.is_file():
        return {}
    try:
        payload = json.loads(parsed_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    prompt_map: dict[str, str] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            case_id = str(key).zfill(3) if str(key).isdigit() else str(key)
            prompt_map[case_id] = _extract_prompt_text(value)
    elif isinstance(payload, list):
        for index, value in enumerate(payload, start=1):
            if not isinstance(value, dict):
                continue
            raw_id = value.get("case_id") or value.get("id") or value.get("sample_id") or index
            case_id = str(raw_id).zfill(3) if str(raw_id).isdigit() else str(raw_id)
            prompt_map[case_id] = _extract_prompt_text(value)
    return {key: value for key, value in prompt_map.items() if value.strip()}


def _extract_prompt_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    for key in (
        "prompt",
        "user_request",
        "request",
        "instruction",
        "task",
        "query",
        "需求",
    ):
        text = str(value.get(key) or "").strip()
        if text:
            return text
    return json.dumps(value, ensure_ascii=False)


def _case_dirs(case_eval_root: Path, requested_cases: list[str]) -> list[Path]:
    eval_root = case_eval_root / "eval_results"
    if not eval_root.is_dir():
        raise FileNotFoundError(f"Missing eval_results directory: {eval_root}")
    if requested_cases:
        return [
            eval_root / (case_id.zfill(3) if case_id.isdigit() else case_id)
            for case_id in requested_cases
        ]
    return sorted([item for item in eval_root.iterdir() if item.is_dir()])


def _is_probable_final_output(path: Path) -> bool:
    lowered = "/".join(part.lower() for part in path.parts)
    name = path.name.lower()
    markers = (
        "final",
        "export",
        "result",
        "output",
        "成片",
        "结果",
    )
    return any(marker in lowered or marker in name for marker in markers)


def _candidate_seed_roots(case_dir: Path, system: str) -> list[Path]:
    roots: list[Path] = []
    preferred = case_dir / system / "run_01"
    if preferred.is_dir():
        roots.append(preferred)
    for run_dir in sorted(case_dir.glob("*/run_*")):
        if run_dir not in roots:
            roots.append(run_dir)
    if case_dir not in roots:
        roots.append(case_dir)
    return roots


def _load_run_config(case_dir: Path, system: str) -> dict[str, Any]:
    config_path = case_dir / system / "run_01" / "config.json"
    if not config_path.is_file():
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _raw_case_root_candidates(
    *,
    case_eval_root: Path,
    case_id: str,
    raw_cases_root: Path | None,
) -> list[Path]:
    roots: list[Path] = []
    if raw_cases_root is not None:
        roots.extend([raw_cases_root / case_id, raw_cases_root])
    roots.extend(
        [
            case_eval_root / "cases" / case_id,
            case_eval_root / "cases",
            case_eval_root.parent / "cases" / case_id,
            case_eval_root.parent / "cases",
            case_eval_root.parent / "video_agent_eval" / "cases" / case_id,
            case_eval_root.parent / "video_agent_eval" / "cases",
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve(strict=False))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _collect_config_raw_files(
    *,
    case_eval_root: Path,
    case_dir: Path,
    system: str,
    raw_cases_root: Path | None,
) -> list[dict[str, str]]:
    config = _load_run_config(case_dir, system)
    if not config:
        return []
    case_id = case_dir.name
    raw_files = [str(item) for item in config.get("raw_files", []) if str(item).strip()]
    if not raw_files:
        for item in config.get("staged_files", []):
            if isinstance(item, dict) and str(item.get("source") or "").strip():
                raw_files.append(str(item["source"]))
    seeds: list[dict[str, str]] = []
    seen_sources: set[str] = set()
    roots = _raw_case_root_candidates(
        case_eval_root=case_eval_root,
        case_id=case_id,
        raw_cases_root=raw_cases_root,
    )
    for raw in raw_files:
        basename = Path(raw).name
        candidates = []
        original = Path(raw)
        try:
            if original.is_file():
                candidates.append(original)
        except OSError:
            pass
        candidates.extend(root / basename for root in roots)
        source = None
        for candidate in candidates:
            try:
                if candidate.is_file():
                    source = candidate
                    break
            except OSError:
                continue
        if source is None:
            continue
        resolved = str(source.resolve())
        if resolved in seen_sources:
            continue
        seen_sources.add(resolved)
        seeds.append({"source": resolved, "target": f"user_temp/materials/{source.name}"})
    return seeds


def _collect_seed_files(
    case_eval_root: Path,
    case_dir: Path,
    system: str,
    include_result_videos: bool,
    raw_cases_root: Path | None,
) -> list[dict[str, str]]:
    seeds: list[dict[str, str]] = []
    seen_sources: set[str] = set()
    seeds.extend(
        _collect_config_raw_files(
            case_eval_root=case_eval_root,
            case_dir=case_dir,
            system=system,
            raw_cases_root=raw_cases_root,
        )
    )
    seen_sources.update(str(Path(seed["source"]).resolve(strict=False)) for seed in seeds)
    source_roots = _candidate_seed_roots(case_dir, system)

    def add_seed(source: Path, target_base: str) -> None:
        try:
            resolved = str(source.resolve())
        except OSError:
            resolved = str(source)
        if resolved in seen_sources:
            return
        seen_sources.add(resolved)
        seeds.append({"source": resolved, "target": f"{target_base}/{source.name}"})

    for root in source_roots:
        for user_temp in root.rglob("user_temp"):
            if not user_temp.is_dir():
                continue
            for item in sorted(user_temp.rglob("*")):
                if item.is_file() and item.suffix.lower() in VIDEO_SUFFIXES | TEXT_SEED_SUFFIXES:
                    add_seed(item, "user_temp")
        for temp_dir in root.rglob("temp"):
            if not temp_dir.is_dir():
                continue
            for item in sorted(temp_dir.rglob("*_analysis.json")):
                if item.is_file():
                    add_seed(item, "user_temp")

    if not seeds:
        for root in source_roots:
            for item in sorted(root.rglob("*")):
                if not item.is_file():
                    continue
                suffix = item.suffix.lower()
                if suffix in TEXT_SEED_SUFFIXES and (
                    item.name.endswith("_analysis.json") or item.name in {"analysis.json", "blueprint.json"}
                ):
                    add_seed(item, "user_temp")
                elif suffix in VIDEO_SUFFIXES and (include_result_videos or not _is_probable_final_output(item)):
                    add_seed(item, "user_temp")

    return seeds


def _target_duration_from_prompt(prompt: str, default_seconds: float) -> float:
    patterns = [
        r"(\d+(?:\.\d+)?)\s*(?:秒|s|sec|seconds)",
        r"(\d+(?:\.\d+)?)\s*(?:分钟|分|min|minutes)",
    ]
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, prompt, flags=re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1))
        if index == 1:
            value *= 60.0
        if value > 0:
            return value
    return default_seconds


def build_fixture_payload(
    *,
    case_eval_root: Path,
    case_dir: Path,
    prompt_map: dict[str, str],
    prefix: str,
    system: str,
    default_duration_seconds: float,
    include_result_videos: bool,
    raw_cases_root: Path | None,
) -> dict[str, Any]:
    case_id = case_dir.name
    fixture_id = _safe_fixture_id(prefix, case_id)
    user_request = prompt_map.get(case_id) or prompt_map.get(str(int(case_id)) if case_id.isdigit() else case_id) or (
        f"请基于评测样本 {case_id} 的已有素材完成一个节奏清晰、主题贴合的短视频剪辑。"
    )
    target_duration = _target_duration_from_prompt(user_request, default_duration_seconds)
    seeds = _collect_seed_files(
        case_eval_root,
        case_dir,
        system,
        include_result_videos,
        raw_cases_root,
    )
    editing_blueprint = (
        "Long-horizon agentic editing RL fixture generated from Crayotter_case_eval. "
        "Use existing local materials and analysis only. Prefer a complete tool trajectory: "
        "inspect/validate inputs, build a rough cut, align narration/subtitles/audio if required, "
        "export a final video, then inspect the final duration. The reward will combine hard "
        "artifact validity, stage-level dense progress, final duration, and optional final-video judge."
    )
    return {
        "fixture_id": fixture_id,
        "description": f"Crayotter case-eval sample {case_id} converted for multi-trajectory Phase 3 RL.",
        "user_request": user_request,
        "target_duration_seconds": target_duration,
        "editing_blueprint": editing_blueprint,
        "allowed_tools": _phase3_tool_names(),
        "runtime_seed": seeds,
        "metadata": {
            "case_eval_root": str(case_eval_root),
            "case_id": case_id,
            "seed_system": system,
            "seed_file_count": len(seeds),
            "include_result_videos": include_result_videos,
            "raw_cases_root": str(raw_cases_root) if raw_cases_root else "",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert Crayotter_case_eval samples into Phase 3 RL fixtures."
    )
    parser.add_argument("--case-eval-root", required=True)
    parser.add_argument("--output-root", default=str(_project_root() / "phase3_rl" / "fixtures"))
    parser.add_argument("--cases", nargs="*", default=[])
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--system", default="ours", help="Preferred eval system folder used to find workspace seeds.")
    parser.add_argument(
        "--raw-cases-root",
        default="",
        help="Optional root containing original case folders, e.g. /path/to/cases.",
    )
    parser.add_argument("--default-duration-seconds", type=float, default=60.0)
    parser.add_argument("--include-result-videos", action="store_true")
    parser.add_argument("--print-fixtures", action="store_true")
    args = parser.parse_args()

    case_eval_root = Path(args.case_eval_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    prompt_map = _load_prompt_map(case_eval_root)
    raw_cases_root = Path(args.raw_cases_root).expanduser().resolve() if args.raw_cases_root else None
    fixture_ids: list[str] = []
    written: list[dict[str, Any]] = []

    for case_dir in _case_dirs(case_eval_root, args.cases):
        if not case_dir.is_dir():
            raise FileNotFoundError(f"Missing case directory: {case_dir}")
        payload = build_fixture_payload(
            case_eval_root=case_eval_root,
            case_dir=case_dir,
            prompt_map=prompt_map,
            prefix=args.prefix,
            system=args.system,
            default_duration_seconds=args.default_duration_seconds,
            include_result_videos=bool(args.include_result_videos),
            raw_cases_root=raw_cases_root,
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
