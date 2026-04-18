# Crayotter Phase 3 RL Pipeline

[English](./README.md) | [中文](./README_CN.md)

This directory documents the current `verl + Qwen3.5 + Crayotter` Phase 3 RL smoke pipeline, including how the pipeline is wired, which environment assumptions matter, and how to reproduce the current single-GPU smoke run.

## What Is Included

- local scripted rollout validation for Phase 3 fixtures
- export of Phase 3 data into `verl` JSONL format
- export of Crayotter tool schemas into native `verl` tool config YAML
- `CrayotterSubprocessTool` integration for tool execution
- `CrayotterPhase3ToolAgentLoop` integration for episode reward attachment
- a conservative single-GPU GRPO smoke launcher based on sglang multi-turn rollout

This is an on-policy GRPO smoke path. It is meant to verify the integration, not to benchmark training throughput.

## Directory Map

- `fixtures/`: Phase 3 fixtures. `local_smoke` is the default smoke case.
- `generated/`: exported dataset, tool config, and optional validation or rollout dumps.
- `runs/local/`: local rollout traces and per-episode runtime roots.
- `runs/verl/`: runtime roots created during `verl` rollouts.
- `local_env.py`: local Phase 3 rollout loop.
- `export_verl_phase3_dataset.py`: fixture-to-JSONL export.
- `generate_verl_tool_config.py`: tool-schema-to-YAML export.
- `verl_tools.py`: Crayotter tool wrapper for `verl`.
- `verl_agent_loop.py`: custom Phase 3 tool agent loop registration.
- `run_verl_phase3_grpo.sh`: smoke-run launcher.

## How The Pipeline Works

1. A fixture defines the task, allowed tools, seeded files, and optional scripted turns.
2. `prompt_builder.py` turns the fixture into the multi-turn prompt expected by Phase 3.
3. `generate_verl_tool_config.py` generates a native `verl` tool config YAML.
4. `verl_tools.py` exposes Crayotter tools through `CrayotterSubprocessTool`.
5. `verl_agent_loop.py` registers `CrayotterPhase3ToolAgentLoop`, which extends `verl`'s `ToolAgentLoop` and writes episode reward.
6. `run_verl_phase3_grpo.sh` launches `python3 -m verl.trainer.main_ppo` with `algorithm.adv_estimator=grpo` and the sglang multi-turn config path.

## `verl` Contract In This Repo

This pipeline is built around the vendored checkout under `_vendor/verl`.

- `VERL_DIR` defaults to `$PROJECT_DIR/_vendor/verl`.
- `RUN_CWD` should point at the same repo root so Hydra can find `verl/trainer/config`.
- `MODEL_PATH` points to model weights and is independent from `VERL_DIR`.
- The model does not need to live next to `verl`.

`_vendor/verl` is not a pristine upstream copy. It contains local patches required by the current smoke path, including:

- Qwen3.5 `mRoPE` / `position_ids` handling
- nested `position_ids` handling in padding and TensorDict selection
- a safer entropy path in `ray_trainer.py`
- fallback behavior in `attention_utils.py`
- dependency pins in `_vendor/verl/requirements_sglang.txt`

If `_vendor/verl` is replaced, those patches must be preserved. A historical duplicate clone at `/root/verl` was removed from the reference server; the recommended setup is to keep a single active `verl` checkout per machine.

## Reference AutoDL Environment

The environment below was inspected on `2026-04-17` and matches the smoke path that was manually validated.

| Item | Reference value |
| --- | --- |
| GPU | NVIDIA GeForce RTX 4090 24 GB |
| Driver | 580.76.05 |
| CUDA | 13.0 |
| Base Python | `/root/miniconda3/bin/python` |
| Working Python | `/root/venvs/verl_q35/bin/python` |
| Project root | `/root/autodl-tmp/Crayotter-main` |
| Vendored `verl` | `/root/autodl-tmp/Crayotter-main/_vendor/verl` |
| Extra Python package root | `/root/autodl-tmp/pylibs` |
| Historical duplicate clone | `/root/verl` (removed) |

The smoke path worked from `/root/venvs/verl_q35`, not from the `base` conda environment.

## Dependency Files

The RL runtime is not described by a single file.

| File or layer | Role | Notes |
| --- | --- | --- |
| repo-root `requirements.txt` | Crayotter app dependencies | Not the full RL runtime. Keep this file in UTF-8 when syncing to Linux. |
| `_vendor/verl/requirements_sglang.txt` | nearest committed `verl + sglang` dependency spec | Includes the key pins for the smoke path. |
| live server state | actual working runtime | May also rely on a venv, `PYTHONPATH`, and packages installed into a separate target directory. |

Key pinned entries in `_vendor/verl/requirements_sglang.txt`:

- `transformers==5.5.3`
- `sglang[all]==0.5.9`
- `huggingface_hub==1.10.1`

One important gotcha: on the reference server, `sglang` was effectively provided through `/root/autodl-tmp/pylibs`, not through the venv site-packages alone. That means `pip show sglang` may look empty even when imports work.

## Recommended Reproduction Flow

1. Activate the validated Python environment and export the required runtime variables:

```bash
source /root/venvs/verl_q35/bin/activate

export CUDA_HOME=/usr/local/cuda-13.0
export OMP_NUM_THREADS=1
export VLLM_CACHE_ROOT=/root/autodl-tmp/vllm-cache
export TORCHINDUCTOR_CACHE_DIR=/root/autodl-tmp/vllm-cache/torchinductor
export SGLANG_DISABLE_CUDNN_CHECK=1
export PYTHONPATH=/root/autodl-tmp/pylibs:/root/autodl-tmp/Crayotter-main/_vendor/verl:/root/autodl-tmp/Crayotter-main:${PYTHONPATH:-}
```

2. Verify imports before training:

```bash
python - <<'PY'
import torch
import vllm
import ray
import tensordict
import verl
import phase3_rl
print("torch:", torch.__version__)
print("vllm:", vllm.__version__)
print("verl import ok")
print("phase3_rl import ok")
PY
```

3. Validate the local scripted smoke path:

```bash
python -m phase3_rl.run_local_rollout --fixture local_smoke --policy scripted
```

4. Export the training assets:

```bash
python -m phase3_rl.export_verl_phase3_dataset --fixtures local_smoke
python -m phase3_rl.generate_verl_tool_config --fixture local_smoke
```

5. Launch a short GRPO smoke run:

```bash
VERL_DIR=/root/autodl-tmp/Crayotter-main/_vendor/verl \
MODEL_PATH=/root/autodl-tmp/models/Qwen3.5-0.8B \
EXPERIMENT_NAME=crayotter-phase3-grpo-debug \
RESUME_MODE=disable \
TOTAL_TRAINING_STEPS=10 \
TRAIN_BATCH_SIZE=1 \
VAL_BATCH_SIZE=1 \
PPO_MINI_BATCH_SIZE=1 \
PPO_MICRO_BATCH_SIZE=1 \
ROLLOUT_N=1 \
bash /root/autodl-tmp/Crayotter-main/phase3_rl/run_verl_phase3_grpo.sh
```

The launcher itself already adds `$VERL_DIR:$PROJECT_DIR` to `PYTHONPATH`. The extra `/root/autodl-tmp/pylibs` prefix still needs to be exported externally on the reference server.

## Common Failure Modes

- Wrong Python: `base` may import some packages but still miss the actual RL stack. Use `/root/venvs/verl_q35`.
- Missing `sglang`: check whether `/root/autodl-tmp/pylibs` is present in `PYTHONPATH`.
- Wrong `verl`: if more than one checkout exists, keep `_vendor/verl` first in `PYTHONPATH` and set `VERL_DIR` explicitly.
- `flash-attn` build failures: the validated server ended up using a prebuilt `flash_attn 2.8.3` wheel. Prefer a wheel over source build for `torch 2.11 + cu130`.
- Model path issues: an earlier download flow used `local_dir_use_symlinks=False` to avoid local model path problems inside `verl`. Prefer real files over a symlink-heavy layout.
- Environment drift: the live server changed over time. For any run that should be reproducible, record the exact package versions used together.

## Outputs

- local traces: `phase3_rl/runs/local/<fixture>_xxx/`
- exported dataset: `phase3_rl/generated/phase3_fixtures.jsonl`
- generated tool config: `phase3_rl/generated/tool_config.yaml`
- optional validation dumps: `phase3_rl/generated/val_dumps/`
- optional rollout dumps: `phase3_rl/generated/rollout_dumps/`
- checkpoints: `_vendor/verl/checkpoints/`
