"""Software assets API.

Manual CRUD + Intune mobileApps sync + many-to-many assignments to
Entra groups or Intune users.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import require_permissions
from app.models.inventory import (
    ASSIGNMENT_PRINCIPAL_TYPES,
    EntraGroup,
    IntuneUser,
    Software,
    SoftwareAssignment,
)
from app.platform.auth_context import RequestAuthContext
from app.services import software_service


router = APIRouter(prefix="/software", tags=["software"])


def require_view():
    return require_permissions(*get_settings().auth_required_permissions)


def require_write():
    return require_permissions("asset-inventory.write")


def require_manage():
    return require_permissions("asset-inventory.manage")


# ────────────────────────── Schemas ─────────────────────────────────────


class SoftwareIn(BaseModel):
    """Create/update payload. All fields optional on update."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str | None = None
    link: str | None = None
    description: str | None = None
    category: str | None = None
    vendor: str | None = None
    license_cost_cents: int | None = None
    seat_count: int | None = None
    internal_owner_upn: str | None = None
    notes: str | None = None


class SoftwareOut(BaseModel):
    id: int
    name: str
    link: str | None
    description: str | None
    category: str | None
    vendor: str | None
    license_cost_cents: int | None
    seat_count: int | None
    internal_owner_upn: str | None
    source: str
    intune_app_id: str | None
    intune_app_type: str | None
    intune_publisher: str | None
    intune_synced_at: datetime | None
    notes: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    assignment_count: int = 0


class SoftwareSyncResponse(BaseModel):
    fetched: int
    created: int
    updated: int


class AssignmentOut(BaseModel):
    id: int
    software_id: int
    principal_type: str
    principal_id: str
    principal_display: str | None
    notes: str | None
    created_at: datetime
    created_by_upn: str | None


class AssignmentIn(BaseModel):
    principal_type: str = Field(..., description="'group' or 'user'")
    principal_id: str
    notes: str | None = None


# ────────────────────────── Helpers ─────────────────────────────────────


def _to_out(row: Software, *, assignment_count: int = 0) -> SoftwareOut:
    return SoftwareOut(
        id=row.id,
        name=row.name,
        link=row.link,
        description=row.description,
        category=row.category,
        vendor=row.vendor,
        license_cost_cents=row.license_cost_cents,
        seat_count=row.seat_count,
        internal_owner_upn=row.internal_owner_upn,
        source=row.source,
        intune_app_id=row.intune_app_id,
        intune_app_type=row.intune_app_type,
        intune_publisher=row.intune_publisher,
        intune_synced_at=row.intune_synced_at,
        notes=row.notes,
        archived_at=row.archived_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        assignment_count=assignment_count,
    )


def _assignment_to_out(row: SoftwareAssignment) -> AssignmentOut:
    return AssignmentOut(
        id=row.id,
        software_id=row.software_id,
        principal_type=row.principal_type,
        principal_id=row.principal_id,
        principal_display=row.principal_display,
        notes=row.notes,
        created_at=row.created_at,
        created_by_upn=row.created_by_upn,
    )


def _resolve_principal_display(
    db: Session, principal_type: str, principal_id: str
) -> str | None:
    if principal_type == "group":
        row = db.get(EntraGroup, principal_id)
        return row.display_name if row else None
    if principal_type == "user":
        row = db.get(IntuneUser, principal_id)
        return row.display_name or row.user_principal_name if row else None
    return None


# ────────────────────────── Endpoints ───────────────────────────────────


@router.get("", response_model=list[SoftwareOut])
def list_software(
    q: str | None = Query(None, description="Free-text search across name, vendor, category, description"),
    category: str | None = None,
    source: str | None = None,
    include_archived: bool = False,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
) -> list[SoftwareOut]:
    stmt = select(Software)
    if not include_archived:
        stmt = stmt.where(Software.archived_at.is_(None))
    if source:
        stmt = stmt.where(Software.source == source)
    if category:
        stmt = stmt.where(Software.category == category)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Software.name).like(like),
                func.lower(Software.vendor).like(like),
                func.lower(Software.category).like(like),
                func.lower(Software.description).like(like),
                func.lower(Software.internal_owner_upn).like(like),
            )
        )
    stmt = stmt.order_by(Software.name.asc())
    rows = list(db.execute(stmt).scalars())

    # Bulk-load assignment counts in one query to avoid N+1.
    counts: dict[int, int] = {}
    if rows:
        result = db.execute(
            select(
                SoftwareAssignment.software_id,
                func.count(SoftwareAssignment.id),
            )
            .where(SoftwareAssignment.software_id.in_([r.id for r in rows]))
            .group_by(SoftwareAssignment.software_id)
        ).all()
        counts = {sid: cnt for sid, cnt in result}

    return [_to_out(r, assignment_count=counts.get(r.id, 0)) for r in rows]


@router.get("/categories", response_model=list[str])
def list_software_categories(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
) -> list[str]:
    rows = db.execute(
        select(Software.category)
        .where(Software.category.is_not(None))
        .where(Software.category != "")
        .distinct()
        .order_by(Software.category.asc())
    ).scalars()
    return [r for r in rows if r]


@router.post("", response_model=SoftwareOut, status_code=status.HTTP_201_CREATED)
def create_software(
    payload: SoftwareIn,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_write()),
) -> SoftwareOut:
    if not payload.name or not payload.name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name is required.")
    row = Software(
        name=payload.name.strip(),
        link=payload.link,
        description=payload.description,
        category=payload.category,
        vendor=payload.vendor,
        license_cost_cents=payload.license_cost_cents,
        seat_count=payload.seat_count,
        internal_owner_upn=payload.internal_owner_upn,
        notes=payload.notes,
        source="manual",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.post("/sync", response_model=SoftwareSyncResponse)
def sync_software_from_intune(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_manage()),
) -> SoftwareSyncResponse:
    """Pull every mobileApp from Intune and upsert into the software table.

    Requires `DeviceManagementApps.Read.All` application permission on the
    shared Graph app reg."""
    try:
        result = software_service.sync_from_intune(db)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    return SoftwareSyncResponse(
        fetched=result.total_fetched,
        created=result.created,
        updated=result.updated,
    )


@router.get("/{software_id}", response_model=SoftwareOut)
def get_software(
    software_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
) -> SoftwareOut:
    row = db.get(Software, software_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Software not found.")
    count = db.execute(
        select(func.count(SoftwareAssignment.id)).where(
            SoftwareAssignment.software_id == software_id
        )
    ).scalar_one()
    return _to_out(row, assignment_count=count or 0)


@router.patch("/{software_id}", response_model=SoftwareOut)
def update_software(
    software_id: int,
    payload: SoftwareIn,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_write()),
) -> SoftwareOut:
    row = db.get(Software, software_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Software not found.")
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        if field == "name" and isinstance(value, str):
            value = value.strip()
            if not value:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "name cannot be empty.")
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.post("/{software_id}/archive", response_model=SoftwareOut)
def archive_software(
    software_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_write()),
) -> SoftwareOut:
    row = db.get(Software, software_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Software not found.")
    if row.archived_at is None:
        row.archived_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
    return _to_out(row)


@router.post("/{software_id}/unarchive", response_model=SoftwareOut)
def unarchive_software(
    software_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_write()),
) -> SoftwareOut:
    row = db.get(Software, software_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Software not found.")
    if row.archived_at is not None:
        row.archived_at = None
        db.commit()
        db.refresh(row)
    return _to_out(row)


# ───────── Assignments ─────────


@router.get("/{software_id}/assignments", response_model=list[AssignmentOut])
def list_assignments(
    software_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
) -> list[AssignmentOut]:
    if db.get(Software, software_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Software not found.")
    rows = list(
        db.execute(
            select(SoftwareAssignment)
            .where(SoftwareAssignment.software_id == software_id)
            .order_by(SoftwareAssignment.principal_type.asc(), SoftwareAssignment.created_at.asc())
        ).scalars()
    )
    return [_assignment_to_out(r) for r in rows]


@router.post("/{software_id}/assignments", response_model=AssignmentOut, status_code=status.HTTP_201_CREATED)
def add_assignment(
    software_id: int,
    payload: AssignmentIn,
    db: Session = Depends(get_db),
    auth: RequestAuthContext = Depends(require_write()),
) -> AssignmentOut:
    if db.get(Software, software_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Software not found.")
    if payload.principal_type not in ASSIGNMENT_PRINCIPAL_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"principal_type must be one of {ASSIGNMENT_PRINCIPAL_TYPES}.",
        )
    if not payload.principal_id.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "principal_id is required.")

    existing = db.execute(
        select(SoftwareAssignment).where(
            SoftwareAssignment.software_id == software_id,
            SoftwareAssignment.principal_type == payload.principal_type,
            SoftwareAssignment.principal_id == payload.principal_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _assignment_to_out(existing)

    display = _resolve_principal_display(db, payload.principal_type, payload.principal_id)
    if display is None and payload.principal_type == "group":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Group not in local cache. Sync groups first.",
        )
    if display is None and payload.principal_type == "user":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "User not in local cache. Sync users first.",
        )

    row = SoftwareAssignment(
        software_id=software_id,
        principal_type=payload.principal_type,
        principal_id=payload.principal_id,
        principal_display=display,
        notes=payload.notes,
        created_by_upn=auth.email or auth.user_name,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _assignment_to_out(row)


@router.delete("/{software_id}/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_assignment(
    software_id: int,
    assignment_id: int,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_write()),
) -> None:
    row = db.get(SoftwareAssignment, assignment_id)
    if row is None or row.software_id != software_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found.")
    db.delete(row)
    db.commit()
