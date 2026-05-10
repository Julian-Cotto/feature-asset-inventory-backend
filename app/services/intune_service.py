"""Microsoft Intune (Graph API) lookup.

Read-only for now — looks up `managedDevices` by serial number, returns a
normalized record. No device-creation API is offered (Intune has no Graph
endpoint to POST a managedDevice; devices materialize via enrollment).

Auth: Azure AD app registration with `DeviceManagementManagedDevices.Read.All`
application permission, OAuth2 client-credentials flow. Token cached in-process.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings


logger = logging.getLogger("intune")


# ────────────────────────── Result types ─────────────────────────────────


@dataclass
class IntuneDevice:
    intune_id: str
    serial_number: str | None
    device_name: str | None
    manufacturer: str | None
    model: str | None
    operating_system: str | None
    os_version: str | None
    assigned_upn: str | None
    chassis_type: str | None
    last_sync_dt: str | None
    managed_by: str | None
    ownership: str | None
    compliance: str | None
    raw: dict[str, Any]


@dataclass
class IntuneLookupResult:
    found: bool
    device: IntuneDevice | None
    error: str | None
    raw: dict[str, Any] | None


# ────────────────────────── Token cache ──────────────────────────────────


@dataclass
class _CachedToken:
    token: str
    expires_at: float


_token_lock = threading.Lock()
_token: _CachedToken | None = None


def _cached_token() -> str | None:
    global _token
    with _token_lock:
        if _token and _token.expires_at > time.time() + 30:
            return _token.token
    return None


def _store_token(token: str, ttl_seconds: int) -> None:
    global _token
    with _token_lock:
        _token = _CachedToken(token=token, expires_at=time.time() + ttl_seconds)


def _is_configured() -> bool:
    s = get_settings()
    return bool(s.intune_tenant_id and s.intune_client_id and s.intune_client_secret)


def _fetch_token(timeout: float) -> str:
    s = get_settings()
    url = f"https://login.microsoftonline.com/{s.intune_tenant_id}/oauth2/v2.0/token"
    resp = httpx.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": s.intune_client_id,
            "client_secret": s.intune_client_secret,
            "scope": "https://graph.microsoft.com/.default",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload["access_token"]
    expires_in = int(payload.get("expires_in", 3600))
    _store_token(token, expires_in)
    return token


def _ensure_token(timeout: float) -> str:
    cached = _cached_token()
    if cached:
        return cached
    return _fetch_token(timeout)


# ────────────────────────── Field mapping ────────────────────────────────


def _chassis_to_asset_type(chassis: str | None) -> str | None:
    if not chassis:
        return None
    c = chassis.lower()
    if "laptop" in c or "notebook" in c or "convertible" in c:
        return "laptop"
    if "desktop" in c or "tower" in c or "all-in-one" in c or "allinone" in c:
        return "desktop"
    return None


def _device_from_payload(item: dict[str, Any]) -> IntuneDevice:
    return IntuneDevice(
        intune_id=str(item.get("id", "")),
        serial_number=item.get("serialNumber"),
        device_name=item.get("deviceName"),
        manufacturer=item.get("manufacturer"),
        model=item.get("model"),
        operating_system=item.get("operatingSystem"),
        os_version=item.get("osVersion"),
        assigned_upn=item.get("userPrincipalName") or item.get("emailAddress"),
        chassis_type=item.get("chassisType"),
        last_sync_dt=item.get("lastSyncDateTime"),
        managed_by=item.get("managementAgent"),
        ownership=item.get("ownerType") or item.get("managedDeviceOwnerType"),
        compliance=item.get("complianceState"),
        raw=item,
    )


# ────────────────────────── Public API ───────────────────────────────────


def lookup_by_serial(serial: str) -> IntuneLookupResult:
    """Find a managedDevice by serial number. Empty result if not found."""

    serial = (serial or "").strip()
    if not serial:
        return IntuneLookupResult(found=False, device=None, error="Empty serial.", raw=None)

    if not _is_configured():
        return IntuneLookupResult(
            found=False,
            device=None,
            error="Intune not configured (set INTUNE_TENANT_ID / INTUNE_CLIENT_ID / INTUNE_CLIENT_SECRET).",
            raw=None,
        )

    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds

    try:
        token = _ensure_token(timeout)
    except Exception as e:
        return IntuneLookupResult(
            found=False, device=None, error=f"Intune auth failed: {e}", raw=None
        )

    # Escape single quotes for OData
    safe_serial = serial.replace("'", "''")
    url = f"{settings.intune_graph_base_url.rstrip('/')}/deviceManagement/managedDevices"
    params = {"$filter": f"serialNumber eq '{safe_serial}'"}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    try:
        resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPStatusError as e:
        return IntuneLookupResult(
            found=False,
            device=None,
            error=f"Intune HTTP {e.response.status_code}",
            raw=None,
        )
    except Exception as e:
        return IntuneLookupResult(
            found=False, device=None, error=f"Intune lookup failed: {e}", raw=None
        )

    items = payload.get("value") or []
    if not items:
        return IntuneLookupResult(found=False, device=None, error=None, raw=payload)

    return IntuneLookupResult(
        found=True,
        device=_device_from_payload(items[0]),
        error=None,
        raw=payload,
    )


def device_portal_url(intune_id: str) -> str:
    s = get_settings()
    base = s.intune_portal_base_url.rstrip("/")
    return f"{base}/#view/Microsoft_Intune_Devices/DeviceSettingsMenuBlade/~/overview/mdmDeviceId/{intune_id}"


# ────────────────────────── Bulk listing ─────────────────────────────────


def list_all_managed_devices(
    *, max_pages: int = 200, page_size: int = 100
) -> list[IntuneDevice]:
    """Page through every managedDevice and return as IntuneDevice records.
    Raises on auth / network failure (caller decides how to surface).

    Graph default page size is ~100; we pass `$top=100` explicitly. Subsequent
    pages come back via `@odata.nextLink` which already encodes any filters.
    """

    if not _is_configured():
        raise RuntimeError(
            "Intune not configured (set INTUNE_TENANT_ID / INTUNE_CLIENT_ID / INTUNE_CLIENT_SECRET)."
        )

    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = _ensure_token(timeout)

    base = settings.intune_graph_base_url.rstrip("/")
    next_url: str | None = (
        f"{base}/deviceManagement/managedDevices?$top={page_size}"
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    devices: list[IntuneDevice] = []
    pages = 0

    while next_url and pages < max_pages:
        resp = httpx.get(next_url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
        for item in payload.get("value", []) or []:
            devices.append(_device_from_payload(item))
        next_url = payload.get("@odata.nextLink")
        pages += 1

    return devices
