#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a
source .env
set +a

: "${NETUID:?NETUID is required}"
: "${VALIDATOR1_NAME:?VALIDATOR1_NAME is required}"
: "${VALIDATOR1_HOTKEY:?VALIDATOR1_HOTKEY is required}"
: "${CANARY_TARGET_EPOCH:?CANARY_TARGET_EPOCH is required}"

poll_seconds="${CANARY_POLL_SECONDS:-60}"
timeout_seconds="${CANARY_WAIT_TIMEOUT_SECONDS:-21600}"
deadline=$((SECONDS + timeout_seconds))
runtime_dir="$(dirname "$ROOT")/herald-runtime/netuid${NETUID}"
completion_marker="${CANARY_COMPLETION_MARKER:-${runtime_dir}/quorum-rollout-${CANARY_TARGET_EPOCH}.complete}"
requirements_file="${QUORUM_REQUIREMENTS_FILE:-${runtime_dir}/quorum-requirements-${CANARY_TARGET_EPOCH}.json}"

if [[ -f "$completion_marker" ]]; then
  echo "Guard already completed: $completion_marker"
  exit 0
fi

canary_epoch() {
  .venv/bin/python - "$ROOT" "$VALIDATOR1_NAME" "$VALIDATOR1_HOTKEY" "$NETUID" "${CANARY_STATE_PATH:-}" <<'PY'
import glob
import json
import os
import sys

root, wallet, hotkey, netuid, state_path = sys.argv[1:]
pattern = os.path.join(
    os.path.dirname(root), "herald-runtime", f"netuid{netuid}", "logs",
    wallet, hotkey, f"netuid{netuid}", "*", "herald_state.json",
)
epochs = []
paths = [state_path] if state_path else glob.glob(pattern)
for path in paths:
    try:
        with open(path, encoding="utf-8") as handle:
            epochs.append(int(json.load(handle).get("last_scored_epoch", -1)))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
print(max(epochs, default=-1))
PY
}

last_reported=""
while (( SECONDS < deadline )); do
  scored_epoch="$(canary_epoch)"
  if [[ "$scored_epoch" != "$last_reported" ]]; then
    echo "canary_epoch=${scored_epoch} target_epoch=${CANARY_TARGET_EPOCH}"
    last_reported="$scored_epoch"
  fi
  if (( scored_epoch >= CANARY_TARGET_EPOCH )); then
    break
  fi
  sleep "$poll_seconds"
done

scored_epoch="$(canary_epoch)"
if (( scored_epoch < CANARY_TARGET_EPOCH )); then
  echo "Timed out waiting for canary epoch ${CANARY_TARGET_EPOCH}; latest is ${scored_epoch}" >&2
  exit 1
fi

QUORUM_REQUIREMENTS_FILE="$requirements_file" ./scripts/rollout_validator_quorum.sh
QUORUM_REQUIREMENTS_FILE="$requirements_file" \
  QUORUM_TIMEOUT_SECONDS="${QUORUM_TIMEOUT_SECONDS:-1800}" \
  ./scripts/verify_validator_quorum.sh

mkdir -p "$runtime_dir"
marker_tmp="${completion_marker}.tmp"
printf 'completed_at=%s\ntarget_epoch=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CANARY_TARGET_EPOCH" > "$marker_tmp"
mv "$marker_tmp" "$completion_marker"
