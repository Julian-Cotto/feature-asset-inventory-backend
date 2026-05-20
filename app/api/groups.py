"""Entra groups API.

Cached read of `entra_groups` (source of truth = Microsoft Graph) plus a
lazy live fetch of group members on the detail-view open. Sync is a
metadata-only bulk pull; the `is_managed` flag lets admins curate which
groups appear in the default list."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import require_permissions
from app.models.inventory import EntraGroup, Software, SoftwareAssignment
from app.platform.auth_context import RequestAuthContext
from app.services import entra_group_service, groups_service


router = APIRouter(prefix="/groups", tags=["groups"])


def require_view():
    return require_permissions(*get_settings().auth_required_permissions)


def require_write():
    return require_permissions("asset-inventory.write")


def require_manage():
    return require_permissions("asset-inventory.manage")


# ────────────────────────── Schemas ─────────────────────────────────────


class GroupOut(BaseModel):
    id: str
    display_name: str
    description: str | None
    mail_nickname: str | None
    mail: str | None
    security_enabled: bool
    mail_enabled: bool
    group_types: list[str]
    is_managed: bool
    member_count_cached: int | None
    members_synced_at: datetime | None
    last_synced_at: datetime
    assigned_software_count: int = 0


class GroupMemberOut(BaseModel):
    id: str
    member_type: str  # "user" | "group" | "device" | "other"
    display_name: str | None
    user_principal_name: str | None
    mail: str | None


class AssignedSoftwareOut(BaseModel):
    """A software row currently assigned to this group, plus the assignment
    row's id for delete linking."""
    assignment_id: int
    software_id: int
    name: str
    category: str | None
    vendor: str | None
    archived: bool


class GroupDetailOut(BaseModel):
    group: GroupOut
    members: list[GroupMemberOut]
    members_truncated: bool = False
    assigned_software: list[AssignedSoftwareOut]


class GroupSyncResponse(BaseModel):
    fetched: int
    created: int
    updated: int


class ManagedToggleIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    is_managed: bool


# ────────────────────────── Helpers ─────────────────────────────────────


def _to_out(row: EntraGroup, *, assigned_software_count: int = 0) -> GroupOut:
    return GroupOut(
        id=row.id,
        display_name=row.display_name,
        description=row.description,
        mail_nickname=row.mail_nickname,
        mail=row.mail,
        security_enabled=row.security_enabled,
        mail_enabled=row.mail_enabled,
        group_types=(row.group_types.split(",") if row.group_types else []),
        is_managed=row.is_managed,
        member_count_cached=row.member_count_cached,
        members_synced_at=row.members_synced_at,
        last_synced_at=row.last_synced_at,
        assigned_software_count=assigned_software_count,
    )


# ────────────────────────── Endpoints ───────────────────────────────────


@router.get("", response_model=list[GroupOut])
def list_groups(
    q: str | None = Query(None, description="Free-text search across name, mailNickname, description"),
    managed_only: bool = Query(True, description="Default true — hides the long tail of auto-created groups"),
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
) -> list[GroupOut]:
    rows = entra_group_service.list_groups(db, managed_only=managed_only, search=q)

    counts: dict[str, int] = {}
    if rows:
        from sqlalchemy import func

        result = db.execute(
            select(
                SoftwareAssignment.principal_id,
                func.count(SoftwareAssignment.id),
            )
            .where(
                SoftwareAssignment.principal_type == "group",
                SoftwareAssignment.principal_id.in_([r.id for r in rows]),
            )
            .group_by(SoftwareAssignment.principal_id)
        ).all()
        counts = {pid: cnt for pid, cnt in result}

    return [_to_out(r, assigned_software_count=counts.get(r.id, 0)) for r in rows]


@router.post("/sync", response_model=GroupSyncResponse)
def sync_groups(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_manage()),
) -> GroupSyncResponse:
    """Bulk-pull every group from Graph (metadata only — no members).

    Requires `Group.Read.All` application permission."""
    try:
        result = entra_group_service.sync_all_from_graph(db)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    return GroupSyncResponse(**result)  # type: ignore[arg-type]


@router.get("/{group_id}", response_model=GroupDetailOut)
def get_group(
    group_id: str,
    members_limit: int = Query(500, ge=0, le=2000),
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
) -> GroupDetailOut:
    row = entra_group_service.get_group(db, group_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not in local cache. Sync first.")

    # Lazy member fetch — live from Graph each open. Cheap on small groups,
    # capped via `members_limit` for big ones.
    try:
        all_members = groups_service.list_group_members(group_id)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))

    truncated = members_limit > 0 and len(all_members) > members_limit
    visible = all_members[:members_limit] if members_limit > 0 else all_members

    # Cache the member count for the list view (full count, not truncated).
    entra_group_service.update_member_count_cache(db, group_id, len(all_members))

    # Software currently assigned to this group.
    assigned_rows = list(
        db.execute(
            select(SoftwareAssignment, Software)
            .join(Software, Software.id == SoftwareAssignment.software_id)
            .where(
                SoftwareAssignment.principal_type == "group",
                SoftwareAssignment.principal_id == group_id,
            )
            .order_by(Software.name.asc())
        ).all()
    )

    assigned_software = [
        AssignedSoftwareOut(
            assignment_id=a.id,
            software_id=s.id,
            name=s.name,
            category=s.category,
            vendor=s.vendor,
            archived=s.archived_at is not None,
        )
        for a, s in assigned_rows
    ]

    return GroupDetailOut(
        group=_to_out(row, assigned_software_count=len(assigned_software)),
        members=[
            GroupMemberOut(
                id=m.id,
                member_type=m.member_type,
                display_name=m.display_name,
                user_principal_name=m.user_principal_name,
                mail=m.mail,
            )
            for m in visible
        ],
        members_truncated=truncated,
        assigned_software=assigned_software,
    )


@router.post("/{group_id}/sync", response_model=GroupOut)
def sync_one_group(
    group_id: str,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_write()),
) -> GroupOut:
    try:
        row = entra_group_service.sync_one_from_graph(db, group_id)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found in Entra.")
    return _to_out(row)


@router.patch("/{group_id}/managed", response_model=GroupOut)
def set_group_managed(
    group_id: str,
    payload: ManagedToggleIn,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_write()),
) -> GroupOut:
    row = entra_group_service.set_managed(db, group_id, payload.is_managed)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not in local cache.")
    return _to_out(row)
