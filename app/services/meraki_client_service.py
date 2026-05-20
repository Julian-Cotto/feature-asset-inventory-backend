"""Meraki client cache: per-network snapshot of who's been seen recently.

Source: `GET /networks/{networkId}/clients?timespan=…` per network. Stored
in the `meraki_clients` table indexed by MAC so we can answer "which
networks has this asset's MAC been seen on" without re-hitting the Meraki
API for each lookup.

Sync strategy mirrors the network/VLAN prefetch — fan out HTTP calls in
parallel (bounded), collect results in memory, then upsert into the DB in
one tight transaction. Keeps the SQLite write lock short."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import MerakiClient, Network
from app.services import intune_service, lookup_service

logger = logging.getLogger(__name__)

# Meraki org rate limit is ~10 req/s. 4 parallel client-list fetches leaves
# headroom for the network/uplink/vlan calls happening alongside on a full
# Meraki sync. Each call can paginate multiple times for big networks.
_CLIENT_FETCH_WORKERS = 4

# Default lookback window when bulk-syncing.
DEFAULT_TIMESPAN_SECONDS = 7 * 24 * 60 * 60  # 7 days


@dataclass
class ClientSyncResult:
    networks_visited: int
    clients_total: int
    clients_inserted: int
    clients_updated: int
    clients_deleted: int
    errors: list[dict]


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is not None:
            d = d.astimezone(timezone.utc).replace(tzinfo=None)
        return d
    except (TypeError, ValueError):
        return None


def sync_clients_for_all_networks(
    db: Session,
    *,
    timespan_seconds: int = DEFAULT_TIMESPAN_SECONDS,
) -> ClientSyncResult:
    """Pull recent clients for every active network. Upsert by (network_id,
    mac). Stale entries (in DB but not in the fresh response) get deleted —
    so the cache reflects the lookback window exactly."""

    nets = (
        db.execute(select(Network).where(Network.archived_at.is_(None)))
        .scalars()
        .all()
    )
    if not nets:
        return ClientSyncResult(0, 0, 0, 0, 0, [])

    # 1) HTTP fan-out, no DB tx held.
    raw_by_local_id: dict[int, list[dict]] = {}
    errors: list[dict] = []

    def _fetch(net: Network) -> tuple[int, list[dict], str | None]:
        try:
            clients = lookup_service.list_meraki_clients_for_network(
                net.meraki_network_id,
                timespan_seconds=timespan_seconds,
            )
            return (net.id, clients, None)
        except Exception as e:
            logger.exception(
                "client fetch failed for network %s (%s)",
                net.id,
                net.meraki_network_id,
            )
            return (net.id, [], str(e))

    with ThreadPoolExecutor(max_workers=_CLIENT_FETCH_WORKERS) as ex:
        futures = [ex.submit(_fetch, n) for n in nets]
        for f in as_completed(futures):
            nid, clients, err = f.result()
            raw_by_local_id[nid] = clients
            if err:
                errors.append({"network_id": nid, "error": err})

    # 2) Upsert phase — one transaction, tight loop.
    inserted = 0
    updated = 0
    deleted = 0
    total = 0
    try:
        for net in nets:
            fresh = raw_by_local_id.get(net.id, [])
            existing_rows = (
                db.execute(
                    select(MerakiClient).where(MerakiClient.network_id == net.id)
                )
                .scalars()
                .all()
            )
            by_mac: dict[str, MerakiClient] = {
                r.mac: r for r in existing_rows
            }
            seen_macs: set[str] = set()
            for c in fresh:
                raw_mac = c.get("mac")
                mac = intune_service._normalise_mac(raw_mac)
                if not mac:
                    continue
                seen_macs.add(mac)
                vlan_raw = c.get("vlan")
                try:
                    vlan_val = int(vlan_raw) if vlan_raw is not None else None
                except (TypeError, ValueError):
                    vlan_val = None
                description = (
                    c.get("description")
                    or c.get("dhcpHostname")
                    or None
                )
                row = by_mac.get(mac)
                fields = dict(
                    description=description,
                    ip=c.get("ip") or None,
                    vlan=vlan_val,
                    user=c.get("user") or None,
                    manufacturer=c.get("manufacturer") or None,
                    os=c.get("os") or None,
                    first_seen_at=_parse_iso(c.get("firstSeen")),
                    last_seen_at=_parse_iso(c.get("lastSeen")),
                )
                if row is None:
                    db.add(
                        MerakiClient(
                            network_id=net.id,
                            mac=mac,
                            **fields,
                        )
                    )
                    inserted += 1
                else:
                    for k, v in fields.items():
                        setattr(row, k, v)
                    updated += 1
                total += 1
            # Drop rows that vanished from the response.
            for mac, row in by_mac.items():
                if mac not in seen_macs:
                    db.delete(row)
                    deleted += 1
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("client sync upsert failed")
        raise

    return ClientSyncResult(
        networks_visited=len(nets),
        clients_total=total,
        clients_inserted=inserted,
        clients_updated=updated,
        clients_deleted=deleted,
        errors=errors,
    )


# ────────────────────────── Lookups ──────────────────────────


@dataclass
class AssetAppearance:
    """One row in the 'this asset has shown up on these networks' list."""
    network_id: int
    network_name: str
    mac: str
    description: str | None
    ip: str | None
    vlan: int | None
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    source: str  # "cache" | "live"


def appearances_for_mac(
    db: Session, mac: str, *, refresh: bool = False
) -> list[AssetAppearance]:
    """Cached appearances of `mac` across all networks. When `refresh=True`,
    hits Meraki's org-wide /clients/search live and ignores cache (still
    upserts into cache as a side effect)."""

    mac_norm = intune_service._normalise_mac(mac)
    if not mac_norm:
        return []

    if refresh:
        try:
            records = lookup_service.search_meraki_clients_by_mac(mac_norm)
        except Exception:
            records = []
        # Map meraki networkId → local Network row
        nids = {r.get("network", {}).get("id") for r in records if isinstance(r, dict)}
        nets = (
            db.execute(
                select(Network).where(Network.meraki_network_id.in_(nids))
            )
            .scalars()
            .all()
        )
        nets_by_mid: dict[str, Network] = {n.meraki_network_id: n for n in nets}
        out: list[AssetAppearance] = []
        for r in records:
            if not isinstance(r, dict):
                continue
            mn = r.get("network") or {}
            local = nets_by_mid.get(mn.get("id") or "")
            if local is None:
                continue
            out.append(
                AssetAppearance(
                    network_id=local.id,
                    network_name=local.display_name,
                    mac=mac_norm,
                    description=r.get("description") or r.get("dhcpHostname"),
                    ip=r.get("ip"),
                    vlan=r.get("vlan"),
                    first_seen_at=_parse_iso(r.get("firstSeen")),
                    last_seen_at=_parse_iso(r.get("lastSeen")),
                    source="live",
                )
            )
        return sorted(
            out,
            key=lambda a: (a.last_seen_at is None, -(a.last_seen_at.timestamp() if a.last_seen_at else 0)),
        )

    rows = (
        db.execute(
            select(MerakiClient, Network)
            .join(Network, Network.id == MerakiClient.network_id)
            .where(MerakiClient.mac == mac_norm)
            .where(Network.archived_at.is_(None))
        )
        .all()
    )
    out = [
        AssetAppearance(
            network_id=n.id,
            network_name=n.display_name,
            mac=c.mac,
            description=c.description,
            ip=c.ip,
            vlan=c.vlan,
            first_seen_at=c.first_seen_at,
            last_seen_at=c.last_seen_at,
            source="cache",
        )
        for c, n in rows
    ]
    return sorted(
        out,
        key=lambda a: (a.last_seen_at is None, -(a.last_seen_at.timestamp() if a.last_seen_at else 0)),
    )


def appearances_for_macs(
    db: Session, macs: list[str]
) -> dict[str, list[AssetAppearance]]:
    """Bulk version of `appearances_for_mac` for the locator — one SQL
    round-trip across many MACs. Returns mac → appearances (cache only)."""

    normalised = [intune_service._normalise_mac(m) for m in macs if m]
    normalised = [m for m in normalised if m]
    if not normalised:
        return {}

    rows = (
        db.execute(
            select(MerakiClient, Network)
            .join(Network, Network.id == MerakiClient.network_id)
            .where(MerakiClient.mac.in_(normalised))
            .where(Network.archived_at.is_(None))
        )
        .all()
    )
    out: dict[str, list[AssetAppearance]] = {}
    for c, n in rows:
        out.setdefault(c.mac, []).append(
            AssetAppearance(
                network_id=n.id,
                network_name=n.display_name,
                mac=c.mac,
                description=c.description,
                ip=c.ip,
                vlan=c.vlan,
                first_seen_at=c.first_seen_at,
                last_seen_at=c.last_seen_at,
                source="cache",
            )
        )
    for mac in out:
        out[mac].sort(
            key=lambda a: (
                a.last_seen_at is None,
                -(a.last_seen_at.timestamp() if a.last_seen_at else 0),
            )
        )
    return out


def assets_seen_on_network(
    db: Session, network_id: int
) -> list[tuple[MerakiClient, "object"]]:
    """For Network detail: every MerakiClient cached on this network paired
    with its matched Asset (or None). Returns (client, asset) tuples."""

    from app.models.inventory import Asset

    rows = (
        db.execute(
            select(MerakiClient, Asset)
            .outerjoin(Asset, Asset.mac_address == MerakiClient.mac)
            .where(MerakiClient.network_id == network_id)
        )
        .all()
    )
    return [(c, a) for c, a in rows]
