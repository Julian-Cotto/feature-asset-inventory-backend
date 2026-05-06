

## Cache Usage

This feature includes cache support.

### Cache backend selection

Use in-memory cache:

CACHE_BACKEND=memory

### Example usage

from app.platform.cache.dependencies import get_cache

cache = get_cache()
