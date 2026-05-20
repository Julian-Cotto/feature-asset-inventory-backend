"""Shipment domain service.

- CRUD on shipments + items.
- Auto-assign: pick N oldest in_warehouse assets of a given asset_type.
- Reservation enforcement: an asset already on an active shipment cannot be
  added to another (must be removed first).
- Refresh: pull carrier events, persist new ones, update carrier_status.
- Resolve / cancel: user actions.
- Inbound delivery side-effect: assets flip to in_warehouse.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Iterable

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.inventory import (
    Asset,
    Shipment,
    ShipmentEvent,
    ShipmentItem,
)
from app.services import tracking_service as tracking


logger = logging.getLogger("shipment")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ────────────────────────── Active shipment helpers ──────────────────────


def _is_shipment_active(s: Shipment) -> bool:
    """A shipment "reserves" its assets while open and not delivered/exception."""

    if s.resolution != "open":
        return False
    if s.carrier_status in ("delivered", "exception"):
        return False
    return True


def _other_active_shipment_for_asset(
    db: Session, asset_id: int, exclude_shipment_id: int | None
) -> Shipment | None:
    stmt = (
        select(Shipment)
        .join(ShipmentItem, ShipmentItem.shipment_id == Shipment.id)
        .where(ShipmentItem.asset_id == asset_id)
        .where(Shipment.resolution == "open")
        .where(~Shipment.carrier_status.in_(("delivered", "exception")))
    )
    if exclude_shipment_id is not None:
        stmt = stmt.where(Shipment.id != exclude_shipment_id)
    return db.execute(stmt).scalars().first()


def assert_asset_available_for_shipment(
    db: Session,
    asset_id: int,
    exclude_shipment_id: int | None = None,
    deployment_id: int | None = None,
) -> None:
    """Block asset add when:
      - Already on another active shipment.
      - On an active deployment whose id != deployment_id (this shipment's
        owning deployment is allowed to ship its own assets).
    """

    other = _other_active_shipment_for_asset(db, asset_id, exclude_shipment_id)
    if other is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Asset #{asset_id} is already on active shipment "
                f"#{other.id} (tracking {other.tracking_number}). Remove it from "
                "that shipment first."
            ),
        )

    # Cross-feature check: don't ship an asset reserved by a deployment unless
    # this shipment belongs to the same deployment.
    from app.models.inventory import Deployment, DeploymentItem  # local import to avoid cycle

    stmt = (
        select(Deployment)
        .join(DeploymentItem, DeploymentItem.deployment_id == Deployment.id)
        .where(DeploymentItem.asset_id == asset_id)
        .where(Deployment.status.in_(("planning", "in_progress")))
    )
    if deployment_id is not None:
        stmt = stmt.where(Deployment.id != deployment_id)
    other_deploy = db.execute(stmt).scalars().first()
    if other_deploy is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Asset #{asset_id} is reserved by deployment "
                f"#{other_deploy.id} '{other_deploy.name}'. Remove it from that "
                "deployment first or link this shipment to that deployment."
            ),
        )


# ────────────────────────── Auto-assign ──────────────────────────────────


def _pick_assets_for_auto_assign(
    db: Session,
    asset_type: str,
    quantity: int,
    *,
    model: str | None = None,
    manufacturer: str | None = None,
) -> list[Asset]:
    """Oldest-onboarded N available assets matching type (and optionally
    model / manufacturer): status=active, no UPN, at a warehouse-type
    location, not on another active shipment."""

    if quantity <= 0:
        return []

    from app.models import Location
    from app.services.inventory_service import _available_clauses

    reserved_subq = (
        select(ShipmentItem.asset_id)
        .join(Shipment, Shipment.id == ShipmentItem.shipment_id)
        .where(Shipment.resolution == "open")
        .where(~Shipment.carrier_status.in_(("delivered", "exception")))
    )

    stmt = (
        select(Asset)
        .outerjoin(Location, Location.id == Asset.location_id)
        .where(Asset.asset_type == asset_type)
        .where(Asset.archived_at.is_(None))
        .where(Asset.id.notin_(reserved_subq))
    )
    for clause in _available_clauses():
        stmt = stmt.where(clause)
    if model:
        stmt = stmt.where(Asset.model == model)
    if manufacturer:
        stmt = stmt.where(Asset.manufacturer == manufacturer)
    stmt = stmt.order_by(Asset.onboarded_at.asc()).limit(quantity)
    return list(db.execute(stmt).scalars().all())


# ────────────────────────── Create / list / get ──────────────────────────


def create_shipment(
    db: Session,
    *,
    tracking_number: str,
    carrier: str,
    direction: str,
    description: str | None,
    notes: str | None,
    from_location_id: int | None,
    from_address: dict | None,
    to_location_id: int | None,
    to_address: dict | None,
    asset_ids: list[int] | None,
    auto_assign: list[dict] | None,  # [{asset_type, quantity}]
    actor_upn: str | None,
    deployment_id: int | None = None,
) -> Shipment:
    asset_ids = list(asset_ids or [])

    # Resolve auto-assign requests into concrete asset_ids
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
                        f"only {len(picked)} available in_warehouse. Loosen "
                        "filter, add stock, or pick manually."
                    ),
                )
            asset_ids.extend(a.id for a in picked)

    # Dedupe while preserving order
    seen: set[int] = set()
    unique_ids: list[int] = []
    for aid in asset_ids:
        if aid not in seen:
            seen.add(aid)
            unique_ids.append(aid)

    if not unique_ids:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Shipment must include at least one asset.",
        )

    # Reservation check for every asset
    for aid in unique_ids:
        assert_asset_available_for_shipment(
            db,
            aid,
            exclude_shipment_id=None,
            deployment_id=deployment_id,
        )
        # Verify asset exists
        if db.get(Asset, aid) is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"Asset #{aid} not found."
            )

    fa = from_address or {}
    ta = to_address or {}

    shipment = Shipment(
        tracking_number=tracking_number.strip(),
        carrier=carrier,
        direction=direction,
        description=description,
        notes=notes,
        deployment_id=deployment_id,
        from_location_id=from_location_id,
        from_address_line1=fa.get("address_line1"),
        from_address_line2=fa.get("address_line2"),
        from_city=fa.get("city"),
        from_state=fa.get("state"),
        from_postal_code=fa.get("postal_code"),
        from_country=fa.get("country"),
        to_location_id=to_location_id,
        to_address_line1=ta.get("address_line1"),
        to_address_line2=ta.get("address_line2"),
        to_city=ta.get("city"),
        to_state=ta.get("state"),
        to_postal_code=ta.get("postal_code"),
        to_country=ta.get("country"),
        carrier_status="pending",
        resolution="open",
        created_by_upn=actor_upn,
        updated_by_upn=actor_upn,
    )
    db.add(shipment)
    db.flush()  # need shipment.id

    for aid in unique_ids:
        db.add(ShipmentItem(shipment_id=shipment.id, asset_id=aid))

    db.commit()
    db.refresh(shipment)
    return shipment


def list_shipments(
    db: Session,
    *,
    direction: str | None = None,
    resolution: str | None = None,
    carrier_status: str | None = None,
    q: str | None = None,
    archived: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[Shipment]:
    """archived=False (default): only non-archived. archived=True: only
    archived. No way to fetch both at once — frontend toggles between the
    two views."""

    stmt = select(Shipment)
    if archived:
        stmt = stmt.where(Shipment.archived_at.is_not(None))
    else:
        stmt = stmt.where(Shipment.archived_at.is_(None))
    if direction:
        stmt = stmt.where(Shipment.direction == direction)
    if resolution:
        stmt = stmt.where(Shipment.resolution == resolution)
    if carrier_status:
        stmt = stmt.where(Shipment.carrier_status == carrier_status)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Shipment.tracking_number.ilike(like),
                Shipment.description.ilike(like),
            )
        )
    stmt = stmt.order_by(Shipment.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_shipment(db: Session, shipment_id: int) -> Shipment:
    s = db.get(Shipment, shipment_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shipment not found.")
    return s


# ────────────────────────── Item edits ───────────────────────────────────


def add_item(
    db: Session, shipment_id: int, asset_id: int, actor_upn: str | None
) -> ShipmentItem:
    shipment = get_shipment(db, shipment_id)
    if not _is_shipment_active(shipment):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot modify items on a closed shipment.",
        )
    if db.get(Asset, asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Asset #{asset_id} not found.")
    assert_asset_available_for_shipment(
        db,
        asset_id,
        exclude_shipment_id=shipment_id,
        deployment_id=shipment.deployment_id,
    )

    existing = (
        db.query(ShipmentItem)
        .filter(
            ShipmentItem.shipment_id == shipment_id, ShipmentItem.asset_id == asset_id
        )
        .one_or_none()
    )
    if existing is not None:
        return existing

    item = ShipmentItem(shipment_id=shipment_id, asset_id=asset_id)
    db.add(item)
    shipment.updated_by_upn = actor_upn
    db.commit()
    db.refresh(item)
    return item


def remove_item(
    db: Session, shipment_id: int, item_id: int, actor_upn: str | None
) -> None:
    shipment = get_shipment(db, shipment_id)
    if not _is_shipment_active(shipment):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot modify items on a closed shipment.",
        )
    item = db.get(ShipmentItem, item_id)
    if item is None or item.shipment_id != shipment_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shipment item not found.")
    db.delete(item)
    shipment.updated_by_upn = actor_upn
    db.commit()


# ────────────────────────── Refresh from carrier ─────────────────────────


def refresh_shipment(db: Session, shipment_id: int) -> Shipment:
    shipment = get_shipment(db, shipment_id)

    if shipment.resolution == "cancelled":
        # Don't poll cancelled shipments
        shipment.last_polled_at = _now()
        db.commit()
        db.refresh(shipment)
        return shipment

    result = tracking.fetch_tracking(shipment.carrier, shipment.tracking_number)
    shipment.last_polled_at = _now()
    shipment.last_poll_error = result.error

    if result.error and not result.events:
        db.commit()
        db.refresh(shipment)
        return shipment

    # De-dupe events by (occurred_at, status, description). Carriers can
    # return overlapping batches; we just want the union.
    existing_keys = {
        (e.occurred_at, e.status, e.description) for e in shipment.events
    }

    previous_status = shipment.carrier_status

    for ev in result.events:
        key = (ev.occurred_at, ev.status, ev.description)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        db.add(
            ShipmentEvent(
                shipment_id=shipment.id,
                occurred_at=ev.occurred_at,
                status=ev.status,
                location=ev.location,
                description=ev.description,
                raw_json=json.dumps(ev.raw) if ev.raw is not None else None,
            ),
        )

    if result.status and result.status != "unknown":
        shipment.carrier_status = result.status

    db.commit()

    # Inbound + just-delivered → flip linked assets to in_warehouse
    if (
        previous_status != "delivered"
        and shipment.carrier_status == "delivered"
        and shipment.direction == "inbound"
    ):
        _on_inbound_delivered(db, shipment)

    db.refresh(shipment)
    return shipment


def _on_inbound_delivered(db: Session, shipment: Shipment) -> None:
    """When an inbound shipment is delivered, drop assets at the
    destination location. Status stays active; assignment (assigned_upn)
    untouched — caller can clear it manually if returning to stock."""

    location_id = shipment.to_location_id
    for item in shipment.items:
        asset = item.asset
        if asset is None or asset.archived_at is not None:
            continue
        if location_id and asset.location_id != location_id:
            asset.location_id = location_id
    db.commit()


# ────────────────────────── Resolve / cancel ─────────────────────────────


def resolve_shipment(
    db: Session, shipment_id: int, actor_upn: str | None
) -> Shipment:
    shipment = get_shipment(db, shipment_id)
    if shipment.resolution == "resolved":
        return shipment
    if shipment.resolution == "cancelled":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Cannot resolve a cancelled shipment."
        )
    shipment.resolution = "resolved"
    shipment.resolved_at = _now()
    shipment.resolved_by_upn = actor_upn
    db.commit()
    db.refresh(shipment)
    return shipment


def cancel_shipment(
    db: Session, shipment_id: int, actor_upn: str | None
) -> Shipment:
    shipment = get_shipment(db, shipment_id)
    if shipment.resolution == "cancelled":
        return shipment
    shipment.resolution = "cancelled"
    shipment.cancelled_at = _now()
    shipment.cancelled_by_upn = actor_upn
    db.commit()
    db.refresh(shipment)
    return shipment


def archive_shipment(
    db: Session, shipment_id: int, actor_upn: str | None
) -> Shipment:
    shipment = get_shipment(db, shipment_id)
    if shipment.archived_at is not None:
        return shipment
    shipment.archived_at = _now()
    shipment.archived_by_upn = actor_upn
    db.commit()
    db.refresh(shipment)
    return shipment


def unarchive_shipment(
    db: Session, shipment_id: int, actor_upn: str | None
) -> Shipment:
    shipment = get_shipment(db, shipment_id)
    shipment.archived_at = None
    shipment.archived_by_upn = None
    shipment.updated_by_upn = actor_upn
    db.commit()
    db.refresh(shipment)
    return shipment


def delete_shipment(db: Session, shipment_id: int) -> None:
    """Hard delete. ShipmentItem + ShipmentEvent cascade via ORM."""

    shipment = get_shipment(db, shipment_id)
    db.delete(shipment)
    db.commit()


# ────────────────────────── Update header ────────────────────────────────


def update_shipment(
    db: Session,
    shipment_id: int,
    *,
    description: str | None = None,
    notes: str | None = None,
    direction: str | None = None,
    actor_upn: str | None = None,
) -> Shipment:
    shipment = get_shipment(db, shipment_id)
    if not _is_shipment_active(shipment):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Cannot edit a closed shipment."
        )

    if description is not None:
        shipment.description = description or None
    if notes is not None:
        shipment.notes = notes or None
    if direction is not None:
        shipment.direction = direction
    shipment.updated_by_upn = actor_upn
    db.commit()
    db.refresh(shipment)
    return shipment
