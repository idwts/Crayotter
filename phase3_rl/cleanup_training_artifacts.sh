#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/server_paths.sh"
crayotter_resolve_server_paths

RUNS_DIR="${RUNS_DIR:-$PROJECT_DIR/phase3_rl/runs/verl}"
RUNS_DIRS="${RUNS_DIRS:-$RUNS_DIR}"
# Only these roots are allowed to contribute reward manifests. Validation and
# historical runs may still be deleted, but must never enter allocator data.
MANIFEST_RUNS_DIRS="${MANIFEST_RUNS_DIRS:-$RUNS_DIRS}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/phase3_rl/logs}"
CHECKPOINT_ROOT="${CHECKPOINT_ROOT:-$VERL_DIR/checkpoints/crayotter-phase3-rl}"
EXPERIMENT_NAME="${EXPERIMENT_NAME:-crayotter-two-stage-qwen35-9b-grpo}"

CLEANUP_INTERVAL_SECONDS="${CLEANUP_INTERVAL_SECONDS:-300}"
CLEANUP_MIN_AGE_MINUTES="${CLEANUP_MIN_AGE_MINUTES:-45}"
CLEANUP_TARGET_FREE_GIB="${CLEANUP_TARGET_FREE_GIB:-180}"
CLEANUP_MIN_FREE_GIB="${CLEANUP_MIN_FREE_GIB:-80}"
CLEANUP_MAX_DELETE_PER_PASS="${CLEANUP_MAX_DELETE_PER_PASS:-300}"
CLEANUP_DELETE_NONCURRENT_CHECKPOINTS="${CLEANUP_DELETE_NONCURRENT_CHECKPOINTS:-0}"
CLEANUP_KEEP_LATEST_CHECKPOINTS="${CLEANUP_KEEP_LATEST_CHECKPOINTS:-1}"
CLEANUP_PRESERVE_CHECKPOINT_STEPS="${CLEANUP_PRESERVE_CHECKPOINT_STEPS:-}"
CLEANUP_ALWAYS_DELETE_ROLLOUTS="${CLEANUP_ALWAYS_DELETE_ROLLOUTS:-1}"
TRAJECTORY_MANIFEST_DIR="${CRAYOTTER_RL_TRAJECTORY_MANIFEST_DIR:-$PROJECT_DIR/phase3_rl/trajectory_manifests/$EXPERIMENT_NAME}"

mkdir -p "$LOG_DIR" "$TRAJECTORY_MANIFEST_DIR"
LOG_FILE="${CLEANUP_LOG_FILE:-$LOG_DIR/training_cleanup_$(date +%Y%m%d_%H%M%S).log}"
LOCK_FILE="${CLEANUP_LOCK_FILE:-$LOG_DIR/training_cleanup.lock}"

crayotter_assert_owned_path "$PROJECT_DIR"
crayotter_assert_owned_path "$CHECKPOINT_ROOT"

IFS=':' read -r -a run_roots <<<"$RUNS_DIRS"
IFS=':' read -r -a manifest_run_roots <<<"$MANIFEST_RUNS_DIRS"
if (( ${#run_roots[@]} == 0 )); then
  echo "No rollout roots were configured" >&2
  exit 2
fi
for run_root in "${run_roots[@]}"; do
  [[ -n "$run_root" ]] || continue
  crayotter_assert_owned_path "$run_root"
done
for run_root in "${manifest_run_roots[@]}"; do
  [[ -n "$run_root" ]] || continue
  crayotter_assert_owned_path "$run_root"
done

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] another cleanup loop is already running; exit" >>"$LOG_FILE"
  exit 0
fi

free_gib() {
  df -BG --output=avail "$SERVER_ROOT" | tail -1 | tr -dc '0-9'
}

should_archive_manifest() {
  local candidate="$1" manifest_root
  for manifest_root in "${manifest_run_roots[@]}"; do
    [[ -n "$manifest_root" ]] || continue
    if [[ "$candidate" == "$manifest_root" ]]; then
      return 0
    fi
  done
  return 1
}

delete_old_rollout_dirs() {
  local before after deleted reward_file manifest_name run_root dir
  before="$(free_gib)"
  deleted=0
  for run_root in "${run_roots[@]}"; do
    [[ -d "$run_root" ]] || continue
    while IFS= read -r -d '' dir; do
      reward_file="$(find "$dir" -maxdepth 2 -type f -name phase3_episode_reward.json -print -quit 2>/dev/null)"
      if [[ -z "$reward_file" ]]; then
        continue
      fi
      if should_archive_manifest "$run_root"; then
        manifest_name="$(basename "$dir").reward.json"
        if [[ ! -f "$TRAJECTORY_MANIFEST_DIR/$manifest_name" ]]; then
          # The reward manager writes this only after group ranking, segment
          # attribution, and allocator update have completed. Never archive a
          # pre-annotation episode reward as if it were training data.
          continue
        fi
      fi
      rm -rf -- "$dir"
      deleted=$((deleted + 1))
      if (( deleted >= CLEANUP_MAX_DELETE_PER_PASS )); then
        break
      fi
      after="$(free_gib)"
      if [[ "$CLEANUP_ALWAYS_DELETE_ROLLOUTS" != "1" ]] && (( after >= CLEANUP_TARGET_FREE_GIB )); then
        break
      fi
    done < <(
      find "$run_root" -mindepth 1 -maxdepth 1 -type d \
        -mmin "+$CLEANUP_MIN_AGE_MINUTES" \
        -printf '%T@ %p\0' 2>/dev/null \
      | sort -z -n \
      | sed -z 's/^[^ ]* //'
    )
    if (( deleted >= CLEANUP_MAX_DELETE_PER_PASS )); then
      break
    fi
  done
  after="$(free_gib)"
  echo "[$(date '+%F %T')] free_before=${before}GiB free_after=${after}GiB deleted_rollout_dirs=${deleted}" >>"$LOG_FILE"
}

delete_stale_current_checkpoints() {
  local experiment_dir latest_complete checkpoint checkpoint_step
  if [[ "$CLEANUP_KEEP_LATEST_CHECKPOINTS" != "1" ]]; then
    return 0
  fi
  experiment_dir="$CHECKPOINT_ROOT/$EXPERIMENT_NAME"
  [[ -d "$experiment_dir" ]] || return 0
  latest_complete=""
  while IFS= read -r checkpoint; do
    if [[ -d "$checkpoint/actor" && -d "$checkpoint/critic" ]]; then
      latest_complete="$checkpoint"
    fi
  done < <(find "$experiment_dir" -mindepth 1 -maxdepth 1 -type d -name 'global_step_*' -print 2>/dev/null | sort -V)
  [[ -n "$latest_complete" ]] || return 0
  while IFS= read -r -d '' checkpoint; do
    if [[ "$checkpoint" != "$latest_complete" && -d "$checkpoint/actor" && -d "$checkpoint/critic" ]]; then
      checkpoint_step="${checkpoint##*/global_step_}"
      if [[ ",${CLEANUP_PRESERVE_CHECKPOINT_STEPS// /,}," == *",${checkpoint_step},"* ]]; then
        echo "[$(date '+%F %T')] preserved_checkpoint=$checkpoint configured_steps=$CLEANUP_PRESERVE_CHECKPOINT_STEPS" >>"$LOG_FILE"
        continue
      fi
      if checkpoint_upload_active "$checkpoint"; then
        echo "[$(date '+%F %T')] kept_uploading_checkpoint=$checkpoint" >>"$LOG_FILE"
        continue
      fi
      rm -rf -- "$checkpoint"
      echo "[$(date '+%F %T')] deleted_stale_current_checkpoint=$checkpoint kept=$latest_complete" >>"$LOG_FILE"
    fi
  done < <(find "$experiment_dir" -mindepth 1 -maxdepth 1 -type d -name 'global_step_*' -mmin "+$CLEANUP_MIN_AGE_MINUTES" -print0 2>/dev/null)
}

checkpoint_upload_active() {
  local checkpoint="$1" lock pid
  lock="$checkpoint/.crayotter_upload_in_progress"
  [[ -f "$lock" ]] || return 1
  pid="$(sed -n 's/^pid=//p' "$lock" | head -n 1)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  rm -f -- "$lock"
  return 1
}

delete_noncurrent_checkpoints() {
  local active_upload exp_dir lock
  if [[ "$CLEANUP_DELETE_NONCURRENT_CHECKPOINTS" != "1" ]]; then
    echo "[$(date '+%F %T')] checkpoint_tree_cleanup_disabled current_experiment=$EXPERIMENT_NAME" >>"$LOG_FILE"
    return 0
  fi

  [[ -d "$CHECKPOINT_ROOT" ]] || return 0
  while IFS= read -r -d '' exp_dir; do
    if [[ "$(basename "$exp_dir")" != "$EXPERIMENT_NAME" ]]; then
      active_upload=0
      while IFS= read -r -d '' lock; do
        if checkpoint_upload_active "$(dirname "$lock")"; then
          active_upload=1
          break
        fi
      done < <(find "$exp_dir" -mindepth 2 -maxdepth 2 -type f \
        -name '.crayotter_upload_in_progress' -print0 2>/dev/null)
      if ((active_upload)); then
        echo "[$(date '+%F %T')] kept_checkpoint_tree_with_active_upload=$exp_dir" >>"$LOG_FILE"
        continue
      fi
      rm -rf -- "$exp_dir"
      echo "[$(date '+%F %T')] deleted_old_checkpoint_tree=$exp_dir" >>"$LOG_FILE"
    fi
  done < <(find "$CHECKPOINT_ROOT" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null)
}

echo "[$(date '+%F %T')] cleanup started runs_dirs=$RUNS_DIRS manifest_runs_dirs=$MANIFEST_RUNS_DIRS min_age=${CLEANUP_MIN_AGE_MINUTES}m target_free=${CLEANUP_TARGET_FREE_GIB}GiB min_free=${CLEANUP_MIN_FREE_GIB}GiB delete_noncurrent_checkpoints=${CLEANUP_DELETE_NONCURRENT_CHECKPOINTS} keep_latest_checkpoints=${CLEANUP_KEEP_LATEST_CHECKPOINTS} preserve_checkpoint_steps=${CLEANUP_PRESERVE_CHECKPOINT_STEPS}" >>"$LOG_FILE"

while true; do
  current_free="$(free_gib)"
  if [[ "$CLEANUP_ALWAYS_DELETE_ROLLOUTS" == "1" ]] || (( current_free < CLEANUP_TARGET_FREE_GIB )); then
    delete_old_rollout_dirs
  else
    echo "[$(date '+%F %T')] free=${current_free}GiB no_cleanup_needed" >>"$LOG_FILE"
  fi
  delete_stale_current_checkpoints

  current_free="$(free_gib)"
  if (( current_free < CLEANUP_MIN_FREE_GIB )); then
    delete_noncurrent_checkpoints
    delete_old_rollout_dirs
  fi

  sleep "$CLEANUP_INTERVAL_SECONDS"
done
