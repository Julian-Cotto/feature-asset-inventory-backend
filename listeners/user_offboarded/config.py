from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class ListenerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASSET_INVENTORY_", extra="ignore")
    feature_key: str = "asset-inventory"
    listener_name: str = "user-offboarded"
    event_name: str = "hr.user.offboarded"
    payload_schema: str = "UserOffboarded"

@lru_cache(maxsize=1)
def get_listener_settings() -> ListenerSettings:
    return ListenerSettings()