"""Local cache + sync orchestration for Entra groups.

Source of truth is Microsoft Graph; this module upserts metadata into
the `entra_groups` table. Membership is never persisted — fetch lazily
from `groups_service.list_group_members`."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import EntraGroup
from app.services import groups_service


def _apply_row(row: EntraGroup, g: groups_service.EntraGroupRow) -> EntraGroup:
    row.display_name = g.display_name
    row.description = g.description
    row.mail_nickname = g.mail_nickname
    row.mail = g.mail
    row.security_enabled = g.security_enabled
    row.mail_enabled = g.mail_enabled
    row.group_types = ",".join(g.group_types) if g.group_types else None
    return row


def upsert_group(db: Session, g: groups_service.EntraGroupRow) -> EntraGroup:
    row = db.get(EntraGroup, g.id)
    if row is None:
        row = EntraGroup(id=g.id)
        db.add(row)
    return _apply_row(row, g)


def list_groups(
    db: Session,
    *,
    managed_only: bool,
    search: str | None,
) -> list[EntraGroup]:
    stmt = select(EntraGroup)
    if managed_only:
        stmt = stmt.where(EntraGroup.is_managed.is_(True))
    if search:
        like = f"%{search.lower()}%"
        from sqlalchemy import func, or_

        stmt = stmt.where(
            or_(
                func.lower(EntraGroup.display_name).like(like),
                func.lower(EntraGroup.mail_nickname).like(like),
                func.lower(EntraGroup.description).like(like),
            )
        )
    stmt = stmt.order_by(EntraGroup.display_name.asc())
    return list(db.execute(stmt).scalars())


def get_group(db: Session, group_id: str) -> EntraGroup | None:
    return db.get(EntraGroup, group_id)


def sync_all_from_graph(db: Session) -> dict[str, object]:
    rows = groups_service.list_all_groups()
    created = 0
    updated = 0
    existing_ids = set(db.execute(select(EntraGroup.id)).scalars())
    for g in rows:
        if g.id in existing_ids:
            updated += 1
        else:
            created += 1
        upsert_group(db, g)
    db.commit()
    return {"fetched": len(rows), "created": created, "updated": updated}


def sync_one_from_graph(db: Session, group_id: str) -> EntraGroup | None:
    g = groups_service.get_group(group_id)
    if g is None:
        return None
    row = upsert_group(db, g)
    db.commit()
    db.refresh(row)
    return row


def set_managed(db: Session, group_id: str, managed: bool) -> EntraGroup | None:
    row = db.get(EntraGroup, group_id)
    if row is None:
        return None
    row.is_managed = managed
    db.commit()
    db.refresh(row)
    return row


def update_member_count_cache(
    db: Session,
    group_id: str,
    count: int,
) -> None:
    row = db.get(EntraGroup, group_id)
    if row is None:
        return
    row.member_count_cached = count
    row.members_synced_at = datetime.now(timezone.utc)
    db.commit()
