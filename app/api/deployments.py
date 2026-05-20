from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import require_permissions
from app.platform.auth_context import RequestAuthContext
from app.schemas.deployment import (
    DeploymentCreate,
    DeploymentItemAddResponse,
    DeploymentItemCreate,
    DeploymentOut,
    DeploymentUpdate,
)
from app.schemas.shipment import ShipmentCreate, ShipmentOut
from app.services import deployment_service as svc
from app.services import shipment_service


router = APIRouter(tags=["deployments"])


def require_view():
    return require_permissions(*get_settings().auth_required_permissions)


def require_write():
    return require_permissions("asset-inventory.write")


def require_manage():
    return require_permissions("asset-inventory.manage")


def _actor(auth: RequestAuthContext) -> str | None:
    return getattr(auth, "user_upn", None) or getattr(auth, "email", None)


@router.get("/deployments", response_model=list[DeploymentOut])
def list_deployments(
    status: str | None = None,
    type: str | None = None,
    q: str | None = None,
    archived: bool = False,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    return svc.list_deployments(
        db,
        status_filter=status,
        type_filter=type,
        q=q,
        archived=archived,
        limit=limit,
        offset=offset,
    )


@router.post("/deployments", response_model=DeploymentOut, status_code=201)
def create_deployment(
    payload: DeploymentCreate,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.create_deployment(
        db,
        name=payload.name,
        type_=payload.type,
        description=payload.description,
        notes=payload.notes,
        target_date=payload.target_date,
        target_location_id=payload.target_location_id,
        target_address=(
            payload.target_address.model_dump() if payload.target_address else None
        ),
        asset_ids=payload.asset_ids,
        auto_assign=[a.model_dump() for a in payload.auto_assign],
        actor_upn=_actor(auth),
    )


@router.get("/deployments/{deployment_id}", response_model=DeploymentOut)
def get_deployment(
    deployment_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    return svc.get_deployment(db, deployment_id)


@router.patch("/deployments/{deployment_id}", response_model=DeploymentOut)
def update_deployment(
    deployment_id: int,
    payload: DeploymentUpdate,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.update_deployment(
        db,
        deployment_id,
        name=payload.name,
        type_=payload.type,
        description=payload.description,
        notes=payload.notes,
        target_date=payload.target_date,
        target_location_id=payload.target_location_id,
        target_address=(
            payload.target_address.model_dump() if payload.target_address else None
        ),
        actor_upn=_actor(auth),
    )


@router.post("/deployments/{deployment_id}/items", response_model=DeploymentItemAddResponse)
def add_deployment_item(
    deployment_id: int,
    payload: DeploymentItemCreate,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.add_item(
        db,
        deployment_id,
        payload.asset_id,
        role=payload.role,
        notes=payload.notes,
        force=payload.force,
        actor_upn=_actor(auth),
    )


@router.delete("/deployments/{deployment_id}/items/{item_id}", status_code=204)
def remove_deployment_item(
    deployment_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    svc.remove_item(db, deployment_id, item_id, actor_upn=_actor(auth))


@router.post("/deployments/{deployment_id}/start", response_model=DeploymentOut)
def start_deployment(
    deployment_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.start_deployment(db, deployment_id, actor_upn=_actor(auth))


@router.post("/deployments/{deployment_id}/complete", response_model=DeploymentOut)
def complete_deployment(
    deployment_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.complete_deployment(db, deployment_id, actor_upn=_actor(auth))


@router.post("/deployments/{deployment_id}/cancel", response_model=DeploymentOut)
def cancel_deployment(
    deployment_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
):
    return svc.cancel_deployment(db, deployment_id, actor_upn=_actor(auth))


@router.post("/deployments/{deployment_id}/archive", response_model=DeploymentOut)
def archive_deployment(
    deployment_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
):
    return svc.archive_deployment(db, deployment_id, actor_upn=_actor(auth))


@router.post("/deployments/{deployment_id}/unarchive", response_model=DeploymentOut)
def unarchive_deployment(
    deployment_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
):
    return svc.unarchive_deployment(db, deployment_id, actor_upn=_actor(auth))


@router.delete("/deployments/{deployment_id}", status_code=204)
def delete_deployment(
    deployment_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_manage()),
):
    svc.delete_deployment(db, deployment_id)
    return None


@router.post(
    "/deployments/{deployment_id}/shipments", response_model=ShipmentOut, status_code=201
)
def create_deployment_shipment(
    deployment_id: int,
    payload: ShipmentCreate,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    """Create a shipment pre-linked to this deployment."""

    # Ensure deployment is real & open
    deployment = svc.get_deployment(db, deployment_id)
    if not svc.is_deployment_active(deployment):
        from fastapi import HTTPException, status as status_code

        raise HTTPException(
            status_code.HTTP_409_CONFLICT,
            "Cannot add shipments to a closed deployment.",
        )

    shipment = shipment_service.create_shipment(
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
        actor_upn=_actor(auth),
        deployment_id=deployment_id,
    )
    try:
        shipment_service.refresh_shipment(db, shipment.id)
    except Exception:
        pass
    return shipment_service.get_shipment(db, shipment.id)


@router.post(
    "/deployments/{deployment_id}/shipments/{shipment_id}/link",
    response_model=ShipmentOut,
)
def link_existing_shipment(
    deployment_id: int,
    shipment_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.link_shipment(db, deployment_id, shipment_id, actor_upn=_actor(auth))


@router.delete(
    "/deployments/{deployment_id}/shipments/{shipment_id}", status_code=204
)
def unlink_shipment(
    deployment_id: int,
    shipment_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    svc.unlink_shipment(db, deployment_id, shipment_id, actor_upn=_actor(auth))
