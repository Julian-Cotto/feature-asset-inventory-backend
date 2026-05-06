from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class JobSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASSET_INVENTORY_", extra="ignore")
    feature_key: str = "asset-inventory"
    job_name: str = "stale-repair-check"
    schedule: str = "0 3 * * *"
    log_level: str = "INFO"

@lru_cache(maxsize=1)
def get_job_settings() -> JobSettings:
    return JobSettings()