"""Deployment domain service.

A Deployment is a planned rollout to a location (acquisition / new build /
expansion / relocation / etc.). It owns asset assignments and optionally
shipments.

Reservation rules:
  - An asset on an active deployment (planning | in_progress) is reserved.
  - Active = planning OR in_progress; completed/cancelled release reservation.
  - When adding an asset that's already reserved:
      * If the conflicting record is a deployment in `planning` → caller can
        force-move (we remove from old, add to new).
      * If the conflict is `in_progress` deployment OR active shipment not on
        this same deployment → blocked, no force.
  - Same logic applied symmetrically by shipment_service.

Lifecycle:
  - planning → in_progress (start)
  - in_progress → completed (location side-effect: each item asset's
    location_id flips to deployment's target_location_id)
  - planning|in_progress → cancelled (releases reservation)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.inventory import (
    Asset,
    AssetHistory,
    Deployment,
    DeploymentItem,
    Shipment,
    ShipmentItem,
)


logger = logging.getLogger("deployment")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ────────────────────────── Reservation helpers ─────────────────────────


_ACTIVE_DEPLOYMENT_STATUSES = ("planning", "in_progress")


def is_deployment_active(d: Deployment) -> bool:
    return d.status in _ACTIVE_DEPLOYMENT_STATUSES


def active_deployment_for_asset(
    db: Session, asset_id: int, exclude_deployment_id: int | None = None
) -> Deployment | None:
    stmt = (
        select(Deployment)
        .join(DeploymentItem, DeploymentItem.deployment_id == Deployment.id)
        .where(DeploymentItem.asset_id == asset_id)
        .where(Deployment.status.in_(_ACTIVE_DEPLOYMENT_STATUSES))
    )
    if exclude_deployment_id is not None:
        stmt = stmt.where(Deployment.id != exclude_deployment_id)
    return db.execute(stmt).scalars().first()


def active_shipment_for_asset(
    db: Session, asset_id: int, exclude_deployment_id: int | None = None
) -> Shipment | None:
    """Active shipment NOT belonging to the given deployment."""

    stmt = (
        select(Shipment)
        .join(ShipmentItem, ShipmentItem.shipment_id == Shipment.id)
        .where(ShipmentItem.asset_id == asset_id)
        .where(Shipment.resolution == "open")
        .where(~Shipment.carrier_status.in_(("delivered", "exception")))
    )
    if exclude_deployment_id is not None:
        stmt = stmt.where(
            or_(
                Shipment.deployment_id.is_(None),
                Shipment.deployment_id != exclude_deployment_id,
            )
        )
    return db.execute(stmt).scalars().first()


def _release_asset_from_planning_deployment(
    db: Session, asset_id: int, exclude_deployment_id: int
) -> Deployment | None:
    """If the asset is on another deployment in `planning` state, remove
    it from there. Returns the deployment from which it was removed."""

    stmt = (
        select(Deployment)
        .join(DeploymentItem, DeploymentItem.deployment_id == Deployment.id)
        .where(DeploymentItem.asset_id == asset_id)
        .where(Deployment.id != exclude_deployment_id)
        .where(Deployment.status == "planning")
    )
    other = db.execute(stmt).scalars().first()
    if other is None:
        return None

    db.query(DeploymentItem).filter(
        DeploymentItem.deployment_id == other.id,
        DeploymentItem.asset_id == asset_id,
    ).delete()
    return other


def assert_asset_available_for_deployment(
    db: Session,
    asset_id: int,
    *,
    deployment_id: int | None,
    force: bool = False,
) -> dict:
    """Check reservation conflicts. Mutates DB only if force=True and the
    conflict is a planning-state deployment (the asset is moved over).

    Returns a dict describing what happened:
      { "conflict": None | "deployment_in_progress" | "deployment_planning" | "shipment_active",
        "released_from_deployment_id": <id> | None }

    Raises 409 when conflict is unmovable or force=False with a movable conflict.
    """

    other_deploy = active_deployment_for_asset(db, asset_id, deployment_id)
    if other_deploy is not None:
        if other_deploy.status == "in_progress":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=(
                    f"Asset #{asset_id} is reserved by deployment "
                    f"#{other_deploy.id} '{other_deploy.name}' (in progress). "
                    "Cancel that deployment first if you need to reassign."
                ),
            )
        # other_deploy is in planning — movable
        if not force:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "conflict": "deployment_planning",
                    "deployment_id": other_deploy.id,
                    "deployment_name": other_deploy.name,
                    "message": (
                        f"Asset #{asset_id} is currently on deployment "
                        f"#{other_deploy.id} '{other_deploy.name}' (planning). "
                        "Pass force=true to move it."
                    ),
                },
            )
        # forced — release
        released = _release_asset_from_planning_deployment(
            db, asset_id, exclude_deployment_id=deployment_id or 0
        )
        return {
            "conflict": "deployment_planning",
            "released_from_deployment_id": released.id if released else None,
        }

    other_shipment = active_shipment_for_asset(db, asset_id, deployment_id)
    if other_shipment is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Asset #{asset_id} is on active shipment #{other_shipment.id} "
                f"(tracking {other_shipment.tracking_number}). Resolve or cancel "
                "that shipment first."
            ),
        )

    return {"conflict": None, "released_from_deployment_id": None}


# ────────────────────────── Auto-assign ─────────────────────────────────


def _pick_assets_for_auto_assign(
    db: Session,
    asset_type: str,
    quantity: int,
    *,
    model: str | None = None,
    manufacturer: str | None = None,
) -> list[Asset]:
    """Oldest-onboarded N in-warehouse assets matching type (and optionally
    model / manufacturer), not on any active deployment or active shipment."""

    if quantity <= 0:
        return []

    reserved_by_deployment = (
        select(DeploymentItem.asset_id)
        .join(Deployment, Deployment.id == DeploymentItem.deployment_id)
        .where(Deployment.status.in_(_ACTIVE_DEPLOYMENT_STATUSES))
    )
    reserved_by_shipment = (
        select(ShipmentItem.asset_id)
        .join(Shipment, Shipment.id == ShipmentItem.shipment_id)
        .where(Shipment.resolution == "open")
        .where(~Shipment.carrier_status.in_(("delivered", "exception")))
    )

    from app.models import Location

    stmt = (
        select(Asset)
        .join(Location, Location.id == Asset.location_id)
        .where(Asset.asset_type == asset_type)
        .where(Asset.archived_at.is_(None))
        .where(Asset.status_code == "active")
        .where(Asset.assigned_upn.is_(None))
        .where(Location.type == "warehouse")
        .where(Asset.id.notin_(reserved_by_deployment))
        .where(Asset.id.notin_(reserved_by_shipment))
    )
    if model:
        stmt = stmt.where(Asset.model == model)
    if manufacturer:
        stmt = stmt.where(Asset.manufacturer == manufacturer)

    stmt = stmt.order_by(Asset.onboarded_at.asc()).limit(quantity)
    return list(db.execute(stmt).scalars().all())


# ────────────────────────── Create / list / get ─────────────────────────


def create_deployment(
    db: Session,
    *,
    name: str,
    type_: str | None,
    description: str | None,
    notes: str | None,
    target_date: datetime | None,
    target_location_id: int | None,
    target_address: dict | None,
    asset_ids: list[int] | None,
    auto_assign: list[dict] | None,
    actor_upn: str | None,
) -> Deployment:
    asset_ids = list(asset_ids or [])

    if auto_assign:
        for req in auto_assign:
            asset_type = str(req.get("asset_type", "")).strip()
            qty = int(req.get("quantity", 0))
            model = (req.get("model") or "").strip() or None
            manufacturer = (req.get("manufacturer") or "").strip() or None
            picked = _pick_assets_for_auto_assign(
                db, asset_type, qty, model=model, manufacturer=manufacturer
            )
            if len(picked) < qty:
                criteria = asset_type
                if model:
                    criteria += f" / {model}"
                if manufacturer:
                    criteria += f" / {manufacturer}"
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail=(
                        f"Auto-assign requested {qty} {criteria} assets but "
                        f"only {len(picked)} available in_warehouse and unreserved. "
                        "Loosen filter, add more stock, or pick assets manually."
                    ),
                )
            asset_ids.extend(a.id for a in picked)

    seen: set[int] = set()
    unique_ids: list[int] = []
    for aid in asset_ids:
        if aid not in seen:
            seen.add(aid)
            unique_ids.append(aid)

    ta = target_address or {}
    deployment = Deployment(
        name=name.strip(),
        type=(type_ or "").strip() or None,
        description=description,
        notes=notes,
        target_date=target_date,
        target_location_id=target_location_id,
        target_address_line1=ta.get("address_line1"),
        target_address_line2=ta.get("address_line2"),
        target_city=ta.get("city"),
        target_state=ta.get("state"),
        target_postal_code=ta.get("postal_code"),
        target_country=ta.get("country"),
        status="planning",
        created_by_upn=actor_upn,
        updated_by_upn=actor_upn,
    )
    db.add(deployment)
    db.flush()  # need id

    # Reservation check + add items
    for aid in unique_ids:
        if db.get(Asset, aid) is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"Asset #{aid} not found."
            )
        # No force on creation — caller must remove from old plans first
        assert_asset_available_for_deployment(
            db, aid, deployment_id=deployment.id, force=False
        )
        db.add(DeploymentItem(deployment_id=deployment.id, asset_id=aid))

    db.commit()
    db.refresh(deployment)
    return deployment


def list_deployments(
    db: Session,
    *,
    status_filter: str | None = None,
    type_filter: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[Deployment]:
    stmt = select(Deployment)
    if status_filter:
        stmt = stmt.where(Deployment.status == status_filter)
    if type_filter:
        stmt = stmt.where(Deployment.type == type_filter)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Deployment.name.ilike(like),
                Deployment.description.ilike(like),
                Deployment.target_city.ilike(like),
            )
        )
    stmt = stmt.order_by(Deployment.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_deployment(db: Session, deployment_id: int) -> Deployment:
    d = db.get(Deployment, deployment_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deployment not found.")
    return d


# ────────────────────────── Update header ───────────────────────────────


def update_deployment(
    db: Session,
    deployment_id: int,
    *,
    name: str | None = None,
    type_: str | None = None,
    description: str | None = None,
    notes: str | None = None,
    target_date: datetime | None = None,
    target_location_id: int | None = None,
    target_address: dict | None = None,
    actor_upn: str | None = None,
) -> Deployment:
    d = get_deployment(db, deployment_id)
    if d.status in ("completed", "cancelled"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Cannot edit a closed deployment."
        )

    if name is not None:
        d.name = name.strip()
    if type_ is not None:
        d.type = type_.strip() or None
    if description is not None:
        d.description = description or None
    if notes is not None:
        d.notes = notes or None
    if target_date is not None:
        d.target_date = target_date
    if target_location_id is not None:
        d.target_location_id = target_location_id or None
    if target_address is not None:
        ta = target_address
        d.target_address_line1 = ta.get("address_line1")
        d.target_address_line2 = ta.get("address_line2")
        d.target_city = ta.get("city")
        d.target_state = ta.get("state")
        d.target_postal_code = ta.get("postal_code")
        d.target_country = ta.get("country")

    d.updated_by_upn = actor_upn
    db.commit()
    db.refresh(d)
    return d


# ────────────────────────── Items ───────────────────────────────────────


def add_item(
    db: Session,
    deployment_id: int,
    asset_id: int,
    *,
    role: str | None = None,
    notes: str | None = None,
    force: bool = False,
    actor_upn: str | None = None,
) -> dict:
    d = get_deployment(db, deployment_id)
    if d.status not in ("planning",):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Items can only be added while deployment is in 'planning'.",
        )
    if db.get(Asset, asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Asset #{asset_id} not found.")

    info = assert_asset_available_for_deployment(
        db, asset_id, deployment_id=deployment_id, force=force
    )

    existing = (
        db.query(DeploymentItem)
        .filter(
            DeploymentItem.deployment_id == deployment_id,
            DeploymentItem.asset_id == asset_id,
        )
        .one_or_none()
    )
    if existing is not None:
        return {"item": existing, "released_from_deployment_id": info.get("released_from_deployment_id")}

    item = DeploymentItem(
        deployment_id=deployment_id,
        asset_id=asset_id,
        role=role,
        notes=notes,
    )
    db.add(item)
    d.updated_by_upn = actor_upn
    db.commit()
    db.refresh(item)
    return {
        "item": item,
        "released_from_deployment_id": info.get("released_from_deployment_id"),
    }


def remove_item(
    db: Session,
    deployment_id: int,
    item_id: int,
    actor_upn: str | None,
) -> None:
    d = get_deployment(db, deployment_id)
    if d.status not in ("planning",):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Items can only be removed while deployment is in 'planning'.",
        )
    item = db.get(DeploymentItem, item_id)
    if item is None or item.deployment_id != deployment_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deployment item not found.")
    db.delete(item)
    d.updated_by_upn = actor_upn
    db.commit()


# ────────────────────────── Lifecycle ───────────────────────────────────


def start_deployment(
    db: Session, deployment_id: int, actor_upn: str | None
) -> Deployment:
    d = get_deployment(db, deployment_id)
    if d.status != "planning":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot start deployment from status '{d.status}'.",
        )
    if not d.items:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot start a deployment with no items.",
        )
    d.status = "in_progress"
    d.started_at = _now()
    d.updated_by_upn = actor_upn
    db.commit()
    db.refresh(d)
    return d


def complete_deployment(
    db: Session, deployment_id: int, actor_upn: str | None
) -> Deployment:
    d = get_deployment(db, deployment_id)
    if d.status not in ("in_progress",):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot complete deployment from status '{d.status}'.",
        )

    target_loc_id = d.target_location_id
    if target_loc_id is not None:
        for item in d.items:
            asset = item.asset
            if asset is None or asset.archived_at is not None:
                continue
            if asset.location_id != target_loc_id:
                prev_location = asset.location_id
                asset.location_id = target_loc_id
                # History entry
                db.add(
                    AssetHistory(
                        asset_id=asset.id,
                        event_type="location_change",
                        from_value=str(prev_location) if prev_location else None,
                        to_value=str(target_loc_id),
                        performed_by_upn=actor_upn,
                        notes=f"Deployment '{d.name}' completed",
                    )
                )

    d.status = "completed"
    d.completed_at = _now()
    d.completed_by_upn = actor_upn
    d.updated_by_upn = actor_upn
    db.commit()
    db.refresh(d)
    return d


def cancel_deployment(
    db: Session, deployment_id: int, actor_upn: str | None
) -> Deployment:
    d = get_deployment(db, deployment_id)
    if d.status in ("completed", "cancelled"):
        return d
    d.status = "cancelled"
    d.cancelled_at = _now()
    d.cancelled_by_upn = actor_upn
    d.updated_by_upn = actor_upn
    db.commit()
    db.refresh(d)
    return d


# ────────────────────────── Shipment linking ────────────────────────────


def link_shipment(
    db: Session,
    deployment_id: int,
    shipment_id: int,
    actor_upn: str | None,
) -> Shipment:
    d = get_deployment(db, deployment_id)
    if not is_deployment_active(d):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Cannot link shipments to a closed deployment."
        )
    shipment = db.get(Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shipment not found.")
    if shipment.deployment_id is not None and shipment.deployment_id != deployment_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Shipment #{shipment_id} already belongs to deployment "
            f"#{shipment.deployment_id}. Unlink it first.",
        )
    shipment.deployment_id = deployment_id
    shipment.updated_by_upn = actor_upn
    db.commit()
    db.refresh(shipment)
    return shipment


def unlink_shipment(
    db: Session,
    deployment_id: int,
    shipment_id: int,
    actor_upn: str | None,
) -> None:
    d = get_deployment(db, deployment_id)
    if not is_deployment_active(d):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot modify shipments on a closed deployment.",
        )
    shipment = db.get(Shipment, shipment_id)
    if shipment is None or shipment.deployment_id != deployment_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Shipment is not linked to this deployment."
        )
    shipment.deployment_id = None
    shipment.updated_by_upn = actor_upn
    db.commit()
