#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERL_DIR="${VERL_DIR:-$PROJECT_DIR/_vendor/verl}"
if [[ -z "${CONFIG_PATH+x}" ]]; then
  if [[ -d "$VERL_DIR/examples/sglang_multiturn/config" ]]; then
    CONFIG_PATH="$VERL_DIR/examples/sglang_multiturn/config"
    CONFIG_NAME="${CONFIG_NAME:-gsm8k_multiturn_grpo}"
  else
    CONFIG_PATH="$VERL_DIR/verl/trainer/config"
    CONFIG_NAME="${CONFIG_NAME:-ppo_trainer}"
  fi
else
  CONFIG_NAME="${CONFIG_NAME:-gsm8k_multiturn_grpo}"
fi
RUN_CWD="${RUN_CWD:-$VERL_DIR}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TRAINER_MODULE="${TRAINER_MODULE:-verl.trainer.main_ppo}"

TRAIN_FILE="${TRAIN_FILE:-$PROJECT_DIR/phase3_rl/generated/phase3_fixtures.jsonl}"
VAL_FILE="${VAL_FILE:-$TRAIN_FILE}"
TOOL_CONFIG="${TOOL_CONFIG:-$PROJECT_DIR/phase3_rl/generated/tool_config.yaml}"
AGENT_LOOP_CONFIG="${AGENT_LOOP_CONFIG:-$PROJECT_DIR/phase3_rl/verl_agent_loop.yaml}"
MODEL_PATH="${MODEL_PATH:-Qwen/Qwen3.5-27B}"
EXPERIMENT_NAME="${EXPERIMENT_NAME:-crayotter-phase3-grpo}"
PROJECT_NAME="${PROJECT_NAME:-crayotter-phase3-rl}"
FIXTURES_WAS_SET=1
if [[ -z "${FIXTURES+x}" ]]; then
  FIXTURES="local_smoke"
  FIXTURES_WAS_SET=0
fi
DATASET_REPEAT="${DATASET_REPEAT:-16}"
TOOL_FIXTURES="${TOOL_FIXTURES:-$FIXTURES}"
CASE_EVAL_ROOT="${CASE_EVAL_ROOT:-}"
CASE_EVAL_CASES="${CASE_EVAL_CASES:-}"
CASE_EVAL_PREFIX="${CASE_EVAL_PREFIX:-case_eval}"
CASE_EVAL_SYSTEM="${CASE_EVAL_SYSTEM:-ours}"
CASE_EVAL_INCLUDE_RESULT_VIDEOS="${CASE_EVAL_INCLUDE_RESULT_VIDEOS:-0}"
CASE_EVAL_RAW_CASES_ROOT="${CASE_EVAL_RAW_CASES_ROOT:-}"
CASE_EVAL_BUILDER_MODULE="${CASE_EVAL_BUILDER_MODULE:-phase3_rl.build_case_eval_fixtures}"
LONG_HORIZON_REVISION_ROUNDS="${LONG_HORIZON_REVISION_ROUNDS:-}"
LONG_HORIZON_PREVIOUS_OUTPUT_LIMIT="${LONG_HORIZON_PREVIOUS_OUTPUT_LIMIT:-}"

MAX_PROMPT_LENGTH="${MAX_PROMPT_LENGTH:-10240}"
MAX_RESPONSE_LENGTH="${MAX_RESPONSE_LENGTH:-10240}"
REGENERATE_ASSETS="${REGENERATE_ASSETS:-1}"
TRAIN_BATCH_SIZE="${TRAIN_BATCH_SIZE:-4}"
PPO_MINI_BATCH_SIZE="${PPO_MINI_BATCH_SIZE:-4}"
PPO_MICRO_BATCH_SIZE="${PPO_MICRO_BATCH_SIZE:-1}"
ROLLOUT_N="${ROLLOUT_N:-4}"
LOGPROB_MICRO_BATCH_SIZE="${LOGPROB_MICRO_BATCH_SIZE:-1}"
VAL_BATCH_SIZE="${VAL_BATCH_SIZE:-1}"
TOTAL_TRAINING_STEPS="${TOTAL_TRAINING_STEPS:-200}"
SAVE_FREQ="${SAVE_FREQ:-20}"
TEST_FREQ="${TEST_FREQ:-20}"
RESUME_MODE="${RESUME_MODE:-auto}"
LOGGER="${LOGGER:-[\"console\"]}"
N_GPUS_PER_NODE="${N_GPUS_PER_NODE:-1}"
NNODES="${NNODES:-1}"
RETURN_MULTI_MODAL_INPUTS="${RETURN_MULTI_MODAL_INPUTS:-False}"
DATA_LOADER_NUM_WORKERS="${DATA_LOADER_NUM_WORKERS:-0}"

ATTN_IMPLEMENTATION="${ATTN_IMPLEMENTATION:-sdpa}"
MODEL_DTYPE="${MODEL_DTYPE:-bfloat16}"
ROLLOUT_DTYPE="${ROLLOUT_DTYPE:-bfloat16}"
ROLLOUT_BACKEND="${ROLLOUT_BACKEND:-vllm}"
ROLLOUT_MODE="${ROLLOUT_MODE:-async}"
ROLLOUT_ENFORCE_EAGER="${ROLLOUT_ENFORCE_EAGER:-False}"
ROLLOUT_MAX_NUM_SEQS="${ROLLOUT_MAX_NUM_SEQS:-32}"
ROLLOUT_TP_SIZE="${ROLLOUT_TP_SIZE:-1}"
ROLLOUT_GPU_MEMORY_UTILIZATION="${ROLLOUT_GPU_MEMORY_UTILIZATION:-0.55}"
VLLM_DISABLE_CUSTOM_ALL_REDUCE="${VLLM_DISABLE_CUSTOM_ALL_REDUCE:-}"
ROLLOUT_TEMPERATURE="${ROLLOUT_TEMPERATURE:-0.8}"
ROLLOUT_TOP_P="${ROLLOUT_TOP_P:-0.95}"
VAL_DO_SAMPLE="${VAL_DO_SAMPLE:-True}"
VAL_TEMPERATURE="${VAL_TEMPERATURE:-$ROLLOUT_TEMPERATURE}"
VAL_TOP_P="${VAL_TOP_P:-$ROLLOUT_TOP_P}"
ROLLOUT_MAX_MODEL_LEN="${ROLLOUT_MAX_MODEL_LEN:-$((MAX_PROMPT_LENGTH + MAX_RESPONSE_LENGTH))}"
MAX_ASSISTANT_TURNS="${MAX_ASSISTANT_TURNS:-12}"
AGENT_LOOP_WORKERS="${AGENT_LOOP_WORKERS:-4}"
MAX_PARALLEL_TOOL_CALLS="${MAX_PARALLEL_TOOL_CALLS:-2}"
USE_REMOVE_PADDING="${USE_REMOVE_PADDING:-False}"
ULYSSES_SEQUENCE_PARALLEL_SIZE="${ULYSSES_SEQUENCE_PARALLEL_SIZE:-1}"
MULTI_TURN_FORMAT="${MULTI_TURN_FORMAT:-hermes}"

ACTOR_LR="${ACTOR_LR:-1e-6}"
KL_LOSS_COEF="${KL_LOSS_COEF:-0.001}"
LORA_RANK="${LORA_RANK:-0}"
LORA_ALPHA="${LORA_ALPHA:-128}"
LORA_TARGET_MODULES="${LORA_TARGET_MODULES:-all-linear}"
LORA_EXCLUDE_MODULES="${LORA_EXCLUDE_MODULES:-.*visual.*}"
TRAINING_STRATEGY="${TRAINING_STRATEGY:-}"
ACTOR_USE_TORCH_COMPILE="${ACTOR_USE_TORCH_COMPILE:-}"
REF_USE_TORCH_COMPILE="${REF_USE_TORCH_COMPILE:-}"
ADV_ESTIMATOR="${ADV_ESTIMATOR:-gae}"
CRITIC_LR="${CRITIC_LR:-5e-6}"
CRITIC_MICRO_BATCH_SIZE="${CRITIC_MICRO_BATCH_SIZE:-$PPO_MICRO_BATCH_SIZE}"
CRITIC_OFFLOAD="${CRITIC_OFFLOAD:-True}"
CRITIC_PARAM_OFFLOAD="${CRITIC_PARAM_OFFLOAD:-$CRITIC_OFFLOAD}"
CRITIC_OPTIMIZER_OFFLOAD="${CRITIC_OPTIMIZER_OFFLOAD:-$CRITIC_OFFLOAD}"
CRITIC_OFFLOAD_POLICY="${CRITIC_OFFLOAD_POLICY:-}"
CRITIC_OPTIMIZER_FOREACH="${CRITIC_OPTIMIZER_FOREACH:-}"
ACTOR_USE_DYNAMIC_BSZ="${ACTOR_USE_DYNAMIC_BSZ:-False}"
REF_USE_DYNAMIC_BSZ="${REF_USE_DYNAMIC_BSZ:-False}"
ROLLOUT_USE_DYNAMIC_BSZ="${ROLLOUT_USE_DYNAMIC_BSZ:-False}"
ENTROPY_FROM_LOGITS_WITH_CHUNKING="${ENTROPY_FROM_LOGITS_WITH_CHUNKING:-False}"
ENTROPY_FROM_LOGITS_CHUNK_SIZE="${ENTROPY_FROM_LOGITS_CHUNK_SIZE:-2048}"
ACTOR_PARAM_OFFLOAD="${ACTOR_PARAM_OFFLOAD:-False}"
ACTOR_OPTIMIZER_OFFLOAD="${ACTOR_OPTIMIZER_OFFLOAD:-False}"
CRITIC_USE_DYNAMIC_BSZ="${CRITIC_USE_DYNAMIC_BSZ:-False}"
CRITIC_ENGINE_USE_DYNAMIC_BSZ="${CRITIC_ENGINE_USE_DYNAMIC_BSZ:-False}"
CRITIC_ENGINE_MICRO_BATCH_SIZE="${CRITIC_ENGINE_MICRO_BATCH_SIZE:-$CRITIC_MICRO_BATCH_SIZE}"
CRITIC_ENGINE_INFER_MICRO_BATCH_SIZE="${CRITIC_ENGINE_INFER_MICRO_BATCH_SIZE:-$CRITIC_MICRO_BATCH_SIZE}"
PROCESS_REWARD_MANAGER="${PROCESS_REWARD_MANAGER:-$PROJECT_DIR/phase3_rl/process_reward_manager.py}"
PROCESS_AGENT_LOOP_MANAGER="${PROCESS_AGENT_LOOP_MANAGER:-phase3_rl.process_agent_loop_manager.CrayotterProcessRewardAgentLoopManager}"

SGLANG_ATTENTION_BACKEND="${SGLANG_ATTENTION_BACKEND:-triton}"
SGLANG_MM_ATTENTION_BACKEND="${SGLANG_MM_ATTENTION_BACKEND:-triton_attn}"
SGLANG_SAMPLING_BACKEND="${SGLANG_SAMPLING_BACKEND:-pytorch}"

if [[ "$ROLLOUT_BACKEND" != "vllm" && "$ROLLOUT_BACKEND" != "sglang" ]]; then
  echo "ROLLOUT_BACKEND must be vllm or sglang, got: $ROLLOUT_BACKEND" >&2
  exit 2
fi

export PYTHONPATH="$VERL_DIR:$PROJECT_DIR:${PYTHONPATH:-}"

if [[ -n "$CASE_EVAL_ROOT" ]]; then
  case_eval_args=(
    -m "$CASE_EVAL_BUILDER_MODULE"
    --case-eval-root "$CASE_EVAL_ROOT"
    --prefix "$CASE_EVAL_PREFIX"
    --system "$CASE_EVAL_SYSTEM"
    --print-fixtures
  )
  if [[ -n "$CASE_EVAL_RAW_CASES_ROOT" ]]; then
    case_eval_args+=(--raw-cases-root "$CASE_EVAL_RAW_CASES_ROOT")
  fi
  if [[ "$CASE_EVAL_INCLUDE_RESULT_VIDEOS" == "1" ]]; then
    case_eval_args+=(--include-result-videos)
  fi
  if [[ -n "$LONG_HORIZON_REVISION_ROUNDS" ]]; then
    case_eval_args+=(--revision-rounds "$LONG_HORIZON_REVISION_ROUNDS")
  fi
  if [[ -n "$LONG_HORIZON_PREVIOUS_OUTPUT_LIMIT" ]]; then
    case_eval_args+=(--previous-output-limit "$LONG_HORIZON_PREVIOUS_OUTPUT_LIMIT")
  fi
  if [[ -n "$CASE_EVAL_CASES" ]]; then
    # shellcheck disable=SC2206
    case_ids=($CASE_EVAL_CASES)
    case_eval_args+=(--cases "${case_ids[@]}")
  fi
  CASE_EVAL_FIXTURES="$("$PYTHON_BIN" "${case_eval_args[@]}")"
  CASE_EVAL_FIXTURES="$(
    printf '%s\n' "$CASE_EVAL_FIXTURES" \
      | tr '[:space:]' '\n' \
      | awk -v prefix="$CASE_EVAL_PREFIX" 'index($0, prefix) == 1 { print }' \
      | xargs
  )"
  if [[ -z "$CASE_EVAL_FIXTURES" ]]; then
    echo "No case-eval fixtures were generated with prefix: $CASE_EVAL_PREFIX" >&2
    exit 1
  fi
  if [[ "$FIXTURES_WAS_SET" == "0" || "$FIXTURES" == "local_smoke" ]]; then
    FIXTURES="$CASE_EVAL_FIXTURES"
    TOOL_FIXTURES="$CASE_EVAL_FIXTURES"
  fi
fi

if [[ "$REGENERATE_ASSETS" == "1" ]] || [[ ! -f "$TRAIN_FILE" ]]; then
  # shellcheck disable=SC2086
  "$PYTHON_BIN" -m phase3_rl.export_verl_phase3_dataset \
    --fixtures $FIXTURES \
    --repeat "$DATASET_REPEAT" \
    --episode-base-dir "${EPISODE_BASE_DIR:-$PROJECT_DIR/phase3_rl/runs/verl}" \
    --output "$TRAIN_FILE"
fi

if [[ "$REGENERATE_ASSETS" == "1" ]] || [[ ! -f "$TOOL_CONFIG" ]]; then
  # shellcheck disable=SC2086
  "$PYTHON_BIN" -m phase3_rl.generate_verl_tool_config \
    --fixtures $TOOL_FIXTURES \
    --output "$TOOL_CONFIG"
fi

if [[ ! -d "$RUN_CWD/verl/trainer/config" ]]; then
  echo "Expected Hydra config root at: $RUN_CWD/verl/trainer/config" >&2
  echo "Set RUN_CWD or VERL_DIR to a valid verl repo root." >&2
  exit 1
fi

export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-true}"
if [[ -z "${OMP_NUM_THREADS:-}" ]] || ! [[ "$OMP_NUM_THREADS" =~ ^[1-9][0-9]*$ ]]; then
  export OMP_NUM_THREADS=1
fi

if (( TRAIN_BATCH_SIZE < 1 || PPO_MINI_BATCH_SIZE < 1 || PPO_MICRO_BATCH_SIZE < 1 )); then
  echo "Training batch sizes must be positive integers." >&2
  exit 2
fi
if (( TRAIN_BATCH_SIZE % PPO_MINI_BATCH_SIZE != 0 )); then
  echo "TRAIN_BATCH_SIZE must be divisible by PPO_MINI_BATCH_SIZE." >&2
  exit 2
fi
if (( PPO_MINI_BATCH_SIZE % PPO_MICRO_BATCH_SIZE != 0 )); then
  echo "PPO_MINI_BATCH_SIZE must be divisible by PPO_MICRO_BATCH_SIZE." >&2
  exit 2
fi
if (( N_GPUS_PER_NODE < 1 || ROLLOUT_TP_SIZE < 1 )); then
  echo "N_GPUS_PER_NODE and ROLLOUT_TP_SIZE must be positive integers." >&2
  exit 2
fi
if (( N_GPUS_PER_NODE % ROLLOUT_TP_SIZE != 0 )); then
  echo "N_GPUS_PER_NODE must be divisible by ROLLOUT_TP_SIZE." >&2
  exit 2
fi
if (( ULYSSES_SEQUENCE_PARALLEL_SIZE < 1 )); then
  echo "ULYSSES_SEQUENCE_PARALLEL_SIZE must be a positive integer." >&2
  exit 2
fi
if (( N_GPUS_PER_NODE % ULYSSES_SEQUENCE_PARALLEL_SIZE != 0 )); then
  echo "N_GPUS_PER_NODE must be divisible by ULYSSES_SEQUENCE_PARALLEL_SIZE." >&2
  exit 2
fi
if (( PPO_MICRO_BATCH_SIZE * ULYSSES_SEQUENCE_PARALLEL_SIZE < N_GPUS_PER_NODE )); then
  echo "PPO_MICRO_BATCH_SIZE * ULYSSES_SEQUENCE_PARALLEL_SIZE must cover all training GPUs." >&2
  exit 2
fi
if (( CRITIC_MICRO_BATCH_SIZE * ULYSSES_SEQUENCE_PARALLEL_SIZE < N_GPUS_PER_NODE )); then
  echo "CRITIC_MICRO_BATCH_SIZE * ULYSSES_SEQUENCE_PARALLEL_SIZE must cover all training GPUs." >&2
  exit 2
fi

args=(
  "--config-path=$CONFIG_PATH"
  "--config-name=$CONFIG_NAME"
  "algorithm.adv_estimator=$ADV_ESTIMATOR"
  "data.train_files=$TRAIN_FILE"
  "data.val_files=$VAL_FILE"
  "data.train_batch_size=$TRAIN_BATCH_SIZE"
  "data.val_batch_size=$VAL_BATCH_SIZE"
  "data.max_prompt_length=$MAX_PROMPT_LENGTH"
  "data.max_response_length=$MAX_RESPONSE_LENGTH"
  "data.filter_overlong_prompts=True"
  "data.truncation=error"
  "data.return_raw_chat=True"
  "data.return_multi_modal_inputs=$RETURN_MULTI_MODAL_INPUTS"
  "data.dataloader_num_workers=$DATA_LOADER_NUM_WORKERS"
  "actor_rollout_ref.model.path=$MODEL_PATH"
  "+actor_rollout_ref.model.override_config.attn_implementation=$ATTN_IMPLEMENTATION"
  "+actor_rollout_ref.model.override_config._attn_implementation=$ATTN_IMPLEMENTATION"
  "actor_rollout_ref.model.use_remove_padding=$USE_REMOVE_PADDING"
  "actor_rollout_ref.model.enable_gradient_checkpointing=True"
  "actor_rollout_ref.actor.optim.lr=$ACTOR_LR"
  "actor_rollout_ref.actor.ulysses_sequence_parallel_size=$ULYSSES_SEQUENCE_PARALLEL_SIZE"
  "actor_rollout_ref.actor.ppo_mini_batch_size=$PPO_MINI_BATCH_SIZE"
  "actor_rollout_ref.actor.ppo_micro_batch_size_per_gpu=$PPO_MICRO_BATCH_SIZE"
  "actor_rollout_ref.actor.use_dynamic_bsz=$ACTOR_USE_DYNAMIC_BSZ"
  "actor_rollout_ref.actor.use_kl_loss=True"
  "actor_rollout_ref.actor.kl_loss_coef=$KL_LOSS_COEF"
  "actor_rollout_ref.actor.kl_loss_type=low_var_kl"
  "actor_rollout_ref.actor.entropy_coeff=0"
  "actor_rollout_ref.actor.entropy_from_logits_with_chunking=$ENTROPY_FROM_LOGITS_WITH_CHUNKING"
  "actor_rollout_ref.actor.entropy_from_logits_chunk_size=$ENTROPY_FROM_LOGITS_CHUNK_SIZE"
  "actor_rollout_ref.actor.fsdp_config.ulysses_sequence_parallel_size=$ULYSSES_SEQUENCE_PARALLEL_SIZE"
  "actor_rollout_ref.actor.fsdp_config.param_offload=$ACTOR_PARAM_OFFLOAD"
  "actor_rollout_ref.actor.fsdp_config.optimizer_offload=$ACTOR_OPTIMIZER_OFFLOAD"
  "actor_rollout_ref.actor.fsdp_config.model_dtype=$MODEL_DTYPE"
  "actor_rollout_ref.actor.fsdp_config.dtype=$MODEL_DTYPE"
  "actor_rollout_ref.rollout.name=$ROLLOUT_BACKEND"
  "actor_rollout_ref.rollout.mode=$ROLLOUT_MODE"
  "actor_rollout_ref.rollout.dtype=$ROLLOUT_DTYPE"
  "actor_rollout_ref.rollout.enforce_eager=$ROLLOUT_ENFORCE_EAGER"
  "actor_rollout_ref.rollout.max_num_seqs=$ROLLOUT_MAX_NUM_SEQS"
  "actor_rollout_ref.rollout.max_model_len=$ROLLOUT_MAX_MODEL_LEN"
  "actor_rollout_ref.rollout.tensor_model_parallel_size=$ROLLOUT_TP_SIZE"
  "actor_rollout_ref.rollout.gpu_memory_utilization=$ROLLOUT_GPU_MEMORY_UTILIZATION"
  "actor_rollout_ref.rollout.log_prob_micro_batch_size_per_gpu=$LOGPROB_MICRO_BATCH_SIZE"
  "actor_rollout_ref.rollout.log_prob_use_dynamic_bsz=$ROLLOUT_USE_DYNAMIC_BSZ"
  "actor_rollout_ref.rollout.n=$ROLLOUT_N"
  "actor_rollout_ref.rollout.temperature=$ROLLOUT_TEMPERATURE"
  "actor_rollout_ref.rollout.top_p=$ROLLOUT_TOP_P"
  "actor_rollout_ref.rollout.val_kwargs.do_sample=$VAL_DO_SAMPLE"
  "actor_rollout_ref.rollout.val_kwargs.temperature=$VAL_TEMPERATURE"
  "actor_rollout_ref.rollout.val_kwargs.top_p=$VAL_TOP_P"
  "actor_rollout_ref.rollout.multi_turn.enable=True"
  "actor_rollout_ref.rollout.multi_turn.format=$MULTI_TURN_FORMAT"
  "actor_rollout_ref.rollout.multi_turn.max_assistant_turns=$MAX_ASSISTANT_TURNS"
  "actor_rollout_ref.rollout.multi_turn.max_parallel_calls=$MAX_PARALLEL_TOOL_CALLS"
  "actor_rollout_ref.rollout.multi_turn.tool_config_path=$TOOL_CONFIG"
  "actor_rollout_ref.rollout.agent.num_workers=$AGENT_LOOP_WORKERS"
  "actor_rollout_ref.rollout.agent.default_agent_loop=crayotter_phase3_tool_agent"
  "actor_rollout_ref.rollout.agent.agent_loop_config_path=$AGENT_LOOP_CONFIG"
  "actor_rollout_ref.ref.log_prob_micro_batch_size_per_gpu=$LOGPROB_MICRO_BATCH_SIZE"
  "actor_rollout_ref.ref.log_prob_use_dynamic_bsz=$REF_USE_DYNAMIC_BSZ"
  "actor_rollout_ref.ref.entropy_from_logits_with_chunking=$ENTROPY_FROM_LOGITS_WITH_CHUNKING"
  "actor_rollout_ref.ref.entropy_from_logits_chunk_size=$ENTROPY_FROM_LOGITS_CHUNK_SIZE"
  "actor_rollout_ref.ref.fsdp_config.ulysses_sequence_parallel_size=$ULYSSES_SEQUENCE_PARALLEL_SIZE"
  "actor_rollout_ref.ref.fsdp_config.param_offload=True"
  "actor_rollout_ref.ref.fsdp_config.model_dtype=$MODEL_DTYPE"
  "actor_rollout_ref.ref.fsdp_config.dtype=$MODEL_DTYPE"
  "critic.model.path=$MODEL_PATH"
  "+critic.model.override_config.attn_implementation=$ATTN_IMPLEMENTATION"
  "+critic.model.override_config._attn_implementation=$ATTN_IMPLEMENTATION"
  "critic.model.use_remove_padding=$USE_REMOVE_PADDING"
  "critic.model.enable_gradient_checkpointing=True"
  "critic.optim.lr=$CRITIC_LR"
  "critic.ulysses_sequence_parallel_size=$ULYSSES_SEQUENCE_PARALLEL_SIZE"
  "critic.ppo_micro_batch_size_per_gpu=$CRITIC_MICRO_BATCH_SIZE"
  "critic.use_dynamic_bsz=$CRITIC_USE_DYNAMIC_BSZ"
  "+critic.engine.use_dynamic_bsz=$CRITIC_ENGINE_USE_DYNAMIC_BSZ"
  "+critic.engine.micro_batch_size_per_gpu=$CRITIC_ENGINE_MICRO_BATCH_SIZE"
  "+critic.engine.infer_micro_batch_size_per_gpu=$CRITIC_ENGINE_INFER_MICRO_BATCH_SIZE"
  "critic.strategy=${TRAINING_STRATEGY:-fsdp2}"
  "critic.fsdp.ulysses_sequence_parallel_size=$ULYSSES_SEQUENCE_PARALLEL_SIZE"
  "critic.fsdp.param_offload=$CRITIC_PARAM_OFFLOAD"
  "critic.fsdp.optimizer_offload=$CRITIC_OPTIMIZER_OFFLOAD"
  "critic.fsdp.model_dtype=$MODEL_DTYPE"
  "critic.fsdp.dtype=$MODEL_DTYPE"
  "critic.fsdp.use_torch_compile=False"
  "algorithm.use_kl_in_reward=False"
  "trainer.logger=$LOGGER"
  "trainer.project_name=$PROJECT_NAME"
  "trainer.experiment_name=$EXPERIMENT_NAME"
  "trainer.n_gpus_per_node=$N_GPUS_PER_NODE"
  "trainer.nnodes=$NNODES"
  "trainer.total_training_steps=$TOTAL_TRAINING_STEPS"
  "trainer.test_freq=$TEST_FREQ"
  "trainer.save_freq=$SAVE_FREQ"
  "trainer.resume_mode=$RESUME_MODE"
  "trainer.critic_warmup=0"
)

if [[ -n "${TRAINER_BALANCE_BATCH:-}" ]]; then
  args+=("trainer.balance_batch=$TRAINER_BALANCE_BATCH")
fi

if [[ "${CRAYOTTER_RL_PROCESS_REWARD:-0}" == "1" || "${CRAYOTTER_RL_PROCESS_REWARD:-}" == "true" ]]; then
  args+=(
    "+actor_rollout_ref.rollout.agent.agent_loop_manager_class=$PROCESS_AGENT_LOOP_MANAGER"
  )
fi

if [[ -n "$TRAINING_STRATEGY" ]]; then
  args+=(
    "actor_rollout_ref.actor.strategy=$TRAINING_STRATEGY"
    "actor_rollout_ref.ref.strategy=$TRAINING_STRATEGY"
  )
fi

if [[ -n "$ACTOR_USE_TORCH_COMPILE" ]]; then
  args+=("actor_rollout_ref.actor.use_torch_compile=$ACTOR_USE_TORCH_COMPILE")
fi

if [[ -n "$REF_USE_TORCH_COMPILE" ]]; then
  args+=("actor_rollout_ref.ref.use_torch_compile=$REF_USE_TORCH_COMPILE")
fi

# PyTorch Adam's foreach implementation allocates temporary tensor lists during
# optimizer.step().  On large critics this can OOM even when parameters and
# optimizer state are otherwise offloaded.  Allow memory-constrained runs to
# select the lower-peak scalar implementation without changing the optimizer.
if [[ -n "$CRITIC_OPTIMIZER_FOREACH" ]]; then
  args+=("critic.optim.override_optimizer_config={foreach: $CRITIC_OPTIMIZER_FOREACH}")
fi

# FSDP2 can offload parameters and gradients through its native CPU offload
# policy.  Keep this opt-in because it trades update latency for GPU headroom.
if [[ -n "$CRITIC_OFFLOAD_POLICY" ]]; then
  args+=("critic.fsdp.offload_policy=$CRITIC_OFFLOAD_POLICY")
fi

if [[ "$ROLLOUT_BACKEND" == "sglang" ]]; then
  args+=(
    "+actor_rollout_ref.rollout.engine_kwargs.sglang.attention_backend=$SGLANG_ATTENTION_BACKEND"
    "+actor_rollout_ref.rollout.engine_kwargs.sglang.mm_attention_backend=$SGLANG_MM_ATTENTION_BACKEND"
    "+actor_rollout_ref.rollout.engine_kwargs.sglang.sampling_backend=$SGLANG_SAMPLING_BACKEND"
  )
fi

if [[ "$ROLLOUT_BACKEND" == "vllm" && -n "$VLLM_DISABLE_CUSTOM_ALL_REDUCE" ]]; then
  args+=("+actor_rollout_ref.rollout.engine_kwargs.vllm.disable_custom_all_reduce=$VLLM_DISABLE_CUSTOM_ALL_REDUCE")
fi

if (( LORA_RANK > 0 )); then
  args+=(
    "actor_rollout_ref.model.lora_rank=$LORA_RANK"
    "actor_rollout_ref.model.lora_alpha=$LORA_ALPHA"
    "actor_rollout_ref.model.target_modules=$LORA_TARGET_MODULES"
    "actor_rollout_ref.model.exclude_modules=$LORA_EXCLUDE_MODULES"
    "actor_rollout_ref.rollout.load_format=safetensors"
    "actor_rollout_ref.rollout.layered_summon=True"
  )
fi

cd "$RUN_CWD"
"$PYTHON_BIN" -m "$TRAINER_MODULE" "${args[@]}" "$@"
