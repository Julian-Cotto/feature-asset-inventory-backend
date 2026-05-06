from pydantic import BaseModel
from typing import Any


class RegistryFeatureManifestEnvelope(BaseModel):
    featureKey: str
    version: str
    environment: str
    manifest: dict[str, Any]


class RegistryFeatureReference(BaseModel):
    featureKey: str
    version: str
    environment: str
    manifestUrl: str | None = None
    manifest: dict[str, Any] | None = None