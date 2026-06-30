#!/bin/bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_PARENT="$(cd "$PROJECT_ROOT/.." && pwd)"
VENV_PATH=${VENV_PATH:-"$PROJECT_PARENT/venv_herald"}
NAME="herald_brief_board"

if [ -f "$PROJECT_ROOT/.env" ]; then
  export $(grep -v '^#' "$PROJECT_ROOT/.env" | sed 's/ *= */=/g' | xargs)
fi

if [ ! -d "$VENV_PATH" ]; then
  echo "Error: virtualenv not found at $VENV_PATH; run setup_env.sh first"
  exit 1
fi
source "$VENV_PATH/bin/activate"
cd "$PROJECT_ROOT"

PORT=${BRIEF_BOARD_PORT:-8093}
if pm2 list | grep -q "$NAME"; then
  pm2 restart "$NAME"
else
  pm2 start uvicorn --name "$NAME" -- herald.services.app:app --host 0.0.0.0 --port "$PORT"
fi
