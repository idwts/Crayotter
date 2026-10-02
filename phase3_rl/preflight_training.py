from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the Phase 3 verl training host.")
    parser.add_argument("--verl-dir", required=True)
    parser.add_argument("--backend", choices=("vllm", "sglang"), default="vllm")
    parser.add_argument("--expected-gpus", type=int, default=4)
    parser.add_argument("--min-gpu-memory-gib", type=float, default=0.0)
    parser.add_argument("--min-disk-free-gib", type=float, default=50.0)
    return parser


def _import_version(module_name: str) -> str:
    module = importlib.import_module(module_name)
    return str(getattr(module, "__version__", "unknown"))


def main() -> int:
    args = _parser().parse_args()
    verl_dir = Path(args.verl_dir).expanduser().resolve()
    sys.path.insert(0, str(verl_dir))

    checks: dict[str, Any] = {
        "python": sys.version.split()[0],
        "verl_dir": str(verl_dir),
        "backend": args.backend,
        "judge_enabled": os.environ.get("CRAYOTTER_RL_JUDGE_ENABLED", "").lower()
        in {"1", "true", "yes", "on"},
        "judge_key_present": bool(
            os.environ.get("CRAYOTTER_RL_JUDGE_API_KEY")
            or os.environ.get("DASHSCOPE_API_KEY")
        ),
        "adv_estimator": os.environ.get("ADV_ESTIMATOR", "gae").lower(),
        "segment_allocator_enabled": os.environ.get(
            "CRAYOTTER_RL_SEGMENT_ALLOCATOR_ENABLED",
            "0",
        ).lower() in {"1", "true", "yes", "on"},
    }
    errors: list[str] = []

    if sys.version_info < (3, 10):
        errors.append("Python 3.10 or newer is required.")
    if not (verl_dir / "verl" / "trainer" / "config").is_dir():
        errors.append(f"Missing verl Hydra config root under {verl_dir}.")
    if checks["adv_estimator"] != "gae":
        errors.append("Long-horizon segment training requires critic-based PPO/GAE.")

    dynamic_flags = {
        name: os.environ.get(name, "False").lower() in {"1", "true", "yes", "on"}
        for name in (
            "ACTOR_USE_DYNAMIC_BSZ",
            "REF_USE_DYNAMIC_BSZ",
            "ROLLOUT_USE_DYNAMIC_BSZ",
            "CRITIC_USE_DYNAMIC_BSZ",
            "CRITIC_ENGINE_USE_DYNAMIC_BSZ",
        )
    }
    checks["dynamic_batch_flags"] = dynamic_flags
    if any(dynamic_flags.values()):
        errors.append(
            "Dynamic batching must be disabled for the current Qwen3.5/critic NestedTensor path."
        )

    for module_name in ("torch", "ray", "tensordict", "verl", args.backend):
        try:
            checks[f"{module_name}_version"] = _import_version(module_name)
        except Exception as exc:
            errors.append(f"Cannot import {module_name}: {exc}")
    if checks["judge_enabled"]:
        try:
            checks["openai_version"] = _import_version("openai")
        except Exception as exc:
            errors.append(f"Cannot import openai for the enabled AI judge: {exc}")

    try:
        import torch

        gpu_count = torch.cuda.device_count()
        checks["visible_gpu_count"] = gpu_count
        checks["gpu_names"] = [
            torch.cuda.get_device_name(index) for index in range(gpu_count)
        ]
        checks["gpu_memory_gib"] = [
            round(
                torch.cuda.get_device_properties(index).total_memory / (1024**3),
                1,
            )
            for index in range(gpu_count)
        ]
        if gpu_count != args.expected_gpus:
            errors.append(
                f"Expected {args.expected_gpus} visible GPUs, found {gpu_count}."
            )
        checks["gpu_capabilities"] = [
            ".".join(map(str, torch.cuda.get_device_capability(index)))
            for index in range(gpu_count)
        ]
        if gpu_count and not all(
            torch.cuda.get_device_capability(index)[0] >= 8
            for index in range(gpu_count)
        ):
            errors.append("All visible GPUs must support native bfloat16.")
        if args.min_gpu_memory_gib > 0 and any(
            memory < args.min_gpu_memory_gib
            for memory in checks["gpu_memory_gib"]
        ):
            errors.append(
                "At least one visible GPU has less than "
                f"{args.min_gpu_memory_gib:.1f} GiB memory."
            )
    except Exception as exc:
        errors.append(f"CUDA validation failed: {exc}")

    disk = shutil.disk_usage(verl_dir if verl_dir.exists() else Path.cwd())
    checks["disk_free_gib"] = round(disk.free / (1024**3), 1)
    if disk.free < args.min_disk_free_gib * 1024**3:
        errors.append(
            f"Less than {args.min_disk_free_gib:.1f} GiB free disk space is available."
        )
    if checks["judge_enabled"] and not checks["judge_key_present"]:
        errors.append("AI judge is enabled but no judge API key is set.")

    checks["errors"] = errors
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
