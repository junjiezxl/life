#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
export PATH="/Users/junjie/.nvm/versions/node/v22.23.1/bin:$PATH"

if [ ! -d "$ROOT_DIR/frontend/dist" ]; then
  echo "Building frontend..."
  (cd "$ROOT_DIR/frontend" && npm install && npm run build)
fi

echo "Starting LifeHub on http://127.0.0.1:8000 ..."
cd "$ROOT_DIR/backend"
exec "$ROOT_DIR/backend/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000
