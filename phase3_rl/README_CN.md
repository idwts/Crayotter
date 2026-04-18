# Crayotter Phase 3 RL Pipeline

[English](./README.md) | [中文](./README_CN.md)

这个目录记录的是当前 `verl + Qwen3.5 + Crayotter` 的 Phase 3 RL smoke pipeline，主要说明这条链路是如何接起来的、依赖哪些环境前提，以及怎样按当前方式复现单卡 smoke run。

## 这个目录提供什么

- 基于 fixture 的本地 scripted rollout 验证
- 将 Phase 3 任务导出为 `verl` 可读的 JSONL 数据集
- 将 Crayotter 工具 schema 导出为 `verl` 原生 tool config YAML
- 通过 `CrayotterSubprocessTool` 接入工具执行
- 通过 `CrayotterPhase3ToolAgentLoop` 接入 episode reward
- 基于 sglang multi-turn rollout 的单卡 GRPO smoke 启动脚本

这条链路是 on-policy GRPO smoke path，目标是验证集成是否打通，不是做吞吐或效果 benchmark。

## 目录说明

- `fixtures/`：Phase 3 fixture。默认 smoke case 是 `local_smoke`。
- `generated/`：导出的数据集、tool config，以及可选的 validation 或 rollout dump。
- `runs/local/`：本地 rollout trace 和每次 episode 的运行目录。
- `runs/verl/`：`verl` rollout 过程中创建的运行目录。
- `local_env.py`：本地 Phase 3 rollout 主循环。
- `export_verl_phase3_dataset.py`：fixture 到 JSONL 的导出脚本。
- `generate_verl_tool_config.py`：tool schema 到 YAML 的导出脚本。
- `verl_tools.py`：Crayotter 工具到 `verl` 工具的包装层。
- `verl_agent_loop.py`：自定义 Phase 3 tool agent loop 注册。
- `run_verl_phase3_grpo.sh`：smoke run 启动入口。

## Pipeline 是怎么接起来的

1. fixture 定义任务、允许调用的工具、种子文件和可选 scripted turns。
2. `prompt_builder.py` 把 fixture 组装成符合 Phase 3 格式的多轮 prompt。
3. `generate_verl_tool_config.py` 生成 `verl` 原生 tool config YAML。
4. `verl_tools.py` 通过 `CrayotterSubprocessTool` 把 Crayotter 工具暴露给 `verl`。
5. `verl_agent_loop.py` 注册 `CrayotterPhase3ToolAgentLoop`，在 `verl` 的 `ToolAgentLoop` 基础上补充 episode reward。
6. `run_verl_phase3_grpo.sh` 通过 `python3 -m verl.trainer.main_ppo`，配合 `algorithm.adv_estimator=grpo` 和 sglang multi-turn 配置启动训练。

## 仓库里的 `verl` 约定

当前 pipeline 以 `_vendor/verl` 这份 vendored checkout 为准。

- `VERL_DIR` 默认是 `$PROJECT_DIR/_vendor/verl`
- `RUN_CWD` 应指向同一个 repo root，这样 Hydra 才能找到 `verl/trainer/config`
- `MODEL_PATH` 单独指向模型权重，和 `VERL_DIR` 没有目录绑定关系
- 模型不需要和 `verl` 放在同一个目录下

需要特别说明的是，`_vendor/verl` 不是纯上游版本，而是带本地补丁的版本，当前 smoke path 依赖这些修改，主要包括：

- Qwen3.5 的 `mRoPE` / `position_ids` 处理
- padding 和 TensorDict 选择阶段对 nested `position_ids` 的处理
- `ray_trainer.py` 中更稳妥的 entropy 路径
- `attention_utils.py` 中的 fallback 行为
- `_vendor/verl/requirements_sglang.txt` 中的依赖 pin

如果替换 `_vendor/verl`，这些补丁需要一并保留。参考服务器上曾经有过另一份 `/root/verl` clone，但已经删除；推荐做法是一台机器只保留一份正在使用的 `verl` checkout。

## 参考 AutoDL 环境

下面这套环境是在 `2026-04-17` 实际检查过、并与当前 smoke 路径对应的参考环境。

| 项目 | 参考值 |
| --- | --- |
| GPU | NVIDIA GeForce RTX 4090 24 GB |
| Driver | 580.76.05 |
| CUDA | 13.0 |
| base Python | `/root/miniconda3/bin/python` |
| 实际工作 Python | `/root/venvs/verl_q35/bin/python` |
| 项目根目录 | `/root/autodl-tmp/Crayotter-main` |
| vendored `verl` | `/root/autodl-tmp/Crayotter-main/_vendor/verl` |
| 额外 Python 包目录 | `/root/autodl-tmp/pylibs` |
| 历史重复 clone | `/root/verl`（已删除） |

这条 smoke 路径是在 `/root/venvs/verl_q35` 里跑通的，不是在 `base` conda 环境里。

## 依赖文件怎么理解

这条 RL 运行时不能只看一个 `requirements.txt`。

| 文件或层级 | 作用 | 说明 |
| --- | --- | --- |
| 仓库根目录 `requirements.txt` | Crayotter 主应用依赖 | 不是完整 RL 运行时。同步到 Linux 时建议保持 UTF-8 编码。 |
| `_vendor/verl/requirements_sglang.txt` | 最接近当前 `verl + sglang` smoke path 的提交内依赖说明 | 这里有当前最关键的 pin。 |
| 云端 live 环境 | 实际运行时 | 可能同时依赖 venv、`PYTHONPATH` 和单独目录安装的包。 |

`_vendor/verl/requirements_sglang.txt` 里当前最关键的 pin 包括：

- `transformers==5.5.3`
- `sglang[all]==0.5.9`
- `huggingface_hub==1.10.1`

一个很容易踩的点是：参考服务器上的 `sglang` 实际上是通过 `/root/autodl-tmp/pylibs` 暴露给 Python 的，不只是 venv site-packages 自己提供。所以 `pip show sglang` 看起来为空，不代表运行时一定缺这个包。

## 推荐复现流程

1. 先激活实际工作环境，并导出运行时变量：

```bash
source /root/venvs/verl_q35/bin/activate

export CUDA_HOME=/usr/local/cuda-13.0
export OMP_NUM_THREADS=1
export VLLM_CACHE_ROOT=/root/autodl-tmp/vllm-cache
export TORCHINDUCTOR_CACHE_DIR=/root/autodl-tmp/vllm-cache/torchinductor
export SGLANG_DISABLE_CUDNN_CHECK=1
export PYTHONPATH=/root/autodl-tmp/pylibs:/root/autodl-tmp/Crayotter-main/_vendor/verl:/root/autodl-tmp/Crayotter-main:${PYTHONPATH:-}
```

2. 正式训练前先做 import 检查：

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

3. 先验证本地 scripted smoke 路径：

```bash
python -m phase3_rl.run_local_rollout --fixture local_smoke --policy scripted
```

4. 导出训练所需资产：

```bash
python -m phase3_rl.export_verl_phase3_dataset --fixtures local_smoke
python -m phase3_rl.generate_verl_tool_config --fixture local_smoke
```

5. 启动一个短的 GRPO smoke run：

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

`run_verl_phase3_grpo.sh` 自己会把 `$VERL_DIR:$PROJECT_DIR` 加进 `PYTHONPATH`，但参考服务器上的 `/root/autodl-tmp/pylibs` 仍然需要你在外层手动 export。

## 常见失败点

- Python 用错：`base` 可能能导入一部分包，但并不是实际跑通 RL 的环境。请使用 `/root/venvs/verl_q35`。
- `sglang` 缺失：优先检查 `/root/autodl-tmp/pylibs` 是否在 `PYTHONPATH` 里。
- 导入到错误的 `verl`：如果机器上存在多份 checkout，让 `_vendor/verl` 优先出现在 `PYTHONPATH` 里，并显式设置 `VERL_DIR`。
- `flash-attn` 源码编译失败：参考服务器最终使用的是预编译 `flash_attn 2.8.3` wheel，优先使用与 `torch 2.11 + cu130` 匹配的 wheel。
- 模型路径问题：历史下载流程里用过 `local_dir_use_symlinks=False` 来避免本地模型目录在 `verl` 里出路径问题，建议优先使用真实文件而不是复杂软链接。
- 环境漂移：服务器是长期交互式环境，不要默认 live server 和提交到仓库的 requirement pin 完全一致。需要复现的 run 应单独记录精确版本。

## 输出产物

- 本地 traces：`phase3_rl/runs/local/<fixture>_xxx/`
- 导出的数据集：`phase3_rl/generated/phase3_fixtures.jsonl`
- 生成的 tool config：`phase3_rl/generated/tool_config.yaml`
- 可选 validation dump：`phase3_rl/generated/val_dumps/`
- 可选 rollout dump：`phase3_rl/generated/rollout_dumps/`
- checkpoints：`_vendor/verl/checkpoints/`
