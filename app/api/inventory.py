from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import (
    require_admin,
    require_operator,
    require_permissions,
    require_reader,
)
from app.platform.auth_context import RequestAuthContext
from app.schemas.inventory import (
    AssetArchive,
    AssetAssign,
    AssetBulkLocation,
    AssetCreate,
    AssetHistoryOut,
    AssetOut,
    AssetStatusChange,
    AssetStatusCreate,
    AssetStatusOut,
    AssetStatusUpdate,
    AssetUpdate,
    DashboardStats,
    LocationCreate,
    LocationOut,
    LocationUpdate,
)
from app.services import inventory_service as svc


router = APIRouter(tags=["inventory"])


def require_view():
    return require_permissions(*get_settings().auth_required_permissions)


def require_write():
    return require_permissions("asset-inventory.write")


def require_manage():
    return require_permissions("asset-inventory.manage")


# ----- statuses -----

@router.get("/statuses", response_model=list[AssetStatusOut])
def list_statuses_endpoint(
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    return svc.list_statuses(db, include_inactive=include_inactive)


@router.post("/statuses", response_model=AssetStatusOut, status_code=status.HTTP_201_CREATED)
def create_status_endpoint(
    payload: AssetStatusCreate,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_manage()),
):
    return svc.create_status(db, payload)


@router.patch("/statuses/{code}", response_model=AssetStatusOut)
def update_status_endpoint(
    code: str,
    payload: AssetStatusUpdate,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_manage()),
):
    return svc.update_status(db, code, payload)


# ----- locations -----

@router.get("/locations", response_model=list[LocationOut])
def list_locations_endpoint(
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    return svc.list_locations(db, include_inactive=include_inactive)


@router.get("/locations/{location_id}", response_model=LocationOut)
def get_location_endpoint(
    location_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    return svc.get_location(db, location_id)


@router.post("/locations", response_model=LocationOut, status_code=status.HTTP_201_CREATED)
def create_location_endpoint(
    payload: LocationCreate,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
):
    return svc.create_location(db, payload, actor_upn=auth.user_name)


@router.patch("/locations/{location_id}", response_model=LocationOut)
def update_location_endpoint(
    location_id: int,
    payload: LocationUpdate,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
):
    return svc.update_location(db, location_id, payload, actor_upn=auth.user_name)


@router.delete("/locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_location_endpoint(
    location_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_manage()),
):
    svc.delete_location(db, location_id)
    return None


@router.post("/locations/sync")
def sync_locations_endpoint(
    dry_run: bool = Query(default=False),
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
):
    """Mirror corporate locations from Snowflake `LOCATIONS_ALL_V` into the
    local `Location` table. Idempotent — upsert by `code` (LOCATIONID).
    Locations missing from the Snowflake result are deactivated, not deleted."""

    from app.services import snowflake_service

    actor_upn = getattr(auth, "user_upn", None) or getattr(auth, "email", None)
    try:
        return snowflake_service.sync_locations(
            db, actor_upn=actor_upn, dry_run=dry_run
        )
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e


# ----- assets -----

@router.get("/assets", response_model=list[AssetOut])
def list_assets_endpoint(
    q: str | None = Query(default=None, description="Search asset_tag/serial/model/manufacturer"),
    asset_type: str | None = Query(default=None),
    status_code: str | None = Query(default=None),
    location_id: int | None = Query(default=None),
    assigned_upn: str | None = Query(default=None),
    model: str | None = Query(default=None, description="Exact model match"),
    manufacturer: str | None = Query(default=None, description="Exact manufacturer match"),
    include_archived: bool = Query(default=False),
    available_only: bool = Query(
        default=False,
        description=(
            "Only return assets that are in_warehouse AND not reserved by "
            "an active deployment or open shipment. Forces status_code="
            "in_warehouse."
        ),
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    return svc.list_assets(
        db,
        q=q,
        asset_type=asset_type,
        status_code=status_code,
        location_id=location_id,
        assigned_upn=assigned_upn,
        model=model,
        manufacturer=manufacturer,
        include_archived=include_archived,
        available_only=available_only,
        limit=limit,
        offset=offset,
    )


@router.get("/assets/facets")
def get_asset_facets_endpoint(
    available_only: bool = Query(
        default=False,
        description=(
            "When true, only count assets that are in_warehouse AND not "
            "reserved by an active deployment / open shipment."
        ),
    ),
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    return svc.get_asset_facets(db, available_only=available_only)


@router.get("/stats", response_model=DashboardStats)
def get_dashboard_stats_endpoint(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    """Aggregated dashboard metrics: assets, warranty, Intune freshness,
    shipments, deployments, plus 30-day onboarding + warranty-change series."""

    return svc.get_dashboard_stats(db)


@router.post("/assets/bulk-location")
def bulk_set_location_endpoint(
    payload: AssetBulkLocation,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
):
    """Set the same location on many assets at once. `location_id=null`
    clears the location. Records a `location_change` history entry per
    asset that actually changed."""

    actor_upn = getattr(auth, "user_upn", None) or getattr(auth, "email", None)
    return svc.bulk_set_location(
        db,
        asset_ids=payload.asset_ids,
        location_id=payload.location_id,
        actor_upn=actor_upn,
    )


@router.post("/assets/vendor-refresh")
def refresh_vendor_models_endpoint(
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
):
    """Refresh Lenovo + Dell friendly model names and warranty status for
    every Lenovo / Dell / unknown-manufacturer asset."""

    actor_upn = getattr(auth, "user_upn", None) or getattr(auth, "email", None)
    return svc.refresh_vendor_models(db, actor_upn=actor_upn)


@router.get("/assets/lookup", response_model=AssetOut)
def lookup_asset_endpoint(
    serial: str = Query(min_length=1, max_length=128, description="Serial number from scan"),
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    asset = svc.lookup_by_serial(db, serial)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No asset with serial '{serial}'")
    return asset


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset_endpoint(
    asset_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    return svc.get_asset(db, asset_id)


@router.post("/assets", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
def onboard_asset_endpoint(
    payload: AssetCreate,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.onboard_asset(db, payload, actor_upn=auth.user_name)


@router.patch("/assets/{asset_id}", response_model=AssetOut)
def update_asset_endpoint(
    asset_id: int,
    payload: AssetUpdate,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.update_asset(db, asset_id, payload, actor_upn=auth.user_name)


@router.post("/assets/{asset_id}/assign", response_model=AssetOut)
def assign_asset_endpoint(
    asset_id: int,
    payload: AssetAssign,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.assign_asset(db, asset_id, payload, actor_upn=auth.user_name)


@router.post("/assets/{asset_id}/unassign", response_model=AssetOut)
def unassign_asset_endpoint(
    asset_id: int,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.unassign_asset(db, asset_id, actor_upn=auth.user_name)


@router.post("/assets/{asset_id}/status", response_model=AssetOut)
def change_status_endpoint(
    asset_id: int,
    payload: AssetStatusChange,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.change_status(db, asset_id, payload, actor_upn=auth.user_name)


@router.post("/assets/{asset_id}/archive", response_model=AssetOut)
def archive_asset_endpoint(
    asset_id: int,
    payload: AssetArchive,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
):
    return svc.archive_asset(db, asset_id, payload, actor_upn=auth.user_name)


@router.get("/assets/{asset_id}/history", response_model=list[AssetHistoryOut])
def get_history_endpoint(
    asset_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    return svc.get_history(db, asset_id)
