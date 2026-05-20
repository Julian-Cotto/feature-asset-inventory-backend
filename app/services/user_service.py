"""IntuneUser cache service.

Source of truth is Microsoft Graph. This service refreshes the local
`intune_users` cache from Graph and provides typed read APIs for the
Users view. Device assignments are NOT cached here — they're queried
live from Graph via `intune_service.list_devices_for_upn`.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import IntuneUser
from app.services import intune_service


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    # Graph returns ISO-8601 with `Z`; Python 3.11+ fromisoformat handles `Z`.
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _dump_list(value: list[str] | None) -> str | None:
    if value is None:
        return None
    if len(value) == 0:
        # Stored as `[]` so the API can distinguish "fetched but empty" from
        # "never fetched" (null).
        return "[]"
    return json.dumps(value)


def _apply_graph_user(row: IntuneUser, g: intune_service.GraphUser) -> IntuneUser:
    row.user_principal_name = g.user_principal_name
    row.display_name = g.display_name
    row.mail = g.mail
    row.job_title = g.job_title
    row.department = g.department
    row.office_location = g.office_location
    row.account_enabled = g.account_enabled
    row.user_type = g.user_type
    row.last_sign_in_at = _parse_dt(g.last_sign_in_at)
    row.sign_in_status = g.sign_in_status
    row.manager_id = g.manager_id
    row.manager_display_name = g.manager_display_name
    row.company_name = g.company_name
    row.employee_id = g.employee_id
    row.employee_type = g.employee_type
    row.employee_hire_date = _parse_dt(g.employee_hire_date)
    row.employee_org_division = g.employee_org_division
    row.employee_org_cost_center = g.employee_org_cost_center
    row.street_address = g.street_address
    row.city = g.city
    row.state = g.state
    row.postal_code = g.postal_code
    row.country = g.country
    row.mobile_phone = g.mobile_phone
    row.business_phones_json = _dump_list(g.business_phones)
    row.fax_number = g.fax_number
    row.mail_nickname = g.mail_nickname
    row.other_mails_json = _dump_list(g.other_mails)
    row.proxy_addresses_json = _dump_list(g.proxy_addresses)
    row.im_addresses_json = _dump_list(g.im_addresses)
    return row


def upsert_user(db: Session, g: intune_service.GraphUser) -> IntuneUser:
    row = db.get(IntuneUser, g.id)
    if row is None:
        row = IntuneUser(id=g.id)
        db.add(row)
    return _apply_graph_user(row, g)


def list_users(db: Session) -> list[IntuneUser]:
    return list(
        db.execute(
            select(IntuneUser).order_by(IntuneUser.display_name.asc().nullslast())
        ).scalars()
    )


def get_user(db: Session, user_id: str) -> IntuneUser | None:
    return db.get(IntuneUser, user_id)


def sync_all_from_graph(db: Session) -> dict[str, object]:
    """Pull every active member user from Graph and upsert into the cache.

    Returns counts plus the tenant-level `sign_in_status` so the UI can
    show why `last_sign_in_at` may be null across the board."""
    result = intune_service.list_active_member_users()
    created = 0
    updated = 0
    existing_ids = set(db.execute(select(IntuneUser.id)).scalars())
    for g in result.users:
        if g.id in existing_ids:
            updated += 1
        else:
            created += 1
        upsert_user(db, g)
    db.commit()
    return {
        "fetched": len(result.users),
        "created": created,
        "updated": updated,
        "sign_in_status": result.sign_in_status,
    }


def sync_one_from_graph(db: Session, user_id_or_upn: str) -> IntuneUser | None:
    """Refresh a single user from Graph. Returns None if Graph 404s."""
    g = intune_service.get_user_by_id(user_id_or_upn)
    if g is None:
        return None
    row = upsert_user(db, g)
    db.commit()
    db.refresh(row)
    return row
