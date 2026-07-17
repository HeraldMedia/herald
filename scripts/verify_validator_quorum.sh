#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a
source .env
set +a

: "${NETUID:?NETUID is required}"
: "${SUBTENSOR_NETWORK:?SUBTENSOR_NETWORK is required}"
: "${HERALD_RESULTS_ENDPOINT:?HERALD_RESULTS_ENDPOINT is required}"
: "${QUORUM_REQUIREMENTS_FILE:?QUORUM_REQUIREMENTS_FILE is required}"

timeout_seconds="${QUORUM_TIMEOUT_SECONDS:-900}"
interval_seconds="${QUORUM_POLL_SECONDS:-15}"

.venv/bin/python -m herald.validator.news.quorum_check \
  --endpoint "$HERALD_RESULTS_ENDPOINT" \
  --network "$SUBTENSOR_NETWORK" \
  --netuid "$NETUID" \
  --requirements "$QUORUM_REQUIREMENTS_FILE" \
  --timeout "$timeout_seconds" \
  --interval "$interval_seconds"
