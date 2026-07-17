#!/bin/bash

# Exit on error
set -e

# Get the absolute path of the project root
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_PARENT="$(cd "$PROJECT_ROOT/.." && pwd)"

MINER_PROCESS_NAME="herald_miner"

ENV_FILE=${HERALD_MINER_ENV_FILE:-"$PROJECT_ROOT/.env"}
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: environment file not found at $ENV_FILE"
    exit 1
fi
echo "Loading environment variables from $ENV_FILE..."
set -a
source "$ENV_FILE"
set +a

VENV_PATH=${VENV_PATH:-"$PROJECT_PARENT/venv_herald"}

# Activate virtual environment
if [ ! -d "$VENV_PATH" ]; then
    echo "Error: Virtual environment not found at $VENV_PATH"
    echo "Please run setup_env.sh first"
    exit 1
fi

echo "Activating virtual environment..."
source "$VENV_PATH/bin/activate"

cd "$PROJECT_ROOT"

# Default values for optional parameters
SUBTENSOR_CHAIN_ENDPOINT=${SUBTENSOR_CHAIN_ENDPOINT:-"wss://entrypoint-finney.opentensor.ai:443"}
SUBTENSOR_NETWORK=${SUBTENSOR_NETWORK:-"finney"}
PORT=${PORT:-8091}
LOGGING=${LOGGING:-"--logging.debug"}
DEV_MODE=${DEV_MODE:-false}
DISABLE_AUTO_UPDATE=${DISABLE_AUTO_UPDATE:-true}

# Handle boolean flags
DEV_MODE_FLAG=""
if [ "${DEV_MODE,,}" = "true" ]; then
    DEV_MODE_FLAG="--dev_mode"
fi

DISABLE_AUTO_UPDATE_FLAG=""
if [ "${DISABLE_AUTO_UPDATE,,}" = "true" ]; then
    DISABLE_AUTO_UPDATE_FLAG="--neuron.disable_auto_update"
fi

# Check if required environment variables are set
if [ -z "${NETUID:-}" ] || [ -z "${WALLET_NAME:-}" ] || [ -z "${HOTKEY_NAME:-}" ]; then
    echo "Error: Required environment variables NETUID, WALLET_NAME, and HOTKEY_NAME must be set in .env file"
    exit 1
fi

# STOP MINER PROCESS
if pm2 list | grep -q "$MINER_PROCESS_NAME"; then
    echo "Replacing existing '$MINER_PROCESS_NAME' process with the current configuration..."
    pm2 delete "$MINER_PROCESS_NAME"
fi

AXON_ARGS=(--axon.port "$PORT")
if [ -n "${AXON_EXTERNAL_IP:-}" ]; then
    AXON_ARGS+=(--axon.external_ip "$AXON_EXTERNAL_IP")
fi
if [ -n "${AXON_EXTERNAL_PORT:-}" ]; then
    AXON_ARGS+=(--axon.external_port "$AXON_EXTERNAL_PORT")
fi

pm2 start python --name "$MINER_PROCESS_NAME" -- neurons/miner.py \
    --netuid "$NETUID" \
    --subtensor.chain_endpoint "$SUBTENSOR_CHAIN_ENDPOINT" \
    --subtensor.network "$SUBTENSOR_NETWORK" \
    --wallet.name "$WALLET_NAME" \
    --wallet.hotkey "$HOTKEY_NAME" \
    "${AXON_ARGS[@]}" \
    $LOGGING $DEV_MODE_FLAG $DISABLE_AUTO_UPDATE_FLAG
