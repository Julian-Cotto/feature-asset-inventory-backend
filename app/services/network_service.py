"""Networks: Meraki Dashboard → local cache, plus asset ↔ network linking.

We pull a flat list of networks from Meraki and store one row per network.
For each row we capture:
  - identity (Meraki id, name, productTypes, timezone)
  - subnet (first VLAN's CIDR, or singleLan, or "" if neither)
  - firewall_ip = MX appliance LAN gateway IP (from the same VLAN/lan record)
  - wan_ip = MX uplink public IP (from the org-wide uplink statuses call)
  - switch_ips = JSON list of MS device management IPs

After sync we re-link assets to networks:
  - networking gear (gateway/switch/ap from Meraki) → match Meraki device's
    networkId → asset.network_id by Asset.serial_number.
  - client devices (laptop/desktop/thin_client) → match defender_last_ip
    against each network's subnet_cidr (ipaddress.ip_network containment).

Idempotent. Safe to re-run."""

from __future__ import annotations

import ipaddress
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import Asset, Network, NetworkVlan
from app.services import lookup_service

logger = logging.getLogger(__name__)

# How many Meraki HTTP calls to run in parallel when pre-fetching per-network
# subnet/gateway info. Meraki's per-org rate limit is 10 req/s. Each network
# may need 2 calls (vlans, then singleLan fallback on 400/404), and other
# org-level calls happen alongside, so 4 workers keeps headroom under the
# limit. 429s still get retried with Retry-After in `lookup_service._meraki_get`.
_PREFETCH_WORKERS = 4


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class NetworkSyncResult:
    fetched: int
    created: int
    updated: int
    archived: int
    skipped: int
    assets_linked: int
    errors: list[dict]


# ───────────────────────── Meraki → local upsert ─────────────────────────


def _extract_wan_ip(uplink_entry: dict | None) -> str | None:
    """Pick the first uplink with status=active and a public IP."""
    if not uplink_entry:
        return None
    for u in uplink_entry.get("uplinks") or []:
        ip = u.get("publicIp") or u.get("ip")
        if ip and u.get("status") in (None, "active", "ready"):
            return str(ip)
    # fallback to any IP we can find
    for u in uplink_entry.get("uplinks") or []:
        ip = u.get("publicIp") or u.get("ip")
        if ip:
            return str(ip)
    return None


# Match priorities for finding the "Corp VLAN" on each Meraki network.
# We try (in order):
#   1. A VLAN whose `name` contains "corp" (case-insensitive). Many orgs
#      keep VLAN names consistent across sites even when numeric IDs vary
#      site-by-site, so name matching wins.
#   2. A VLAN with id == CORP_VLAN_ID (this org's nominal "VLAN 30").
# Either match is treated as the Corp VLAN for IP-based client linking.
CORP_VLAN_ID = 30
CORP_VLAN_NAME_SUBSTR = "corp"


def _extract_subnet_and_gateway(network_id: str) -> tuple[str | None, str | None]:
    """Default subnet + gateway for the network (lowest-id VLAN, typically
    the management VLAN). Falls back to singleLan when VLANs aren't enabled.
    Returns (subnet_cidr, applianceIp)."""

    vlans = lookup_service.get_meraki_appliance_vlans(network_id)
    if vlans:
        sorted_vlans = sorted(vlans, key=lambda v: int(v.get("id") or 9999))
        v = sorted_vlans[0]
        return (v.get("subnet") or None, v.get("applianceIp") or None)

    single = lookup_service.get_meraki_appliance_single_lan(network_id)
    if single:
        return (single.get("subnet") or None, single.get("applianceIp") or None)

    return (None, None)


def _pick_corp_vlan(vlans: list[dict]) -> dict | None:
    """Choose the corp/client VLAN from a list of Meraki VLAN records.
    Name match (contains "corp") wins; numeric id fallback if no name hit."""

    for v in vlans:
        name = (v.get("name") or "").lower()
        if CORP_VLAN_NAME_SUBSTR in name:
            return v
    for v in vlans:
        try:
            if int(v.get("id") or 0) == CORP_VLAN_ID:
                return v
        except (TypeError, ValueError):
            continue
    return None


def _extract_corp_vlan(network_id: str) -> tuple[str | None, str | None]:
    """Return (subnet_cidr, applianceIp) for the Corp VLAN if present,
    else (None, None). Separate from the default-VLAN picker so the UI can
    surface both — the management subnet and the actual client subnet."""

    vlans = lookup_service.get_meraki_appliance_vlans(network_id)
    v = _pick_corp_vlan(vlans)
    if v is None:
        return (None, None)
    return (v.get("subnet") or None, v.get("applianceIp") or None)


def _switch_ips_for_network(
    network_id: str,
    devices_by_network: dict[str, list[dict]],
) -> list[str]:
    out: list[str] = []
    for d in devices_by_network.get(network_id, []):
        model = (d.get("model") or "").upper()
        if not model.startswith("MS"):
            continue
        ip = d.get("lanIp")
        if ip:
            out.append(str(ip))
    return sorted(set(out))


@dataclass
class _NetworkPrefetch:
    """Per-network HTTP-derived facts, computed before the DB tx opens.

    `vlans` is the full Meraki VLAN list (or a 1-item synthetic list for
    singleLan networks) — gets persisted into the `network_vlans` table so
    the locator can match any device IP into any VLAN."""
    subnet: str | None
    gateway: str | None
    corp_subnet: str | None
    corp_gateway: str | None
    vlans: list[dict] = field(default_factory=list)


def _prefetch_network_details(
    network_records: list[dict],
) -> dict[str, _NetworkPrefetch]:
    """Hit Meraki's VLAN endpoint (one call per network — returns all VLANs)
    in parallel and derive (default subnet, default gateway, corp-VLAN
    subnet, corp-VLAN gateway) for each appliance-capable network.

    Runs *before* any DB transaction opens so the upsert loop is purely
    in-memory + DB writes — no HTTP IO holding a SQLite write lock open."""

    candidates = [
        (n.get("id") or "").strip()
        for n in network_records
        if (n.get("productTypes") or []) and "appliance" in (n.get("productTypes") or [])
    ]
    candidates = [c for c in candidates if c]

    out: dict[str, _NetworkPrefetch] = {}
    if not candidates:
        return out

    def _fetch_one(nid: str) -> tuple[str, _NetworkPrefetch]:
        try:
            # One VLAN-list call gives us everything; pick default + corp
            # without hitting Meraki twice.
            vlans = lookup_service.get_meraki_appliance_vlans(nid)
            if vlans:
                sorted_vlans = sorted(
                    vlans, key=lambda v: int(v.get("id") or 9999)
                )
                default_v = sorted_vlans[0]
                subnet = default_v.get("subnet") or None
                gateway = default_v.get("applianceIp") or None
                corp_v = _pick_corp_vlan(vlans)
                corp_subnet = corp_v.get("subnet") if corp_v else None
                corp_gateway = corp_v.get("applianceIp") if corp_v else None

                # Diagnostic — surfaces WHY corp ended up None per network.
                logger.info(
                    "meraki net %s vlans=%s corp_match=%s",
                    nid,
                    [
                        f"{v.get('id')}:{v.get('name')}"
                        for v in sorted_vlans
                    ],
                    f"{corp_v.get('id')}:{corp_v.get('name')}" if corp_v else "none",
                )
                return (
                    nid,
                    _NetworkPrefetch(
                        subnet=subnet,
                        gateway=gateway,
                        corp_subnet=corp_subnet,
                        corp_gateway=corp_gateway,
                        vlans=sorted_vlans,
                    ),
                )

            single = lookup_service.get_meraki_appliance_single_lan(nid)
            if single:
                logger.info(
                    "meraki net %s has no VLANs (singleLan mode); subnet=%s",
                    nid,
                    single.get("subnet"),
                )
                # Treat single-LAN as a synthetic "VLAN 0" so the same
                # downstream code path persists + matches against it.
                synthetic = [
                    {
                        "id": 0,
                        "name": "Single LAN",
                        "subnet": single.get("subnet"),
                        "applianceIp": single.get("applianceIp"),
                    }
                ]
                return (
                    nid,
                    _NetworkPrefetch(
                        subnet=single.get("subnet") or None,
                        gateway=single.get("applianceIp") or None,
                        corp_subnet=None,
                        corp_gateway=None,
                        vlans=synthetic,
                    ),
                )
            logger.info(
                "meraki net %s returned no VLANs and no singleLan", nid
            )
        except Exception:
            logger.exception("prefetch subnet for %s failed", nid)
        return (nid, _NetworkPrefetch(None, None, None, None, []))

    with ThreadPoolExecutor(max_workers=_PREFETCH_WORKERS) as ex:
        futures = [ex.submit(_fetch_one, c) for c in candidates]
        for f in as_completed(futures):
            nid, pf = f.result()
            out[nid] = pf
    return out


def _upsert_vlans_for_network(
    db: Session,
    network_row: Network,
    fresh_vlans: list[dict],
) -> None:
    """Replace the local VLAN list for a network with the Meraki one.
    Inserts new VLAN rows, updates existing rows in-place, deletes VLANs
    that disappeared from Meraki. No-op when `fresh_vlans` is empty."""

    existing_rows = (
        db.execute(
            select(NetworkVlan).where(NetworkVlan.network_id == network_row.id)
        )
        .scalars()
        .all()
    )
    by_vid: dict[int, NetworkVlan] = {r.meraki_vlan_id: r for r in existing_rows}

    seen_vids: set[int] = set()
    for v in fresh_vlans:
        try:
            vid = int(v.get("id") or 0)
        except (TypeError, ValueError):
            continue
        name = v.get("name") or None
        subnet = v.get("subnet") or None
        appliance_ip = v.get("applianceIp") or None

        row = by_vid.get(vid)
        if row is None:
            db.add(
                NetworkVlan(
                    network_id=network_row.id,
                    meraki_vlan_id=vid,
                    name=name,
                    subnet_cidr=subnet,
                    appliance_ip=appliance_ip,
                )
            )
        else:
            row.name = name
            row.subnet_cidr = subnet
            row.appliance_ip = appliance_ip
        seen_vids.add(vid)

    # Delete VLANs that no longer exist on the Meraki side.
    for vid, row in by_vid.items():
        if vid in seen_vids:
            continue
        db.delete(row)


def sync_from_meraki(db: Session, actor_upn: str | None) -> NetworkSyncResult:
    """Pull every network in the org, upsert into `networks`. Re-link
    assets afterwards. Returns counts + per-row errors.

    Implementation note: every Meraki HTTP call happens BEFORE we open a
    DB transaction (or between commits). SQLite serialises writers, so a
    write transaction that stays open across slow HTTP calls causes
    "database is locked" for every other request. The pre-fetch pattern
    here keeps the DB tx short and tight."""

    # 1) All HTTP IO up front — no DB tx held during these.
    network_records = lookup_service.list_meraki_network_records()
    if not network_records:
        # Meraki unconfigured or empty org — still link assets we have.
        linked = link_assets_to_networks(db)
        return NetworkSyncResult(
            fetched=0,
            created=0,
            updated=0,
            archived=0,
            skipped=0,
            assets_linked=linked,
            errors=[],
        )

    uplinks = lookup_service.list_meraki_org_uplink_statuses()
    try:
        all_devices = lookup_service.list_all_meraki_devices()
    except Exception:
        all_devices = []
    devices_by_network: dict[str, list[dict]] = {}
    for d in all_devices:
        nid = d.get("networkId")
        if nid:
            devices_by_network.setdefault(nid, []).append(d)

    prefetch_by_id = _prefetch_network_details(network_records)

    # 2) Now the DB phase — every record is in memory, loop is fast.
    now = _utcnow()
    settings_org_id = None
    try:
        from app.config import get_settings

        settings_org_id = get_settings().meraki_org_id.strip() or None
    except Exception:
        pass

    fetched = len(network_records)
    created = 0
    updated = 0
    skipped = 0
    archived = 0
    errors: list[dict] = []
    seen_ids: set[str] = set()

    try:
        for n in network_records:
            meraki_id = (n.get("id") or "").strip()
            if not meraki_id:
                skipped += 1
                continue
            try:
                seen_ids.add(meraki_id)

                name = (n.get("name") or meraki_id).strip()
                product_types = n.get("productTypes") or []
                tz = n.get("timeZone") or None

                pf = prefetch_by_id.get(
                    meraki_id, _NetworkPrefetch(None, None, None, None)
                )
                subnet = pf.subnet
                firewall_ip = pf.gateway
                corp_subnet = pf.corp_subnet
                corp_gateway = pf.corp_gateway
                wan_ip = _extract_wan_ip(uplinks.get(meraki_id))
                switch_ips = _switch_ips_for_network(meraki_id, devices_by_network)

                existing = db.execute(
                    select(Network).where(Network.meraki_network_id == meraki_id)
                ).scalar_one_or_none()

                if existing is None:
                    row = Network(
                        meraki_network_id=meraki_id,
                        meraki_org_id=settings_org_id,
                        name=name,
                        subnet_cidr=subnet,
                        wan_ip=wan_ip,
                        firewall_ip=firewall_ip,
                        corp_vlan_subnet=corp_subnet,
                        corp_vlan_gateway_ip=corp_gateway,
                        switch_ips_json=json.dumps(switch_ips) if switch_ips else None,
                        product_types_csv=",".join(product_types) or None,
                        timezone=tz,
                        meraki_synced_at=now,
                        created_by_upn=actor_upn,
                        updated_by_upn=actor_upn,
                    )
                    db.add(row)
                    db.flush()  # need row.id for vlan upsert below
                    _upsert_vlans_for_network(db, row, pf.vlans)
                    created += 1
                else:
                    changed = False
                    if existing.name != name:
                        existing.name = name
                        changed = True
                    if existing.subnet_cidr != subnet:
                        existing.subnet_cidr = subnet
                        changed = True
                    if existing.wan_ip != wan_ip:
                        existing.wan_ip = wan_ip
                        changed = True
                    if existing.firewall_ip != firewall_ip:
                        existing.firewall_ip = firewall_ip
                        changed = True
                    if existing.corp_vlan_subnet != corp_subnet:
                        existing.corp_vlan_subnet = corp_subnet
                        changed = True
                    if existing.corp_vlan_gateway_ip != corp_gateway:
                        existing.corp_vlan_gateway_ip = corp_gateway
                        changed = True
                    new_switch_json = (
                        json.dumps(switch_ips) if switch_ips else None
                    )
                    if existing.switch_ips_json != new_switch_json:
                        existing.switch_ips_json = new_switch_json
                        changed = True
                    ptcsv = ",".join(product_types) or None
                    if existing.product_types_csv != ptcsv:
                        existing.product_types_csv = ptcsv
                        changed = True
                    if existing.timezone != tz:
                        existing.timezone = tz
                        changed = True
                    if existing.archived_at is not None:
                        existing.archived_at = None
                        changed = True
                    existing.meraki_synced_at = now
                    _upsert_vlans_for_network(db, existing, pf.vlans)
                    if changed:
                        existing.updated_by_upn = actor_upn
                        updated += 1
            except Exception as e:
                logger.exception("network sync row failed for %s", n.get("id"))
                errors.append({"meraki_network_id": n.get("id"), "error": str(e)})

        # Archive locally-known networks no longer in Meraki.
        local_rows = db.execute(select(Network)).scalars().all()
        for row in local_rows:
            if row.meraki_network_id in seen_ids:
                continue
            if row.archived_at is None:
                row.archived_at = now
                archived += 1

        db.commit()
    except Exception:
        # If anything escapes the per-row try/except (commit failure,
        # bulk archive failure) make sure the session isn't left in a
        # pending-rollback state for the caller's next query.
        db.rollback()
        raise

    # 3) Re-link assets in its own transaction.
    try:
        assets_linked = link_assets_to_networks(db, meraki_devices=all_devices)
    except Exception:
        db.rollback()
        logger.exception("post-sync asset relink failed")
        assets_linked = 0

    return NetworkSyncResult(
        fetched=fetched,
        created=created,
        updated=updated,
        archived=archived,
        skipped=skipped,
        assets_linked=assets_linked,
        errors=errors,
    )


# ───────────────────────── Asset ↔ Network linking ─────────────────────────


@dataclass
class VlanSubnet:
    """Compiled VLAN row for fast IP membership checks. Built by
    `build_vlan_index()` and reused across the linker + locator."""

    network: ipaddress.IPv4Network | ipaddress.IPv6Network
    network_id: int
    network_name: str
    vlan_id: int
    vlan_name: str | None
    cidr: str


def build_vlan_index(db: Session) -> list[VlanSubnet]:
    """One-shot SQL fetch that joins active networks → their VLANs and
    pre-parses each VLAN's CIDR. Tolerates bad CIDRs (skips them).

    Public so the locator can reuse the exact same matching surface.
    Sorted by prefix-length descending so a /26 wins over an overlapping
    /22 when both contain an IP."""

    rows = db.execute(
        select(NetworkVlan, Network)
        .join(Network, Network.id == NetworkVlan.network_id)
        .where(Network.archived_at.is_(None))
        .where(NetworkVlan.subnet_cidr.is_not(None))
    ).all()

    out: list[VlanSubnet] = []
    for v, n in rows:
        try:
            parsed = ipaddress.ip_network(v.subnet_cidr, strict=False)
        except (ValueError, TypeError):
            continue
        out.append(
            VlanSubnet(
                network=parsed,
                network_id=n.id,
                network_name=n.display_name,
                vlan_id=v.meraki_vlan_id,
                vlan_name=v.name,
                cidr=v.subnet_cidr,
            )
        )
    out.sort(key=lambda s: (-s.network.prefixlen, s.network_id, s.vlan_id))
    return out


def match_ip(ip_str: str, index: list[VlanSubnet]) -> VlanSubnet | None:
    """Find the most-specific VLAN containing `ip_str`, or None. Silent on
    bad inputs — Defender often returns blank strings."""

    if not ip_str:
        return None
    try:
        ip = ipaddress.ip_address(ip_str.strip())
    except (ValueError, TypeError):
        return None
    for s in index:
        if ip in s.network:
            return s
    return None


def link_assets_to_networks(
    db: Session,
    *,
    meraki_devices: list[dict] | None = None,
) -> int:
    """For each asset:
      - networking gear with a Meraki serial → look up its Meraki device
        record, set network_id by serial→networkId.
      - client devices with defender_last_ip → walk every VLAN across every
        network, pick the most-specific subnet match.

    Returns number of assets whose network_id changed."""

    if meraki_devices is None:
        try:
            meraki_devices = lookup_service.list_all_meraki_devices()
        except Exception:
            meraki_devices = []
    serial_to_mid: dict[str, str] = {}
    for d in meraki_devices or []:
        s = (d.get("serial") or "").strip()
        nid = (d.get("networkId") or "").strip()
        if s and nid:
            serial_to_mid[s.upper()] = nid

    nets = (
        db.execute(select(Network).where(Network.archived_at.is_(None)))
        .scalars()
        .all()
    )
    mid_to_local_id: dict[str, int] = {n.meraki_network_id: n.id for n in nets}

    vlan_index = build_vlan_index(db)

    changed = 0
    assets = (
        db.execute(select(Asset).where(Asset.archived_at.is_(None)))
        .scalars()
        .all()
    )
    for a in assets:
        new_network_id: int | None = a.network_id

        if a.serial_number and a.serial_number.upper() in serial_to_mid:
            mid = serial_to_mid[a.serial_number.upper()]
            new_network_id = mid_to_local_id.get(mid) or None
        elif a.defender_last_ip and vlan_index:
            hit = match_ip(a.defender_last_ip, vlan_index)
            new_network_id = hit.network_id if hit else None

        if new_network_id != a.network_id:
            a.network_id = new_network_id
            changed += 1

    if changed:
        db.commit()
    return changed


# ───────────────────────── CRUD-ish helpers for API ─────────────────────────


def list_networks(db: Session, *, include_archived: bool = False) -> list[Network]:
    stmt = select(Network)
    if not include_archived:
        stmt = stmt.where(Network.archived_at.is_(None))
    stmt = stmt.order_by(Network.name.asc())
    return list(db.execute(stmt).scalars())


def get_network(db: Session, network_id: int) -> Network | None:
    return db.get(Network, network_id)


def parse_switch_ips(row: Network) -> list[str]:
    if not row.switch_ips_json:
        return []
    try:
        v = json.loads(row.switch_ips_json)
        return [str(x) for x in v] if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []


def list_assets_for_network(db: Session, network_id: int) -> list[Asset]:
    stmt = (
        select(Asset)
        .where(Asset.network_id == network_id)
        .where(Asset.archived_at.is_(None))
        .order_by(Asset.asset_type.asc(), Asset.serial_number.asc())
    )
    return list(db.execute(stmt).scalars())


def update_network(
    db: Session,
    network_id: int,
    *,
    name_override: str | None | object = ...,  # ... = sentinel "no change"
    location_id: int | None | object = ...,
    notes: str | None | object = ...,
    actor_upn: str | None = None,
) -> Network | None:
    row = db.get(Network, network_id)
    if row is None:
        return None
    if name_override is not ...:
        row.name_override = name_override or None
    if location_id is not ...:
        row.location_id = location_id
    if notes is not ...:
        row.notes = notes or None
    row.updated_by_upn = actor_upn
    db.commit()
    return row
