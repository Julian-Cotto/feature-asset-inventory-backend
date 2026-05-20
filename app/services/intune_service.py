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
from typing import Any, Iterable

import httpx

from app.config import get_settings


logger = logging.getLogger("intune")


# ────────────────────────── Shared HTTP client ──────────────────────────
# Process-singleton httpx.Client. Reuses TCP+TLS across all calls →
# ~100-200ms saved per request vs a fresh `httpx.get`. Keepalive caps
# prevent runaway connection counts on big tenants.

_client_lock = threading.Lock()
_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                ),
                http2=False,
            )
    return _client


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
    # Bridges Intune ↔ Defender. `aadDeviceId` on Defender's machine entity
    # matches this value on Intune's managedDevice.
    aad_device_id: str | None
    # MACs as seen by Intune. We prefer wifi (most laptops join wifi first,
    # and Meraki APs see the wifi MAC). Ethernet kept as a fallback.
    wifi_mac: str | None
    ethernet_mac: str | None
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
    resp = _http().post(
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


def _normalise_mac(raw: str | None) -> str | None:
    """Meraki returns MACs as lowercase colon-separated (`aa:bb:cc:dd:ee:ff`);
    Intune returns them packed (`AABBCCDDEEFF`). Normalize everything to the
    Meraki shape so equality matching just works downstream."""
    if not raw:
        return None
    cleaned = "".join(c for c in raw if c.isalnum()).lower()
    if len(cleaned) != 12:
        return None
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))


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
        aad_device_id=item.get("azureADDeviceId") or item.get("azureAdDeviceId"),
        wifi_mac=_normalise_mac(item.get("wiFiMacAddress")),
        ethernet_mac=_normalise_mac(item.get("ethernetMacAddress")),
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
        resp = _http().get(url, params=params, headers=headers, timeout=timeout)
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
    """Page through every managedDevice. Uses pipelined paging — the next
    page's HTTP request is fired before the current page is parsed."""
    from app.services._graph_paging import paginate

    if not _is_configured():
        raise RuntimeError(
            "Intune not configured (set INTUNE_TENANT_ID / INTUNE_CLIENT_ID / INTUNE_CLIENT_SECRET)."
        )

    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = _ensure_token(timeout)

    base = settings.intune_graph_base_url.rstrip("/")
    first_url = f"{base}/deviceManagement/managedDevices?$top={page_size}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    return paginate(
        _http(),
        first_url,
        headers=headers,
        timeout=timeout,
        parse=lambda payload: (_device_from_payload(it) for it in (payload.get("value") or [])),
        max_pages=max_pages,
    )


# ────────────────────────── Graph: Users ─────────────────────────────────


@dataclass
class GraphUser:
    """Subset of Microsoft Graph user properties we cache + display."""
    id: str
    user_principal_name: str
    display_name: str | None
    mail: str | None
    job_title: str | None
    department: str | None
    office_location: str | None
    account_enabled: bool
    user_type: str | None
    last_sign_in_at: str | None
    sign_in_status: str  # "ok" | "permission_missing" | "license_unavailable"
    manager_id: str | None
    manager_display_name: str | None

    # Identity / org
    company_name: str | None = None
    employee_id: str | None = None
    employee_type: str | None = None
    employee_hire_date: str | None = None  # ISO 8601 from Graph
    employee_org_division: str | None = None
    employee_org_cost_center: str | None = None

    # Contact — address
    street_address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None

    # Contact — phones
    mobile_phone: str | None = None
    business_phones: list[str] | None = None
    fax_number: str | None = None

    # Contact — mail / IM
    mail_nickname: str | None = None
    other_mails: list[str] | None = None
    proxy_addresses: list[str] | None = None
    im_addresses: list[str] | None = None


@dataclass
class GraphSponsor:
    id: str
    display_name: str | None
    user_principal_name: str | None
    mail: str | None


@dataclass
class UserListResult:
    """Bulk list response. `sign_in_status` reflects what we saw at the
    tenant level: 403 on the signInActivity select → permission_missing;
    200 with every row null → license_unavailable; otherwise ok."""
    users: list[GraphUser]
    sign_in_status: str


_USER_SELECT_FIELDS = (
    "id,userPrincipalName,displayName,mail,jobTitle,department,"
    "officeLocation,accountEnabled,userType,signInActivity,"
    "companyName,employeeId,employeeType,employeeHireDate,employeeOrgData,"
    "streetAddress,city,state,postalCode,country,"
    "mobilePhone,businessPhones,faxNumber,"
    "mailNickname,otherMails,proxyAddresses,imAddresses"
)


def _coerce_str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return None


def _user_from_payload(item: dict[str, Any], *, sign_in_status: str = "ok") -> GraphUser:
    sign_in = item.get("signInActivity") or {}
    org = item.get("employeeOrgData") or {}
    return GraphUser(
        id=item.get("id") or "",
        user_principal_name=item.get("userPrincipalName") or "",
        display_name=item.get("displayName"),
        mail=item.get("mail"),
        job_title=item.get("jobTitle"),
        department=item.get("department"),
        office_location=item.get("officeLocation"),
        account_enabled=bool(item.get("accountEnabled", True)),
        user_type=item.get("userType"),
        last_sign_in_at=sign_in.get("lastSignInDateTime"),
        sign_in_status=sign_in_status,
        manager_id=None,
        manager_display_name=None,
        company_name=item.get("companyName"),
        employee_id=item.get("employeeId"),
        employee_type=item.get("employeeType"),
        employee_hire_date=item.get("employeeHireDate"),
        employee_org_division=org.get("division"),
        employee_org_cost_center=org.get("costCenter"),
        street_address=item.get("streetAddress"),
        city=item.get("city"),
        state=item.get("state"),
        postal_code=item.get("postalCode"),
        country=item.get("country"),
        mobile_phone=item.get("mobilePhone"),
        business_phones=_coerce_str_list(item.get("businessPhones")),
        fax_number=item.get("faxNumber"),
        mail_nickname=item.get("mailNickname"),
        other_mails=_coerce_str_list(item.get("otherMails")),
        proxy_addresses=_coerce_str_list(item.get("proxyAddresses")),
        im_addresses=_coerce_str_list(item.get("imAddresses")),
    )


def list_active_member_users(*, max_pages: int = 200, page_size: int = 100) -> UserListResult:
    """All active member users (accountEnabled=true, userType='Member').

    Pages through Graph and reports the tenant-level sign-in availability:

      - "ok"                   field came back for at least one user
      - "permission_missing"   Graph 403d on signInActivity (needs
                               AuditLog.Read.All app permission)
      - "license_unavailable"  200 OK but every row had null signInActivity
                               (tenant likely lacks Entra ID P1/P2)
    """
    from app.services._graph_paging import paginate

    if not _is_configured():
        raise RuntimeError("Intune/Graph not configured.")

    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = _ensure_token(timeout)

    base = settings.intune_graph_base_url.rstrip("/")
    full_url = (
        f"{base}/users"
        f"?$select={_USER_SELECT_FIELDS}"
        f"&$filter=accountEnabled eq true and userType eq 'Member'"
        f"&$top={page_size}"
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    users: list[GraphUser] = []
    permission_missing = False
    any_sign_in_present = False

    def _parse(payload: dict) -> Iterable[GraphUser]:
        nonlocal any_sign_in_present
        for item in payload.get("value", []) or []:
            if (item.get("signInActivity") or {}).get("lastSignInDateTime"):
                any_sign_in_present = True
            yield _user_from_payload(item)

    try:
        users = paginate(
            _http(), full_url, headers=headers, timeout=timeout,
            parse=_parse, max_pages=max_pages,
        )
    except httpx.HTTPStatusError as e:
        # signInActivity needs AuditLog.Read.All. If forbidden, strip and retry.
        if e.response.status_code == 403 and "signInActivity" in full_url:
            permission_missing = True
            logger.warning(
                "graph_users_signinactivity_forbidden",
                extra={
                    "event": "graph_users_signinactivity_forbidden",
                    "hint": "Azure AD app likely missing AuditLog.Read.All. "
                            "Retrying users sync without signInActivity field; "
                            "last_sign_in_at will be null for all users.",
                },
            )
            stripped = full_url.replace(",signInActivity", "")
            users = paginate(
                _http(), stripped, headers=headers, timeout=timeout,
                parse=_parse, max_pages=max_pages,
            )
        else:
            raise

    if permission_missing:
        status = "permission_missing"
    elif users and not any_sign_in_present:
        status = "license_unavailable"
        logger.warning(
            "graph_users_signinactivity_empty",
            extra={
                "event": "graph_users_signinactivity_empty",
                "hint": "Graph returned 200 but every user has null signInActivity. "
                        "Tenant likely lacks Entra ID P1/P2 license required for "
                        "signInActivity reads.",
                "user_count": len(users),
            },
        )
    else:
        status = "ok"

    # Backfill status on every row.
    for u in users:
        u.sign_in_status = status

    return UserListResult(users=users, sign_in_status=status)


def get_user_by_id(user_id: str) -> GraphUser | None:
    """Fetch a single Graph user by object id or UPN.

    Also resolves the user's manager via a follow-up `/manager` request so the
    detail view can render org context. Returns None on 404."""
    if not _is_configured():
        raise RuntimeError("Intune/Graph not configured.")

    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = _ensure_token(timeout)
    base = settings.intune_graph_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    safe = user_id.replace("'", "''")
    user_url = f"{base}/users/{safe}?$select={_USER_SELECT_FIELDS}"
    resp = _http().get(user_url, headers=headers, timeout=timeout)
    if resp.status_code == 404:
        return None
    permission_missing = False
    if resp.status_code == 403 and "signInActivity" in user_url:
        permission_missing = True
        logger.warning(
            "graph_user_signinactivity_forbidden",
            extra={"event": "graph_user_signinactivity_forbidden", "user_id": user_id},
        )
        user_url = user_url.replace(",signInActivity", "")
        resp = _http().get(user_url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    # In single-user mode we can't distinguish "tenant-wide license issue"
    # from "this user has never signed in". Default to "ok" and let the
    # bulk-sync's tenant-level status drive the UI hint.
    status = "permission_missing" if permission_missing else "ok"
    user = _user_from_payload(payload, sign_in_status=status)

    # Best-effort manager resolution.
    try:
        mgr = _http().get(
            f"{base}/users/{safe}/manager?$select=id,displayName",
            headers=headers,
            timeout=timeout,
        )
        if mgr.status_code == 200:
            data = mgr.json()
            user.manager_id = data.get("id")
            user.manager_display_name = data.get("displayName")
    except Exception:
        pass

    return user


def list_sponsors_for_user(user_id: str) -> list[GraphSponsor]:
    """Live fetch of `/users/{id}/sponsors`. Best-effort: returns [] on 404
    (user gone) or 403 (tenant lacks the relationship)."""
    if not _is_configured():
        raise RuntimeError("Intune/Graph not configured.")

    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = _ensure_token(timeout)
    base = settings.intune_graph_base_url.rstrip("/")
    safe = user_id.replace("'", "''")
    url = (
        f"{base}/users/{safe}/sponsors"
        f"?$select=id,displayName,userPrincipalName,mail"
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    sponsors: list[GraphSponsor] = []
    next_url: str | None = url
    pages = 0
    while next_url and pages < 20:
        resp = _http().get(next_url, headers=headers, timeout=timeout)
        if resp.status_code in (403, 404):
            logger.warning(
                "graph_user_sponsors_unavailable",
                extra={
                    "event": "graph_user_sponsors_unavailable",
                    "user_id": user_id,
                    "status_code": resp.status_code,
                },
            )
            return []
        resp.raise_for_status()
        payload = resp.json()
        for item in payload.get("value", []) or []:
            sponsors.append(
                GraphSponsor(
                    id=str(item.get("id") or ""),
                    display_name=item.get("displayName"),
                    user_principal_name=item.get("userPrincipalName"),
                    mail=item.get("mail"),
                )
            )
        next_url = payload.get("@odata.nextLink")
        pages += 1
    return sponsors


# ────────────────────────── Graph: Device ↔ User ─────────────────────────


def list_devices_for_upn(upn: str) -> list[IntuneDevice]:
    """All managedDevices whose userPrincipalName matches `upn`.

    Used by the Users view to (a) list devices currently assigned to a user
    and (b) list devices in the staging pool (`upn == intune_staging_upn`)."""
    if not _is_configured():
        raise RuntimeError("Intune/Graph not configured.")
    if not upn:
        return []

    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = _ensure_token(timeout)
    base = settings.intune_graph_base_url.rstrip("/")
    safe = upn.replace("'", "''")
    url = (
        f"{base}/deviceManagement/managedDevices"
        f"?$filter=userPrincipalName eq '{safe}'"
        f"&$top=100"
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    devices: list[IntuneDevice] = []
    next_url: str | None = url
    pages = 0
    while next_url and pages < 50:
        resp = _http().get(next_url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
        for item in payload.get("value", []) or []:
            devices.append(_device_from_payload(item))
        next_url = payload.get("@odata.nextLink")
        pages += 1
    return devices


def set_device_primary_user(device_id: str, user_id: str) -> None:
    """Assign a Graph user as the managedDevice's primary user.

    Requires `DeviceManagementManagedDevices.ReadWrite.All` app permission.
    Raises on non-2xx response."""
    if not _is_configured():
        raise RuntimeError("Intune/Graph not configured.")

    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = _ensure_token(timeout)
    base = settings.intune_graph_base_url.rstrip("/")
    url = f"{base}/deviceManagement/managedDevices/{device_id}/users/$ref"
    body = {"@odata.id": f"{base}/users/{user_id}"}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = _http().post(url, json=body, headers=headers, timeout=timeout)
    if resp.status_code not in (200, 204):
        resp.raise_for_status()


def clear_device_primary_user(device_id: str) -> None:
    """Remove the managedDevice's primary user. Same permission as set."""
    if not _is_configured():
        raise RuntimeError("Intune/Graph not configured.")

    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = _ensure_token(timeout)
    base = settings.intune_graph_base_url.rstrip("/")
    url = f"{base}/deviceManagement/managedDevices/{device_id}/users/$ref"
    headers = {"Authorization": f"Bearer {token}"}
    resp = _http().delete(url, headers=headers, timeout=timeout)
    if resp.status_code not in (200, 204):
        resp.raise_for_status()
