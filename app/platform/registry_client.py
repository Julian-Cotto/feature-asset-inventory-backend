from __future__ import annotations

import httpx

from app.platform.registry_models import RegistryFeatureReference


class RegistryClient:
    def __init__(self, base_url: str, api_token: str | None = None, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    async def get_feature(self, feature_key: str, environment: str | None = None) -> RegistryFeatureReference:
        params = {}
        if environment:
            params["environment"] = environment

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/api/features/{feature_key}",
                params=params,
                headers=self._headers(),
            )
            response.raise_for_status()
            return RegistryFeatureReference.model_validate(response.json())

    async def publish_feature(self, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/features/publish",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()