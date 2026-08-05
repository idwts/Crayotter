#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${1:-$ROOT/configs/grpb.env}"
[[ -f "$CONFIG" ]] || { echo "Missing config: $CONFIG" >&2; exit 2; }

set -a
source "$CONFIG"
set +a

: "${VERL_DIR:?Set VERL_DIR.}"
: "${MODEL_PATH:?Set MODEL_PATH.}"
: "${TRAIN_FILE:?Set TRAIN_FILE.}"
: "${VAL_FILE:?Set VAL_FILE.}"
: "${TOOL_CONFIG:?Set TOOL_CONFIG.}"

if [[ "${CRAYOTTER_RL_JUDGE_ENABLED:-false}" == "true" ]] &&
   [[ -z "${CRAYOTTER_RL_JUDGE_API_KEY:-}" ]]; then
  echo "Export CRAYOTTER_RL_JUDGE_API_KEY when the judge is enabled." >&2
  exit 2
fi

exec bash "$ROOT/phase3_rl/run_verl_phase3_grpo.sh"
