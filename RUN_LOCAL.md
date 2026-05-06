# IT Asset Inventory — Local Run Guide

Feature key: `asset-inventory`
Route: `/inventory/it`
Backend port: **8200**
Frontend port: **3200** (Vite dev server)

## 1. Backend (FastAPI + SQLite stub)

This repo. Run from its root:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # if not present; edit AUTH_MODE=mock for local
AUTH_MODE=mock APP_ENVIRONMENT=local uvicorn app.main:app --reload --host 0.0.0.0 --port 8200
```

On startup the app:
- creates SQLite tables (`data/asset_inventory.db`)
- seeds default statuses: `in_warehouse`, `assigned`, `in_repair`, `lost`, `retired`

Health: <http://localhost:8200/api/inventory/it/health>
OpenAPI: <http://localhost:8200/docs>

### Mock auth headers (for direct curl)

```
Authorization: Bearer dev-token
X-Debug-User-Id: dev
X-Debug-User-Name: dev@local
X-Debug-Roles: admin
```

### Quick smoke test

```bash
curl -s -H 'Authorization: Bearer x' -H 'X-Debug-Roles: admin' \
  http://localhost:8200/api/inventory/it/statuses | jq
```

## 2. Frontend (standalone dev — no shell)

Frontend lives at `../feature-asset-inventory-frontend/` (sibling repo).

```bash
cd ../feature-asset-inventory-frontend
npm install
npm run dev
```

Open <http://localhost:3200/>. `src/main.tsx` injects a dev shell-auth context
(role: admin, accessToken: dev-token) when `import.meta.env.DEV`.

## 3. Run inside the shell

The shell loads features via the bootstrap-api → registry chain. In order:

1. **Registry service** running on :8010 (see `app-platform-registry-service/`)
2. **Shell bootstrap-api** running on :8000/8001 (see `app-platform-shell-bootstrap-api/`)
3. **Shell** running on :3000 (see `app-platform-shell/`)

Then publish + activate this feature's manifest:

```bash
# Publish
curl -X POST http://localhost:8010/api/releases \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <release-write-token>' \
  -d @contracts/feature-manifest.local.json

# Activate for local environment
curl -X POST 'http://localhost:8010/api/admin/features/asset-inventory/versions/0.1.0/activate?environment=local' \
  -H 'Authorization: Bearer <admin-token>'
```

Refresh shell — feature appears in nav as **IT Inventory** under group **Inventory**.

## 4. Snowflake DDL (production target)

`infra/snowflake/schema.sql` creates the production tables under
`test.test.*`. Run as a role with `USAGE` on database `test`, schema `test`,
and `CREATE TABLE`. Idempotent: uses `CREATE TABLE IF NOT EXISTS` and
`MERGE` for status seeding. Re-run to add new statuses defined in the file.

```bash
snowsql -a <account> -u <user> -d test -s test -f infra/snowflake/schema.sql
```

To switch the backend from SQLite stub to Snowflake later, update
`DATABASE_URL` to a Snowflake SQLAlchemy URL (requires
`snowflake-sqlalchemy` package) and disable `db_create_all_on_startup` /
`db_seed_default_statuses`.

## Permissions / role mapping

Configured in `app/config.py`:

| Role       | Permissions                                                             |
| ---------- | ----------------------------------------------------------------------- |
| `admin`    | `*`                                                                     |
| `developer`| `asset-inventory.view`, `.write`, `.manage`                              |
| `operator` | `asset-inventory.view`, `.write`                                         |
| `reader`   | `asset-inventory.view`                                                   |

Endpoints require:
- `view` — list/get assets, locations, statuses, history, lookup
- `write` — onboard, update, assign/unassign, status change, archive
- `manage` — locations CRUD, statuses CRUD

`assigned_upn` is captured from the `RequestAuthContext.user_name` (UPN
from the forwarded Entra token) at assign time.

## Known scaffold artifacts

- `app/api/platform_capabilities.py` and `app/api/cache_capabilities.py`
  contain unrendered Jinja placeholders (scaffold template bug). They
  are **not** wired in `app/main.py`. Delete them or fix the templates
  before re-introducing.
- `../feature-asset-inventory-frontend/src/tests/App.test.tsx` was emitted
  twice by the scaffold and has duplicate identifiers. TypeScript build
  via `tsc --noEmit` ignores it; vitest will fail until the duplicate
  block is removed.
- `jobs/`, `listeners/`, `workers/` directories are scaffold stubs kept
  intact per project decision; not exercised by the current feature
  surface.
