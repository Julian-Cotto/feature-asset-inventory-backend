#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$ROOT_DIR/.venv"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

log() {
  echo "[bootstrap] $*"
}

warn() {
  echo "[bootstrap][warn] $*" >&2
}

err() {
  echo "[bootstrap][error] $*" >&2
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    err "Required command not found: $cmd"
    exit 1
  fi
}

log "Bootstrapping IT Asset Inventory"
log "Root directory: $ROOT_DIR"

require_cmd "$PYTHON_BIN"

if [ -d "$FRONTEND_DIR" ]; then
  require_cmd npm
fi

if [ -d "$BACKEND_DIR" ]; then
  if [ ! -d "$VENV_DIR" ]; then
    log "Creating virtual environment..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  else
    log "Virtual environment already exists."
  fi

  VENV_PYTHON="$VENV_DIR/bin/python"
  VENV_PIP="$VENV_DIR/bin/pip"

  if [ ! -x "$VENV_PYTHON" ]; then
    err "Virtual environment Python not found at $VENV_PYTHON"
    exit 1
  fi

  log "Upgrading pip..."
  "$VENV_PYTHON" -m pip install --upgrade pip wheel setuptools

  if [ -f "$BACKEND_DIR/pyproject.toml" ]; then
    log "Installing backend package in editable mode with dev dependencies..."
    (
      cd "$BACKEND_DIR"
      "$VENV_PIP" install -e ".[dev]"
    )
  elif [ -f "$ROOT_DIR/pyproject.toml" ]; then
    log "Installing project package in editable mode with dev dependencies from repo root..."
    (
      cd "$ROOT_DIR"
      "$VENV_PIP" install -e ".[dev]"
    )
  elif [ -f "$BACKEND_DIR/requirements.txt" ]; then
    log "Installing backend requirements..."
    "$VENV_PIP" install -r "$BACKEND_DIR/requirements.txt"
  else
    warn "No backend dependency file found."
  fi
fi

if [ -d "$FRONTEND_DIR" ]; then
  if [ -f "$FRONTEND_DIR/package-lock.json" ]; then
    log "Installing frontend dependencies with npm ci..."
    (
      cd "$FRONTEND_DIR"
      npm ci
    )
  elif [ -f "$FRONTEND_DIR/package.json" ]; then
    log "Installing frontend dependencies with npm install..."
    (
      cd "$FRONTEND_DIR"
      npm install
    )
  else
    warn "No frontend package.json found."
  fi
fi

copy_env_if_missing() {
  local target_dir="$1"

  if [ ! -f "$target_dir/.env" ]; then
    if [ -f "$target_dir/.env.example" ]; then
      log "Creating .env from .env.example in $target_dir"
      cp "$target_dir/.env.example" "$target_dir/.env"
    else
      warn "No .env.example found in $target_dir"
    fi
  else
    log ".env already exists in $target_dir"
  fi
}

log "Bootstrapping feature project..."

# ----------------------------
# Ensure .env files exist
# ----------------------------

copy_env_if_missing "$ROOT_DIR/backend"
copy_env_if_missing "$ROOT_DIR/frontend"


echo
log "Bootstrap complete."
log "Next steps:"
log "  1. Start the feature locally:"
log "     ./scripts/run-local.sh"
log "  2. Start the shell separately."
echo