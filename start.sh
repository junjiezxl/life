#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -d "/Users/junjie/.nvm/versions/node/v22.23.1/bin" ]; then
  export PATH="/Users/junjie/.nvm/versions/node/v22.23.1/bin:$PATH"
fi

if [ ! -x "$ROOT_DIR/backend/.venv/bin/uvicorn" ]; then
  echo "Creating Python virtualenv and installing backend dependencies..."
  python3 -m venv "$ROOT_DIR/backend/.venv"
  "$ROOT_DIR/backend/.venv/bin/pip" install --upgrade pip
  "$ROOT_DIR/backend/.venv/bin/pip" install -r "$ROOT_DIR/backend/requirements.txt"
fi

if [ ! -d "$ROOT_DIR/frontend/dist" ]; then
  echo "Building frontend..."
  (cd "$ROOT_DIR/frontend" && npm install && npm run build)
fi

HOST="${LIFEHUB_HOST:-0.0.0.0}"
PORT="${LIFEHUB_PORT:-8000}"

echo "Starting LifeHub on http://${HOST}:${PORT} ..."
cd "$ROOT_DIR/backend"
exec "$ROOT_DIR/backend/.venv/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT"
