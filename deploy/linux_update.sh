#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${LIFEHUB_APP_DIR:-/opt/lifehub}"
BRANCH="${LIFEHUB_GIT_BRANCH:-main}"
SERVICE_NAME="${LIFEHUB_SERVICE_NAME:-lifehub}"
FORCE_UPDATE="${1:-}"
REPO_URL="${LIFEHUB_REPO_URL:-https://github.com/junjiezxl/life.git}"
MIRROR_URLS="${LIFEHUB_GIT_MIRRORS:-https://gh-proxy.com/${REPO_URL} https://ghfast.top/${REPO_URL} ${REPO_URL}}"

if [ ! -d "$APP_DIR/.git" ]; then
  echo "[lifehub-updater] Git repo not found at $APP_DIR" >&2
  exit 1
fi

cd "$APP_DIR"
git config --global --add safe.directory "$APP_DIR" >/dev/null 2>&1 || true

LOCAL_SHA="$(git rev-parse HEAD)"

FETCHED=0
for url in $MIRROR_URLS; do
  if GIT_TERMINAL_PROMPT=0 timeout 25 git fetch --quiet "$url" "$BRANCH"; then
    FETCHED=1
    break
  fi
done

if [ "$FETCHED" != "1" ]; then
  echo "[lifehub-updater] Failed to fetch branch $BRANCH from all remotes" >&2
  exit 1
fi

REMOTE_SHA="$(git rev-parse FETCH_HEAD)"

NEED_RESTART=0
REBUILD_FRONTEND=0
UPDATE_PYTHON=0

if [ "$LOCAL_SHA" != "$REMOTE_SHA" ] || [ "$FORCE_UPDATE" = "--force" ] || [ ! -f "$APP_DIR/.build_meta.json" ] || [ ! -f "$APP_DIR/frontend/dist/index.html" ]; then
  echo "[lifehub-updater] Updating $LOCAL_SHA -> $REMOTE_SHA"
  CHANGED_FILES=""
  if [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
    CHANGED_FILES="$(git diff --name-only "$LOCAL_SHA" "$REMOTE_SHA" || true)"
    git reset --hard "$REMOTE_SHA"
    NEED_RESTART=1
  fi

  if [ ! -x "$APP_DIR/backend/.venv/bin/uvicorn" ]; then
    python3 -m venv "$APP_DIR/backend/.venv"
    UPDATE_PYTHON=1
  elif echo "$CHANGED_FILES" | grep -q "^backend/requirements.txt"; then
    UPDATE_PYTHON=1
  fi

  if [ "$UPDATE_PYTHON" = "1" ]; then
    PIP_BIN="$APP_DIR/backend/.venv/bin/pip"
    "$PIP_BIN" install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple -q || "$PIP_BIN" install --upgrade pip -q
    "$PIP_BIN" install -r "$APP_DIR/backend/requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple -q || "$PIP_BIN" install -r "$APP_DIR/backend/requirements.txt" -q
    NEED_RESTART=1
  fi

  if [ ! -f "$APP_DIR/frontend/dist/index.html" ] || echo "$CHANGED_FILES" | grep -q "^frontend/"; then
    REBUILD_FRONTEND=1
  fi

  if [ "$REBUILD_FRONTEND" = "1" ]; then
    cd "$APP_DIR/frontend"
    export npm_config_registry="${NPM_REGISTRY:-https://registry.npmmirror.com}"
    npm ci --no-audit --no-fund --silent || npm install --no-audit --no-fund --silent
    npm run build
    cd "$APP_DIR"
    NEED_RESTART=1
  fi

  if echo "$CHANGED_FILES" | grep -q "^deploy/nginx-lifehub.conf.template"; then
    if [ -f "/etc/nginx/sites-available/lifehub.conf" ]; then
      cp "$APP_DIR/deploy/nginx-lifehub.conf.template" /etc/nginx/sites-available/lifehub.conf
      if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx || true
      fi
    fi
  fi

  COUNT="$(git rev-list --count HEAD)"
  PATCH=$((COUNT - 31))
  if [ "$PATCH" -lt 0 ]; then PATCH=0; fi
  NEW_VERSION="v1.0.${PATCH}"
  SHORT_SHA="$(git rev-parse --short=7 HEAD)"
  BUILD_TIME="$(TZ='Asia/Shanghai' date '+%Y-%m-%d %H:%M:%S')"
  COMMIT_MSG="$(git log -1 --pretty=%s)"

  python3 - "$APP_DIR/.build_meta.json" "$NEW_VERSION" "$SHORT_SHA" "$BUILD_TIME" "$COMMIT_MSG" << 'PYEOF'
import json, sys
meta = {
    "version": sys.argv[2],
    "sha": sys.argv[3],
    "build_time": sys.argv[4],
    "commit_msg": sys.argv[5],
}
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(meta, fh, ensure_ascii=False, indent=2)
PYEOF

  if [ "$NEED_RESTART" = "1" ] && command -v systemctl >/dev/null 2>&1; then
    echo "[lifehub-updater] Restarting $SERVICE_NAME ($NEW_VERSION @ $SHORT_SHA)"
    systemctl restart "$SERVICE_NAME"
  fi
fi
