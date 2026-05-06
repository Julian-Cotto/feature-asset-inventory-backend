from __future__ import annotations

import json
from pathlib import Path


def _require_authorization(manifest: dict) -> dict:
    authorization = manifest.get("authorization")

    if not isinstance(authorization, dict):
        raise ValueError("Resolved manifest is missing authorization block.")

    required_permissions = authorization.get("requiredPermissions")
    required_flags = authorization.get("requiredFlags")

    if not isinstance(required_permissions, list):
        raise ValueError("authorization.requiredPermissions must be a list.")

    if not isinstance(required_flags, list):
        raise ValueError("authorization.requiredFlags must be a list.")

    return authorization


def main() -> int:
    manifest_path = Path("build/feature-manifest.resolved.json")
    output_path = Path("build/registry-payload.json")

    if not manifest_path.exists():
        raise FileNotFoundError(f"Resolved manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    _require_authorization(manifest)

    payload = {
        "featureKey": manifest["featureKey"],
        "version": manifest["version"],
        "environment": manifest.get("environment", "dev"),
        "manifest": manifest,
    }

    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Registry payload written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())