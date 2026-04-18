#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERL_DIR="${VERL_DIR:-$PROJECT_DIR/_vendor/verl}"
CONFIG_PATH="${CONFIG_PATH:-$VERL_DIR/examples/sglang_multiturn/config}"
RUN_CWD="${RUN_CWD:-$VERL_DIR}"

TRAIN_FILE="${TRAIN_FILE:-$PROJECT_DIR/phase3_rl/generated/phase3_fixtures.jsonl}"
VAL_FILE="${VAL_FILE:-$TRAIN_FILE}"
TOOL_CONFIG="${TOOL_CONFIG:-$PROJECT_DIR/phase3_rl/generated/tool_config.yaml}"
AGENT_LOOP_CONFIG="${AGENT_LOOP_CONFIG:-$PROJECT_DIR/phase3_rl/verl_agent_loop.yaml}"
MODEL_PATH="${MODEL_PATH:-Qwen/Qwen2.5-0.5B-Instruct}"
EXPERIMENT_NAME="${EXPERIMENT_NAME:-crayotter-phase3-grpo-smoke}"
FIXTURES="${FIXTURES:-local_smoke}"
TOOL_FIXTURE="${TOOL_FIXTURE:-local_smoke}"
MAX_PROMPT_LENGTH="${MAX_PROMPT_LENGTH:-16384}"
MAX_RESPONSE_LENGTH="${MAX_RESPONSE_LENGTH:-1536}"
REGENERATE_ASSETS="${REGENERATE_ASSETS:-1}"
TRAIN_BATCH_SIZE="${TRAIN_BATCH_SIZE:-1}"
PPO_MINI_BATCH_SIZE="${PPO_MINI_BATCH_SIZE:-1}"
PPO_MICRO_BATCH_SIZE="${PPO_MICRO_BATCH_SIZE:-1}"
ROLLOUT_N="${ROLLOUT_N:-4}"
LOGPROB_MICRO_BATCH_SIZE="${LOGPROB_MICRO_BATCH_SIZE:-1}"
VAL_BATCH_SIZE="${VAL_BATCH_SIZE:-1}"
ATTN_IMPLEMENTATION="${ATTN_IMPLEMENTATION:-sdpa}"
MODEL_DTYPE="${MODEL_DTYPE:-bfloat16}"
ROLLOUT_DTYPE="${ROLLOUT_DTYPE:-bfloat16}"
ROLLOUT_ENFORCE_EAGER="${ROLLOUT_ENFORCE_EAGER:-True}"
ROLLOUT_MAX_NUM_SEQS="${ROLLOUT_MAX_NUM_SEQS:-32}"
SGLANG_ATTENTION_BACKEND="${SGLANG_ATTENTION_BACKEND:-triton}"
SGLANG_MM_ATTENTION_BACKEND="${SGLANG_MM_ATTENTION_BACKEND:-triton_attn}"
SGLANG_SAMPLING_BACKEND="${SGLANG_SAMPLING_BACKEND:-pytorch}"
AGENT_LOOP_WORKERS="${AGENT_LOOP_WORKERS:-1}"
USE_REMOVE_PADDING="${USE_REMOVE_PADDING:-False}"

if [[ "$REGENERATE_ASSETS" == "1" ]] || [[ ! -f "$TRAIN_FILE" ]]; then
  python3 -m phase3_rl.export_verl_phase3_dataset --fixtures $FIXTURES --output "$TRAIN_FILE"
fi

if [[ "$REGENERATE_ASSETS" == "1" ]] || [[ ! -f "$TOOL_CONFIG" ]]; then
  python3 -m phase3_rl.generate_verl_tool_config --fixture "$TOOL_FIXTURE" --output "$TOOL_CONFIG"
fi

if [[ ! -d "$RUN_CWD/verl/trainer/config" ]]; then
  echo "Expected Hydra config root at: $RUN_CWD/verl/trainer/config"
  echo "Set RUN_CWD or VERL_DIR to a valid verl repo root before running this script."
  exit 1
fi

# Prefer the vendored verl checkout over any separately installed copy so
# the trainer module and Hydra configs stay in sync.
export PYTHONPATH="$VERL_DIR:$PROJECT_DIR:${PYTHONPATH:-}"

# Some environments propagate a non-numeric OMP_NUM_THREADS, which libgomp
# rejects before training starts.
if [[ -n "${OMP_NUM_THREADS:-}" ]] && ! [[ "${OMP_NUM_THREADS}" =~ ^[0-9]+$ ]]; then
  export OMP_NUM_THREADS=1
fi

cd "$RUN_CWD"

python3 -m verl.trainer.main_ppo \
  --config-path="$CONFIG_PATH" \
  --config-name='gsm8k_multiturn_grpo' \
  algorithm.adv_estimator=grpo \
  data.train_files="$TRAIN_FILE" \
  data.val_files="$VAL_FILE" \
  data.train_batch_size="$TRAIN_BATCH_SIZE" \
  data.val_batch_size="$VAL_BATCH_SIZE" \
  data.max_prompt_length="$MAX_PROMPT_LENGTH" \
  data.max_response_length="$MAX_RESPONSE_LENGTH" \
  data.filter_overlong_prompts=True \
  data.truncation='error' \
  data.return_raw_chat=True \
  actor_rollout_ref.model.path="$MODEL_PATH" \
  +actor_rollout_ref.model.override_config.attn_implementation="$ATTN_IMPLEMENTATION" \
  actor_rollout_ref.model.use_remove_padding="$USE_REMOVE_PADDING" \
  actor_rollout_ref.model.enable_gradient_checkpointing=True \
  actor_rollout_ref.actor.optim.lr=1e-6 \
  actor_rollout_ref.actor.ppo_mini_batch_size="$PPO_MINI_BATCH_SIZE" \
  actor_rollout_ref.actor.ppo_micro_batch_size_per_gpu="$PPO_MICRO_BATCH_SIZE" \
  actor_rollout_ref.actor.use_kl_loss=True \
  actor_rollout_ref.actor.kl_loss_coef=0.001 \
  actor_rollout_ref.actor.kl_loss_type=low_var_kl \
  actor_rollout_ref.actor.entropy_coeff=0 \
  actor_rollout_ref.actor.fsdp_config.param_offload=False \
  actor_rollout_ref.actor.fsdp_config.optimizer_offload=False \
  actor_rollout_ref.actor.fsdp_config.model_dtype="$MODEL_DTYPE" \
  actor_rollout_ref.actor.fsdp_config.dtype="$MODEL_DTYPE" \
  actor_rollout_ref.rollout.name=sglang \
  actor_rollout_ref.rollout.mode=async \
  actor_rollout_ref.rollout.dtype="$ROLLOUT_DTYPE" \
  actor_rollout_ref.rollout.enforce_eager="$ROLLOUT_ENFORCE_EAGER" \
  actor_rollout_ref.rollout.max_num_seqs="$ROLLOUT_MAX_NUM_SEQS" \
  actor_rollout_ref.rollout.tensor_model_parallel_size=1 \
  actor_rollout_ref.rollout.gpu_memory_utilization=0.55 \
  actor_rollout_ref.rollout.log_prob_micro_batch_size_per_gpu="$LOGPROB_MICRO_BATCH_SIZE" \
  actor_rollout_ref.rollout.n="$ROLLOUT_N" \
  +actor_rollout_ref.rollout.engine_kwargs.sglang.attention_backend="$SGLANG_ATTENTION_BACKEND" \
  +actor_rollout_ref.rollout.engine_kwargs.sglang.mm_attention_backend="$SGLANG_MM_ATTENTION_BACKEND" \
  +actor_rollout_ref.rollout.engine_kwargs.sglang.sampling_backend="$SGLANG_SAMPLING_BACKEND" \
  actor_rollout_ref.rollout.multi_turn.enable=True \
  actor_rollout_ref.rollout.multi_turn.max_assistant_turns=8 \
  actor_rollout_ref.rollout.multi_turn.max_parallel_calls=1 \
  actor_rollout_ref.rollout.multi_turn.tool_config_path="$TOOL_CONFIG" \
  actor_rollout_ref.rollout.agent.num_workers="$AGENT_LOOP_WORKERS" \
  actor_rollout_ref.rollout.agent.default_agent_loop=crayotter_phase3_tool_agent \
  actor_rollout_ref.rollout.agent.agent_loop_config_path="$AGENT_LOOP_CONFIG" \
  actor_rollout_ref.ref.log_prob_micro_batch_size_per_gpu="$LOGPROB_MICRO_BATCH_SIZE" \
  actor_rollout_ref.ref.fsdp_config.param_offload=True \
  actor_rollout_ref.ref.fsdp_config.model_dtype="$MODEL_DTYPE" \
  actor_rollout_ref.ref.fsdp_config.dtype="$MODEL_DTYPE" \
  algorithm.use_kl_in_reward=False \
  trainer.logger='["console"]' \
  trainer.project_name='crayotter-phase3-rl' \
  trainer.experiment_name="$EXPERIMENT_NAME" \
  trainer.n_gpus_per_node=1 \
  trainer.nnodes=1 \
  trainer.total_training_steps=10 \
  trainer.test_freq=5 \
  trainer.save_freq=5 \
  trainer.critic_warmup=0 \
  "$@"
