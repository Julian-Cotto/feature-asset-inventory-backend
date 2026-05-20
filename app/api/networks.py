"""Networks API.

Cached read of `networks` (source of truth = Meraki Dashboard). Sync pulls
every network in the org + their primary subnet + WAN/firewall/switch IPs
and re-links assets to networks (gateway/switch/AP by serial, client
devices by Defender IP subnet match)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import require_permissions
from app.models.inventory import Asset, Network, NetworkVlan
from app.platform.auth_context import RequestAuthContext
from app.services import network_service


router = APIRouter(prefix="/networks", tags=["networks"])


def require_view():
    return require_permissions(*get_settings().auth_required_permissions)


def require_write():
    return require_permissions("asset-inventory.write")


def require_manage():
    return require_permissions("asset-inventory.manage")


# ────────────────────────── Schemas ─────────────────────────────────────


class VlanOut(BaseModel):
    id: int
    meraki_vlan_id: int
    name: str | None
    subnet_cidr: str | None
    appliance_ip: str | None


class NetworkOut(BaseModel):
    id: int
    meraki_network_id: str
    meraki_org_id: str | None
    name: str
    name_override: str | None
    display_name: str
    location_id: int | None
    location_name: str | None
    subnet_cidr: str | None
    wan_ip: str | None
    firewall_ip: str | None
    corp_vlan_subnet: str | None
    corp_vlan_gateway_ip: str | None
    switch_ips: list[str]
    product_types: list[str]
    timezone: str | None
    notes: str | None
    archived_at: datetime | None
    meraki_synced_at: datetime | None
    asset_count: int = 0
    vlan_count: int = 0
    vlans: list[VlanOut] = []
    created_at: datetime
    updated_at: datetime


class NetworkAssetOut(BaseModel):
    """Compact asset summary used in network detail panels (no full Asset)."""
    asset_id: int
    asset_type: str
    serial_number: str
    manufacturer: str | None
    model: str | None
    device_name: str | None
    assigned_upn: str | None
    defender_last_ip: str | None
    status_code: str
    link_reason: str  # "meraki_serial" | "ip_match" | "meraki_client" | "manual"
    meraki_last_seen_at: datetime | None = None
    meraki_last_ip: str | None = None
    meraki_vlan: int | None = None


class NetworkDetailOut(BaseModel):
    network: NetworkOut
    networking_equipment: list[NetworkAssetOut]
    client_devices: list[NetworkAssetOut]


class NetworkUpdateIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name_override: str | None = None
    location_id: int | None = None
    notes: str | None = None
    # Use ellipsis-style sentinels in service layer; pydantic just passes
    # through whatever the client sent.


class NetworkSyncResponse(BaseModel):
    fetched: int
    created: int
    updated: int
    archived: int
    skipped: int
    assets_linked: int
    errors: list[dict]


# ────────────────────────── Helpers ─────────────────────────────────────


_CLIENT_TYPES = {"laptop", "desktop", "thin_client"}
_NETWORK_GEAR_TYPES = {"gateway", "switch", "ap"}


def _vlan_to_out(v: NetworkVlan) -> VlanOut:
    return VlanOut(
        id=v.id,
        meraki_vlan_id=v.meraki_vlan_id,
        name=v.name,
        subnet_cidr=v.subnet_cidr,
        appliance_ip=v.appliance_ip,
    )


def _to_out(
    row: Network,
    *,
    asset_count: int = 0,
    vlan_count: int = 0,
    include_vlans: bool = False,
) -> NetworkOut:
    """Serialize a Network row. `include_vlans=True` triggers a lazy load of
    the VLAN collection — fine for the detail endpoint, avoid in list
    responses where it'd N+1 over hundreds of networks."""
    vlans = list(row.vlans) if include_vlans else []
    return NetworkOut(
        id=row.id,
        meraki_network_id=row.meraki_network_id,
        meraki_org_id=row.meraki_org_id,
        name=row.name,
        name_override=row.name_override,
        display_name=row.display_name,
        location_id=row.location_id,
        location_name=row.location_name,
        subnet_cidr=row.subnet_cidr,
        wan_ip=row.wan_ip,
        firewall_ip=row.firewall_ip,
        corp_vlan_subnet=row.corp_vlan_subnet,
        corp_vlan_gateway_ip=row.corp_vlan_gateway_ip,
        switch_ips=network_service.parse_switch_ips(row),
        product_types=(
            row.product_types_csv.split(",") if row.product_types_csv else []
        ),
        timezone=row.timezone,
        notes=row.notes,
        archived_at=row.archived_at,
        meraki_synced_at=row.meraki_synced_at,
        asset_count=asset_count,
        vlan_count=len(vlans) if include_vlans else vlan_count,
        vlans=[_vlan_to_out(v) for v in vlans] if include_vlans else [],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _asset_to_out(
    a: Asset,
    *,
    link_reason: str,
    meraki_last_seen_at: datetime | None = None,
    meraki_last_ip: str | None = None,
    meraki_vlan: int | None = None,
) -> NetworkAssetOut:
    return NetworkAssetOut(
        asset_id=a.id,
        asset_type=a.asset_type,
        serial_number=a.serial_number,
        manufacturer=a.manufacturer,
        model=a.override_model or a.model,
        device_name=a.intune_device_name,
        assigned_upn=a.assigned_upn,
        defender_last_ip=a.defender_last_ip,
        status_code=a.status_code,
        link_reason=link_reason,
        meraki_last_seen_at=meraki_last_seen_at,
        meraki_last_ip=meraki_last_ip,
        meraki_vlan=meraki_vlan,
    )


# ────────────────────────── Endpoints ───────────────────────────────────


@router.get("", response_model=list[NetworkOut])
def list_networks(
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
) -> list[NetworkOut]:
    rows = network_service.list_networks(db, include_archived=include_archived)
    if not rows:
        return []

    # Asset + VLAN counts per network (two aggregate queries rather than
    # N lazy loads).
    from sqlalchemy import func

    ids = [r.id for r in rows]
    asset_rows = db.execute(
        select(Asset.network_id, func.count(Asset.id))
        .where(Asset.network_id.in_(ids))
        .where(Asset.archived_at.is_(None))
        .group_by(Asset.network_id)
    ).all()
    asset_counts = {nid: cnt for nid, cnt in asset_rows}

    vlan_rows = db.execute(
        select(NetworkVlan.network_id, func.count(NetworkVlan.id))
        .where(NetworkVlan.network_id.in_(ids))
        .group_by(NetworkVlan.network_id)
    ).all()
    vlan_counts = {nid: cnt for nid, cnt in vlan_rows}

    return [
        _to_out(
            r,
            asset_count=asset_counts.get(r.id, 0),
            vlan_count=vlan_counts.get(r.id, 0),
        )
        for r in rows
    ]


@router.post("/sync", response_model=NetworkSyncResponse)
def sync_networks(
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_manage()),
) -> NetworkSyncResponse:
    """Pull every Meraki network + its first VLAN subnet, MX WAN/LAN IPs,
    switch management IPs. Re-link assets to networks afterwards.

    Requires MERAKI_API_KEY + MERAKI_ORG_ID env vars."""

    actor_upn = getattr(auth, "user_upn", None) or getattr(auth, "email", None)
    result = network_service.sync_from_meraki(db, actor_upn=actor_upn)
    return NetworkSyncResponse(
        fetched=result.fetched,
        created=result.created,
        updated=result.updated,
        archived=result.archived,
        skipped=result.skipped,
        assets_linked=result.assets_linked,
        errors=result.errors,
    )


@router.post("/relink-assets", response_model=dict)
def relink_assets(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_manage()),
) -> dict:
    """Re-run the asset ↔ network linker without re-pulling Meraki data.
    Useful after manual Defender IP refresh."""
    changed = network_service.link_assets_to_networks(db)
    return {"assets_linked": changed}


@router.get("/{network_id}/vlans-debug", response_model=dict)
def debug_vlans(
    network_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_manage()),
) -> dict:
    """Diagnostic: returns the raw VLAN list Meraki has for this network,
    so you can see exactly which IDs/names exist and figure out why the
    Corp VLAN matcher missed. Use when Corp VLAN columns are empty."""
    from app.services import lookup_service

    row = network_service.get_network(db, network_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Network not found.")
    try:
        vlans = lookup_service.get_meraki_appliance_vlans(row.meraki_network_id)
    except Exception as e:
        return {
            "meraki_network_id": row.meraki_network_id,
            "error": str(e),
            "vlans": [],
        }
    single = None
    if not vlans:
        try:
            single = lookup_service.get_meraki_appliance_single_lan(
                row.meraki_network_id
            )
        except Exception:
            single = None
    picked = network_service._pick_corp_vlan(vlans) if vlans else None
    return {
        "meraki_network_id": row.meraki_network_id,
        "name": row.name,
        "vlans": vlans,
        "single_lan": single,
        "corp_match": (
            {"id": picked.get("id"), "name": picked.get("name")}
            if picked
            else None
        ),
    }


@router.get("/{network_id}", response_model=NetworkDetailOut)
def get_network(
    network_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
) -> NetworkDetailOut:
    row = network_service.get_network(db, network_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Network not found.")

    assets = network_service.list_assets_for_network(db, network_id)

    # Build the device record index once so we can mark which assets are
    # linked by Meraki serial (authoritative) vs IP-match (best-effort).
    from app.services import lookup_service

    serial_index: set[str] = set()
    try:
        for d in lookup_service.list_all_meraki_devices():
            s = (d.get("serial") or "").strip().upper()
            nid = (d.get("networkId") or "").strip()
            if s and nid == row.meraki_network_id:
                serial_index.add(s)
    except Exception:
        # Meraki unconfigured / offline → fall back to "ip_match" for all.
        serial_index = set()

    gear: list[NetworkAssetOut] = []
    clients: list[NetworkAssetOut] = []
    client_asset_ids: set[int] = set()
    for a in assets:
        is_meraki_match = (
            a.serial_number and a.serial_number.upper() in serial_index
        )
        link_reason = (
            "meraki_serial"
            if is_meraki_match
            else ("ip_match" if a.defender_last_ip else "manual")
        )
        out = _asset_to_out(a, link_reason=link_reason)
        if a.asset_type in _NETWORK_GEAR_TYPES:
            gear.append(out)
        else:
            clients.append(out)
            client_asset_ids.add(a.id)

    # Augment with MAC-matched assets that the Meraki client cache says
    # have appeared on this network — even if their stale defender_last_ip
    # doesn't fit any VLAN here. This is the "where has device X been
    # seen?" answer.
    from app.services import meraki_client_service

    pairs = meraki_client_service.assets_seen_on_network(db, network_id)
    for client_row, matched_asset in pairs:
        if matched_asset is None:
            continue
        if matched_asset.id in client_asset_ids:
            continue
        if matched_asset.asset_type in _NETWORK_GEAR_TYPES:
            continue
        clients.append(
            _asset_to_out(
                matched_asset,
                link_reason="meraki_client",
                meraki_last_seen_at=client_row.last_seen_at,
                meraki_last_ip=client_row.ip,
                meraki_vlan=client_row.vlan,
            )
        )
        client_asset_ids.add(matched_asset.id)

    return NetworkDetailOut(
        network=_to_out(row, asset_count=len(assets), include_vlans=True),
        networking_equipment=gear,
        client_devices=clients,
    )


@router.patch("/{network_id}", response_model=NetworkOut)
def update_network(
    network_id: int,
    payload: NetworkUpdateIn,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
) -> NetworkOut:
    # We can't tell from Pydantic which fields were set vs left default
    # without `model_fields_set`. Use that to pick sentinels.
    fields_set = payload.model_fields_set
    actor_upn = getattr(auth, "user_upn", None) or getattr(auth, "email", None)
    row = network_service.update_network(
        db,
        network_id,
        name_override=payload.name_override if "name_override" in fields_set else ...,
        location_id=payload.location_id if "location_id" in fields_set else ...,
        notes=payload.notes if "notes" in fields_set else ...,
        actor_upn=actor_upn,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Network not found.")
    return _to_out(row, include_vlans=True)
