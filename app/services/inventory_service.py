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
    DEFAULT_LOCATIONS,
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


def seed_default_locations(db: Session) -> None:
    """Idempotent seed for manually-managed locations (e.g. internal
    warehouses) that aren't sourced from Snowflake. Snowflake sync skips
    these via PROTECTED_LOCATION_CODES."""

    existing = {row.code for row in db.scalars(select(Location)).all()}
    added = False
    for entry in DEFAULT_LOCATIONS:
        if entry["code"] in existing:
            continue
        db.add(Location(**entry, is_active=True))
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
    model: str | None = None,
    manufacturer: str | None = None,
    include_archived: bool = False,
    available_only: bool = False,
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
    if model:
        stmt = stmt.where(Asset.model == model)
    if manufacturer:
        stmt = stmt.where(Asset.manufacturer == manufacturer)
    if not include_archived:
        stmt = stmt.where(Asset.archived_at.is_(None))

    if available_only:
        # Pickable = status=active AND assigned_upn IS NULL AND at a
        # warehouse-type location AND not reserved by an active deployment
        # or open shipment. Mirrors find_unreserved_for_deployment.
        from app.models import (
            Deployment,
            DeploymentItem,
            Location,
            Shipment,
            ShipmentItem,
        )
        from sqlalchemy import not_

        reserved_by_deployment = (
            select(DeploymentItem.asset_id)
            .join(Deployment, Deployment.id == DeploymentItem.deployment_id)
            .where(Deployment.status.in_(("planning", "in_progress")))
        )
        reserved_by_shipment = (
            select(ShipmentItem.asset_id)
            .join(Shipment, Shipment.id == ShipmentItem.shipment_id)
            .where(Shipment.resolution == "open")
            .where(not_(Shipment.carrier_status.in_(("delivered", "exception"))))
        )
        # Available = operational (status=active) AND not assigned to anyone
        # AND sitting at a warehouse-type location AND not reserved.
        stmt = (
            stmt.outerjoin(Location, Location.id == Asset.location_id)
            .where(Asset.status_code == "active")
            .where(Asset.assigned_upn.is_(None))
            .where(Location.type == "warehouse")
            .where(Asset.id.notin_(reserved_by_deployment))
            .where(Asset.id.notin_(reserved_by_shipment))
        )

    stmt = stmt.order_by(Asset.id.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt).all())


def get_asset_facets(db: Session, available_only: bool = False) -> dict:
    """Distinct (asset_type, manufacturer, model, series, generation)
    combinations across non-archived assets, with counts. Frontend uses to
    populate filter dropdowns.

    available_only=True restricts the count + result rows to assets that
    are in_warehouse AND not reserved by an active deployment / open
    shipment. Rows whose available count is zero are dropped — matches
    deployment auto-assign eligibility."""

    from sqlalchemy import func as sql_func

    stmt = (
        select(
            Asset.asset_type,
            Asset.manufacturer,
            Asset.model,
            Asset.series,
            Asset.generation,
            sql_func.count(Asset.id),
        )
        .where(Asset.archived_at.is_(None))
        .group_by(
            Asset.asset_type,
            Asset.manufacturer,
            Asset.model,
            Asset.series,
            Asset.generation,
        )
        .order_by(Asset.asset_type, Asset.manufacturer, Asset.series, Asset.model)
    )

    if available_only:
        from app.models import (
            Deployment,
            DeploymentItem,
            Location,
            Shipment,
            ShipmentItem,
        )
        from sqlalchemy import not_

        reserved_by_deployment = (
            select(DeploymentItem.asset_id)
            .join(Deployment, Deployment.id == DeploymentItem.deployment_id)
            .where(Deployment.status.in_(("planning", "in_progress")))
        )
        reserved_by_shipment = (
            select(ShipmentItem.asset_id)
            .join(Shipment, Shipment.id == ShipmentItem.shipment_id)
            .where(Shipment.resolution == "open")
            .where(not_(Shipment.carrier_status.in_(("delivered", "exception"))))
        )
        stmt = (
            stmt.outerjoin(Location, Location.id == Asset.location_id)
            .where(Asset.status_code == "active")
            .where(Asset.assigned_upn.is_(None))
            .where(Location.type == "warehouse")
            .where(Asset.id.notin_(reserved_by_deployment))
            .where(Asset.id.notin_(reserved_by_shipment))
        )

    rows: list[dict] = []
    for asset_type, manufacturer, model, series, generation, count in db.execute(stmt).all():
        rows.append(
            {
                "asset_type": asset_type,
                "manufacturer": manufacturer,
                "model": model,
                "series": series,
                "generation": generation,
                "count": int(count),
            }
        )
    return {"models": rows}


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


def bulk_set_location(
    db: Session,
    *,
    asset_ids: list[int],
    location_id: int | None,
    actor_upn: str | None,
) -> dict:
    """Set `location_id` on every asset in `asset_ids`. `location_id=None`
    clears the location. Records a `location_change` history entry per
    asset that actually changed. Skips archived assets."""

    if not asset_ids:
        return {"updated": 0, "unchanged": 0, "skipped": 0, "errors": []}

    if location_id is not None:
        loc = db.get(Location, location_id)
        if loc is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Location {location_id} not found"
            )

    updated = 0
    unchanged = 0
    skipped = 0
    errors: list[dict] = []

    assets = db.scalars(select(Asset).where(Asset.id.in_(asset_ids))).all()
    found_ids = {a.id for a in assets}
    missing = set(asset_ids) - found_ids
    for mid in missing:
        errors.append({"asset_id": mid, "error": "not found"})

    for asset in assets:
        try:
            if asset.archived_at is not None:
                skipped += 1
                continue
            if asset.location_id == location_id:
                unchanged += 1
                continue
            prev_loc = (
                str(asset.location_id) if asset.location_id is not None else None
            )
            asset.location_id = location_id
            asset.updated_by_upn = actor_upn
            _record_history(
                db,
                asset_id=asset.id,
                event_type="location_change",
                from_value=prev_loc,
                to_value=str(location_id) if location_id is not None else None,
                actor_upn=actor_upn,
                notes="Bulk location update",
            )
            updated += 1
        except Exception as e:
            errors.append({"asset_id": asset.id, "error": str(e)})

    db.commit()
    return {
        "updated": updated,
        "unchanged": unchanged,
        "skipped": skipped,
        "errors": errors,
    }


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


# ────────────────────────── Intune sync ──────────────────────────────────


_COMPUTER_TYPES = {"laptop", "desktop", "thin_client"}

# Lenovo MTM: 4-10 char alnum starting with a digit. Catches both the short
# machine-type prefix (e.g. 21MV, 20XX) and the full MTM (e.g. 21T1S25T00).
import re as _re
_MTM_RE = _re.compile(r"^[0-9][A-Z0-9]{3,9}$")


def _looks_like_lenovo_mtm(value: str | None) -> bool:
    return bool(value) and bool(_MTM_RE.match(value or ""))


def _looks_like_friendly_name(value: str | None) -> bool:
    """Heuristic: real product names contain whitespace ('ThinkBook 14 G7
    ARP'). Anything without it is almost certainly a raw MTM code."""

    return bool(value) and " " in (value or "").strip()


from dataclasses import dataclass


@dataclass
class VendorEnrichment:
    """Common shape for vendor lookups (Lenovo, Dell). series/generation
    are Lenovo-only — Dell leaves them None."""

    manufacturer: str | None = None
    model: str | None = None
    series: str | None = None
    generation: str | None = None
    warranty_active: bool | None = None
    warranty_end_date: datetime | None = None


_EMPTY_VENDOR = VendorEnrichment()


def _vendor_cache_check(
    db,
    serial: str,
    expected_source: str,
    require_friendly_name: bool = True,
):
    """Shared cache lookup for vendor enrichments. Returns the cached row
    when fresh, source matches, AND warranty data is present. Pre-warranty
    cache rows trigger a re-fetch.

    require_friendly_name: when True (Lenovo), reject rows whose model is
    null or MTM-like — Lenovo lookups exist mostly to upgrade MTM → friendly
    name. When False (Dell), accept rows missing model since the bulk path
    can skip the productdetails fetch entirely."""

    from app.config import get_settings
    from app.services import lookup_service

    cached = lookup_service._read_cache(db, serial)
    if cached is None:
        return None
    if not lookup_service._is_fresh(cached, get_settings().lookup_cache_ttl_hours):
        return None
    if cached.source != expected_source:
        return None
    if require_friendly_name and not _looks_like_friendly_name(cached.model):
        return None
    if cached.warranty_active is None and cached.warranty_end_date is None:
        return None
    return cached


def _enrich_lenovo(
    db: Session,
    serial: str,
) -> VendorEnrichment:
    from app.config import get_settings
    from app.services import lookup_service

    cached = _vendor_cache_check(db, serial, "lenovo")
    if cached is not None:
        return VendorEnrichment(
            manufacturer="Lenovo" if cached.model else None,
            model=cached.model,
            series=cached.series,
            generation=cached.generation,
            warranty_active=cached.warranty_active,
            warranty_end_date=cached.warranty_end_date,
        )

    timeout = get_settings().lookup_http_timeout_seconds
    result = lookup_service._lenovo_lookup(serial, timeout)
    try:
        lookup_service._write_cache(db, result)
    except Exception:
        pass
    if result.model or result.warranty_end_date is not None or result.warranty_active is not None:
        return VendorEnrichment(
            manufacturer="Lenovo" if result.model else None,
            model=result.model,
            series=result.series,
            generation=result.generation,
            warranty_active=result.warranty_active,
            warranty_end_date=result.warranty_end_date,
        )
    return _EMPTY_VENDOR


def _enrich_dell(
    db: Session,
    serial: str,
    skip_product_name: bool = False,
) -> VendorEnrichment:
    from app.config import get_settings
    from app.services import lookup_service

    # When the caller already has a model, accept cache rows without one.
    cached = _vendor_cache_check(
        db,
        serial,
        "dell",
        require_friendly_name=not skip_product_name,
    )
    if cached is not None:
        return VendorEnrichment(
            manufacturer="Dell" if cached.model else None,
            model=cached.model,
            series=None,
            generation=None,
            warranty_active=cached.warranty_active,
            warranty_end_date=cached.warranty_end_date,
        )

    timeout = get_settings().dell_lookup_timeout_seconds
    result = lookup_service._dell_lookup(serial, timeout, skip_product_name=skip_product_name)
    try:
        lookup_service._write_cache(db, result)
    except Exception:
        pass
    if result.model or result.warranty_end_date is not None or result.warranty_active is not None:
        return VendorEnrichment(
            manufacturer=result.manufacturer or ("Dell" if not skip_product_name else None),
            model=result.model,
            series=None,
            generation=None,
            warranty_active=result.warranty_active,
            warranty_end_date=result.warranty_end_date,
        )
    return _EMPTY_VENDOR


def _enrich_vendor(
    db: Session,
    serial: str | None,
    manufacturer: str | None = None,
    skip_product_name: bool = False,
) -> VendorEnrichment:
    """Dispatch by manufacturer:
      - "Dell" → Dell warranty (+ product name unless skip_product_name)
      - "Lenovo" or unknown/blank → Lenovo friendly name + warranty
      - anything else → empty (skip; we only support these two vendors)
    """

    if not serial:
        return _EMPTY_VENDOR

    m = (manufacturer or "").strip().lower()
    try:
        if "dell" in m:
            return _enrich_dell(db, serial, skip_product_name=skip_product_name)
        if not m or "lenovo" in m:
            return _enrich_lenovo(db, serial)
    except Exception:
        return _EMPTY_VENDOR

    return _EMPTY_VENDOR


# Back-compat alias for callers that still reference the old name in tests
_enrich_lenovo_friendly_model = _enrich_vendor


def sync_asset_from_intune(
    db: Session, asset_id: int, actor_upn: str | None
) -> dict:
    """Re-fetch from Intune by serial. Fill blank fields, store intune_id,
    auto-assign UPN if asset has no assignee yet."""

    from app.services import intune_service

    asset = get_asset(db, asset_id)

    if asset.asset_type not in _COMPUTER_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Intune sync not supported for asset_type '{asset.asset_type}'.",
        )

    result = intune_service.lookup_by_serial(asset.serial_number)
    if result.error:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Intune lookup failed: {result.error}",
        )

    asset.intune_synced_at = _utcnow()

    if not result.found or result.device is None:
        asset.updated_by_upn = actor_upn
        db.commit()
        db.refresh(asset)
        return {"asset": asset, "found": False, "changed": []}

    d = result.device
    changed: list[str] = []

    # Enrich from vendor (Lenovo or Dell) for friendly product name + warranty.
    enrichment_manufacturer = asset.manufacturer or d.manufacturer
    enrichment = _enrich_vendor(
        db, d.serial_number, manufacturer=enrichment_manufacturer
    )
    friendly_model = enrichment.model
    friendly_series = enrichment.series
    friendly_generation = enrichment.generation

    def maybe_set(field: str, new_value: str | None) -> None:
        if not new_value:
            return
        if getattr(asset, field):
            return  # don't overwrite manual edits
        setattr(asset, field, new_value)
        changed.append(field)

    maybe_set("manufacturer", d.manufacturer)

    # Model: upgrade MTM-looking value to Lenovo friendly name when available
    if friendly_model and not _looks_like_lenovo_mtm(friendly_model):
        if not asset.model or (_looks_like_lenovo_mtm(asset.model) and asset.model != friendly_model):
            asset.model = friendly_model
            changed.append("model")
    else:
        maybe_set("model", d.model)

    maybe_set("series", friendly_series)
    maybe_set("generation", friendly_generation)
    maybe_set("os", d.operating_system)
    maybe_set("os_version", d.os_version)

    # Warranty: always overwrite when Lenovo gave us data — source of truth
    if enrichment.warranty_end_date is not None or enrichment.warranty_active is not None:
        if asset.warranty_active != enrichment.warranty_active:
            asset.warranty_active = enrichment.warranty_active
            changed.append("warranty_active")
        if asset.warranty_end_date != enrichment.warranty_end_date:
            asset.warranty_end_date = enrichment.warranty_end_date
            changed.append("warranty_end_date")
        asset.warranty_synced_at = _utcnow()

    if d.intune_id and asset.intune_id != d.intune_id:
        asset.intune_id = d.intune_id
        changed.append("intune_id")

    # Intune-sourced fields are always overwritten (Intune is source of truth)
    def overwrite(field: str, new_value) -> None:
        if getattr(asset, field) != new_value:
            setattr(asset, field, new_value)
            changed.append(field)

    overwrite("intune_device_name", d.device_name)
    overwrite("intune_managed_by", d.managed_by)
    overwrite("intune_ownership", d.ownership)
    overwrite("intune_compliance", d.compliance)

    # last_sync_dt comes back as ISO string; parse to datetime for the column
    if d.last_sync_dt:
        try:
            parsed = datetime.fromisoformat(d.last_sync_dt.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            if asset.intune_last_check_in != parsed:
                asset.intune_last_check_in = parsed
                changed.append("intune_last_check_in")
        except Exception:
            pass

    # Auto-assign UPN if asset has no assignee yet — status untouched,
    # assignment is its own thing (Intune-driven).
    if d.assigned_upn and not asset.assigned_upn:
        asset.assigned_upn = d.assigned_upn
        asset.assigned_at = _utcnow()
        changed.append("assigned_upn")
        _record_history(
            db,
            asset_id=asset.id,
            event_type="assign",
            from_value=None,
            to_value=d.assigned_upn,
            actor_upn=actor_upn,
            notes="Pulled from Intune sync",
        )

    asset.updated_by_upn = actor_upn

    if changed:
        _record_history(
            db,
            asset_id=asset.id,
            event_type="update",
            from_value=None,
            to_value=",".join(changed),
            actor_upn=actor_upn,
            notes="Fields populated from Intune sync",
        )

    db.commit()
    db.refresh(asset)
    return {"asset": asset, "found": True, "changed": changed}


def _intune_to_asset_type(operating_system: str | None, chassis: str | None) -> str | None:
    """Map an Intune device to our asset_type. Returns None to skip (mobile
    OSes only — phones / tablets aren't tracked in this inventory).

    Heuristic:
      - iOS / iPadOS / Android → skip
      - chassis explicitly says desktop / tower / all-in-one → desktop
      - everything else (Windows / macOS / Linux / unknown) → laptop
        (user can edit per-asset if a desktop got mislabeled)
    """

    os = (operating_system or "").lower()
    chassis_norm = (chassis or "").lower()

    if any(k in os for k in ("ios", "ipados", "android")):
        return None

    if any(
        k in chassis_norm
        for k in ("desktop", "tower", "allinone", "all-in-one")
    ):
        return "desktop"

    return "laptop"


def _parse_intune_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def bulk_sync_from_intune(db: Session, actor_upn: str | None) -> dict:
    """Pull every managedDevice from Intune. Create assets that don't exist
    by serial; update those that do. Skip non-computer chassis types and
    devices without a serial number."""

    from app.services import intune_service

    try:
        intune_devices = intune_service.list_all_managed_devices()
    except Exception as e:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Intune bulk fetch failed: {e}"
        ) from e

    created = 0
    updated = 0
    skipped_no_serial = 0
    skipped_non_computer = 0
    errors: list[dict] = []
    now = _utcnow()

    for d in intune_devices:
        try:
            serial = (d.serial_number or "").strip()
            if not serial:
                skipped_no_serial += 1
                continue

            asset_type = _intune_to_asset_type(d.operating_system, d.chassis_type)

            # Look up by serial
            existing = (
                db.query(Asset).filter(Asset.serial_number == serial).one_or_none()
            )

            # Enrich with vendor (Lenovo / Dell) friendly name + warranty.
            enrichment_manufacturer = (
                existing.manufacturer if existing is not None else None
            ) or d.manufacturer
            enrichment = _enrich_vendor(
                db, serial, manufacturer=enrichment_manufacturer
            )
            friendly_model = enrichment.model
            friendly_series = enrichment.series
            friendly_generation = enrichment.generation
            warranty_synced = (
                enrichment.warranty_end_date is not None
                or enrichment.warranty_active is not None
            )

            if existing is None:
                # Skip non-computer types when creating new assets — phones,
                # tablets, etc. don't fit the inventory model.
                if asset_type is None:
                    skipped_non_computer += 1
                    continue

                new_asset = Asset(
                    serial_number=serial,
                    asset_type=asset_type,
                    manufacturer=d.manufacturer,
                    model=friendly_model or d.model,
                    series=friendly_series,
                    generation=friendly_generation,
                    os=d.operating_system,
                    os_version=d.os_version,
                    status_code="active",
                    assigned_upn=d.assigned_upn,
                    assigned_at=now if d.assigned_upn else None,
                    intune_id=d.intune_id,
                    intune_synced_at=now,
                    intune_device_name=d.device_name,
                    intune_managed_by=d.managed_by,
                    intune_ownership=d.ownership,
                    intune_compliance=d.compliance,
                    intune_last_check_in=_parse_intune_dt(d.last_sync_dt),
                    warranty_active=enrichment.warranty_active,
                    warranty_end_date=enrichment.warranty_end_date,
                    warranty_synced_at=now if warranty_synced else None,
                    notes="Imported from Intune bulk sync",
                    created_by_upn=actor_upn,
                    updated_by_upn=actor_upn,
                )
                db.add(new_asset)
                db.flush()

                _record_history(
                    db,
                    asset_id=new_asset.id,
                    event_type="onboard",
                    from_value=None,
                    to_value="intune-bulk",
                    actor_upn=actor_upn,
                    notes="Created from Intune bulk sync",
                )
                if d.assigned_upn:
                    _record_history(
                        db,
                        asset_id=new_asset.id,
                        event_type="assign",
                        from_value=None,
                        to_value=d.assigned_upn,
                        actor_upn=actor_upn,
                        notes="Auto-assigned from Intune",
                    )
                db.commit()
                created += 1
            else:
                # Update existing — same overlay rules as per-asset sync
                changed: list[str] = []

                def maybe_set(field: str, new_value: str | None) -> None:
                    if not new_value:
                        return
                    if getattr(existing, field):
                        return
                    setattr(existing, field, new_value)
                    changed.append(field)

                maybe_set("manufacturer", d.manufacturer)

                # Model: upgrade MTM-looking value to friendly name when available
                if friendly_model and not _looks_like_lenovo_mtm(friendly_model):
                    if not existing.model or (
                        _looks_like_lenovo_mtm(existing.model)
                        and existing.model != friendly_model
                    ):
                        existing.model = friendly_model
                        changed.append("model")
                else:
                    maybe_set("model", d.model)

                maybe_set("series", friendly_series)
                maybe_set("generation", friendly_generation)
                maybe_set("os", d.operating_system)
                maybe_set("os_version", d.os_version)

                if d.intune_id and existing.intune_id != d.intune_id:
                    existing.intune_id = d.intune_id
                    changed.append("intune_id")

                # Always-overwrite Intune-sourced fields
                for field, new_value in (
                    ("intune_device_name", d.device_name),
                    ("intune_managed_by", d.managed_by),
                    ("intune_ownership", d.ownership),
                    ("intune_compliance", d.compliance),
                ):
                    if getattr(existing, field) != new_value:
                        setattr(existing, field, new_value)
                        changed.append(field)

                last_check_in = _parse_intune_dt(d.last_sync_dt)
                if last_check_in and existing.intune_last_check_in != last_check_in:
                    existing.intune_last_check_in = last_check_in
                    changed.append("intune_last_check_in")

                # Warranty: always overwrite when Lenovo gave us data
                if warranty_synced:
                    if existing.warranty_active != enrichment.warranty_active:
                        existing.warranty_active = enrichment.warranty_active
                        changed.append("warranty_active")
                    if existing.warranty_end_date != enrichment.warranty_end_date:
                        existing.warranty_end_date = enrichment.warranty_end_date
                        changed.append("warranty_end_date")
                    existing.warranty_synced_at = now

                # Auto-assign UPN if asset has no assignee yet — status
                # untouched; assignment is its own thing now.
                if d.assigned_upn and not existing.assigned_upn:
                    existing.assigned_upn = d.assigned_upn
                    existing.assigned_at = now
                    changed.append("assigned_upn")
                    _record_history(
                        db,
                        asset_id=existing.id,
                        event_type="assign",
                        from_value=None,
                        to_value=d.assigned_upn,
                        actor_upn=actor_upn,
                        notes="Auto-assigned from Intune bulk sync",
                    )

                existing.intune_synced_at = now
                existing.updated_by_upn = actor_upn

                if changed:
                    _record_history(
                        db,
                        asset_id=existing.id,
                        event_type="update",
                        from_value=None,
                        to_value=",".join(changed)[:1024],
                        actor_upn=actor_upn,
                        notes="Updated from Intune bulk sync",
                    )
                    updated += 1

                db.commit()
        except Exception as e:
            db.rollback()
            errors.append(
                {
                    "intune_id": d.intune_id,
                    "serial": d.serial_number,
                    "error": str(e),
                },
            )

    return {
        "total_devices": len(intune_devices),
        "created": created,
        "updated": updated,
        "skipped_no_serial": skipped_no_serial,
        "skipped_non_computer": skipped_non_computer,
        "errors": errors,
    }


def refresh_vendor_models(db: Session, actor_upn: str | None) -> dict:
    """Pull friendly product names + warranty status for every asset that's
    Lenovo or Dell-branded (or has no manufacturer set yet). Bypasses Intune;
    only touches manufacturer / model / series / generation / warranty fields.

    Vendor lookups run in a thread pool (Dell calls are slow due to multiple
    HTTP round trips). DB writes happen on the main thread to avoid SQLite
    write contention.

    Returns counts: checked, updated, no_match, errors[].
    """

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from sqlalchemy import or_ as sql_or

    from app.db import SessionLocal

    # Eligible: not archived AND (manufacturer null/empty OR Lenovo OR Dell)
    stmt = (
        select(Asset)
        .where(Asset.archived_at.is_(None))
        .where(
            sql_or(
                Asset.manufacturer.is_(None),
                Asset.manufacturer == "",
                Asset.manufacturer.ilike("lenovo"),
                Asset.manufacturer.ilike("dell"),
                Asset.manufacturer.ilike("dell inc%"),
            )
        )
        .order_by(Asset.id.asc())
    )
    assets = list(db.execute(stmt).scalars().all())

    # Snapshot lookup inputs before we hand off to workers (avoid sharing
    # ORM objects across threads / sessions).
    inputs: list[tuple[int, str, str | None, bool]] = [
        (a.id, a.serial_number, a.manufacturer, bool(a.model and a.model.strip()))
        for a in assets
    ]

    def _worker(item: tuple[int, str, str | None, bool]):
        asset_id, serial, manufacturer, has_model = item
        try:
            with SessionLocal() as wdb:
                e = _enrich_vendor(
                    wdb,
                    serial,
                    manufacturer=manufacturer,
                    skip_product_name=has_model,
                )
            return (asset_id, e, None)
        except Exception as ex:
            return (asset_id, _EMPTY_VENDOR, str(ex))

    enrichments: dict[int, VendorEnrichment] = {}
    worker_errors: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_worker, item) for item in inputs]
        for fut in as_completed(futures):
            asset_id, enrichment, err = fut.result()
            enrichments[asset_id] = enrichment
            if err:
                worker_errors[asset_id] = err

    checked = 0
    updated = 0
    no_match = 0
    errors: list[dict] = []
    now = _utcnow()

    for asset in assets:
        checked += 1
        try:
            err = worker_errors.get(asset.id)
            if err:
                errors.append(
                    {"asset_id": asset.id, "serial": asset.serial_number, "error": err}
                )
                continue

            enrichment = enrichments.get(asset.id, _EMPTY_VENDOR)
            friendly_model = enrichment.model
            friendly_series = enrichment.series
            friendly_generation = enrichment.generation
            warranty_synced = (
                enrichment.warranty_end_date is not None
                or enrichment.warranty_active is not None
            )
            if not friendly_model and not warranty_synced:
                no_match += 1
                continue

            changed: list[str] = []

            # Manufacturer fill if blank — vendor-aware
            if not asset.manufacturer and enrichment.manufacturer:
                asset.manufacturer = enrichment.manufacturer
                changed.append("manufacturer")

            # Model: overwrite MTM-looking; fill blank; otherwise keep manual edit
            if friendly_model and (
                not asset.model
                or (
                    _looks_like_lenovo_mtm(asset.model)
                    and not _looks_like_lenovo_mtm(friendly_model)
                )
            ):
                if asset.model != friendly_model:
                    asset.model = friendly_model
                    changed.append("model")

            # Series / generation: fill blank only
            if friendly_series and not asset.series:
                asset.series = friendly_series
                changed.append("series")
            if friendly_generation and not asset.generation:
                asset.generation = friendly_generation
                changed.append("generation")

            # Warranty: always overwrite when Lenovo gave us data
            if warranty_synced:
                if asset.warranty_active != enrichment.warranty_active:
                    asset.warranty_active = enrichment.warranty_active
                    changed.append("warranty_active")
                if asset.warranty_end_date != enrichment.warranty_end_date:
                    asset.warranty_end_date = enrichment.warranty_end_date
                    changed.append("warranty_end_date")
                asset.warranty_synced_at = now

            if changed:
                asset.updated_by_upn = actor_upn
                _record_history(
                    db,
                    asset_id=asset.id,
                    event_type="update",
                    from_value=None,
                    to_value=",".join(changed)[:1024],
                    actor_upn=actor_upn,
                    notes="Lenovo friendly model refresh",
                )
                db.commit()
                updated += 1
            else:
                no_match += 1
        except Exception as e:
            db.rollback()
            errors.append(
                {
                    "asset_id": asset.id,
                    "serial": asset.serial_number,
                    "error": str(e),
                }
            )

    return {
        "checked": checked,
        "updated": updated,
        "no_match": no_match,
        "errors": errors,
    }


# ===================== Dashboard stats =====================


def get_dashboard_stats(db: Session) -> dict:
    """Aggregate asset / warranty / shipment / deployment / Intune metrics
    plus 30-day onboarding + warranty-change time series for sparklines.

    Single endpoint feeding the home dashboard. Recomputed per call —
    queries are small (counts + grouped counts) and SQLite handles in <50ms
    for fleets in the low thousands.
    """

    from datetime import date, timedelta
    from sqlalchemy import case, func as sql_func

    from app.models import (
        AssetHistory,
        Deployment,
        Shipment,
    )

    now = _utcnow()
    today = now.date()
    cutoff_30d = now - timedelta(days=30)
    cutoff_7d = now - timedelta(days=7)

    # ── assets ───────────────────────────────────────────────
    base = select(Asset).where(Asset.archived_at.is_(None))
    total_assets = db.scalar(select(sql_func.count()).select_from(base.subquery())) or 0

    by_status_rows = db.execute(
        select(Asset.status_code, sql_func.count(Asset.id))
        .where(Asset.archived_at.is_(None))
        .group_by(Asset.status_code)
    ).all()
    by_status = [{"code": code, "count": int(c)} for code, c in by_status_rows]

    by_type_rows = db.execute(
        select(Asset.asset_type, sql_func.count(Asset.id))
        .where(Asset.archived_at.is_(None))
        .group_by(Asset.asset_type)
    ).all()
    by_type = [{"type": t, "count": int(c)} for t, c in by_type_rows]

    # ── warranty ─────────────────────────────────────────────
    def _count(condition) -> int:
        return int(
            db.scalar(
                select(sql_func.count(Asset.id)).where(
                    Asset.archived_at.is_(None), condition
                )
            )
            or 0
        )

    warranty_on = _count(Asset.warranty_active.is_(True))
    warranty_off = _count(Asset.warranty_active.is_(False))
    warranty_unknown = _count(Asset.warranty_active.is_(None))

    def _expiring_within(days: int) -> int:
        return _count(
            (Asset.warranty_active.is_(True))
            & (Asset.warranty_end_date.is_not(None))
            & (Asset.warranty_end_date <= now + timedelta(days=days))
        )

    expiring_30d = _expiring_within(30)
    expiring_60d = _expiring_within(60)
    expiring_90d = _expiring_within(90)

    # ── intune ───────────────────────────────────────────────
    last_bulk_sync = db.scalar(
        select(sql_func.max(Asset.intune_synced_at))
    )
    stale_7d = _count(
        (Asset.intune_id.is_not(None)) & (Asset.intune_last_check_in < cutoff_7d)
    )
    synced_count = _count(Asset.intune_id.is_not(None))

    # ── shipments ────────────────────────────────────────────
    def _ship_count(condition) -> int:
        return int(
            db.scalar(select(sql_func.count(Shipment.id)).where(condition)) or 0
        )

    ship_open = _ship_count(Shipment.resolution == "open")
    ship_in_transit = _ship_count(
        (Shipment.resolution == "open") & (Shipment.carrier_status == "in_transit")
    )
    ship_exception = _ship_count(
        (Shipment.resolution == "open") & (Shipment.carrier_status == "exception")
    )

    # ── deployments ──────────────────────────────────────────
    def _dep_count(condition) -> int:
        return int(
            db.scalar(select(sql_func.count(Deployment.id)).where(condition)) or 0
        )

    dep_planning = _dep_count(Deployment.status == "planning")
    dep_in_progress = _dep_count(Deployment.status == "in_progress")
    dep_completed_30d = _dep_count(
        (Deployment.status == "completed") & (Deployment.completed_at >= cutoff_30d)
    )

    # ── series (30-day windows) ──────────────────────────────
    onboards_30d = _series_30d(
        db,
        select(
            sql_func.date(AssetHistory.performed_at).label("d"),
            sql_func.count(AssetHistory.id),
        )
        .where(AssetHistory.event_type == "onboard")
        .where(AssetHistory.performed_at >= cutoff_30d)
        .group_by("d"),
        today,
    )

    warranty_changes_30d = _series_30d(
        db,
        select(
            sql_func.date(AssetHistory.performed_at).label("d"),
            sql_func.count(AssetHistory.id),
        )
        .where(AssetHistory.event_type == "update")
        .where(AssetHistory.to_value.ilike("%warranty%"))
        .where(AssetHistory.performed_at >= cutoff_30d)
        .group_by("d"),
        today,
    )

    return {
        "assets": {
            "total": int(total_assets),
            "by_status": by_status,
            "by_type": by_type,
        },
        "warranty": {
            "on": warranty_on,
            "off": warranty_off,
            "unknown": warranty_unknown,
            "expiring_30d": expiring_30d,
            "expiring_60d": expiring_60d,
            "expiring_90d": expiring_90d,
        },
        "intune": {
            "last_bulk_sync_at": last_bulk_sync,
            "stale_7d_count": stale_7d,
            "synced_count": synced_count,
        },
        "shipments": {
            "open": ship_open,
            "in_transit": ship_in_transit,
            "exception": ship_exception,
        },
        "deployments": {
            "planning": dep_planning,
            "in_progress": dep_in_progress,
            "completed_30d": dep_completed_30d,
        },
        "onboards_30d": onboards_30d,
        "warranty_changes_30d": warranty_changes_30d,
    }


def _series_30d(db: Session, stmt, today) -> list[dict]:
    """Run a (date, count) grouping query and pad to a complete 30-day
    window so the frontend doesn't have to fill gaps. Date column may come
    back as date or string depending on the dialect."""

    from datetime import date, timedelta

    rows = db.execute(stmt).all()
    by_date: dict[str, int] = {}
    for d, c in rows:
        if isinstance(d, date):
            key = d.isoformat()
        else:
            key = str(d)[:10]
        by_date[key] = int(c)

    points: list[dict] = []
    for offset in range(29, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        points.append({"date": day, "count": by_date.get(day, 0)})
    return points
