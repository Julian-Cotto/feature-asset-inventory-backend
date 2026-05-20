"""Users API — Microsoft Graph member users + Intune device assignment.

Reads come from the local `intune_users` cache (synced via `/users/sync` or
`/users/{id}/sync`). Device assignments are queried live from Graph and
written live to Graph (`primaryUser` on `managedDevice`)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import require_permissions
from app.platform.auth_context import RequestAuthContext
from app.services import intune_service, user_service


def _load_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [str(v) for v in parsed if v is not None]
    return []


router = APIRouter(prefix="/users", tags=["users"])


def require_view():
    return require_permissions(*get_settings().auth_required_permissions)


def require_write():
    return require_permissions("asset-inventory.write")


def require_manage():
    return require_permissions("asset-inventory.manage")


# ────────────────────────── Schemas ─────────────────────────────────────


class UserOut(BaseModel):
    """Cached IntuneUser row. Source of truth is Microsoft Graph; this is
    a denormalized read from the `intune_users` table."""
    id: str
    user_principal_name: str
    display_name: str | None
    mail: str | None
    job_title: str | None
    department: str | None
    office_location: str | None
    account_enabled: bool
    user_type: str | None
    last_sign_in_at: datetime | None
    sign_in_status: str  # "ok" | "permission_missing" | "license_unavailable"
    manager_id: str | None
    manager_display_name: str | None

    # Identity / org
    company_name: str | None = None
    employee_id: str | None = None
    employee_type: str | None = None
    employee_hire_date: datetime | None = None
    employee_org_division: str | None = None
    employee_org_cost_center: str | None = None

    # Contact
    street_address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    mobile_phone: str | None = None
    business_phones: list[str] = []
    fax_number: str | None = None
    mail_nickname: str | None = None
    other_mails: list[str] = []
    proxy_addresses: list[str] = []
    im_addresses: list[str] = []

    synced_at: datetime


class SponsorOut(BaseModel):
    id: str
    display_name: str | None
    user_principal_name: str | None
    mail: str | None


class DeviceSummary(BaseModel):
    """Compact managedDevice projection for the Users view."""
    intune_id: str
    serial_number: str | None
    device_name: str | None
    manufacturer: str | None
    model: str | None
    operating_system: str | None
    assigned_upn: str | None


class UserDetailOut(BaseModel):
    user: UserOut
    assigned_devices: list[DeviceSummary]
    sponsors: list[SponsorOut] = []
    sponsors_status: str = "ok"  # "ok" | "unavailable" (403/404 from Graph)


class SyncAllResponse(BaseModel):
    fetched: int
    created: int
    updated: int
    sign_in_status: str  # tenant-level signInActivity availability


class AssignableDevicesResponse(BaseModel):
    staging_upn: str
    devices: list[DeviceSummary]


class AssignDeviceRequest(BaseModel):
    device_id: str  # managedDevice id


class AssignmentOk(BaseModel):
    ok: bool = True
    device_id: str


# ────────────────────────── Helpers ─────────────────────────────────────


def _to_out(row: Any) -> UserOut:
    return UserOut(
        id=row.id,
        user_principal_name=row.user_principal_name,
        display_name=row.display_name,
        mail=row.mail,
        job_title=row.job_title,
        department=row.department,
        office_location=row.office_location,
        account_enabled=row.account_enabled,
        user_type=row.user_type,
        last_sign_in_at=row.last_sign_in_at,
        sign_in_status=getattr(row, "sign_in_status", "ok"),
        manager_id=row.manager_id,
        manager_display_name=row.manager_display_name,
        company_name=getattr(row, "company_name", None),
        employee_id=getattr(row, "employee_id", None),
        employee_type=getattr(row, "employee_type", None),
        employee_hire_date=getattr(row, "employee_hire_date", None),
        employee_org_division=getattr(row, "employee_org_division", None),
        employee_org_cost_center=getattr(row, "employee_org_cost_center", None),
        street_address=getattr(row, "street_address", None),
        city=getattr(row, "city", None),
        state=getattr(row, "state", None),
        postal_code=getattr(row, "postal_code", None),
        country=getattr(row, "country", None),
        mobile_phone=getattr(row, "mobile_phone", None),
        business_phones=_load_list(getattr(row, "business_phones_json", None)),
        fax_number=getattr(row, "fax_number", None),
        mail_nickname=getattr(row, "mail_nickname", None),
        other_mails=_load_list(getattr(row, "other_mails_json", None)),
        proxy_addresses=_load_list(getattr(row, "proxy_addresses_json", None)),
        im_addresses=_load_list(getattr(row, "im_addresses_json", None)),
        synced_at=row.synced_at,
    )


def _device_to_summary(d: intune_service.IntuneDevice) -> DeviceSummary:
    return DeviceSummary(
        intune_id=d.intune_id,
        serial_number=d.serial_number,
        device_name=d.device_name,
        manufacturer=d.manufacturer,
        model=d.model,
        operating_system=d.operating_system,
        assigned_upn=d.assigned_upn,
    )


# ────────────────────────── Endpoints ───────────────────────────────────


@router.get("", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
) -> list[UserOut]:
    return [_to_out(row) for row in user_service.list_users(db)]


@router.post("/sync", response_model=SyncAllResponse)
def sync_all_users(
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_manage()),
) -> SyncAllResponse:
    """Pull every active member user from Graph into the cache."""
    try:
        result = user_service.sync_all_from_graph(db)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    return SyncAllResponse(**result)


@router.get("/{user_id}", response_model=UserDetailOut)
def get_user(
    user_id: str,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
) -> UserDetailOut:
    row = user_service.get_user(db, user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not in local cache. Sync first.")
    try:
        devices = intune_service.list_devices_for_upn(row.user_principal_name)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))

    # Sponsors are a relationship endpoint; live fetch each visit. Soft-fail
    # so a sponsors permission gap doesn't break the detail page.
    sponsors: list[SponsorOut] = []
    sponsors_status = "ok"
    try:
        for s in intune_service.list_sponsors_for_user(row.id):
            sponsors.append(
                SponsorOut(
                    id=s.id,
                    display_name=s.display_name,
                    user_principal_name=s.user_principal_name,
                    mail=s.mail,
                )
            )
    except RuntimeError:
        sponsors_status = "unavailable"
    except Exception:
        sponsors_status = "unavailable"

    return UserDetailOut(
        user=_to_out(row),
        assigned_devices=[_device_to_summary(d) for d in devices],
        sponsors=sponsors,
        sponsors_status=sponsors_status,
    )


@router.post("/{user_id}/sync", response_model=UserOut)
def sync_one_user(
    user_id: str,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_write()),
) -> UserOut:
    try:
        row = user_service.sync_one_from_graph(db, user_id)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found in Graph.")
    return _to_out(row)


@router.get("/{user_id}/assignable-devices", response_model=AssignableDevicesResponse)
def list_assignable_devices(
    user_id: str,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
) -> AssignableDevicesResponse:
    """Devices in the staging pool (current primaryUser == intune_staging_upn).

    Live Graph query. The `user_id` is only used for authorization context
    (validate the user exists in cache); the returned device list is the
    same global staging pool."""
    row = user_service.get_user(db, user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not in local cache.")
    staging_upn = get_settings().intune_staging_upn
    try:
        devices = intune_service.list_devices_for_upn(staging_upn)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    return AssignableDevicesResponse(
        staging_upn=staging_upn,
        devices=[_device_to_summary(d) for d in devices],
    )


@router.post("/{user_id}/devices/assign", response_model=AssignmentOk)
def assign_device(
    user_id: str,
    payload: AssignDeviceRequest,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_write()),
) -> AssignmentOk:
    """Set this managedDevice's primaryUser to the given user (Graph write)."""
    row = user_service.get_user(db, user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not in local cache.")
    try:
        intune_service.set_device_primary_user(payload.device_id, row.id)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Graph write failed: {e}")
    return AssignmentOk(device_id=payload.device_id)


@router.post("/{user_id}/devices/{device_id}/unassign", response_model=AssignmentOk)
def unassign_device(
    user_id: str,
    device_id: str,
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_write()),
) -> AssignmentOk:
    """Clear the managedDevice's primaryUser (Graph write).

    `user_id` is used for authorization context only — the operation
    itself is a property of the device, not the user."""
    row = user_service.get_user(db, user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not in local cache.")
    try:
        intune_service.clear_device_primary_user(device_id)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Graph write failed: {e}")
    return AssignmentOk(device_id=device_id)
