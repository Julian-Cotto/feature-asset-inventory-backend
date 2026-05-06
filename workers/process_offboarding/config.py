from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASSET_INVENTORY_", extra="ignore")
    feature_key: str = "asset-inventory"
    worker_name: str = "process-offboarding"
    trigger_kind: str = "topic-subscription"
    topic_name: str = "hr-events"
    subscription_name: str = "asset-inventory-offboarding"
    queue_name: str = ""
    event_name: str = "UserOffboarded"
    dead_letter_enabled: bool = True
    max_concurrency: int = 2
    max_retries: int = 3
    retry_backoff: str = "exponential"

@lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()