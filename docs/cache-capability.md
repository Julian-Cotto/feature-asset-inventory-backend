# Cache capability

This feature includes cache support.

## Memory cache

The feature can use in-process memory cache for local and single-instance scenarios.

## Cache backend selection

CACHE_BACKEND=memory

## Main files

- `backend/app/platform/cache/config.py`
- `backend/app/platform/cache/memory_cache.py`
- `backend/app/platform/cache/cache_factory.py`


## Runtime usage

### API
- `backend/app/platform/cache/dependencies.py`

### Jobs / listeners
- `backend/app/runtime/cache.py`