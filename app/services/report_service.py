"""Report aggregations for the Reports view. Read-only, fast.

Six top-level reports, one fn each:
  - fleet_health     OS / mfr / model / age / compliance
  - warranty         expiration calendar + out-of-warranty + replacement candidates
  - stock            stock-by-model + stale stock + deployment pipeline + cycle time
  - shipments        funnel + late + carrier perf + direction
  - intune           stale check-ins + managed-by + sync recency
  - activity         turnover + repair + lost/retired + recent feed
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import (
    Asset,
    AssetHistory,
    Deployment,
    Location,
    Shipment,
)
from app.models.inventory import (
    EntraGroup,
    IntuneUser,
    Software,
    SoftwareAssignment,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _windows_version(os: str | None, os_version: str | None) -> str:
    """Resolve Windows 10 vs 11 from build number. Mirrors osDisplay util."""

    if not os:
        return "Unknown"
    o = os.lower()
    if "server" in o:
        return "Windows Server"
    if "windows" in o:
        v = (os_version or "").strip()
        if v.startswith("10.0"):
            try:
                build = int(v.split(".")[2])
                return "Windows 11" if build >= 22000 else "Windows 10"
            except Exception:
                return "Windows 10"
        return "Windows"
    if any(k in o for k in ("mac", "osx", "os x")):
        return "macOS"
    if any(k in o for k in ("linux", "ubuntu", "debian", "rhel")):
        return "Linux"
    return os


# ────────────────────────── 1. Fleet health ─────────────────────────────


NETWORK_TYPES = {"ap", "switch", "gateway"}


def _fleet_slice(rows: list) -> dict:
    os_counts: dict[str, int] = {}
    mfr_counts: dict[str, int] = {}
    model_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    compliance_counts: dict[str, int] = {}
    now = _utcnow()
    age_buckets = {
        "<1y": 0,
        "1-2y": 0,
        "2-3y": 0,
        "3-4y": 0,
        "4-5y": 0,
        "5+y": 0,
    }
    win10 = win11 = 0

    for os, ver, atype, mfr, model, onboarded, compliance in rows:
        winv = _windows_version(os, ver)
        os_counts[winv] = os_counts.get(winv, 0) + 1
        if winv == "Windows 10":
            win10 += 1
        elif winv == "Windows 11":
            win11 += 1

        mfr_key = (mfr or "Unknown").strip() or "Unknown"
        mfr_counts[mfr_key] = mfr_counts.get(mfr_key, 0) + 1

        if model:
            mk = f"{mfr_key} {model}".strip()
            model_counts[mk] = model_counts.get(mk, 0) + 1

        type_counts[atype] = type_counts.get(atype, 0) + 1

        comp = (compliance or "unknown").lower()
        compliance_counts[comp] = compliance_counts.get(comp, 0) + 1

        if onboarded is not None:
            years = (now - onboarded).days / 365.0
            if years < 1:
                age_buckets["<1y"] += 1
            elif years < 2:
                age_buckets["1-2y"] += 1
            elif years < 3:
                age_buckets["2-3y"] += 1
            elif years < 4:
                age_buckets["3-4y"] += 1
            elif years < 5:
                age_buckets["4-5y"] += 1
            else:
                age_buckets["5+y"] += 1

    top_models = sorted(model_counts.items(), key=lambda x: -x[1])[:10]

    return {
        "os": [{"label": k, "count": v} for k, v in sorted(os_counts.items(), key=lambda x: -x[1])],
        "win10_count": win10,
        "win11_count": win11,
        "manufacturer": [
            {"label": k, "count": v} for k, v in sorted(mfr_counts.items(), key=lambda x: -x[1])
        ],
        "top_models": [{"label": k, "count": v} for k, v in top_models],
        "asset_type": [
            {"label": k, "count": v} for k, v in sorted(type_counts.items(), key=lambda x: -x[1])
        ],
        "compliance": [
            {"label": k, "count": v} for k, v in sorted(compliance_counts.items(), key=lambda x: -x[1])
        ],
        "age_buckets": [{"label": k, "count": v} for k, v in age_buckets.items()],
    }


def fleet_health(db: Session) -> dict:
    rows = db.execute(
        select(Asset.os, Asset.os_version, Asset.asset_type, Asset.manufacturer, Asset.model, Asset.onboarded_at, Asset.intune_compliance)
        .where(Asset.archived_at.is_(None))
    ).all()
    device_rows = [r for r in rows if r[2] not in NETWORK_TYPES]
    network_rows = [r for r in rows if r[2] in NETWORK_TYPES]
    devices = _fleet_slice(device_rows)
    return {**devices, "network": _fleet_slice(network_rows)}


# ────────────────────────── 2. Warranty ─────────────────────────────────


def warranty_report(db: Session) -> dict:
    now = _utcnow()
    horizon = now + timedelta(days=365)

    # 12-month expiration calendar
    rows = db.execute(
        select(Asset.warranty_end_date)
        .where(Asset.archived_at.is_(None))
        .where(Asset.warranty_end_date.is_not(None))
        .where(Asset.warranty_end_date <= horizon)
        .where(Asset.warranty_end_date >= now)
    ).all()
    calendar: dict[str, int] = {}
    for (end,) in rows:
        key = end.strftime("%Y-%m")
        calendar[key] = calendar.get(key, 0) + 1
    calendar_points = [
        {"month": k, "count": v} for k, v in sorted(calendar.items())
    ]

    # Out-of-warranty by location
    out_rows = db.execute(
        select(Location.name, func.count(Asset.id))
        .join(Asset, Asset.location_id == Location.id)
        .where(Asset.archived_at.is_(None))
        .where(Asset.warranty_active.is_(False))
        .group_by(Location.name)
        .order_by(func.count(Asset.id).desc())
    ).all()
    out_by_location = [{"location": r[0], "count": int(r[1])} for r in out_rows]

    # Replacement candidates: out of warranty AND age > 3 years
    three_years_ago = now - timedelta(days=365 * 3)
    cand_rows = db.execute(
        select(Asset)
        .where(Asset.archived_at.is_(None))
        .where(Asset.warranty_active.is_(False))
        .where(Asset.onboarded_at < three_years_ago)
        .order_by(Asset.onboarded_at.asc())
        .limit(50)
    ).scalars().all()
    candidates = [
        {
            "asset_id": a.id,
            "serial_number": a.serial_number,
            "model": a.model,
            "manufacturer": a.manufacturer,
            "assigned_upn": a.assigned_upn,
            "onboarded_at": a.onboarded_at.isoformat() if a.onboarded_at else None,
            "warranty_end_date": a.warranty_end_date.isoformat() if a.warranty_end_date else None,
        }
        for a in cand_rows
    ]

    return {
        "calendar_12m": calendar_points,
        "out_by_location": out_by_location,
        "replacement_candidates": candidates,
    }


# ────────────────────────── 3. Stock ────────────────────────────────────


def stock_report(db: Session) -> dict:
    """Stock-by-model, stale stock at warehouse, deployment pipeline, cycle time."""

    from app.services.inventory_service import (
        _STOCK_UPNS,
        _reserved_subqueries,
    )

    now = _utcnow()

    # Stock by model — assets at warehouse-type location, unassigned-or-stock-upn, status=active, not reserved
    reserved_dep, reserved_ship = _reserved_subqueries()
    stmt = (
        select(Asset.asset_type, Asset.manufacturer, Asset.model, func.count(Asset.id))
        .outerjoin(Location, Location.id == Asset.location_id)
        .where(Asset.archived_at.is_(None))
        .where(Asset.status_code == "active")
        .where((Asset.assigned_upn.is_(None)) | (Asset.assigned_upn.in_(_STOCK_UPNS)))
        .where((Location.type == "warehouse") | (Asset.location_id.is_(None)))
        .where(Asset.id.notin_(reserved_dep))
        .where(Asset.id.notin_(reserved_ship))
        .group_by(Asset.asset_type, Asset.manufacturer, Asset.model)
        .order_by(func.count(Asset.id).desc())
    )
    stock_rows = db.execute(stmt).all()
    stock_by_model = [
        {
            "asset_type": r[0],
            "manufacturer": r[1],
            "model": r[2],
            "count": int(r[3]),
        }
        for r in stock_rows
    ]

    # Stale stock — same pool, age buckets
    stale_stmt = (
        select(Asset)
        .outerjoin(Location, Location.id == Asset.location_id)
        .where(Asset.archived_at.is_(None))
        .where(Asset.status_code == "active")
        .where((Asset.assigned_upn.is_(None)) | (Asset.assigned_upn.in_(_STOCK_UPNS)))
        .where((Location.type == "warehouse") | (Asset.location_id.is_(None)))
        .where(Asset.id.notin_(reserved_dep))
        .where(Asset.id.notin_(reserved_ship))
        .order_by(Asset.onboarded_at.asc())
        .limit(100)
    )
    stale_assets = db.scalars(stale_stmt).all()
    stale = []
    for a in stale_assets:
        days = (now - a.onboarded_at).days if a.onboarded_at else None
        stale.append(
            {
                "asset_id": a.id,
                "serial_number": a.serial_number,
                "asset_type": a.asset_type,
                "manufacturer": a.manufacturer,
                "model": a.model,
                "days_at_warehouse": days,
                "onboarded_at": a.onboarded_at.isoformat() if a.onboarded_at else None,
            }
        )

    # Deployment pipeline counts + recent names
    pipe_counts: dict[str, int] = {}
    pipe_names: dict[str, list[dict]] = {"planning": [], "in_progress": [], "completed": []}
    for status in ("planning", "in_progress"):
        cnt = db.scalar(
            select(func.count(Deployment.id))
            .where(Deployment.archived_at.is_(None))
            .where(Deployment.status == status)
        ) or 0
        pipe_counts[status] = int(cnt)
        names = db.execute(
            select(Deployment.id, Deployment.name)
            .where(Deployment.archived_at.is_(None))
            .where(Deployment.status == status)
            .order_by(Deployment.target_date.asc().nulls_last())
            .limit(10)
        ).all()
        pipe_names[status] = [{"id": r[0], "name": r[1]} for r in names]
    completed_30d = db.scalar(
        select(func.count(Deployment.id))
        .where(Deployment.status == "completed")
        .where(Deployment.completed_at >= now - timedelta(days=30))
    ) or 0
    pipe_counts["completed_30d"] = int(completed_30d)

    # Cycle time: avg days from created_at to completed_at for last 50 completed
    cycle_rows = db.execute(
        select(Deployment.created_at, Deployment.completed_at)
        .where(Deployment.status == "completed")
        .where(Deployment.completed_at.is_not(None))
        .order_by(Deployment.completed_at.desc())
        .limit(50)
    ).all()
    deltas = [(c[1] - c[0]).days for c in cycle_rows if c[0] and c[1]]
    avg_cycle_days = round(sum(deltas) / len(deltas), 1) if deltas else None

    return {
        "stock_by_model": stock_by_model,
        "stale_stock": stale,
        "deployment_pipeline": {"counts": pipe_counts, "samples": pipe_names},
        "deployment_avg_cycle_days": avg_cycle_days,
    }


# ────────────────────────── 4. Shipments ────────────────────────────────


def shipments_report(db: Session) -> dict:
    now = _utcnow()
    open_q = (
        select(Shipment.carrier_status, Shipment.carrier, Shipment.direction, Shipment.created_at)
        .where(Shipment.archived_at.is_(None))
        .where(Shipment.resolution == "open")
    )
    rows = db.execute(open_q).all()
    funnel: dict[str, int] = {}
    carrier_counts: dict[str, int] = {}
    direction_counts: dict[str, int] = {"inbound": 0, "outbound": 0}
    late: list[dict] = []
    for cs, c, d, created in rows:
        funnel[cs] = funnel.get(cs, 0) + 1
        carrier_counts[c] = carrier_counts.get(c, 0) + 1
        if d in direction_counts:
            direction_counts[d] += 1

    # Late = open + (no event progress in 7d) OR (open > 14d total). Use created_at simple.
    late_threshold = now - timedelta(days=14)
    late_rows = db.execute(
        select(Shipment)
        .where(Shipment.archived_at.is_(None))
        .where(Shipment.resolution == "open")
        .where(Shipment.created_at < late_threshold)
        .where(Shipment.carrier_status.notin_(("delivered",)))
        .order_by(Shipment.created_at.asc())
        .limit(50)
    ).scalars().all()
    for s in late_rows:
        late.append(
            {
                "shipment_id": s.id,
                "tracking_number": s.tracking_number,
                "carrier": s.carrier,
                "carrier_status": s.carrier_status,
                "direction": s.direction,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "days_open": (now - s.created_at).days if s.created_at else None,
            }
        )

    # Avg transit days by carrier — delivered shipments last 90d
    delivered = db.execute(
        select(Shipment.carrier, Shipment.created_at, Shipment.resolved_at)
        .where(Shipment.carrier_status == "delivered")
        .where(Shipment.resolved_at.is_not(None))
        .where(Shipment.resolved_at >= now - timedelta(days=90))
    ).all()
    carrier_perf: dict[str, list[int]] = {}
    for c, created, resolved in delivered:
        if created and resolved:
            carrier_perf.setdefault(c, []).append((resolved - created).days)
    carrier_avg = [
        {
            "carrier": c,
            "avg_days": round(sum(d) / len(d), 1) if d else None,
            "count": len(d),
        }
        for c, d in carrier_perf.items()
    ]

    return {
        "funnel": [{"label": k, "count": v} for k, v in funnel.items()],
        "carrier_counts": [{"label": k, "count": v} for k, v in carrier_counts.items()],
        "direction": [{"label": k, "count": v} for k, v in direction_counts.items()],
        "late": late,
        "carrier_avg_days": carrier_avg,
    }


# ────────────────────────── 5. Intune sync health ───────────────────────


def intune_report(db: Session) -> dict:
    now = _utcnow()
    cutoff_7 = now - timedelta(days=7)
    cutoff_30 = now - timedelta(days=30)

    stale_rows = db.execute(
        select(Asset)
        .where(Asset.archived_at.is_(None))
        .where(Asset.intune_id.is_not(None))
        .where(Asset.intune_last_check_in < cutoff_7)
        .order_by(Asset.intune_last_check_in.asc().nulls_first())
        .limit(100)
    ).scalars().all()
    stale = []
    for a in stale_rows:
        last = a.intune_last_check_in
        stale.append(
            {
                "asset_id": a.id,
                "serial_number": a.serial_number,
                "intune_device_name": a.intune_device_name,
                "assigned_upn": a.assigned_upn,
                "last_check_in": last.isoformat() if last else None,
                "days_since": (now - last).days if last else None,
            }
        )

    managed_rows = db.execute(
        select(Asset.intune_managed_by, func.count(Asset.id))
        .where(Asset.archived_at.is_(None))
        .where(Asset.intune_id.is_not(None))
        .group_by(Asset.intune_managed_by)
    ).all()
    managed_by = [
        {"label": (r[0] or "unknown"), "count": int(r[1])} for r in managed_rows
    ]

    # Recency histogram — bucket by check-in age
    recency_rows = db.execute(
        select(Asset.intune_last_check_in)
        .where(Asset.archived_at.is_(None))
        .where(Asset.intune_id.is_not(None))
    ).all()
    buckets = {"<1d": 0, "1-7d": 0, "7-30d": 0, "30-90d": 0, "90d+": 0, "never": 0}
    for (last,) in recency_rows:
        if last is None:
            buckets["never"] += 1
            continue
        days = (now - last).days
        if days < 1:
            buckets["<1d"] += 1
        elif days < 7:
            buckets["1-7d"] += 1
        elif days < 30:
            buckets["7-30d"] += 1
        elif days < 90:
            buckets["30-90d"] += 1
        else:
            buckets["90d+"] += 1

    return {
        "stale_check_ins": stale,
        "stale_count_7d": int(
            db.scalar(
                select(func.count(Asset.id))
                .where(Asset.archived_at.is_(None))
                .where(Asset.intune_id.is_not(None))
                .where(Asset.intune_last_check_in < cutoff_7)
            )
            or 0
        ),
        "stale_count_30d": int(
            db.scalar(
                select(func.count(Asset.id))
                .where(Asset.archived_at.is_(None))
                .where(Asset.intune_id.is_not(None))
                .where(Asset.intune_last_check_in < cutoff_30)
            )
            or 0
        ),
        "managed_by": managed_by,
        "recency_buckets": [{"label": k, "count": v} for k, v in buckets.items()],
    }


# ────────────────────────── 6. Activity ─────────────────────────────────


def activity_report(db: Session) -> dict:
    now = _utcnow()
    cutoff = now - timedelta(days=180)

    # Turnover: count of assign + unassign events per month
    events = db.execute(
        select(AssetHistory.performed_at, AssetHistory.event_type)
        .where(AssetHistory.performed_at >= cutoff)
        .where(AssetHistory.event_type.in_(("assign", "unassign", "status_change", "archive")))
    ).all()
    monthly: dict[str, dict[str, int]] = {}
    for ts, etype in events:
        key = ts.strftime("%Y-%m")
        bucket = monthly.setdefault(key, {"assign": 0, "unassign": 0, "lost": 0, "retired": 0, "in_repair": 0})
        if etype == "assign":
            bucket["assign"] += 1
        elif etype == "unassign":
            bucket["unassign"] += 1

    # status_change rows need their `to_value` to know what they became
    status_rows = db.execute(
        select(AssetHistory.performed_at, AssetHistory.to_value)
        .where(AssetHistory.performed_at >= cutoff)
        .where(AssetHistory.event_type == "status_change")
    ).all()
    for ts, to_val in status_rows:
        key = ts.strftime("%Y-%m")
        bucket = monthly.setdefault(key, {"assign": 0, "unassign": 0, "lost": 0, "retired": 0, "in_repair": 0})
        if to_val in ("lost", "retired", "in_repair"):
            bucket[to_val] += 1

    monthly_points = [
        {"month": k, **v} for k, v in sorted(monthly.items())
    ]

    # Recent activity feed — last 50 entries with asset context
    recent_rows = db.execute(
        select(AssetHistory, Asset)
        .join(Asset, Asset.id == AssetHistory.asset_id)
        .order_by(AssetHistory.performed_at.desc())
        .limit(50)
    ).all()
    recent = []
    for h, a in recent_rows:
        recent.append(
            {
                "id": h.id,
                "asset_id": a.id,
                "asset_label": a.intune_device_name or a.serial_number,
                "event_type": h.event_type,
                "from_value": h.from_value,
                "to_value": h.to_value,
                "performed_at": h.performed_at.isoformat() if h.performed_at else None,
                "performed_by_upn": h.performed_by_upn,
                "notes": h.notes,
            }
        )

    return {
        "monthly_180d": monthly_points,
        "recent": recent,
    }


# ────────────────────────── 7. Security posture ──────────────────────────


_COMPUTER_TYPES = ("laptop", "desktop", "thin_client")


def _norm(value: str | None, *, lowercase: bool = True) -> str:
    """Normalize a possibly-null categorical value for bucketing."""
    if value is None or str(value).strip() == "":
        return "unknown"
    v = str(value).strip()
    return v.lower() if lowercase else v


def security_report(db: Session) -> dict:
    """Defender posture aggregations + top at-risk machines.

    Only considers computer-type assets (laptops/desktops/thin clients).
    Network gear isn't in Defender's scope."""

    rows = db.execute(
        select(
            Asset.id,
            Asset.serial_number,
            Asset.intune_device_name,
            Asset.manufacturer,
            Asset.model,
            Asset.intune_id,
            Asset.intune_synced_at,
            Asset.defender_id,
            Asset.defender_synced_at,
            Asset.defender_health_status,
            Asset.defender_risk_score,
            Asset.defender_exposure_level,
            Asset.defender_onboarding_status,
            Asset.defender_av_status,
            Asset.defender_last_seen_at,
            Asset.assigned_upn,
        )
        .where(Asset.asset_type.in_(_COMPUTER_TYPES))
        .where(Asset.archived_at.is_(None))
    ).all()

    health: dict[str, int] = {}
    exposure: dict[str, int] = {}
    risk: dict[str, int] = {}
    onboarding: dict[str, int] = {}
    av: dict[str, int] = {}
    matrix: dict[tuple[str, str], int] = {}

    total = 0
    defender_onboarded = 0
    defender_unhealthy = 0
    defender_missing = 0

    at_risk_candidates: list[dict] = []
    missing_defender_rows: list[dict] = []

    for r in rows:
        total += 1
        h = _norm(r.defender_health_status)
        e = _norm(r.defender_exposure_level)
        rk = _norm(r.defender_risk_score)
        ob = _norm(r.defender_onboarding_status)
        avs = _norm(r.defender_av_status)

        health[h] = health.get(h, 0) + 1
        exposure[e] = exposure.get(e, 0) + 1
        risk[rk] = risk.get(rk, 0) + 1
        onboarding[ob] = onboarding.get(ob, 0) + 1
        av[avs] = av.get(avs, 0) + 1
        matrix[(e, h)] = matrix.get((e, h), 0) + 1

        if r.defender_id:
            defender_onboarded += 1
            if h in ("atrisk", "at_risk", "inactive", "impairedcommunications", "noheartbeat"):
                defender_unhealthy += 1
        elif r.intune_id:
            defender_missing += 1
            missing_defender_rows.append(
                {
                    "asset_id": r.id,
                    "serial_number": r.serial_number,
                    "device_name": r.intune_device_name,
                    "intune_synced_at": (
                        r.intune_synced_at.isoformat() if r.intune_synced_at else None
                    ),
                }
            )

        if r.defender_id and h not in ("healthy", "secure", "unknown"):
            at_risk_candidates.append(
                {
                    "asset_id": r.id,
                    "serial_number": r.serial_number,
                    "device_name": r.intune_device_name,
                    "manufacturer": r.manufacturer,
                    "model": r.model,
                    "health_status": r.defender_health_status,
                    "risk_score": r.defender_risk_score,
                    "exposure_level": r.defender_exposure_level,
                    "last_seen_at": (
                        r.defender_last_seen_at.isoformat()
                        if r.defender_last_seen_at
                        else None
                    ),
                    "assigned_upn": r.assigned_upn,
                }
            )

    def _to_label_counts(d: dict[str, int]) -> list[dict]:
        return [
            {"label": k, "count": v}
            for k, v in sorted(d.items(), key=lambda kv: -kv[1])
        ]

    # Rank at-risk: high-exposure high-risk first, then by recency desc.
    risk_order = {"high": 0, "medium": 1, "low": 2, "none": 3, "unknown": 4}

    def _at_risk_key(row: dict) -> tuple:
        return (
            risk_order.get(_norm(row["exposure_level"]), 5),
            risk_order.get(_norm(row["risk_score"]), 5),
            row["last_seen_at"] or "",
        )

    at_risk_candidates.sort(key=_at_risk_key)
    top_at_risk = at_risk_candidates[:25]

    missing_defender_rows.sort(
        key=lambda r: r["intune_synced_at"] or "", reverse=True
    )

    return {
        "counts": {
            "total_computers": total,
            "defender_onboarded": defender_onboarded,
            "defender_unhealthy": defender_unhealthy,
            "defender_missing": defender_missing,
        },
        "health_status": _to_label_counts(health),
        "exposure_level": _to_label_counts(exposure),
        "risk_score": _to_label_counts(risk),
        "onboarding_status": _to_label_counts(onboarding),
        "av_status": _to_label_counts(av),
        "risk_matrix": [
            {"exposure": ex, "health": he, "count": c}
            for (ex, he), c in sorted(matrix.items(), key=lambda kv: -kv[1])
        ],
        "top_at_risk": top_at_risk,
        "missing_defender": missing_defender_rows[:25],
    }


# ────────────────────────── 8. Software ───────────────────────────────────


def software_report(db: Session) -> dict:
    """Software inventory + spend + assignment-coverage aggregations."""

    rows = db.execute(
        select(
            Software.id,
            Software.name,
            Software.source,
            Software.vendor,
            Software.category,
            Software.license_cost_cents,
            Software.seat_count,
            Software.archived_at,
        )
    ).all()

    total = len(rows)
    archived = sum(1 for r in rows if r.archived_at is not None)

    by_source: dict[str, int] = {}
    by_category: dict[str, int] = {}
    spend_by_category: dict[str, dict[str, int]] = {}  # category -> {total_cents, count}
    total_spend = 0
    total_seats = 0

    for r in rows:
        by_source[r.source] = by_source.get(r.source, 0) + 1
        cat = r.category or "Uncategorized"
        by_category[cat] = by_category.get(cat, 0) + 1
        bucket = spend_by_category.setdefault(cat, {"total_cents": 0, "count": 0})
        bucket["count"] += 1
        if r.license_cost_cents is not None:
            bucket["total_cents"] += int(r.license_cost_cents)
            total_spend += int(r.license_cost_cents)
        if r.seat_count is not None:
            total_seats += int(r.seat_count)

    # Assignment count per software (single grouped query)
    counts_rows = db.execute(
        select(SoftwareAssignment.software_id, func.count(SoftwareAssignment.id))
        .group_by(SoftwareAssignment.software_id)
    ).all()
    counts_by_id: dict[int, int] = {sid: cnt for sid, cnt in counts_rows}

    coverage = {"0": 0, "1": 0, "2-5": 0, "6+": 0}
    unassigned: list[dict] = []
    most_assigned: list[dict] = []
    for r in rows:
        if r.archived_at is not None:
            continue
        n = counts_by_id.get(r.id, 0)
        if n == 0:
            coverage["0"] += 1
            unassigned.append(
                {
                    "software_id": r.id,
                    "name": r.name,
                    "source": r.source,
                    "vendor": r.vendor,
                    "category": r.category,
                    "license_cost_cents": r.license_cost_cents,
                }
            )
        elif n == 1:
            coverage["1"] += 1
        elif n <= 5:
            coverage["2-5"] += 1
        else:
            coverage["6+"] += 1

        most_assigned.append(
            {
                "software_id": r.id,
                "name": r.name,
                "vendor": r.vendor,
                "category": r.category,
                "assignment_count": n,
            }
        )

    most_assigned.sort(key=lambda x: x["assignment_count"], reverse=True)
    top_software = [s for s in most_assigned if s["assignment_count"] > 0][:10]

    # Sort unassigned by license cost desc — biggest shelfware risk first.
    unassigned.sort(
        key=lambda x: (x["license_cost_cents"] or 0), reverse=True
    )
    unassigned_sample = unassigned[:25]

    # Top groups by software-assignment count
    group_counts_rows = db.execute(
        select(SoftwareAssignment.principal_id, func.count(SoftwareAssignment.id))
        .where(SoftwareAssignment.principal_type == "group")
        .group_by(SoftwareAssignment.principal_id)
    ).all()
    if group_counts_rows:
        group_ids = [gid for gid, _ in group_counts_rows]
        group_rows = db.execute(
            select(EntraGroup.id, EntraGroup.display_name).where(
                EntraGroup.id.in_(group_ids)
            )
        ).all()
        name_by_id = {gid: name for gid, name in group_rows}
        top_groups = [
            {
                "group_id": gid,
                "display_name": name_by_id.get(gid, gid),
                "software_count": cnt,
            }
            for gid, cnt in group_counts_rows
        ]
        top_groups.sort(key=lambda x: x["software_count"], reverse=True)
        top_groups = top_groups[:10]
    else:
        top_groups = []

    return {
        "totals": {
            "total_software": total,
            "archived": archived,
            "total_spend_cents": total_spend,
            "total_seats": total_seats,
        },
        "by_source": [
            {"label": k, "count": v}
            for k, v in sorted(by_source.items(), key=lambda kv: -kv[1])
        ],
        "by_category": [
            {"label": k, "count": v}
            for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])
        ],
        "spend_by_category": [
            {
                "category": cat,
                "total_cents": meta["total_cents"],
                "software_count": meta["count"],
            }
            for cat, meta in sorted(
                spend_by_category.items(),
                key=lambda kv: -kv[1]["total_cents"],
            )
        ],
        "assignment_coverage": [
            {"bucket": k, "count": v} for k, v in coverage.items()
        ],
        "unassigned_software": unassigned_sample,
        "top_groups": top_groups,
        "top_software": top_software,
    }


# ────────────────────────── 9. People ────────────────────────────────────


def people_report(db: Session) -> dict:
    """Devices-per-person aggregations.

    Source of truth for the people axis is the cached `intune_users` table.
    Devices are counted from `assets.assigned_upn` joined against UPN.
    Users not in the cache (or assets with no UPN) contribute to an
    \"Unassigned / unknown\" bucket so totals stay consistent."""

    # All non-archived assets w/ a UPN
    asset_rows = db.execute(
        select(
            Asset.id,
            Asset.assigned_upn,
            Asset.asset_type,
        )
        .where(Asset.archived_at.is_(None))
    ).all()

    # All cached users
    user_rows = db.execute(
        select(
            IntuneUser.id,
            IntuneUser.user_principal_name,
            IntuneUser.display_name,
            IntuneUser.department,
            IntuneUser.office_location,
            IntuneUser.job_title,
            IntuneUser.manager_id,
            IntuneUser.manager_display_name,
            IntuneUser.account_enabled,
        ).where(IntuneUser.account_enabled.is_(True))
    ).all()

    user_by_upn: dict[str, dict] = {}
    for u in user_rows:
        if not u.user_principal_name:
            continue
        user_by_upn[u.user_principal_name.lower()] = {
            "id": u.id,
            "upn": u.user_principal_name,
            "display_name": u.display_name,
            "department": u.department,
            "office": u.office_location,
            "job_title": u.job_title,
            "manager_id": u.manager_id,
            "manager_name": u.manager_display_name,
        }

    devices_by_department: dict[str, int] = {}
    devices_by_office: dict[str, int] = {}
    devices_by_user: dict[str, int] = {}  # upn -> count
    devices_by_manager: dict[str, dict] = {}  # manager_id -> {name, direct_reports_with_devices: set, devices_total}

    for a in asset_rows:
        upn = (a.assigned_upn or "").lower().strip()
        # Skip the staging UPN — that's the unassigned pool, not a real user
        if upn in ("", "join@hv.ltd"):
            continue
        devices_by_user[upn] = devices_by_user.get(upn, 0) + 1
        user = user_by_upn.get(upn)
        dept = (user["department"] if user else None) or "Unknown"
        office = (user["office"] if user else None) or "Unknown"
        devices_by_department[dept] = devices_by_department.get(dept, 0) + 1
        devices_by_office[office] = devices_by_office.get(office, 0) + 1
        if user and user["manager_id"]:
            mid = user["manager_id"]
            bucket = devices_by_manager.setdefault(
                mid,
                {
                    "manager_id": mid,
                    "manager_name": user["manager_name"] or mid,
                    "direct_reports_with_devices": set(),
                    "devices_total": 0,
                },
            )
            bucket["direct_reports_with_devices"].add(upn)
            bucket["devices_total"] += 1

    total_users = len(user_by_upn)
    users_with_device = sum(
        1 for upn in user_by_upn if upn in devices_by_user
    )
    users_without_device = total_users - users_with_device

    # Sample of users with no device, sorted by department then name
    without_device_sample = [
        {
            "user_id": u["id"],
            "upn": u["upn"],
            "display_name": u["display_name"],
            "department": u["department"],
            "office": u["office"],
            "job_title": u["job_title"],
        }
        for upn, u in user_by_upn.items()
        if upn not in devices_by_user
    ]
    without_device_sample.sort(
        key=lambda x: (
            (x["department"] or "zzz").lower(),
            (x["display_name"] or x["upn"] or "").lower(),
        )
    )

    # Top managers by direct reports with devices
    top_managers = [
        {
            "manager_id": m["manager_id"],
            "manager_name": m["manager_name"],
            "direct_reports_with_devices": len(m["direct_reports_with_devices"]),
            "devices_total": m["devices_total"],
        }
        for m in devices_by_manager.values()
    ]
    top_managers.sort(
        key=lambda x: (-x["direct_reports_with_devices"], -x["devices_total"])
    )

    # Top users by device count (multi-device owners)
    top_users = sorted(
        (
            {
                "upn": upn,
                "display_name": user_by_upn.get(upn, {}).get("display_name"),
                "department": user_by_upn.get(upn, {}).get("department"),
                "device_count": cnt,
            }
            for upn, cnt in devices_by_user.items()
        ),
        key=lambda x: x["device_count"],
        reverse=True,
    )
    top_users_sample = top_users[:15]

    def _to_label_counts(d: dict[str, int]) -> list[dict]:
        return [
            {"label": k, "count": v}
            for k, v in sorted(d.items(), key=lambda kv: -kv[1])
        ]

    return {
        "totals": {
            "total_users": total_users,
            "users_with_device": users_with_device,
            "users_without_device": users_without_device,
            "total_assigned_devices": sum(devices_by_user.values()),
        },
        "devices_by_department": _to_label_counts(devices_by_department),
        "devices_by_office": _to_label_counts(devices_by_office),
        "users_without_devices_sample": without_device_sample[:50],
        "top_managers": top_managers[:15],
        "top_users_by_devices": top_users_sample,
    }
