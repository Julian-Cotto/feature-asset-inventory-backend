from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


LOCAL_ENVIRONMENTS = {"local", "dev", "development", "test"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "IT Asset Inventory Service"
    feature_key: str = "asset-inventory"
    api_base_path: str = "/api/inventory/it"

    app_environment: str = "local"

    auth_mode: str = "entra"
    registry_mode: str = "rest"
    backend_token_strategy: str = "forwarded-bearer"

    allowed_origins_raw: str = Field(
        default="http://localhost:3000,http://localhost:3200,http://localhost:3300,http://localhost:5173"
    )

    auth_debug_headers_enabled: bool = True
    auth_default_dev_user_id: str = "dev-user"
    auth_default_dev_user_name: str = "Local Dev User"
    auth_default_dev_email: str = "dev@example.local"
    auth_default_dev_roles_raw: str = "developer"

    auth_required_permissions_raw: str = "asset-inventory.view"
    auth_role_permission_map_raw: str = (
        "admin:*;"
        "developer:asset-inventory.view|asset-inventory.write|asset-inventory.manage;"
        "reader:asset-inventory.view;"
        "operator:asset-inventory.view|asset-inventory.write"
    )
    auth_scope_permission_map_raw: str = "access_as_user:asset-inventory.view"

    database_url: str = "sqlite:///./data/asset_inventory.db"
    db_create_all_on_startup: bool = True
    db_seed_default_statuses: bool = True

    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_audience: str = ""
    entra_allowed_audiences_raw: str = ""
    entra_jwks_url: str = ""
    entra_issuer: str = ""
    entra_allowed_issuers_raw: str = ""
    entra_require_https_metadata: bool = True
    entra_clock_skew_seconds: int = 60

    @property
    def normalized_app_environment(self) -> str:
        return self.app_environment.strip().lower() or "local"

    @property
    def is_local_environment(self) -> bool:
        return self.normalized_app_environment in LOCAL_ENVIRONMENTS

    @property
    def normalized_auth_mode(self) -> str:
        return self.auth_mode.strip().lower()

    @property
    def allowed_origins(self) -> list[str]:
        return [v.strip() for v in self.allowed_origins_raw.split(",") if v.strip()]

    @property
    def auth_default_dev_roles(self) -> list[str]:
        return [
            v.strip()
            for v in self.auth_default_dev_roles_raw.split(",")
            if v.strip()
        ]

    @property
    def auth_required_permissions(self) -> list[str]:
        return [
            v.strip()
            for v in self.auth_required_permissions_raw.split(",")
            if v.strip()
        ]

    @staticmethod
    def _parse_permission_map(raw: str) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}

        for entry in raw.split(";"):
            entry = entry.strip()
            if not entry or ":" not in entry:
                continue

            key, permissions_raw = entry.split(":", 1)
            key = key.strip()

            permissions = [
                value.strip()
                for value in permissions_raw.replace("|", ",").split(",")
                if value.strip()
            ]

            if key and permissions:
                result[key] = permissions

        return result

    @property
    def auth_role_permission_map(self) -> dict[str, list[str]]:
        return self._parse_permission_map(self.auth_role_permission_map_raw)

    @property
    def auth_scope_permission_map(self) -> dict[str, list[str]]:
        return self._parse_permission_map(self.auth_scope_permission_map_raw)

    @property
    def effective_entra_audiences(self) -> list[str]:
        explicit = [
            v.strip()
            for v in self.entra_allowed_audiences_raw.split(",")
            if v.strip()
        ]

        if explicit:
            return explicit

        audience = self.entra_audience.strip()
        if audience:
            return [audience]

        client_id = self.entra_client_id.strip()
        if client_id:
            return [client_id]

        return []

    @property
    def effective_entra_audience(self) -> str:
        audiences = self.effective_entra_audiences
        return audiences[0] if audiences else ""

    @property
    def effective_entra_issuers(self) -> list[str]:
        explicit = [
            v.strip()
            for v in self.entra_allowed_issuers_raw.split(",")
            if v.strip()
        ]

        if explicit:
            return explicit

        issuer = self.entra_issuer.strip()
        if issuer:
            return [issuer]

        tenant_id = self.entra_tenant_id.strip()
        if not tenant_id:
            return []

        return [
            f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            f"https://sts.windows.net/{tenant_id}/",
        ]

    @property
    def effective_entra_issuer(self) -> str:
        issuers = self.effective_entra_issuers
        return issuers[0] if issuers else ""

    @property
    def effective_entra_jwks_url(self) -> str:
        if self.entra_jwks_url.strip():
            return self.entra_jwks_url.strip()
        if not self.entra_tenant_id.strip():
            return ""
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}/discovery/v2.0/keys"

    def validate_runtime_safety(self) -> None:
        if self.is_local_environment:
            return

        if self.normalized_auth_mode in {"none", "mock"}:
            raise ValueError(
                "Unsafe auth configuration: AUTH_MODE=none/mock is only allowed "
                "when APP_ENVIRONMENT is local/dev/development/test."
            )

        if self.normalized_auth_mode != "entra":
            raise ValueError(
                f"Unsupported production auth mode: {self.auth_mode}. "
                "Use AUTH_MODE=entra."
            )

        missing = []
        if not self.effective_entra_audiences:
            missing.append("ENTRA_AUDIENCE or ENTRA_ALLOWED_AUDIENCES_RAW")
        if not self.effective_entra_issuers:
            missing.append("ENTRA_ISSUER, ENTRA_ALLOWED_ISSUERS_RAW, or ENTRA_TENANT_ID")
        if not self.effective_entra_jwks_url:
            missing.append("ENTRA_JWKS_URL or ENTRA_TENANT_ID")

        if missing:
            raise ValueError(
                "Missing required production Entra settings: "
                + ", ".join(missing)
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime_safety()
    return settings