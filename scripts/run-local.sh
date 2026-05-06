#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BACKEND_PID=""
BOOTSTRAP_PID=""
FRONTEND_PID=""

BACKEND_PORT="${BACKEND_PORT:-8100}"
BOOTSTRAP_PORT="${BOOTSTRAP_PORT:-3050}"
FRONTEND_PORT="${FRONTEND_PORT:-3200}"
REGISTRY_URL="${REGISTRY_URL:-http://localhost:8010}"
PUBLISH_LOCAL_ENABLED="${PUBLISH_LOCAL_ENABLED:-true}"

export BACKEND_PORT BOOTSTRAP_PORT FRONTEND_PORT REGISTRY_URL

API_BASE_PATH="/api/inventory/it"
BACKEND_HEALTH_PATH="${BACKEND_HEALTH_PATH:-${API_BASE_PATH}/health}"
BOOTSTRAP_HEALTH_PATH="${BOOTSTRAP_HEALTH_PATH:-/bootstrap}"

FEATURE_ENVIRONMENT="${FEATURE_ENVIRONMENT:-local}"
FEATURE_FRONTEND_ENTRY_URL="${FEATURE_FRONTEND_ENTRY_URL:-http://localhost:${FRONTEND_PORT}/src/bootstrap-entry.tsx}"
FEATURE_BACKEND_BASE_URL="${FEATURE_BACKEND_BASE_URL:-http://localhost:${BACKEND_PORT}${API_BASE_PATH}}"

export FEATURE_ENVIRONMENT
export FEATURE_FRONTEND_ENTRY_URL
export FEATURE_BACKEND_BASE_URL

PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_DIR="$ROOT_DIR/backend"
SCRIPTS_DIR="$ROOT_DIR/scripts"
BOOTSTRAP_SCRIPT="$SCRIPTS_DIR/serve-bootstrap-mock.py"
PUBLISH_SCRIPT="$SCRIPTS_DIR/publish-local.sh"

log() {
  echo "[run-local] $*"
}

warn() {
  echo "[run-local][warn] $*" >&2
}

err() {
  echo "[run-local][error] $*" >&2
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    err "Required command not found: $cmd"
    exit 1
  fi
}

wait_for_url() {
  local name="$1"
  local url="$2"
  local attempts="${3:-30}"
  local delay="${4:-1}"

  i=1
  while [ "$i" -le "$attempts" ]; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "$name is ready at $url"
      return 0
    fi
    sleep "$delay"
    i=$((i + 1))
  done

  warn "Timed out waiting for $name at $url"
  return 1
}

kill_pid() {
  local pid="$1"
  if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    wait "$pid" 2>/dev/null || true
  fi
}

publish_local_manifest() {
  if [ "$PUBLISH_LOCAL_ENABLED" != "true" ]; then
    log "Local registry publish disabled. Set PUBLISH_LOCAL_ENABLED=true to enable."
    return 0
  fi

  if [ ! -f "$PUBLISH_SCRIPT" ]; then
    warn "Publish script not found at $PUBLISH_SCRIPT"
    return 0
  fi

  log "Publishing local feature manifest to registry..."
  log "Registry: $REGISTRY_URL"
  log "Feature frontend entry: $FEATURE_FRONTEND_ENTRY_URL"
  log "Feature backend base URL: $FEATURE_BACKEND_BASE_URL"

  if bash "$PUBLISH_SCRIPT"; then
    log "Local feature manifest published."
  else
    warn "Local registry publish failed. Feature services are still running."
    warn "Check that registry is running at $REGISTRY_URL."
  fi
}

cleanup() {
  local exit_code=$?

  log "Stopping local services..."
  kill_pid "$FRONTEND_PID"
  kill_pid "$BOOTSTRAP_PID"
  kill_pid "$BACKEND_PID"

  exit "$exit_code"
}

trap cleanup EXIT INT TERM

log "Starting local development for IT Asset Inventory"
log "Root directory: $ROOT_DIR"

require_cmd curl

if [ -d "$BACKEND_DIR" ] || [ -f "$BOOTSTRAP_SCRIPT" ]; then
  if [ ! -x "$PYTHON_BIN" ]; then
    err "Python virtual environment not found or not executable: $PYTHON_BIN"
    err "Create it first, for example:"
    err "  python -m venv .venv"
    err "  .venv/bin/pip install -e \"./backend[dev]\""
    exit 1
  fi
fi

if [ -d "$FRONTEND_DIR" ]; then
  require_cmd npm
  if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    warn "frontend/node_modules not found."
    warn "Run: cd \"$FRONTEND_DIR\" && npm install"
  fi
fi

if [ -d "$BACKEND_DIR" ]; then
  log "Starting backend on port $BACKEND_PORT ..."
  (
    cd "$BACKEND_DIR"
    PYTHONPATH="$BACKEND_DIR" \
    "$PYTHON_BIN" -m uvicorn app.main:app \
      --app-dir "$BACKEND_DIR" \
      --host 0.0.0.0 \
      --port "$BACKEND_PORT"
  ) &
  BACKEND_PID=$!
  log "Backend PID: $BACKEND_PID"
else
  warn "No backend directory found at $BACKEND_DIR"
fi

if [ -f "$BOOTSTRAP_SCRIPT" ]; then
  log "Starting bootstrap mock on port $BOOTSTRAP_PORT ..."
  (
    cd "$ROOT_DIR"
    "$PYTHON_BIN" "$BOOTSTRAP_SCRIPT"
  ) &
  BOOTSTRAP_PID=$!
  log "Bootstrap PID: $BOOTSTRAP_PID"
else
  warn "No bootstrap mock found at $BOOTSTRAP_SCRIPT"
fi

if [ -d "$FRONTEND_DIR" ]; then
  log "Starting frontend dev server ..."
  (
    cd "$FRONTEND_DIR"
    npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT"
  ) &
  FRONTEND_PID=$!
  log "Frontend PID: $FRONTEND_PID"
else
  warn "No frontend directory found at $FRONTEND_DIR"
fi

if [ -n "$BACKEND_PID" ]; then
  if ! wait_for_url "Backend" "http://localhost:${BACKEND_PORT}${BACKEND_HEALTH_PATH}" 30 1; then
    err "Backend failed readiness check."
    exit 1
  fi
fi

if [ -n "$BOOTSTRAP_PID" ]; then
  if ! wait_for_url "Bootstrap" "http://localhost:${BOOTSTRAP_PORT}${BOOTSTRAP_HEALTH_PATH}" 30 1; then
    err "Bootstrap failed readiness check."
    exit 1
  fi
fi

if [ -n "$FRONTEND_PID" ]; then
  if ! wait_for_url "Frontend" "http://localhost:${FRONTEND_PORT}" 30 1; then
    err "Frontend failed readiness check."
    exit 1
  fi
fi

publish_local_manifest

echo
log "Local services started."
if [ -n "$BACKEND_PID" ]; then
  log "Backend:   http://localhost:${BACKEND_PORT}"
fi
if [ -n "$BOOTSTRAP_PID" ]; then
  log "Bootstrap: http://localhost:${BOOTSTRAP_PORT}/bootstrap"
fi
if [ -n "$FRONTEND_PID" ]; then
  log "Frontend:  http://localhost:${FRONTEND_PORT}"
fi
log "Registry:  $REGISTRY_URL"
log "Publish:   PUBLISH_LOCAL_ENABLED=$PUBLISH_LOCAL_ENABLED"
log "Shell runs separately."
log "Press Ctrl+C to stop."
echo

wait