#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a
source .env
set +a

: "${NETUID:?NETUID is required}"
: "${SUBTENSOR_NETWORK:?SUBTENSOR_NETWORK is required}"
: "${VALIDATOR1_NAME:?VALIDATOR1_NAME is required}"
: "${VALIDATOR1_HOTKEY:?VALIDATOR1_HOTKEY is required}"
: "${VALIDATOR2_NAME:?VALIDATOR2_NAME is required}"
: "${VALIDATOR2_HOTKEY:?VALIDATOR2_HOTKEY is required}"
: "${HERALD_REGISTRY_PATH:?HERALD_REGISTRY_PATH is required}"
: "${HERALD_REGISTRY_PUBKEY:?HERALD_REGISTRY_PUBKEY is required}"
: "${HERALD_REGISTRY_AUTHORITY_HOTKEY:?HERALD_REGISTRY_AUTHORITY_HOTKEY is required}"

if [[ "${HERALD_REQUIRE_SIGNED_REGISTRY:-}" != "true" ]]; then
  echo "Refusing rollout: HERALD_REQUIRE_SIGNED_REGISTRY must be true" >&2
  exit 1
fi
if [[ "${HERALD_REQUIRE_SIGNED_BRIEFS:-}" != "true" ]]; then
  echo "Refusing rollout: HERALD_REQUIRE_SIGNED_BRIEFS must be true" >&2
  exit 1
fi

target_epoch="${CANARY_TARGET_EPOCH:-}"
runtime_dir="$(dirname "$ROOT")/herald-runtime/netuid${NETUID}"
requirements_file="${QUORUM_REQUIREMENTS_FILE:-${runtime_dir}/quorum-requirements-${target_epoch:-manual}.json}"
if [[ -n "$target_epoch" ]]; then
  canary_summary="$({ .venv/bin/python - "$ROOT" "$VALIDATOR1_NAME" "$VALIDATOR1_HOTKEY" "$NETUID" "${CANARY_REQUIRED_BRIEF_ID:-}" "$target_epoch" "${CANARY_STATE_PATH:-}" <<'PY'
import glob
import json
import os
import sys

root, wallet, hotkey, netuid, required_brief, target_epoch, state_path = sys.argv[1:]
pattern = os.path.join(
    os.path.dirname(root), "herald-runtime", f"netuid{netuid}", "logs",
    wallet, hotkey, f"netuid{netuid}", "*", "herald_state.json",
)
states = []
paths = [state_path] if state_path else glob.glob(pattern)
for path in paths:
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
            states.append((int(state.get("last_scored_epoch", -1)), state))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
epoch, state = max(states, key=lambda item: item[0], default=(-1, {}))
entry_items = list((state.get("vesting", {}).get("entries", {}) or {}).items())
if required_brief:
    entry_items = [(article_id, entry) for article_id, entry in entry_items
                   if str(entry.get("brief_id", "")) == required_brief]
entries = [entry for _article_id, entry in entry_items]
pool_spent = state.get("pool_spent", {}) or {}
brief_ids = sorted({str(entry.get("brief_id", "")) for entry in entries if entry.get("brief_id")})
requirements = {
    "target_epoch": int(target_epoch),
    "articles": [{
        "article_id": article_id,
        "hotkey": str(entry.get("hotkey", "")),
        "brief_id": str(entry.get("brief_id", "")),
        "outlet_id": str(entry.get("outlet_id", "")),
        "url": str(entry.get("url", "")),
        "min_earned_microusd": int(round(float(entry.get("installment_usd", 0)) * 1_000_000)),
    } for article_id, entry in sorted(entry_items)],
    "miners": sorted({str(entry.get("hotkey", "")) for entry in entries if entry.get("hotkey")}),
    "briefs": [{
        "brief_id": brief_id,
        "min_pool_spent_microusd": int(round(float(pool_spent.get(brief_id, 0)) * 1_000_000)),
    } for brief_id in brief_ids],
}
print(json.dumps({
    "epoch": epoch,
    "vesting_entries": len(entries),
    "uids": sorted({int(entry["uid"]) for entry in entries if "uid" in entry}),
    "pool_spent_usd": sum(float(pool_spent.get(brief_id, 0)) for brief_id in brief_ids),
    "requirements": requirements,
}, separators=(",", ":")))
PY
  } 2>/dev/null)"
  scored_epoch="$(jq -r '.epoch' <<<"$canary_summary")"
  if (( scored_epoch < target_epoch )); then
    echo "Refusing rollout: canary scored epoch $scored_epoch, target is $target_epoch" >&2
    exit 1
  fi
  min_entries="${CANARY_MIN_VESTING_ENTRIES:-0}"
  actual_entries="$(jq -r '.vesting_entries' <<<"$canary_summary")"
  if (( actual_entries < min_entries )); then
    echo "Refusing rollout: canary has $actual_entries vesting entries, requires $min_entries" >&2
    exit 1
  fi
  if [[ -n "${CANARY_REQUIRED_UIDS:-}" ]]; then
    IFS=',' read -ra required_uids <<<"$CANARY_REQUIRED_UIDS"
    for uid in "${required_uids[@]}"; do
      if ! jq -e --argjson uid "$uid" '.uids | index($uid) != null' <<<"$canary_summary" >/dev/null; then
        echo "Refusing rollout: canary state is missing required uid $uid" >&2
        exit 1
      fi
    done
  fi
  min_pool="${CANARY_MIN_POOL_SPENT_USD:-0}"
  actual_pool="$(jq -r '.pool_spent_usd' <<<"$canary_summary")"
  if ! awk -v actual="$actual_pool" -v minimum="$min_pool" 'BEGIN { exit !(actual >= minimum) }'; then
    echo "Refusing rollout: canary pool spend is $actual_pool, requires at least $min_pool" >&2
    exit 1
  fi
  if ! jq -e '.requirements.articles | length > 0 and all(.[];
      (.url | startswith("https://")) and (.url | contains("localhost") | not)
      and (.outlet_id | length > 0))' <<<"$canary_summary" >/dev/null; then
    echo "Refusing rollout: canary articles are not canonical signed-registry URLs" >&2
    exit 1
  fi
  mkdir -p "$runtime_dir"
  requirements_tmp="${requirements_file}.tmp"
  jq '.requirements' <<<"$canary_summary" > "$requirements_tmp"
  mv "$requirements_tmp" "$requirements_file"
  echo "Canary guard passed: $canary_summary"
elif [[ "${CONFIRM_VALIDATOR_ROLLOUT:-}" != "yes" ]]; then
  echo "Set CANARY_TARGET_EPOCH or CONFIRM_VALIDATOR_ROLLOUT=yes to authorize the handoff" >&2
  exit 1
fi

# Independent preflight: local signature plus the finalized on-chain owner commitment.
.venv/bin/python -m herald.registry.admin verify-live-anchor \
  "$HERALD_REGISTRY_PATH" \
  --pubkey "$HERALD_REGISTRY_PUBKEY" \
  --authority "$HERALD_REGISTRY_AUTHORITY_HOTKEY" \
  --netuid "$NETUID" \
  --network "$SUBTENSOR_NETWORK"

# Both validators must fetch and verify the same signed brief payload before either starts.
.venv/bin/python -c 'from herald.validator.utils.briefs import get_briefs; print(f"signed briefs: {len(get_briefs(all=True))}")'

case "$SUBTENSOR_NETWORK" in
  test) process_network="testnet" ;;
  *) process_network="$SUBTENSOR_NETWORK" ;;
esac

log_root="$(dirname "$ROOT")/herald-runtime/netuid${NETUID}/logs"
sim_env=""
if [[ -n "${HERALD_SIM_PROVIDER_BASE:-}" ]]; then
  sim_base="${HERALD_SIM_PROVIDER_BASE%/}"
  curl -fsS "${sim_base}/" >/dev/null
  sim_env="export HERALD_NYT_API_BASE='${sim_base}/nyt-api/svc/search/v2/articlesearch.json' HERALD_SCRAPINGBEE_BASE='${sim_base}/scrapingbee/api/v1' HERALD_SERPAPI_BASE='${sim_base}/serpapi/search.json' HERALD_BRAVE_BASE='${sim_base}/brave/res/v1/web/search' SCRAPINGBEE_API_KEY=sim SERPAPI_API_KEY=sim BRAVE_API_KEY=sim HERALD_NYT_API_KEY=sim &&"
fi

start_validator() {
  local process="$1"
  local wallet="$2"
  local hotkey="$3"
  local slot="$4"
  local command
  command="cd '$ROOT' && set -a && source .env && set +a && unset HERALD_ALLOW_LOCAL_FETCH && ${sim_env} export HERALD_VALIDATOR_STEPS_INTERVAL=1 HERALD_VALIDATOR_WAIT=60 WANDB_MODE=disabled PYTHONUNBUFFERED=1 && exec .venv/bin/python neurons/validator.py --netuid '$NETUID' --subtensor.network '$SUBTENSOR_NETWORK' --wallet.name '$wallet' --wallet.hotkey '$hotkey' --neuron.name 'herald-netuid${NETUID}-v3-${slot}' --neuron.axon_off --neuron.disable_auto_update --neuron.dont_save_events --logging.logging_dir '$log_root'"

  pm2 delete "$process" >/dev/null 2>&1 || true
  pm2 start /usr/bin/bash --name "$process" --interpreter none -- -lc "$command"
}

start_validator "herald_${process_network}_${NETUID}_validator_v1" \
  "$VALIDATOR1_NAME" "$VALIDATOR1_HOTKEY" "v1"
start_validator "herald_${process_network}_${NETUID}_validator_v2" \
  "$VALIDATOR2_NAME" "$VALIDATOR2_HOTKEY" "v2"

pm2 save
echo "Validator quorum rollout started for ${SUBTENSOR_NETWORK} netuid ${NETUID}."
