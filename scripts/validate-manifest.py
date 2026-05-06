from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


def load_schema() -> dict:
    schema_path = Path("src/feature_scaffold/manifest.schema.json")
    if schema_path.exists():
        return json.loads(schema_path.read_text(encoding="utf-8"))

    fallback_schema_path = Path(__file__).resolve().parent.parent / "manifest.schema.json"
    return json.loads(fallback_schema_path.read_text(encoding="utf-8"))


def main() -> int:
    manifest_path = Path("build/feature-manifest.resolved.json")
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    schema = load_schema()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    auth = manifest.get("auth")
    if not isinstance(auth, dict):
        raise ValueError("Manifest auth block is required.")

    if auth.get("mode") not in {"none", "mock", "entra"}:
        raise ValueError("Manifest auth.mode must be one of: none, mock, entra.")

    if not isinstance(auth.get("required"), bool):
        raise ValueError("Manifest auth.required must be boolean.")

    if not isinstance(auth.get("shellAuthRequired"), bool):
        raise ValueError("Manifest auth.shellAuthRequired must be boolean.")

    if not isinstance(auth.get("tokenForwarding"), bool):
        raise ValueError("Manifest auth.tokenForwarding must be boolean.")

    if not isinstance(auth.get("tokenStrategy"), str) or not auth.get("tokenStrategy"):
        raise ValueError("Manifest auth.tokenStrategy must be a non-empty string.")

    if not isinstance(auth.get("allowedDevModes"), list):
        raise ValueError("Manifest auth.allowedDevModes must be an array.")

    if not isinstance(auth.get("roles"), list):
        raise ValueError("Manifest auth.roles must be an array.")

    for mode in auth.get("allowedDevModes", []):
        if mode not in {"none", "mock", "entra"}:
            raise ValueError("Manifest auth.allowedDevModes values must be one of: none, mock, entra.")

    for role in auth.get("roles", []):
        if not isinstance(role, str) or not role.strip():
            raise ValueError("Manifest auth.roles must contain non-empty strings.")

    validator = Draft202012Validator(schema)
    validator.validate(manifest)

    print(f"Manifest validation passed: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
    