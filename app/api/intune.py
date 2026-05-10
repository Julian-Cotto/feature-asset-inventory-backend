from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import require_permissions
from app.platform.auth_context import RequestAuthContext
from app.schemas.inventory import AssetOut
from app.services import intune_service
from app.services import inventory_service as svc


router = APIRouter(tags=["intune"])


def require_view():
    return require_permissions(*get_settings().auth_required_permissions)


def require_write():
    return require_permissions("asset-inventory.write")


def require_manage():
    return require_permissions("asset-inventory.manage")


class IntuneSyncResponse(BaseModel):
    asset: AssetOut
    found: bool
    changed: list[str]


class IntunePortalUrlResponse(BaseModel):
    url: str


class IntuneBulkSyncError(BaseModel):
    intune_id: str | None = None
    serial: str | None = None
    error: str


class IntuneBulkSyncResponse(BaseModel):
    total_devices: int
    created: int
    updated: int
    skipped_no_serial: int
    skipped_non_computer: int
    errors: list[IntuneBulkSyncError]


@router.post("/assets/{asset_id}/intune/sync", response_model=IntuneSyncResponse)
def sync_asset_from_intune(
    asset_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    actor_upn = getattr(auth, "user_upn", None) or getattr(auth, "email", None)
    return svc.sync_asset_from_intune(db, asset_id, actor_upn=actor_upn)


@router.post("/intune/bulk-sync", response_model=IntuneBulkSyncResponse)
def bulk_sync_from_intune(
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
):
    actor_upn = getattr(auth, "user_upn", None) or getattr(auth, "email", None)
    return svc.bulk_sync_from_intune(db, actor_upn=actor_upn)


@router.get(
    "/assets/{asset_id}/intune/portal-url", response_model=IntunePortalUrlResponse
)
def get_intune_portal_url(
    asset_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    asset = svc.get_asset(db, asset_id)
    if not asset.intune_id:
        from fastapi import HTTPException, status

        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Asset has no Intune ID yet. Sync from Intune first.",
        )
    return IntunePortalUrlResponse(url=intune_service.device_portal_url(asset.intune_id))
