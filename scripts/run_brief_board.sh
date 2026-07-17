#!/bin/bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_PARENT="$(cd "$PROJECT_ROOT/.." && pwd)"
NAME="herald_brief_board"

ENV_FILE=${HERALD_BRIEF_BOARD_ENV_FILE:-"$PROJECT_ROOT/.env"}
if [ -f "$ENV_FILE" ]; then
  set -a
  source "$ENV_FILE"
  set +a
fi
VENV_PATH=${VENV_PATH:-"$PROJECT_PARENT/venv_herald"}

if [ "${HERALD_ENABLE_LEGACY_BRIEF_BOARD:-false}" != "true" ]; then
  echo "Error: the JSON Brief Board is legacy-only; use herald-backend or set HERALD_ENABLE_LEGACY_BRIEF_BOARD=true for development/migration"
  exit 1
fi

if [ ! -d "$VENV_PATH" ]; then
  echo "Error: virtualenv not found at $VENV_PATH; run setup_env.sh first"
  exit 1
fi
source "$VENV_PATH/bin/activate"
cd "$PROJECT_ROOT"

PORT=${BRIEF_BOARD_PORT:-8093}
if pm2 list | grep -q "$NAME"; then
  pm2 delete "$NAME"
fi
pm2 start uvicorn --name "$NAME" -- herald.services.legacy:app --host 0.0.0.0 --port "$PORT"
