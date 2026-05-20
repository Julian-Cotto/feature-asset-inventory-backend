"""Microsoft Defender for Endpoint (api.securitycenter.microsoft.com).

Companion to `intune_service`. Same Azure AD app registration is reused,
but Defender requires a different OAuth resource scope, so we maintain a
separate token cache. Devices are linked to Intune via `aadDeviceId`
(== Intune's `azureADDeviceId`).

Required app permissions on the Azure AD registration:
  - WindowsDefenderATP.Machine.Read.All       (Application) — bulk + lookup
  - WindowsDefenderATP.Machine.CollectForensics (Application) — collect action
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings


logger = logging.getLogger("defender")


DEFENDER_API_BASE = "https://api.securitycenter.microsoft.com"
DEFENDER_RESOURCE = "https://api.securitycenter.microsoft.com"


# ────────────────────────── Shared HTTP client ──────────────────────────
# Reused TCP+TLS across calls. Separate from intune_service's client so
# Defender's keep-alive doesn't compete with Graph's connection budget.

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
class DefenderMachine:
    """Subset of Defender machine fields we cache. See:
    https://learn.microsoft.com/en-us/microsoft-365/security/defender-endpoint/api/machine"""
    id: str
    computer_dns_name: str | None
    aad_device_id: str | None
    health_status: str | None       # Active / Inactive / ImpairedCommunication / NoSensorData / Unknown
    risk_score: str | None          # None / Informational / Low / Medium / High
    exposure_level: str | None      # None / Low / Medium / High
    last_seen: str | None           # ISO timestamp
    onboarding_status: str | None   # Onboarded / NotOnboarded / InsufficientInfo
    defender_av_status: str | None  # Updated / OutOfDate / Unknown / NotSupported
    os_build: str | None
    last_ip_address: str | None
    machine_tags: list[str]
    raw: dict[str, Any]


# ────────────────────────── Token cache (separate from Intune) ───────────


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
    """Defender token. Uses the same Azure AD app reg as Intune but a
    distinct resource scope (`api.securitycenter.microsoft.com`)."""
    s = get_settings()
    url = f"https://login.microsoftonline.com/{s.intune_tenant_id}/oauth2/v2.0/token"
    resp = _http().post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": s.intune_client_id,
            "client_secret": s.intune_client_secret,
            "scope": f"{DEFENDER_RESOURCE}/.default",
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


def _machine_from_payload(item: dict[str, Any]) -> DefenderMachine:
    tags = item.get("machineTags") or []
    if not isinstance(tags, list):
        tags = []
    return DefenderMachine(
        id=str(item.get("id", "")),
        computer_dns_name=item.get("computerDnsName"),
        aad_device_id=item.get("aadDeviceId"),
        health_status=item.get("healthStatus"),
        risk_score=item.get("riskScore"),
        exposure_level=item.get("exposureLevel"),
        last_seen=item.get("lastSeen"),
        onboarding_status=item.get("onboardingStatus"),
        defender_av_status=item.get("defenderAvStatus"),
        os_build=str(item["osBuild"]) if item.get("osBuild") is not None else None,
        last_ip_address=item.get("lastIpAddress"),
        machine_tags=[str(t) for t in tags],
        raw=item,
    )


# ────────────────────────── Lookups ─────────────────────────────────────


def lookup_by_aad_device_id(aad_device_id: str) -> DefenderMachine | None:
    """Find a Defender machine by its `aadDeviceId`. Returns None if the
    device isn't onboarded to Defender (empty result from the filter)."""
    if not _is_configured():
        raise RuntimeError(
            "Defender not configured (uses INTUNE_TENANT_ID/CLIENT_ID/CLIENT_SECRET)."
        )
    if not aad_device_id:
        return None

    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = _ensure_token(timeout)
    safe = aad_device_id.replace("'", "''")
    url = f"{DEFENDER_API_BASE}/api/machines?$filter=aadDeviceId+eq+'{safe}'&$top=1"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    resp = _http().get(url, headers=headers, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    items = resp.json().get("value") or []
    return _machine_from_payload(items[0]) if items else None


def list_all_machines(*, max_pages: int = 200) -> list[DefenderMachine]:
    """Page through every onboarded machine. Pipelined pagination: the
    next page's request fires before the current page is parsed."""
    from app.services._graph_paging import paginate

    if not _is_configured():
        raise RuntimeError("Defender not configured.")

    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = _ensure_token(timeout)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    return paginate(
        _http(),
        f"{DEFENDER_API_BASE}/api/machines",
        headers=headers,
        timeout=timeout,
        parse=lambda payload: (
            _machine_from_payload(it) for it in (payload.get("value") or [])
        ),
        max_pages=max_pages,
    )


def index_by_aad_device_id(
    machines: list[DefenderMachine],
) -> dict[str, DefenderMachine]:
    """Build a `aadDeviceId → DefenderMachine` map for local bulk matching."""
    return {m.aad_device_id: m for m in machines if m.aad_device_id}


# ────────────────────────── Actions ─────────────────────────────────────


def collect_forensics(machine_id: str, comment: str = "Triggered from asset inventory") -> dict[str, Any]:
    """POST `/api/machines/{id}/collectInvestigationPackage`. Returns the
    machineAction record (status: Pending → InProgress → Succeeded/Failed).

    Requires the WindowsDefenderATP.Machine.CollectForensics permission."""
    if not _is_configured():
        raise RuntimeError("Defender not configured.")
    if not machine_id:
        raise ValueError("machine_id is required")

    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = _ensure_token(timeout)
    url = f"{DEFENDER_API_BASE}/api/machines/{machine_id}/collectInvestigationPackage"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = _http().post(url, json={"Comment": comment}, headers=headers, timeout=timeout)
    if resp.status_code not in (200, 201, 202):
        resp.raise_for_status()
    return resp.json() if resp.content else {}


# ────────────────────────── Process-local index cache ───────────────────
# Lazy TTL cache of aadDeviceId → DefenderMachine. First lookup after
# expiry triggers a refresh; subsequent lookups within TTL are O(1).
# Bulk syncs call `refresh_cache()` to force-refresh.

CACHE_TTL_SECONDS = 30 * 60

_cache_lock = threading.Lock()
_cache_index: dict[str, DefenderMachine] | None = None
_cache_ts: float = 0.0
_cache_error: str | None = None


def cached_index(*, max_age_seconds: int = CACHE_TTL_SECONDS) -> dict[str, DefenderMachine]:
    """Return the cached aadDeviceId → machine index, refreshing if stale.

    On refresh failure, returns the last-known-good cache (empty dict if
    never populated). Errors are logged + retained in `cache_error()`."""
    global _cache_index, _cache_ts, _cache_error
    with _cache_lock:
        fresh = (
            _cache_index is not None
            and (time.time() - _cache_ts) <= max_age_seconds
        )
        if fresh:
            return _cache_index or {}
    # Refresh outside the lock so concurrent callers don't all block on
    # the HTTP fetch. Last-writer-wins on the index swap.
    try:
        machines = list_all_machines()
        index = index_by_aad_device_id(machines)
        with _cache_lock:
            _cache_index = index
            _cache_ts = time.time()
            _cache_error = None
        logger.info(
            "defender_cache_refreshed",
            extra={"event": "defender_cache_refreshed", "machines": len(index)},
        )
        return index
    except Exception as e:
        with _cache_lock:
            _cache_error = str(e)
        logger.warning(
            "defender_cache_refresh_failed",
            extra={"event": "defender_cache_refresh_failed", "error": str(e)},
        )
        with _cache_lock:
            return _cache_index or {}


def refresh_cache() -> dict[str, DefenderMachine]:
    """Force-refresh the cache. Called by explicit bulk syncs + the
    startup background task."""
    global _cache_index, _cache_ts, _cache_error
    machines = list_all_machines()
    index = index_by_aad_device_id(machines)
    with _cache_lock:
        _cache_index = index
        _cache_ts = time.time()
        _cache_error = None
    return index


def cache_info() -> dict[str, Any]:
    """Diagnostic snapshot — useful for /health endpoints."""
    with _cache_lock:
        return {
            "populated": _cache_index is not None,
            "size": len(_cache_index) if _cache_index is not None else 0,
            "age_seconds": (
                round(time.time() - _cache_ts) if _cache_ts else None
            ),
            "last_error": _cache_error,
        }
