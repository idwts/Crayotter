from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .build_case_eval_fixtures import (
    VIDEO_SUFFIXES,
    _candidate_seed_roots,
    _case_dirs,
    _is_probable_final_output,
    _load_prompt_map,
    build_fixture_payload,
)


FEEDBACK_TEMPLATES = [
    {
        "feedback": "上一版节奏偏拖，开头没有快速进入主题。请保留最贴合主题的素材，压缩重复镜头，让前 10 秒更有信息密度。",
        "preserve_requirements": ["保留与原始需求主题最相关的素材", "尽量复用已有分析结果和已下载素材"],
        "change_requirements": ["缩短冗余片段", "重新组织开头", "导出后检查最终时长"],
    },
    {
        "feedback": "用户希望二次剪辑更像一个完整故事，而不是素材拼接。请强化开头-发展-结尾结构，并修正字幕/旁白与画面的对应关系。",
        "preserve_requirements": ["保留上一版中主题清晰的镜头", "保留有效的素材选择"],
        "change_requirements": ["重排 timeline", "检查旁白或字幕同步", "重新导出新成片"],
    },
    {
        "feedback": "上一版完成度可以，但重点不够突出。请做一次局部返工：保留主体素材，替换弱相关片段，优化转场和节奏。",
        "preserve_requirements": ["保留主体素材和可用 rough cut", "不要简单复制上一版成片"],
        "change_requirements": ["替换弱相关素材", "优化转场/节奏", "验证最终文件可播放"],
    },
]

LONG_HORIZON_OFFLINE_TOOL_NAMES = [
    "inspect_video_duration",
    "cut_video",
    "build_edit_timeline_from_segments",
    "merge_videos",
    "export_video",
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _safe_case_id(case_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in case_id).strip("_")


def _find_previous_outputs(case_dir: Path, system: str, limit: int) -> list[Path]:
    outputs: list[Path] = []
    seen: set[str] = set()
    for root in _candidate_seed_roots(case_dir, system):
        for item in sorted(root.rglob("*")):
            if not item.is_file() or item.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            if not _is_probable_final_output(item):
                continue
            try:
                resolved = str(item.resolve())
            except OSError:
                resolved = str(item)
            if resolved in seen:
                continue
            seen.add(resolved)
            outputs.append(item)
            if len(outputs) >= limit:
                return outputs
    return outputs


def _copyable_seed(source: Path, target: str) -> dict[str, str]:
    return {"source": str(source.resolve(strict=False)), "target": target}


def _build_revision_payload(
    *,
    base_payload: dict[str, Any],
    case_id: str,
    prefix: str,
    revision_round: int,
    previous_outputs: list[Path],
) -> dict[str, Any]:
    template = FEEDBACK_TEMPLATES[(revision_round - 1) % len(FEEDBACK_TEMPLATES)]
    previous_output = previous_outputs[min(revision_round - 1, len(previous_outputs) - 1)] if previous_outputs else None
    previous_target = ""
    seeds = list(base_payload.get("runtime_seed", []))
    if previous_output is not None:
        previous_target = f"user_temp/previous_versions/previous_final_{_safe_case_id(case_id)}_r{revision_round}.mp4"
        seeds.append(_copyable_seed(previous_output, previous_target))

    fixture_id = f"{prefix}_{_safe_case_id(case_id)}_rev{revision_round}"
    original_request = str(base_payload["user_request"])
    user_request = (
        f"{original_request}\n\n"
        f"这是同一项目的第 {revision_round} 次反馈重剪。"
        f"用户反馈：{template['feedback']}"
    )
    if previous_target:
        user_request += f"\n上一版成片已放在 {previous_target}，请先诊断它，再基于原素材局部重剪。"

    editing_blueprint = (
        "Long-horizon agentic editing revision task. The policy should behave like an editor "
        "continuing an existing project rather than starting from scratch. First inspect the previous "
        "version if it is available, then reuse original materials, identify which parts should be "
        "preserved or changed according to feedback, perform a new editing trajectory, export a new "
        "final video, and inspect the exported duration. This training fixture is offline-only: do not "
        "search for or download remote materials. Analyze provided videos with the local rollout model "
        "when semantic evidence is missing, and use the provided local material paths directly.\n\n"
        "Required minimal successful trajectory:\n"
        "1. inspect_video_duration on the previous version if available.\n"
        "2. inspect_video_duration on one or two local materials under user_temp/materials or materials.\n"
        "3. cut_video at least one source material with input_path/start_time/end_time/output_name.\n"
        "4. build_edit_timeline_from_segments or merge_videos using the produced clip path.\n"
        "5. export_video from the merged or clipped artifact.\n"
        "6. inspect_video_duration on the exported final video.\n\n"
        "Reward includes hard artifact validity, stage-level progress, final duration, optional final-video "
        "judge, and revision-specific dense signals for diagnosis, material reuse, and post-export validation.\n\n"
        f"Original request:\n{original_request}\n\n"
        f"User feedback:\n{template['feedback']}"
    )

    metadata = dict(base_payload.get("metadata", {}))
    metadata.update(
        {
            "long_horizon_task": True,
            "task_family": "feedback_revision",
            "case_id": case_id,
            "base_fixture_id": base_payload["fixture_id"],
            "revision_round": revision_round,
            "feedback": template["feedback"],
            "preserve_requirements": template["preserve_requirements"],
            "change_requirements": template["change_requirements"],
            "previous_version_available": previous_output is not None,
            "previous_final_source": str(previous_output.resolve(strict=False)) if previous_output else "",
            "previous_final_target": previous_target,
            "branching_hint": (
                "Treat the previous version as a prefix artifact. Compare alternative fixes at high-impact "
                "stages such as opening shot, timeline ordering, subtitles/narration, and pacing."
            ),
        }
    )

    return {
        "fixture_id": fixture_id,
        "description": (
            f"Long-horizon feedback revision fixture for case {case_id}, round {revision_round}."
        ),
        "user_request": user_request,
        "target_duration_seconds": base_payload["target_duration_seconds"],
        "editing_blueprint": editing_blueprint,
        "allowed_tools": LONG_HORIZON_OFFLINE_TOOL_NAMES,
        "runtime_seed": seeds,
        "metadata": metadata,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build long-horizon feedback-revision Phase 3 RL fixtures from Crayotter_case_eval."
    )
    parser.add_argument("--case-eval-root", required=True)
    parser.add_argument("--output-root", default=str(_project_root() / "phase3_rl" / "fixtures"))
    parser.add_argument("--cases", nargs="*", default=[])
    parser.add_argument("--prefix", default="case_eval_lh")
    parser.add_argument("--system", default="ours")
    parser.add_argument("--raw-cases-root", default="")
    parser.add_argument("--default-duration-seconds", type=float, default=60.0)
    parser.add_argument("--revision-rounds", type=int, default=2)
    parser.add_argument("--previous-output-limit", type=int, default=3)
    parser.add_argument("--print-fixtures", action="store_true")
    args = parser.parse_args()

    if args.revision_rounds < 1:
        parser.error("--revision-rounds must be at least 1")

    case_eval_root = Path(args.case_eval_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    raw_cases_root = Path(args.raw_cases_root).expanduser().resolve() if args.raw_cases_root else None
    prompt_map = _load_prompt_map(case_eval_root)
    fixture_ids: list[str] = []
    written: list[dict[str, Any]] = []

    for case_dir in _case_dirs(case_eval_root, args.cases):
        if not case_dir.is_dir():
            raise FileNotFoundError(f"Missing case directory: {case_dir}")
        case_id = case_dir.name
        base_payload = build_fixture_payload(
            case_eval_root=case_eval_root,
            case_dir=case_dir,
            prompt_map=prompt_map,
            prefix=args.prefix,
            system=args.system,
            default_duration_seconds=args.default_duration_seconds,
            include_result_videos=False,
            raw_cases_root=raw_cases_root,
        )
        previous_outputs = _find_previous_outputs(case_dir, args.system, args.previous_output_limit)
        for revision_round in range(1, args.revision_rounds + 1):
            payload = _build_revision_payload(
                base_payload=base_payload,
                case_id=case_id,
                prefix=args.prefix,
                revision_round=revision_round,
                previous_outputs=previous_outputs,
            )
            fixture_ids.append(payload["fixture_id"])
            fixture_dir = output_root / payload["fixture_id"]
            fixture_dir.mkdir(parents=True, exist_ok=True)
            fixture_path = fixture_dir / "fixture.json"
            fixture_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(
                {
                    "fixture_id": payload["fixture_id"],
                    "case_id": case_id,
                    "revision_round": revision_round,
                    "previous_version_available": payload["metadata"]["previous_version_available"],
                    "seed_file_count": len(payload["runtime_seed"]),
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
