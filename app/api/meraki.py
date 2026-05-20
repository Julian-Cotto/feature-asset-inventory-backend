from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import require_permissions
from app.platform.auth_context import RequestAuthContext
from app.services import inventory_service as svc


router = APIRouter(tags=["meraki"])


def require_manage():
    return require_permissions("asset-inventory.manage")


class MerakiBulkSyncResponse(BaseModel):
    total_devices: int
    created: int
    updated: int
    unchanged: int
    skipped_no_serial: int
    skipped_non_network: int
    errors: list[dict]
    networks_synced: dict | None = None
    clients_synced: dict | None = None


@router.post("/meraki/bulk-sync", response_model=MerakiBulkSyncResponse)
def bulk_sync_from_meraki(
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
):
    """Pull every device from the configured Meraki organization and upsert
    as assets. Filters to firewalls (MX/Z), switches (MS), and access
    points (MR). Cameras / sensors are skipped."""

    actor_upn = getattr(auth, "user_upn", None) or getattr(auth, "email", None)
    return svc.bulk_sync_from_meraki(db, actor_upn=actor_upn)
