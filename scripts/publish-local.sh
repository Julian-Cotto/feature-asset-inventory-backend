#!/usr/bin/env bash

set -euo pipefail

FEATURE_KEY="asset-inventory"

# ---- PORT RESOLUTION (SAME AS run-local) ----
if [ "$FEATURE_KEY" = "catalog" ]; then
  FRONTEND_PORT="${FRONTEND_PORT:-3300}"
  BACKEND_PORT="${BACKEND_PORT:-8200}"
else
  FRONTEND_PORT="${FRONTEND_PORT:-3200}"
  BACKEND_PORT="${BACKEND_PORT:-8100}"
fi

API_BASE_PATH="/api/inventory/it"

REGISTRY_URL="${REGISTRY_URL:-http://localhost:8010}"
FEATURE_ENVIRONMENT="${FEATURE_ENVIRONMENT:-local}"

FEATURE_FRONTEND_ENTRY_URL="${FEATURE_FRONTEND_ENTRY_URL:-http://localhost:${FRONTEND_PORT}/src/bootstrap-entry.tsx}"
FEATURE_BACKEND_BASE_URL="${FEATURE_BACKEND_BASE_URL:-http://localhost:${BACKEND_PORT}${API_BASE_PATH}}"

export FEATURE_ENVIRONMENT
export FEATURE_FRONTEND_ENTRY_URL
export FEATURE_BACKEND_BASE_URL

echo "----------------------------------------"
echo "Publishing feature to local registry"
echo "Feature: $FEATURE_KEY"
echo "Environment: $FEATURE_ENVIRONMENT"
echo "Registry: $REGISTRY_URL"
echo "Frontend: $FEATURE_FRONTEND_ENTRY_URL"
echo "Backend:  $FEATURE_BACKEND_BASE_URL"
echo "----------------------------------------"

echo "→ Rendering manifest..."
python scripts/render-manifest.py

echo "→ Validating manifest..."
python scripts/validate-manifest.py

echo "→ Publishing manifest..."

curl -f -sS \
  -X POST "${REGISTRY_URL}/api/releases" \
  -H "Content-Type: application/json" \
  --data-binary @build/feature-manifest.resolved.json

echo "→ Activating feature..."
curl -i \
  -X POST "${REGISTRY_URL}/api/admin/features/${FEATURE_KEY}/versions/0.1.0/activate?environment=${FEATURE_ENVIRONMENT}"

echo ""
echo "✔ Publish complete"