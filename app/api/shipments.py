from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import require_permissions
from app.platform.auth_context import RequestAuthContext
from app.schemas.shipment import (
    CarrierDetectResult,
    ShipmentCreate,
    ShipmentItemCreate,
    ShipmentItemOut,
    ShipmentOut,
    ShipmentUpdate,
)
from app.services import shipment_service as svc
from app.services import tracking_service


router = APIRouter(tags=["shipments"])


def require_view():
    return require_permissions(*get_settings().auth_required_permissions)


def require_write():
    return require_permissions("asset-inventory.write")


def require_manage():
    return require_permissions("asset-inventory.manage")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_stale(s, threshold_minutes: int) -> bool:
    if s.last_polled_at is None:
        return True
    return s.last_polled_at < _now() - timedelta(minutes=threshold_minutes)


@router.get("/shipments/detect-carrier", response_model=CarrierDetectResult)
def detect_carrier(
    tracking_number: str = Query(..., min_length=1, max_length=64),
    _: RequestAuthContext = Depends(require_view()),
):
    return CarrierDetectResult(
        carrier=tracking_service.detect_carrier(tracking_number),
    )


@router.get("/shipments", response_model=list[ShipmentOut])
def list_shipments(
    direction: str | None = None,
    resolution: str | None = None,
    carrier_status: str | None = None,
    q: str | None = None,
    archived: bool = False,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    return svc.list_shipments(
        db,
        direction=direction,
        resolution=resolution,
        carrier_status=carrier_status,
        q=q,
        archived=archived,
        limit=limit,
        offset=offset,
    )


@router.post("/shipments", response_model=ShipmentOut, status_code=201)
def create_shipment(
    payload: ShipmentCreate,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    shipment = svc.create_shipment(
        db,
        tracking_number=payload.tracking_number,
        carrier=payload.carrier,
        direction=payload.direction,
        description=payload.description,
        notes=payload.notes,
        from_location_id=payload.from_location_id,
        from_address=payload.from_address.model_dump() if payload.from_address else None,
        to_location_id=payload.to_location_id,
        to_address=payload.to_address.model_dump() if payload.to_address else None,
        asset_ids=payload.asset_ids,
        auto_assign=[a.model_dump() for a in payload.auto_assign],
        actor_upn=getattr(auth, "user_upn", None) or getattr(auth, "email", None),
    )
    # Initial refresh — best-effort, never fails the create
    try:
        svc.refresh_shipment(db, shipment.id)
    except Exception:
        pass
    return svc.get_shipment(db, shipment.id)


@router.get("/shipments/{shipment_id}", response_model=ShipmentOut)
def get_shipment(
    shipment_id: int,
    auto_refresh: bool = True,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    shipment = svc.get_shipment(db, shipment_id)
    threshold = get_settings().tracking_auto_refresh_minutes
    if auto_refresh and shipment.resolution == "open" and _is_stale(shipment, threshold):
        try:
            shipment = svc.refresh_shipment(db, shipment_id)
        except Exception:
            pass
    return shipment


@router.patch("/shipments/{shipment_id}", response_model=ShipmentOut)
def update_shipment(
    shipment_id: int,
    payload: ShipmentUpdate,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.update_shipment(
        db,
        shipment_id,
        description=payload.description,
        notes=payload.notes,
        direction=payload.direction,
        actor_upn=getattr(auth, "user_upn", None) or getattr(auth, "email", None),
    )


@router.post("/shipments/{shipment_id}/refresh", response_model=ShipmentOut)
def refresh_shipment(
    shipment_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    return svc.refresh_shipment(db, shipment_id)


@router.post("/shipments/{shipment_id}/resolve", response_model=ShipmentOut)
def resolve_shipment(
    shipment_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.resolve_shipment(
        db,
        shipment_id,
        actor_upn=getattr(auth, "user_upn", None) or getattr(auth, "email", None),
    )


@router.post("/shipments/{shipment_id}/cancel", response_model=ShipmentOut)
def cancel_shipment(
    shipment_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
):
    return svc.cancel_shipment(
        db,
        shipment_id,
        actor_upn=getattr(auth, "user_upn", None) or getattr(auth, "email", None),
    )


@router.post("/shipments/{shipment_id}/archive", response_model=ShipmentOut)
def archive_shipment(
    shipment_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
):
    return svc.archive_shipment(
        db,
        shipment_id,
        actor_upn=getattr(auth, "user_upn", None) or getattr(auth, "email", None),
    )


@router.post("/shipments/{shipment_id}/unarchive", response_model=ShipmentOut)
def unarchive_shipment(
    shipment_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
):
    return svc.unarchive_shipment(
        db,
        shipment_id,
        actor_upn=getattr(auth, "user_upn", None) or getattr(auth, "email", None),
    )


@router.delete("/shipments/{shipment_id}", status_code=204)
def delete_shipment(
    shipment_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_manage()),
):
    svc.delete_shipment(db, shipment_id)
    return None


@router.post("/shipments/{shipment_id}/items", response_model=ShipmentItemOut)
def add_shipment_item(
    shipment_id: int,
    payload: ShipmentItemCreate,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.add_item(
        db,
        shipment_id,
        payload.asset_id,
        actor_upn=getattr(auth, "user_upn", None) or getattr(auth, "email", None),
    )


@router.delete("/shipments/{shipment_id}/items/{item_id}", status_code=204)
def remove_shipment_item(
    shipment_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    svc.remove_item(
        db,
        shipment_id,
        item_id,
        actor_upn=getattr(auth, "user_upn", None) or getattr(auth, "email", None),
    )
