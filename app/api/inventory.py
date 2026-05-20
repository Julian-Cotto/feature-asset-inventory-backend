from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
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
    ReservationRow,
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
    q: str | None = Query(
        default=None,
        description=(
            "Substring ILIKE across asset_tag, serial, device name, "
            "manufacturer, model, OS, OS version, assigned UPN, "
            "intune_id, defender_id, series, notes."
        ),
    ),
    asset_type: str | None = Query(default=None),
    status_code: str | None = Query(default=None),
    location_id: int | None = Query(default=None),
    assigned_upn: str | None = Query(default=None, description="Exact match"),
    model: str | None = Query(default=None, description="Exact model match"),
    manufacturer: str | None = Query(default=None, description="Exact manufacturer match"),
    os: str | None = Query(default=None, description="Exact OS match"),
    assignment_state: str | None = Query(
        default=None, description="'assigned' | 'unassigned'"
    ),
    warranty_state: str | None = Query(
        default=None, description="'on' | 'off' | 'unknown'"
    ),
    defender_health: str | None = Query(
        default=None, description="Exact match on defender_health_status"
    ),
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
        os=os,
        assignment_state=assignment_state,
        warranty_state=warranty_state,
        defender_health=defender_health,
        include_archived=include_archived,
        available_only=available_only,
        limit=limit,
        offset=offset,
    )


@router.get("/assets/count")
def count_assets_endpoint(
    q: str | None = Query(default=None),
    asset_type: str | None = Query(default=None),
    status_code: str | None = Query(default=None),
    location_id: int | None = Query(default=None),
    assigned_upn: str | None = Query(default=None),
    model: str | None = Query(default=None),
    manufacturer: str | None = Query(default=None),
    os: str | None = Query(default=None),
    assignment_state: str | None = Query(default=None),
    warranty_state: str | None = Query(default=None),
    defender_health: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    available_only: bool = Query(default=False),
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    """Total row count matching the same filters as `GET /assets`."""

    return {
        "total": svc.count_assets(
            db,
            q=q,
            asset_type=asset_type,
            status_code=status_code,
            location_id=location_id,
            assigned_upn=assigned_upn,
            model=model,
            manufacturer=manufacturer,
            os=os,
            assignment_state=assignment_state,
            warranty_state=warranty_state,
            defender_health=defender_health,
            include_archived=include_archived,
            available_only=available_only,
        )
    }


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


@router.get("/assets/{asset_id}/network-appearances")
def asset_network_appearances(
    asset_id: int,
    refresh: bool = Query(
        False,
        description=(
            "When true, hits Meraki's org-wide /clients/search live and "
            "bypasses the local cache. Slower; use sparingly."
        ),
    ),
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    """Every Meraki network where this asset's MAC has shown up. Uses the
    `meraki_clients` cache (populated on Meraki sync) by default — set
    `?refresh=true` to force a live lookup."""

    from app.models.inventory import Asset
    from app.services import meraki_client_service

    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found.")
    if not asset.mac_address:
        return {
            "asset_id": asset_id,
            "mac": None,
            "source": "cache",
            "appearances": [],
            "note": (
                "No MAC stored on this asset. Run an Intune bulk sync — "
                "MACs come from `wiFiMacAddress` / `ethernetMacAddress`."
            ),
        }

    rows = meraki_client_service.appearances_for_mac(
        db, asset.mac_address, refresh=refresh
    )
    return {
        "asset_id": asset_id,
        "mac": asset.mac_address,
        "source": "live" if refresh else "cache",
        "appearances": [
            {
                "network_id": r.network_id,
                "network_name": r.network_name,
                "description": r.description,
                "ip": r.ip,
                "vlan": r.vlan,
                "first_seen_at": (
                    r.first_seen_at.isoformat()
                    if r.first_seen_at
                    else None
                ),
                "last_seen_at": (
                    r.last_seen_at.isoformat()
                    if r.last_seen_at
                    else None
                ),
            }
            for r in rows
        ],
    }


@router.get("/assets/locator")
def locate_assets(
    q: str = Query(
        ...,
        description=(
            "Comma-separated list of model tokens to locate. Substring "
            "match against override_model, series, and raw model "
            "(case-insensitive). Example: ?q=thinkbook,elitebook"
        ),
    ),
    format: str = Query(
        "json",
        description="json (default), csv, or xlsx for spreadsheet download.",
    ),
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    """Search assets by model token(s) and group by current network. Use
    `format=csv` or `format=xlsx` for downloads with the same data flat."""

    from app.services import asset_locator_service as locator

    tokens = [t.strip() for t in (q or "").split(",") if t.strip()]
    if not tokens:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Provide at least one model token in `q`."
        )

    result = locator.locate(db, tokens, include_archived=include_archived)
    fmt = (format or "json").lower()

    if fmt == "csv":
        body, filename = locator.to_csv_bytes(result)
        return Response(
            content=body,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if fmt == "xlsx":
        body, filename = locator.to_xlsx_bytes(result)
        return Response(
            content=body,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # JSON: serialise the dataclasses by hand to keep the schema obvious.
    return {
        "tokens": result.tokens,
        "matched": result.matched,
        "groups": [
            {
                "network_id": g.network_id,
                "network_name": g.network_name,
                "network_subnet": g.network_subnet,
                "devices": [
                    {
                        "asset_id": d.asset_id,
                        "serial_number": d.serial_number,
                        "asset_type": d.asset_type,
                        "manufacturer": d.manufacturer,
                        "model": d.model,
                        "override_model": d.override_model,
                        "series": d.series,
                        "generation": d.generation,
                        "effective_model": d.effective_model,
                        "os": d.os,
                        "os_version": d.os_version,
                        "status_code": d.status_code,
                        "assigned_upn": d.assigned_upn,
                        "location_name": d.location_name,
                        "intune_device_name": d.intune_device_name,
                        "defender_last_ip": d.defender_last_ip,
                        "network_id": d.network_id,
                        "network_name": d.network_name,
                        "network_subnet": d.network_subnet,
                        "mac_address": d.mac_address,
                        "matched_vlan_id": d.matched_vlan_id,
                        "matched_vlan_name": d.matched_vlan_name,
                        "matched_token": d.matched_token,
                        "seen_networks": [
                            {
                                "network_id": s.network_id,
                                "network_name": s.network_name,
                                "last_seen_at": (
                                    s.last_seen_at.isoformat()
                                    if s.last_seen_at
                                    else None
                                ),
                                "ip": s.ip,
                                "vlan": s.vlan,
                            }
                            for s in d.seen_networks
                        ],
                    }
                    for d in g.devices
                ],
            }
            for g in result.groups
        ],
    }


@router.get("/assets/filter-options")
def get_asset_filter_options(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    """Distinct values per column for populating filter dropdowns. Returns
    sorted unique non-null values across non-archived assets."""
    from sqlalchemy import distinct
    from app.models import Asset as AssetModel

    def _distinct(col) -> list[str]:
        rows = db.execute(
            select(distinct(col))
            .where(AssetModel.archived_at.is_(None))
            .where(col.is_not(None))
            .order_by(col)
        ).all()
        return [r[0] for r in rows if r[0]]

    return {
        "manufacturers": _distinct(AssetModel.manufacturer),
        "os": _distinct(AssetModel.os),
        "defender_health": _distinct(AssetModel.defender_health_status),
    }


@router.get("/reservations", response_model=list[ReservationRow])
def list_reservations_endpoint(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    return svc.list_reservations(db)


@router.get("/reports/fleet")
def report_fleet_endpoint(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    from app.services import report_service
    return report_service.fleet_health(db)


@router.get("/reports/warranty")
def report_warranty_endpoint(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    from app.services import report_service
    return report_service.warranty_report(db)


@router.get("/reports/stock")
def report_stock_endpoint(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    from app.services import report_service
    return report_service.stock_report(db)


@router.get("/reports/shipments")
def report_shipments_endpoint(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    from app.services import report_service
    return report_service.shipments_report(db)


@router.get("/reports/intune")
def report_intune_endpoint(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    from app.services import report_service
    return report_service.intune_report(db)


@router.get("/reports/activity")
def report_activity_endpoint(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    from app.services import report_service
    return report_service.activity_report(db)


@router.get("/reports/security")
def report_security_endpoint(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    from app.services import report_service
    return report_service.security_report(db)


@router.get("/reports/software")
def report_software_endpoint(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    from app.services import report_service
    return report_service.software_report(db)


@router.get("/reports/people")
def report_people_endpoint(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    from app.services import report_service
    return report_service.people_report(db)


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
