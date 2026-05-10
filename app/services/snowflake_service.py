"""Snowflake source-of-truth sync.

Currently used to mirror corporate locations from
`<csm_db>.CORPORATE.LOCATIONS_ALL_V` into our local `Location` table.

Connection is opened per call (sync ops are infrequent — bulk refresh +
manual trigger) and torn down. No connection pool.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Location, PROTECTED_LOCATION_CODES

logger = logging.getLogger("snowflake_sync")


# ────────────────────────── Connection ──────────────────────────────────


def _connect():
    """Lazy-imports `snowflake.connector` so the dep is optional at import
    time (worker that doesn't run sync doesn't need the package warmed up
    in memory)."""

    import snowflake.connector as sf  # type: ignore

    s = get_settings()
    if not s.database_account.strip() or not s.database_user.strip():
        raise RuntimeError(
            "Snowflake not configured (DATABASE_ACCOUNT / DATABASE_USER missing).",
        )

    return sf.connect(
        account=s.database_account.strip(),
        user=s.database_user.strip(),
        password=s.database_password.strip(),
        warehouse=s.database_warehouse.strip() or None,
    )


# ────────────────────────── Locations sync ──────────────────────────────


def _build_address(row: dict[str, Any]) -> str | None:
    parts = []
    addr1 = (row.get("ADDR1") or "").strip()
    addr2 = (row.get("ADDR2") or "").strip()
    city = (row.get("CITY") or "").strip()
    state = (row.get("STATE") or "").strip()
    zip_code = (row.get("ZIP") or "").strip()

    if addr1:
        parts.append(addr1)
    if addr2:
        parts.append(addr2)
    city_state_zip = ", ".join(p for p in [city, state] if p)
    if city_state_zip and zip_code:
        city_state_zip = f"{city_state_zip} {zip_code}"
    elif zip_code and not city_state_zip:
        city_state_zip = zip_code
    if city_state_zip:
        parts.append(city_state_zip)

    return ", ".join(parts) or None


def sync_locations(
    db: Session,
    *,
    actor_upn: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Pull open locations from Snowflake `CORPORATE.LOCATIONS_ALL_V` filtered
    by `CMPYCODE IN (...)` and upsert into local `Location` table by `code`
    (which mirrors `LOCATIONID`).

    Returns counts: { fetched, created, updated, unchanged, deactivated, errors[] }.

    `deactivated`: existing local locations whose `code` no longer appears
    in the Snowflake result set (closed / company removed) — flipped to
    `is_active=False`. Not deleted (foreign keys may exist via assets).
    """

    s = get_settings()
    csm_db = s.database_csm_database.strip() or s.database_name.strip()
    if not csm_db:
        raise RuntimeError(
            "DATABASE_CSM_DATABASE / DATABASE_NAME missing — can't pick a database.",
        )

    schema = s.database_corporate_schema.strip() or "CORPORATE"
    cmpy_codes = [
        c.strip() for c in s.snowflake_locations_cmpycodes.split(",") if c.strip()
    ]
    if not cmpy_codes:
        raise RuntimeError("No CMPYCODE filter configured.")

    placeholders = ", ".join(["%s"] * len(cmpy_codes))
    query = (
        f"SELECT LOCATIONID, NAME, ADDR1, ADDR2, CITY, STATE, ZIP, COUNTRY, "
        f"CMPYCODE, ALTID "
        f"FROM {csm_db}.{schema}.LOCATIONS_ALL_V "
        f"WHERE CMPYCODE IN ({placeholders}) AND ISOPEN = TRUE"
    )

    rows: list[dict[str, Any]] = []
    try:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(query, tuple(cmpy_codes))
            descr = [d[0] for d in cur.description]
            for r in cur.fetchall():
                rows.append(dict(zip(descr, r)))
        finally:
            conn.close()
    except Exception as e:
        raise RuntimeError(f"Snowflake fetch failed: {e}") from e

    fetched = len(rows)
    created = 0
    updated = 0
    unchanged = 0
    deactivated = 0
    errors: list[dict] = []

    seen_codes: set[str] = set()

    def _norm(v: object, max_len: int) -> str | None:
        s = (str(v) if v is not None else "").strip()
        return s[:max_len] or None

    for row in rows:
        try:
            location_id = (row.get("LOCATIONID") or "").strip()
            if not location_id:
                continue
            seen_codes.add(location_id)

            name = (row.get("NAME") or "").strip() or location_id
            structured = {
                "address_line1": _norm(row.get("ADDR1"), 255),
                "address_line2": _norm(row.get("ADDR2"), 255),
                "city": _norm(row.get("CITY"), 128),
                "state": _norm(row.get("STATE"), 64),
                "postal_code": _norm(row.get("ZIP"), 32),
                "country": _norm(row.get("COUNTRY"), 64),
            }
            address = _build_address(row)

            existing = db.scalar(
                select(Location).where(Location.code == location_id)
            )

            if existing is None:
                if dry_run:
                    created += 1
                    continue
                db.add(
                    Location(
                        code=location_id,
                        name=name[:255],
                        type="site",
                        address=(address or "")[:1024] or None,
                        is_active=True,
                        created_by_upn=actor_upn,
                        updated_by_upn=actor_upn,
                        **structured,
                    )
                )
                created += 1
            else:
                changed = False
                if existing.name != name[:255]:
                    if not dry_run:
                        existing.name = name[:255]
                    changed = True
                addr_trimmed = (address or "")[:1024] or None
                if existing.address != addr_trimmed:
                    if not dry_run:
                        existing.address = addr_trimmed
                    changed = True
                for k, v in structured.items():
                    if getattr(existing, k) != v:
                        if not dry_run:
                            setattr(existing, k, v)
                        changed = True
                if not existing.is_active:
                    if not dry_run:
                        existing.is_active = True
                    changed = True
                if changed:
                    if not dry_run:
                        existing.updated_by_upn = actor_upn
                    updated += 1
                else:
                    unchanged += 1
        except Exception as e:
            errors.append(
                {"location_id": row.get("LOCATIONID"), "error": str(e)}
            )

    # Deactivate stale local locations (in DB but not in Snowflake result).
    # Skip PROTECTED_LOCATION_CODES — those are manually-seeded locations
    # (e.g. internal warehouses) that intentionally don't appear in the
    # Snowflake corporate view.
    if seen_codes:
        protected_or_seen = seen_codes | PROTECTED_LOCATION_CODES
        stale = db.scalars(
            select(Location)
            .where(Location.is_active.is_(True))
            .where(Location.code.notin_(protected_or_seen))
        ).all()
        for loc in stale:
            if not dry_run:
                loc.is_active = False
                loc.updated_by_upn = actor_upn
            deactivated += 1

    if not dry_run:
        db.commit()

    return {
        "fetched": fetched,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "deactivated": deactivated,
        "errors": errors,
        "dry_run": dry_run,
    }
