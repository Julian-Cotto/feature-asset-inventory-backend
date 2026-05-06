from app.platform.cache.config import settings
from app.platform.cache.memory_cache import MemoryCache



_memory_cache = MemoryCache()


def get_cache():
    if settings.cache_backend == "memory":
        return _memory_cache


    raise RuntimeError(f"Unsupported cache backend: {settings.cache_backend}")