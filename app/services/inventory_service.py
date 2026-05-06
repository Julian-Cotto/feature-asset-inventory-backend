from datetime import datetime, timezone
from typing import Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _classify_integrity_error(exc: IntegrityError, default: str) -> HTTPException:
    """Map a SQLAlchemy IntegrityError to a meaningful HTTP error.

    UNIQUE / duplicate -> 409 with which column collided.
    NOT NULL          -> 400 with which column is missing.
    CHECK             -> 400 with which constraint failed.
    FOREIGN KEY       -> 400.
    Anything else     -> 409 default message.
    """
    msg = str(getattr(exc, "orig", exc)) or str(exc)
    lower = msg.lower()
    if "unique" in lower or "duplicate" in lower:
        return HTTPException(status.HTTP_409_CONFLICT, f"Unique constraint violated: {msg}")
    if "not null" in lower or "null value" in lower:
        return HTTPException(status.HTTP_400_BAD_REQUEST, f"Missing required field: {msg}")
    if "check constraint" in lower:
        return HTTPException(status.HTTP_400_BAD_REQUEST, f"Check constraint failed: {msg}")
    if "foreign key" in lower:
        return HTTPException(status.HTTP_400_BAD_REQUEST, f"Foreign key violation: {msg}")
    return HTTPException(status.HTTP_409_CONFLICT, f"{default}: {msg}")

from app.models import (
    Asset,
    AssetHistory,
    AssetStatus,
    DEFAULT_STATUSES,
    Location,
)
from app.schemas.inventory import (
    AssetArchive,
    AssetAssign,
    AssetCreate,
    AssetStatusChange,
    AssetStatusCreate,
    AssetStatusUpdate,
    AssetUpdate,
    LocationCreate,
    LocationUpdate,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _record_history(
    db: Session,
    *,
    asset_id: int,
    event_type: str,
    from_value: str | None,
    to_value: str | None,
    actor_upn: str | None,
    notes: str | None = None,
) -> None:
    db.add(
        AssetHistory(
            asset_id=asset_id,
            event_type=event_type,
            from_value=from_value,
            to_value=to_value,
            performed_by_upn=actor_upn,
            performed_at=_utcnow(),
            notes=notes,
        )
    )


# ===================== Statuses =====================

def seed_default_statuses(db: Session) -> None:
    existing = {row.code for row in db.scalars(select(AssetStatus)).all()}
    added = False
    for entry in DEFAULT_STATUSES:
        if entry["code"] in existing:
            continue
        db.add(AssetStatus(**entry))
        added = True
    if added:
        db.commit()


def list_statuses(db: Session, include_inactive: bool = False) -> list[AssetStatus]:
    stmt = select(AssetStatus).order_by(AssetStatus.sort_order, AssetStatus.code)
    if not include_inactive:
        stmt = stmt.where(AssetStatus.is_active.is_(True))
    return list(db.scalars(stmt).all())


def create_status(db: Session, payload: AssetStatusCreate) -> AssetStatus:
    if db.get(AssetStatus, payload.code) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Status '{payload.code}' exists")
    obj = AssetStatus(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update_status(db: Session, code: str, payload: AssetStatusUpdate) -> AssetStatus:
    obj = db.get(AssetStatus, code)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Status '{code}' not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    db.commit()
    db.refresh(obj)
    return obj


# ===================== Locations =====================

def list_locations(db: Session, include_inactive: bool = False) -> list[Location]:
    stmt = select(Location).order_by(Location.name)
    if not include_inactive:
        stmt = stmt.where(Location.is_active.is_(True))
    return list(db.scalars(stmt).all())


def get_location(db: Session, location_id: int) -> Location:
    loc = db.get(Location, location_id)
    if loc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Location {location_id} not found")
    return loc


def create_location(db: Session, payload: LocationCreate, actor_upn: str | None) -> Location:
    obj = Location(**payload.model_dump(), created_by_upn=actor_upn, updated_by_upn=actor_upn)
    db.add(obj)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _classify_integrity_error(exc, "Location create failed") from exc
    db.refresh(obj)
    return obj


def update_location(
    db: Session, location_id: int, payload: LocationUpdate, actor_upn: str | None
) -> Location:
    loc = get_location(db, location_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(loc, field, value)
    loc.updated_by_upn = actor_upn
    db.commit()
    db.refresh(loc)
    return loc


def delete_location(db: Session, location_id: int) -> None:
    loc = get_location(db, location_id)
    in_use = db.scalar(
        select(Asset.id).where(Asset.location_id == location_id).limit(1)
    )
    if in_use:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Location in use by assets — set inactive instead of deleting",
        )
    db.delete(loc)
    db.commit()


# ===================== Assets =====================

def _ensure_status(db: Session, code: str) -> AssetStatus:
    s = db.get(AssetStatus, code)
    if s is None or not s.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown or inactive status '{code}'")
    return s


def _ensure_location(db: Session, location_id: int | None) -> Location | None:
    if location_id is None:
        return None
    return get_location(db, location_id)


def list_assets(
    db: Session,
    *,
    q: str | None = None,
    asset_type: str | None = None,
    status_code: str | None = None,
    location_id: int | None = None,
    assigned_upn: str | None = None,
    include_archived: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[Asset]:
    stmt = select(Asset)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            (Asset.asset_tag.ilike(like))
            | (Asset.serial_number.ilike(like))
            | (Asset.model.ilike(like))
            | (Asset.manufacturer.ilike(like))
        )
    if asset_type:
        stmt = stmt.where(Asset.asset_type == asset_type)
    if status_code:
        stmt = stmt.where(Asset.status_code == status_code)
    if location_id is not None:
        stmt = stmt.where(Asset.location_id == location_id)
    if assigned_upn:
        stmt = stmt.where(Asset.assigned_upn == assigned_upn)
    if not include_archived:
        stmt = stmt.where(Asset.archived_at.is_(None))
    stmt = stmt.order_by(Asset.id.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt).all())


def get_asset(db: Session, asset_id: int) -> Asset:
    obj = db.get(Asset, asset_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Asset {asset_id} not found")
    return obj


def lookup_by_serial(db: Session, serial: str) -> Asset | None:
    return db.scalar(select(Asset).where(Asset.serial_number == serial.strip()))


def onboard_asset(db: Session, payload: AssetCreate, actor_upn: str | None) -> Asset:
    _ensure_status(db, payload.status_code)
    _ensure_location(db, payload.location_id)

    asset = Asset(
        **payload.model_dump(),
        onboarded_at=_utcnow(),
        created_by_upn=actor_upn,
        updated_by_upn=actor_upn,
    )
    db.add(asset)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _classify_integrity_error(exc, "Asset onboard failed") from exc

    _record_history(
        db,
        asset_id=asset.id,
        event_type="onboard",
        from_value=None,
        to_value=payload.status_code,
        actor_upn=actor_upn,
        notes=payload.notes,
    )
    db.commit()
    db.refresh(asset)
    return asset


def update_asset(
    db: Session, asset_id: int, payload: AssetUpdate, actor_upn: str | None
) -> Asset:
    asset = get_asset(db, asset_id)
    if asset.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot edit archived asset")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return asset

    for field, value in changes.items():
        setattr(asset, field, value)
    asset.updated_by_upn = actor_upn

    _record_history(
        db,
        asset_id=asset.id,
        event_type="update",
        from_value=None,
        to_value=",".join(changes.keys()),
        actor_upn=actor_upn,
    )
    db.commit()
    db.refresh(asset)
    return asset


def assign_asset(
    db: Session, asset_id: int, payload: AssetAssign, actor_upn: str | None
) -> Asset:
    asset = get_asset(db, asset_id)
    if asset.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot assign archived asset")
    if payload.assigned_upn is None and payload.location_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide assigned_upn or location_id",
        )

    _ensure_location(db, payload.location_id)

    if payload.assigned_upn is not None:
        prev = asset.assigned_upn
        asset.assigned_upn = payload.assigned_upn
        asset.assigned_at = _utcnow()
        if asset.status_code == "in_warehouse":
            asset.status_code = "assigned"
        _record_history(
            db,
            asset_id=asset.id,
            event_type="assign",
            from_value=prev,
            to_value=payload.assigned_upn,
            actor_upn=actor_upn,
            notes=payload.notes,
        )

    if payload.location_id is not None:
        prev_loc = str(asset.location_id) if asset.location_id is not None else None
        asset.location_id = payload.location_id
        _record_history(
            db,
            asset_id=asset.id,
            event_type="location_change",
            from_value=prev_loc,
            to_value=str(payload.location_id),
            actor_upn=actor_upn,
            notes=payload.notes,
        )

    asset.updated_by_upn = actor_upn
    db.commit()
    db.refresh(asset)
    return asset


def unassign_asset(db: Session, asset_id: int, actor_upn: str | None) -> Asset:
    asset = get_asset(db, asset_id)
    if asset.assigned_upn is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Asset is not assigned")

    prev = asset.assigned_upn
    asset.assigned_upn = None
    asset.assigned_at = None
    if asset.status_code == "assigned":
        asset.status_code = "in_warehouse"
    asset.updated_by_upn = actor_upn

    _record_history(
        db,
        asset_id=asset.id,
        event_type="unassign",
        from_value=prev,
        to_value=None,
        actor_upn=actor_upn,
    )
    db.commit()
    db.refresh(asset)
    return asset


def change_status(
    db: Session, asset_id: int, payload: AssetStatusChange, actor_upn: str | None
) -> Asset:
    asset = get_asset(db, asset_id)
    if asset.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot modify archived asset")

    _ensure_status(db, payload.status_code)
    if payload.status_code == asset.status_code:
        return asset

    prev = asset.status_code
    asset.status_code = payload.status_code
    asset.updated_by_upn = actor_upn

    _record_history(
        db,
        asset_id=asset.id,
        event_type="status_change",
        from_value=prev,
        to_value=payload.status_code,
        actor_upn=actor_upn,
        notes=payload.notes,
    )
    db.commit()
    db.refresh(asset)
    return asset


def archive_asset(
    db: Session, asset_id: int, payload: AssetArchive, actor_upn: str | None
) -> Asset:
    asset = get_asset(db, asset_id)
    if asset.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Asset already archived")

    prev = asset.status_code
    asset.archived_at = _utcnow()
    asset.status_code = "retired"
    asset.assigned_upn = None
    asset.assigned_at = None
    asset.updated_by_upn = actor_upn

    _record_history(
        db,
        asset_id=asset.id,
        event_type="archive",
        from_value=prev,
        to_value="retired",
        actor_upn=actor_upn,
        notes=payload.notes,
    )
    db.commit()
    db.refresh(asset)
    return asset


def get_history(db: Session, asset_id: int) -> Iterable[AssetHistory]:
    get_asset(db, asset_id)
    stmt = (
        select(AssetHistory)
        .where(AssetHistory.asset_id == asset_id)
        .order_by(AssetHistory.performed_at.desc(), AssetHistory.id.desc())
    )
    return list(db.scalars(stmt).all())
