import time
from typing import Any


class MemoryCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float | None, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None

        expires_at, value = entry
        if expires_at is not None and expires_at < time.time():
            self._store.pop(key, None)
            return None

        return value

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        expires_at = None
        if ttl_seconds is not None:
            expires_at = time.time() + ttl_seconds
        self._store[key] = (expires_at, value)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)