"""Carrier tracking — UPS + FedEx.

Both APIs use OAuth2 client-credentials. Tokens cached in-process for their TTL
to avoid hitting the auth endpoint on every track call.

Each provider returns a normalized `TrackingResult`:
    - status: our `carrier_status` enum
    - events: list of `TrackingEvent` (occurred_at, status, location, description)

Carrier auto-detection lives here too — `detect_carrier(tracking_number)`.
"""

from __future__ import annotations

import base64
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import get_settings


logger = logging.getLogger("tracking")


# ────────────────────────── Format detection ─────────────────────────────

_UPS_RE = re.compile(r"^1Z[A-Z0-9]{16}$", re.IGNORECASE)
_FEDEX_RE = re.compile(r"^(\d{12}|\d{15}|\d{20}|\d{22})$")


def detect_carrier(tracking_number: str) -> str:
    code = (tracking_number or "").strip().replace(" ", "")
    if _UPS_RE.match(code):
        return "ups"
    if _FEDEX_RE.match(code):
        return "fedex"
    return "other"


# ────────────────────────── Result types ─────────────────────────────────


@dataclass
class TrackingEvent:
    occurred_at: datetime
    status: str  # our carrier_status enum
    location: str | None
    description: str | None
    raw: dict[str, Any] | None


@dataclass
class TrackingResult:
    status: str
    events: list[TrackingEvent] = field(default_factory=list)
    error: str | None = None


# ────────────────────────── Token cache ──────────────────────────────────


@dataclass
class _CachedToken:
    token: str
    expires_at: float


_token_lock = threading.Lock()
_token_cache: dict[str, _CachedToken] = {}


def _cached_token(key: str) -> str | None:
    with _token_lock:
        entry = _token_cache.get(key)
        if entry and entry.expires_at > time.time() + 30:
            return entry.token
    return None


def _store_token(key: str, token: str, ttl_seconds: int) -> None:
    with _token_lock:
        _token_cache[key] = _CachedToken(token=token, expires_at=time.time() + ttl_seconds)


# ────────────────────────── UPS provider ─────────────────────────────────


def _ups_token(timeout: float) -> str | None:
    settings = get_settings()
    cid = settings.ups_client_id.strip()
    csec = settings.ups_client_secret.strip()
    if not cid or not csec:
        return None

    cached = _cached_token("ups")
    if cached:
        return cached

    auth = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    resp = httpx.post(
        f"{settings.ups_base_url.rstrip('/')}/security/v1/oauth/token",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data="grant_type=client_credentials",
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload["access_token"]
    expires_in = int(payload.get("expires_in", 14400))  # UPS default 4h
    _store_token("ups", token, expires_in)
    return token


_UPS_STATUS_MAP = {
    # status.type letter → our enum
    "M": "pending",  # Manifest pickup
    "P": "pending",
    "I": "in_transit",
    "O": "out_for_delivery",
    "D": "delivered",
    "X": "exception",
    "RS": "exception",  # returned
}


def _ups_track(tracking_number: str) -> TrackingResult:
    settings = get_settings()
    timeout = settings.tracking_http_timeout_seconds

    try:
        token = _ups_token(timeout)
    except Exception as e:
        return TrackingResult(status="unknown", error=f"UPS auth failed: {e}")

    if not token:
        return TrackingResult(
            status="unknown",
            error="UPS not configured (set UPS_CLIENT_ID and UPS_CLIENT_SECRET).",
        )

    url = f"{settings.ups_base_url.rstrip('/')}/api/track/v1/details/{tracking_number}"
    headers = {
        "Authorization": f"Bearer {token}",
        "transId": f"endy-{int(time.time())}",
        "transactionSrc": "endymion-asset-inventory",
        "Accept": "application/json",
    }

    try:
        resp = httpx.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPStatusError as e:
        return TrackingResult(
            status="unknown", error=f"UPS HTTP {e.response.status_code}"
        )
    except Exception as e:
        return TrackingResult(status="unknown", error=f"UPS track failed: {e}")

    return _parse_ups_payload(payload)


def _parse_ups_payload(payload: dict[str, Any]) -> TrackingResult:
    """UPS shape: trackResponse.shipment[0].package[0].activity[]."""

    try:
        shipments = payload["trackResponse"]["shipment"]
        if not shipments:
            return TrackingResult(status="unknown", error="UPS returned no shipments.")
        packages = shipments[0].get("package") or []
        if not packages:
            return TrackingResult(status="unknown", error="UPS returned no packages.")
        activities = packages[0].get("activity") or []
    except (KeyError, IndexError, TypeError) as e:
        return TrackingResult(status="unknown", error=f"UPS unexpected shape: {e}")

    events: list[TrackingEvent] = []
    for act in activities:
        status_type = (act.get("status") or {}).get("type") or ""
        normalized = _UPS_STATUS_MAP.get(status_type, "unknown")
        location_obj = act.get("location") or {}
        addr = location_obj.get("address") or {}
        loc = ", ".join(
            v for v in [addr.get("city"), addr.get("stateProvince"), addr.get("country")] if v
        )
        description = (act.get("status") or {}).get("description")

        date_str = act.get("date") or ""
        time_str = act.get("time") or "000000"
        try:
            ts = datetime.strptime(date_str + time_str.zfill(6), "%Y%m%d%H%M%S")
        except Exception:
            ts = datetime.now(timezone.utc).replace(tzinfo=None)

        events.append(
            TrackingEvent(
                occurred_at=ts,
                status=normalized,
                location=loc or None,
                description=description,
                raw=act,
            ),
        )

    # Most recent activity drives current status (UPS lists newest first).
    current = events[0].status if events else "unknown"
    return TrackingResult(status=current, events=events)


# ────────────────────────── FedEx provider ───────────────────────────────


def _fedex_token(timeout: float) -> str | None:
    settings = get_settings()
    key = settings.fedex_api_key.strip()
    secret = settings.fedex_secret_key.strip()
    if not key or not secret:
        return None

    cached = _cached_token("fedex")
    if cached:
        return cached

    resp = httpx.post(
        f"{settings.fedex_base_url.rstrip('/')}/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": key,
            "client_secret": secret,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload["access_token"]
    expires_in = int(payload.get("expires_in", 3600))  # FedEx default 1h
    _store_token("fedex", token, expires_in)
    return token


# FedEx eventType codes → our enum. See FedEx Track API docs.
_FEDEX_STATUS_MAP = {
    "PU": "pending",  # Picked Up
    "OC": "pending",  # Order Created / shipping label created
    "AR": "in_transit",  # Arrived at FedEx location
    "DP": "in_transit",  # Departed FedEx location
    "IT": "in_transit",  # In Transit
    "AO": "in_transit",  # Shipment arriving On-Time
    "HA": "in_transit",  # Hold at location request accepted
    "HP": "in_transit",  # Ready for recipient pickup
    "RR": "in_transit",  # Delivery option requested
    "HL": "in_transit",  # Hold at location
    "OD": "out_for_delivery",
    "DL": "delivered",
    "DE": "exception",  # Delivery exception
    "SE": "exception",  # Shipment exception
    "CA": "exception",  # Cancelled
}


def _fedex_track(tracking_number: str) -> TrackingResult:
    settings = get_settings()
    timeout = settings.tracking_http_timeout_seconds

    try:
        token = _fedex_token(timeout)
    except Exception as e:
        return TrackingResult(status="unknown", error=f"FedEx auth failed: {e}")

    if not token:
        return TrackingResult(
            status="unknown",
            error="FedEx not configured (set FEDEX_API_KEY and FEDEX_SECRET_KEY).",
        )

    url = f"{settings.fedex_base_url.rstrip('/')}/track/v1/trackingnumbers"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-locale": "en_US",
    }
    body = {
        "includeDetailedScans": True,
        "trackingInfo": [
            {"trackingNumberInfo": {"trackingNumber": tracking_number}},
        ],
    }

    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPStatusError as e:
        return TrackingResult(
            status="unknown", error=f"FedEx HTTP {e.response.status_code}"
        )
    except Exception as e:
        return TrackingResult(status="unknown", error=f"FedEx track failed: {e}")

    return _parse_fedex_payload(payload)


def _parse_fedex_payload(payload: dict[str, Any]) -> TrackingResult:
    """FedEx shape: output.completeTrackResults[0].trackResults[0].scanEvents[]."""

    try:
        complete = payload["output"]["completeTrackResults"]
        if not complete:
            return TrackingResult(status="unknown", error="FedEx returned no results.")
        track_results = complete[0].get("trackResults") or []
        if not track_results:
            return TrackingResult(status="unknown", error="FedEx returned no trackResults.")
        first = track_results[0]
        # Per-tracking error (e.g. NOTFOUND) — bubble the message up
        track_err = first.get("error") or {}
        if track_err.get("code") and track_err.get("code") != "":
            return TrackingResult(
                status="unknown",
                error=f"FedEx: {track_err.get('message') or track_err.get('code')}",
            )
        scan_events = first.get("scanEvents") or []
    except (KeyError, TypeError) as e:
        return TrackingResult(status="unknown", error=f"FedEx unexpected shape: {e}")

    events: list[TrackingEvent] = []
    for ev in scan_events:
        event_type = ev.get("eventType") or ""
        normalized = _FEDEX_STATUS_MAP.get(event_type, "unknown")

        scan_loc = ev.get("scanLocation") or {}
        loc = ", ".join(
            v
            for v in [
                scan_loc.get("city"),
                scan_loc.get("stateOrProvinceCode"),
                scan_loc.get("countryCode"),
            ]
            if v
        )
        description = ev.get("eventDescription")

        date_str = ev.get("date") or ""
        try:
            # FedEx returns ISO-8601 with offset, e.g. "2025-01-15T08:30:00-06:00"
            ts = datetime.fromisoformat(date_str)
            if ts.tzinfo is not None:
                ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            ts = datetime.now(timezone.utc).replace(tzinfo=None)

        events.append(
            TrackingEvent(
                occurred_at=ts,
                status=normalized,
                location=loc or None,
                description=description,
                raw=ev,
            ),
        )

    # FedEx returns newest first.
    current = events[0].status if events else "unknown"
    return TrackingResult(status=current, events=events)


# ────────────────────────── Public entry point ───────────────────────────


def fetch_tracking(carrier: str, tracking_number: str) -> TrackingResult:
    """Dispatch to the right provider. Never raises — returns error in result."""

    tn = (tracking_number or "").strip().replace(" ", "")
    if not tn:
        return TrackingResult(status="unknown", error="Empty tracking number.")

    if carrier == "ups":
        return _ups_track(tn)
    if carrier == "fedex":
        return _fedex_track(tn)
    return TrackingResult(
        status="unknown",
        error=f"Carrier '{carrier}' is not auto-trackable. Update status manually.",
    )
