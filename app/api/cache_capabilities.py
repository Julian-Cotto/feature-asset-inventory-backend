from fastapi import APIRouter, Depends

from app.platform.cache.dependencies import get_cache

router = APIRouter(tags=["cache"])


@router.get("/cache/capabilities")
def get_cache_capabilities(cache = Depends(get_cache)):
    return {
        "cacheEnabled": {{ "true" if cache_enabled else "false" }},
        "memoryCacheEnabled": {{ "true" if memory_cache_enabled else "false" }},
        "redisEnabled": {{ "true" if redis_enabled else "false" }},
        "cacheTargetApi": {{ "true" if "api" in cache_targets else "false" }},
        "cacheType": cache.__class__.__name__,
    }