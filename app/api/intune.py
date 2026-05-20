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


# ────────────────────────── Defender ─────────────────────────────────────


class DefenderForensicsResponse(BaseModel):
    """Result of triggering `collectInvestigationPackage` on Defender."""
    asset_id: int
    machine_id: str
    action_id: str | None = None
    status: str | None = None
    requestor: str | None = None
    request_source: str | None = None


@router.post(
    "/assets/{asset_id}/defender/collect-forensics",
    response_model=DefenderForensicsResponse,
)
def collect_defender_forensics(
    asset_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
):
    """Trigger Microsoft Defender to collect an investigation (forensic)
    package on this asset's machine. Async on Defender's side — this
    endpoint returns the machineAction record (Pending → InProgress → done).
    Requires the `WindowsDefenderATP.Machine.CollectForensics` permission."""
    from app.services import defender_service
    from fastapi import HTTPException, status

    asset = svc.get_asset(db, asset_id)
    if not asset.defender_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Asset has no Defender machine id. Sync from Intune first "
            "(Defender data is pulled during the same sync via aadDeviceId).",
        )

    actor_upn = getattr(auth, "user_upn", None) or getattr(auth, "email", None)
    comment = f"Triggered from asset inventory (asset #{asset_id}) by {actor_upn or 'unknown'}"

    try:
        result = defender_service.collect_forensics(asset.defender_id, comment=comment)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Defender call failed: {e}")

    return DefenderForensicsResponse(
        asset_id=asset_id,
        machine_id=asset.defender_id,
        action_id=str(result.get("id")) if result.get("id") else None,
        status=result.get("status"),
        requestor=result.get("requestor"),
        request_source=result.get("requestSource"),
    )
