from fastapi import APIRouter, Depends

from app.config import get_settings
from app.dependencies import require_permissions, require_roles
from app.platform.auth_context import RequestAuthContext
from app.schemas.feature import FeatureItem, FeatureItemsResponse
from app.services.feature_service import get_feature_items

router = APIRouter(tags=["feature"])


def require_feature_view_permission():
    return require_permissions(*get_settings().auth_required_permissions)


@router.get("/items", response_model=FeatureItemsResponse)
def get_items(
    auth_context: RequestAuthContext = Depends(require_feature_view_permission()),
) -> FeatureItemsResponse:
    items = get_feature_items()

    return FeatureItemsResponse(
        feature_key="asset-inventory",
        authenticated=auth_context.is_authenticated,
        user=auth_context.user_name,
        permissions=auth_context.permissions,
        items=[FeatureItem(**item) for item in items],
    )


@router.get("/admin-check")
def admin_check(
    auth_context: RequestAuthContext = Depends(require_roles("admin")),
) -> dict:
    return {
        "ok": True,
        "feature_key": "asset-inventory",
        "user": auth_context.user_name,
        "roles": auth_context.roles,
        "permissions": auth_context.permissions,
    }