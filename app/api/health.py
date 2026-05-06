from fastapi import APIRouter
from app.config import get_settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])

@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", service=settings.service_name, feature_key=settings.feature_key, auth_mode=settings.auth_mode)