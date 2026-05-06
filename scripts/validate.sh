#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -d "$ROOT_DIR/backend" ] || [ -d "$ROOT_DIR/jobs" ] || [ -d "$ROOT_DIR/workers" ] || [ -d "$ROOT_DIR/listeners" ]; then
  echo "Running backend and Python component tests..."
  source "$ROOT_DIR/.venv/bin/activate"

  TEST_PATHS=()

  if [ -d "$ROOT_DIR/backend/tests" ]; then
    TEST_PATHS+=("$ROOT_DIR/backend/tests")
  fi

  # Jobs / workers / listeners: one pytest target per package dir so relative imports (from .config) work.
  for kind in jobs workers listeners; do
    base="$ROOT_DIR/$kind"
    [ -d "$base" ] || continue
    for pkg in "$base"/*; do
      [ -d "$pkg" ] || continue
      shopt -s nullglob
      tests=( "$pkg"/test_*.py )
      shopt -u nullglob
      if [ "${#tests[@]}" -gt 0 ]; then
        TEST_PATHS+=("$pkg")
      fi
    done
  done

  if [ "${#TEST_PATHS[@]}" -gt 0 ]; then
    PYTHONPATH="$ROOT_DIR/backend:$ROOT_DIR" pytest "${TEST_PATHS[@]}"
  else
    echo "No Python test directories found (backend/tests or job/worker/listener packages with test_*.py)."
  fi
fi

if [ -d "$ROOT_DIR/frontend" ]; then
  echo "Running frontend tests..."
  cd "$ROOT_DIR/frontend"
  npm test
fi

echo "Validation complete."