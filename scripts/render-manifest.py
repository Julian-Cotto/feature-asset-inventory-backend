#!/usr/bin/env python3

import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = ROOT / "contracts"
BUILD_DIR = ROOT / "build"

SOURCE_MANIFEST = CONTRACTS_DIR / "feature-manifest.json"
OUTPUT_MANIFEST = BUILD_DIR / "feature-manifest.resolved.json"


def ensure_build_dir() -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)


def load_manifest() -> dict:
    if not SOURCE_MANIFEST.exists():
        raise SystemExit(f"Missing source manifest: {SOURCE_MANIFEST}")

    with SOURCE_MANIFEST.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_url(value: str) -> str:
    return value.rstrip("/")


def apply_runtime_overrides(manifest: dict) -> dict:
    manifest["environment"] = os.getenv("FEATURE_ENVIRONMENT", "local")

    manifest["route"] = manifest.get("route") or manifest.get("basePath")
    manifest["nav"] = manifest.get("nav") or manifest.get("navigation")

    frontend_entry = os.getenv("FEATURE_FRONTEND_ENTRY_URL")
    if frontend_entry:
        manifest.setdefault("frontend", {})
        manifest["frontend"]["entryUrl"] = frontend_entry

    backend_base = os.getenv("FEATURE_BACKEND_BASE_URL")
    if backend_base:
        backend_base = normalize_url(backend_base)

        manifest.setdefault("backend", {})
        manifest["backend"]["baseUrl"] = backend_base
        manifest["backend"]["apiBaseUrl"] = backend_base
        manifest["backend"]["healthEndpoint"] = f"{backend_base}/health"

    if "metadata" not in manifest:
        manifest["metadata"] = {
            "ownerTeam": "platform",
            "commitSha": None,
            "buildId": None,
            "releaseDate": None,
        }

    return manifest


def main() -> int:
    ensure_build_dir()

    manifest = load_manifest()
    manifest = apply_runtime_overrides(manifest)

    with OUTPUT_MANIFEST.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"Resolved manifest written to {OUTPUT_MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())