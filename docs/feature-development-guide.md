# Feature Development Guide

- Feature key: `asset-inventory`
- Base path: `/inventory/it`
- API base path: `/api/inventory/it`

This feature follows the platform-integrated pattern:
- microfrontend
- backend
- scheduled jobs
- event-driven workers
- event listeners

## Local development

Create two environment files from the examples (they are gitignored once copied):

1. Copy `.env.example` to `.env` at the **repository root** (same level as `backend/`). The API loads this via `backend/app/config.py` (`pydantic-settings`).
2. Copy `frontend/.env.example` to **`frontend/.env.local`**. Vite reads `.env.local` during `npm run dev` / `vite`.

### Repository root `.env`

Example contents:

```env
FEATURE_KEY=asset-inventory
FEATURE_BASE_PATH=/inventory/it
FEATURE_API_BASE_PATH=/api/inventory/it
AUTH_MODE=mock
REGISTRY_MODE=rest
LOCAL_FRONTEND_URL=http://localhost:3100
LOCAL_BACKEND_URL=http://localhost:8000/api/inventory/it
ALLOWED_ORIGINS_RAW=http://localhost:3000,http://localhost:3100,http://localhost:5173
AUTH_DEBUG_HEADERS_ENABLED=true
```

| Variable | Explanation |
| --- | --- |
| `FEATURE_KEY` | Feature identifier; matches platform registry and deploy configuration. |
| `FEATURE_BASE_PATH` | Browser route prefix for the microfrontend (reference for scripts and docs; same value as in the manifest). |
| `FEATURE_API_BASE_PATH` | HTTP path prefix for this feature’s API (matches container env in Terraform). |
| `AUTH_MODE` | `mock` for local development without Entra (debug headers accepted); use `entra` when validating tokens. `none` disables auth checks where supported. |
| `REGISTRY_MODE` | How the feature resolves shell/registry metadata locally (e.g. `rest`). |
| `LOCAL_FRONTEND_URL` | Where the microfrontend is expected during local runs (used by scripts and documentation). |
| `LOCAL_BACKEND_URL` | Full base URL for the feature API when calling from tooling (scheme + host + port + `FEATURE_API_BASE_PATH`). |
| `ALLOWED_ORIGINS_RAW` | Comma-separated browser origins allowed by the backend CORS middleware. Include every Vite dev origin you use (for example `http://localhost:5173` and the port from `scripts/run-local`). |
| `AUTH_DEBUG_HEADERS_ENABLED` | When `true` and `AUTH_MODE` is `mock`, the API trusts `X-Debug-*` headers for a synthetic user (see `backend/app/config.py`). |

Adjust ports and origins to match how you start the shell and Vite. For Entra-backed local testing, set `AUTH_MODE=entra` and fill the `ENTRA_*` variables in `backend/app/config.py` via the same `.env` file using the names documented there.

### Frontend `frontend/.env.local`

Example contents:

```env
VITE_API_BASE_URL=http://localhost:8000/api/inventory/it
VITE_AUTH_MODE=mock
```

| Variable | Explanation |
| --- | --- |
| `VITE_API_BASE_URL` | Absolute base URL for `fetch` in `frontend/src/services/apiClient.ts` (must include scheme, host, port, and the feature API prefix). Must point at the running backend. |
| `VITE_AUTH_MODE` | Should match backend `AUTH_MODE` so the client sends mock debug headers only when the API expects them. |

Vite only exposes variables prefixed with `VITE_`. After changing `.env.local`, restart the dev server.