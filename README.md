# IT Asset Inventory

Platform-integrated feature module for the portal architecture.

## Included components

- Frontend: enabled
- Backend: enabled
- Scheduled jobs: 1
- Event-driven jobs: 1
- Event listeners: 1

## Local development

### Prerequisites

- Python 3.11+
- Node.js 20+
- npm

### Bootstrap the repository

Linux/macOS:

```bash
./scripts/bootstrap.sh
```

Windows:

```powershell
.\scripts\bootstrap.ps1
```

### Run the local stack

After bootstrap, start the backend, frontend, and bootstrap mock (used for local integration):

Linux/macOS:

```bash
./scripts/run-local.sh
```

Windows:

```powershell
.\scripts\run-local.ps1
```

The bootstrap mock serves `http://localhost:3050/bootstrap` by default (override with `BOOTSTRAP_PORT`). The shell runs separately; this repository does not start the shell automatically.