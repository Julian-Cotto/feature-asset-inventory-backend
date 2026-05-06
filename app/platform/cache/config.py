from pydantic_settings import BaseSettings, SettingsConfigDict


class CacheSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cache_backend: str = "memory"
    redis_url: str | None = None


settings = CacheSettings()