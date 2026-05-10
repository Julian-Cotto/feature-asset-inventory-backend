"""Device identification by scanned code.

Two providers wired today:
  - Meraki (Q…-…-… serials) → official Dashboard API. Needs MERAKI_API_KEY.
  - Lenovo (7-8 char alnum)  → undocumented public warranty endpoint. No auth.

Results are cached in `device_lookups` to keep external traffic low and
absorb provider hiccups gracefully.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.inventory import DeviceLookup
from app.services import intune_service


logger = logging.getLogger("lookup")


# ────────────────────────── Format detection ─────────────────────────────

_MERAKI_RE = re.compile(r"^Q[A-Z0-9]{3}-[A-Z0-9]{4}-[A-Z0-9]{4}$", re.IGNORECASE)
_LENOVO_RE = re.compile(r"^[A-Z0-9]{7,8}$", re.IGNORECASE)
_UPC_RE = re.compile(r"^\d{8}$|^\d{12}$|^\d{13}$")


def detect_source(code: str) -> str:
    """Return the provider that should handle this code, or "unknown"."""

    c = code.strip()
    if _MERAKI_RE.match(c):
        return "meraki"
    if _UPC_RE.match(c):
        return "upc"
    if _LENOVO_RE.match(c):
        return "lenovo"
    return "unknown"


# ────────────────────────── Result type ──────────────────────────────────


@dataclass
class LookupResult:
    code: str
    source: str  # "meraki" | "lenovo" | "upc" | "unknown"
    asset_type: str | None
    manufacturer: str | None
    model: str | None
    series: str | None
    generation: str | None
    cpu: str | None
    os: str | None
    os_version: str | None
    intune_id: str | None
    assigned_upn: str | None
    warranty_active: bool | None
    warranty_end_date: datetime | None
    raw: dict[str, Any] | None
    cached: bool
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "source": self.source,
            "assetType": self.asset_type,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "series": self.series,
            "generation": self.generation,
            "cpu": self.cpu,
            "os": self.os,
            "osVersion": self.os_version,
            "intuneId": self.intune_id,
            "assignedUpn": self.assigned_upn,
            "warrantyActive": self.warranty_active,
            "warrantyEndDate": (
                self.warranty_end_date.isoformat() if self.warranty_end_date else None
            ),
            "raw": self.raw,
            "cached": self.cached,
            "error": self.error,
        }


# ────────────────────────── Cache plumbing ───────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _read_cache(db: Session, code: str) -> DeviceLookup | None:
    return db.query(DeviceLookup).filter(DeviceLookup.code == code).one_or_none()


def _is_fresh(row: DeviceLookup, ttl_hours: int) -> bool:
    return row.fetched_at >= _now_utc() - timedelta(hours=ttl_hours)


def _row_to_result(row: DeviceLookup) -> LookupResult:
    raw: dict[str, Any] | None = None
    if row.raw_json:
        try:
            raw = json.loads(row.raw_json)
        except Exception:
            raw = None

    return LookupResult(
        code=row.code,
        source=row.source,
        asset_type=row.asset_type,
        manufacturer=row.manufacturer,
        model=row.model,
        series=row.series,
        generation=row.generation,
        cpu=row.cpu,
        os=row.os,
        os_version=row.os_version,
        intune_id=row.intune_id,
        assigned_upn=row.assigned_upn,
        warranty_active=row.warranty_active,
        warranty_end_date=row.warranty_end_date,
        raw=raw,
        cached=True,
        error=row.error,
    )


def _write_cache(db: Session, result: LookupResult) -> None:
    existing = _read_cache(db, result.code)
    raw_blob = json.dumps(result.raw) if result.raw is not None else None

    if existing is None:
        db.add(
            DeviceLookup(
                code=result.code,
                source=result.source,
                asset_type=result.asset_type,
                manufacturer=result.manufacturer,
                model=result.model,
                series=result.series,
                generation=result.generation,
                cpu=result.cpu,
                os=result.os,
                os_version=result.os_version,
                intune_id=result.intune_id,
                assigned_upn=result.assigned_upn,
                warranty_active=result.warranty_active,
                warranty_end_date=result.warranty_end_date,
                raw_json=raw_blob,
                error=result.error,
                fetched_at=_now_utc(),
            ),
        )
    else:
        existing.source = result.source
        existing.asset_type = result.asset_type
        existing.manufacturer = result.manufacturer
        existing.model = result.model
        existing.series = result.series
        existing.generation = result.generation
        existing.cpu = result.cpu
        existing.os = result.os
        existing.os_version = result.os_version
        existing.intune_id = result.intune_id
        existing.assigned_upn = result.assigned_upn
        existing.warranty_active = result.warranty_active
        existing.warranty_end_date = result.warranty_end_date
        existing.raw_json = raw_blob
        existing.error = result.error
        existing.fetched_at = _now_utc()

    db.commit()


# ────────────────────────── Providers ────────────────────────────────────


def _empty_result(code: str, source: str, error: str | None = None) -> LookupResult:
    return LookupResult(
        code=code,
        source=source,
        asset_type=None,
        manufacturer=None,
        model=None,
        series=None,
        generation=None,
        cpu=None,
        os=None,
        os_version=None,
        intune_id=None,
        assigned_upn=None,
        warranty_active=None,
        warranty_end_date=None,
        raw=None,
        cached=False,
        error=error,
    )


def _meraki_lookup(code: str, timeout: float) -> LookupResult:
    settings = get_settings()
    api_key = settings.meraki_api_key.strip()
    org_id = settings.meraki_org_id.strip()

    if not api_key or not org_id:
        return _empty_result(
            code,
            "meraki",
            "Meraki provider not configured (set MERAKI_API_KEY and MERAKI_ORG_ID).",
        )

    url = f"{settings.meraki_base_url.rstrip('/')}/organizations/{org_id}/inventoryDevices/{code}"
    headers = {
        "X-Cisco-Meraki-API-Key": api_key,
        "Accept": "application/json",
    }

    try:
        resp = httpx.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPStatusError as e:
        return _empty_result(code, "meraki", f"Meraki HTTP {e.response.status_code}")
    except Exception as e:
        return _empty_result(code, "meraki", f"Meraki lookup failed: {e}")

    model = (payload.get("model") or "").strip()
    asset_type = _meraki_model_to_type(model)

    return LookupResult(
        code=code,
        source="meraki",
        asset_type=asset_type,
        manufacturer="Meraki",
        model=model or None,
        series=None,
        generation=None,
        cpu=None,
        os=None,
        os_version=None,
        intune_id=None,
        assigned_upn=None,
        warranty_active=None,
        warranty_end_date=None,
        raw=payload,
        cached=False,
        error=None,
    )


def _meraki_model_to_type(model: str) -> str | None:
    """MX/MS/MR/MV → asset type. Best-effort."""

    if not model:
        return None
    upper = model.upper()
    if upper.startswith("MR"):
        return "ap"
    if upper.startswith("MS"):
        return "switch"
    if upper.startswith(("MX", "Z3", "Z4", "VMX")):
        return "gateway"
    return None


_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class _LenovoHtml:
    name: str | None
    warranty_active: bool | None
    warranty_end_date: datetime | None


def _lenovo_html_scrape(code: str, timeout: float) -> _LenovoHtml:
    """Fetch the Lenovo warranty HTML page once; extract friendly product
    name AND warranty status / end date.

    Flow:
      1. Hit /api/v4/mse/getproducts?productId=<serial> → JSON with canonical URL
      2. Fetch that warranty HTML page (or permalink fallback)
      3. Extract:
         - friendly name from `<div class="prod-name-text">…</div>`
         - warranty end date from inline JSON `EntireWarrantyPeriod.End` (ms)
         - warranty active from `BaseWarranties[].StatusV2` ("Active") or
           by comparing end date to now.
    """

    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    candidate_urls: list[str] = []
    try:
        resp = httpx.get(
            "https://pcsupport.lenovo.com/us/en/api/v4/mse/getproducts",
            params={"productId": code},
            headers={**headers, "Accept": "application/json"},
            timeout=timeout,
        )
        if resp.status_code == 200:
            payload = resp.json()
            url = _extract_warranty_url(payload, code)
            if url:
                candidate_urls.append(url)
    except Exception:
        pass

    candidate_urls.append(
        f"https://pcsupport.lenovo.com/us/en/products/{code}/warranty"
    )

    for url in candidate_urls:
        try:
            resp = httpx.get(
                url,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                continue
            text = resp.text
            name = _parse_prod_name_text(text)
            active, end = _parse_warranty(text)
            if name or end is not None:
                return _LenovoHtml(name=name, warranty_active=active, warranty_end_date=end)
        except Exception:
            continue

    return _LenovoHtml(name=None, warranty_active=None, warranty_end_date=None)


_WARRANTY_END_MS_RE = re.compile(
    r'"EntireWarrantyPeriod"\s*:\s*\{[^{}]*?"End"\s*:\s*(\d{10,16})',
)
_WARRANTY_END_DATE_RE = re.compile(
    r'"BaseWarranties"\s*:\s*\[(.*?)\]',
    re.DOTALL,
)
_WARRANTY_ENDDATE_FIELD_RE = re.compile(
    r'"EndDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"',
)
_WARRANTY_STATUSV2_RE = re.compile(r'"StatusV2"\s*:\s*"([^"]+)"')


def _parse_warranty(html: str) -> tuple[bool | None, datetime | None]:
    """Return (active, end_date). Either may be None if not found."""

    if not html:
        return (None, None)

    end_dt: datetime | None = None
    m = _WARRANTY_END_MS_RE.search(html)
    if m:
        try:
            end_dt = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            end_dt = None

    if end_dt is None:
        bw = _WARRANTY_END_DATE_RE.search(html)
        if bw:
            latest: datetime | None = None
            for ds in _WARRANTY_ENDDATE_FIELD_RE.findall(bw.group(1)):
                try:
                    parsed = datetime.strptime(ds, "%Y-%m-%d")
                    if latest is None or parsed > latest:
                        latest = parsed
                except Exception:
                    continue
            end_dt = latest

    active: bool | None = None
    if end_dt is not None:
        active = end_dt > _now_utc()
    else:
        statuses = [s.lower() for s in _WARRANTY_STATUSV2_RE.findall(html)]
        if statuses:
            active = any(s == "active" for s in statuses)

    return (active, end_dt)


def _extract_warranty_url(payload: Any, code: str) -> str | None:
    """Walk the getproducts JSON for any string that looks like a Lenovo
    warranty page URL containing the serial."""

    target = code.lower()
    if isinstance(payload, dict):
        for v in payload.values():
            url = _extract_warranty_url(v, code)
            if url:
                return url
    elif isinstance(payload, list):
        for v in payload:
            url = _extract_warranty_url(v, code)
            if url:
                return url
    elif isinstance(payload, str):
        s = payload
        # Accept either fully qualified URL or relative path
        lower = s.lower()
        if (
            ("/products/" in lower or lower.startswith("/products/"))
            and target in lower
        ):
            if s.startswith("//"):
                return "https:" + s
            if s.startswith("/"):
                return "https://pcsupport.lenovo.com" + s
            if s.startswith(("http://", "https://")):
                return s
    return None


_PROD_NAME_RE = re.compile(
    r'<div\s+class=(?:"|\')prod-name-text(?:"|\')\s*>\s*([^<]+?)\s*</div>',
    re.IGNORECASE | re.DOTALL,
)


def _parse_prod_name_text(html: str) -> str | None:
    m = _PROD_NAME_RE.search(html or "")
    if not m:
        return None
    name = m.group(1).strip()
    # Many entries are formatted "<series> - Type <MT>" — keep as-is; the
    # split helper later distinguishes series / generation.
    return name or None


def _lenovo_lookup(code: str, timeout: float) -> LookupResult:
    settings = get_settings()
    if not settings.lenovo_lookup_enabled:
        return _empty_result(code, "lenovo", "Lenovo lookup disabled in config.")

    headers = {
        "Accept": "application/json, text/html, */*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": _BROWSER_UA,
    }

    payload: Any = None
    model = ""

    # Primary: mse/getproducts JSON endpoint — returns list of product info
    # including a friendly `Name` field.
    try:
        resp = httpx.get(
            "https://pcsupport.lenovo.com/us/en/api/v4/mse/getproducts",
            params={"productId": code},
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        logger.debug("Lenovo getproducts failed for %s: %s", code, e)
        payload = None

    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            model = (first.get("Name") or "").strip()
    elif isinstance(payload, dict):
        # Defensive: handle alternate response shapes
        candidates = [payload]
        data = payload.get("data")
        if isinstance(data, dict):
            candidates.append(data)
        if isinstance(data, list):
            candidates.extend([d for d in data if isinstance(d, dict)])
        for c in candidates:
            model = (
                c.get("Name")
                or c.get("productName")
                or c.get("model")
                or c.get("displayName")
                or ""
            ).strip()
            if model:
                break

    # Always scrape HTML page — only place warranty data lives. Also covers
    # the case where getproducts JSON didn't yield a friendly name.
    html_scrape = _lenovo_html_scrape(code, timeout)
    if not model and html_scrape.name:
        model = html_scrape.name

    # Strip noisy "- Type 21MV" suffix
    if model:
        model = _LENOVO_TYPE_SUFFIX_RE.sub("", model).strip()

    series, generation = _lenovo_split_series_generation(model)
    asset_type = _lenovo_name_to_type(model)

    return LookupResult(
        code=code,
        source="lenovo",
        asset_type=asset_type,
        manufacturer="Lenovo" if model else None,
        model=model or None,
        series=series,
        generation=generation,
        cpu=None,
        os=None,
        os_version=None,
        intune_id=None,
        assigned_upn=None,
        warranty_active=html_scrape.warranty_active,
        warranty_end_date=html_scrape.warranty_end_date,
        raw=payload,
        cached=False,
        error=None if model else "Lenovo lookup found no product name.",
    )


_LENOVO_GEN_FULL_RE = re.compile(r"\s+Gen\s+(\d+[A-Z]?)\b", re.IGNORECASE)
# Compact form: "G7" / "G11" embedded mid-name (e.g. "ThinkBook 14 G7 ARP")
_LENOVO_GEN_SHORT_RE = re.compile(r"\s+G(\d+[A-Z]?)\b", re.IGNORECASE)
# Strip "- Type 21MV" suffix Lenovo's HTML appends
_LENOVO_TYPE_SUFFIX_RE = re.compile(r"\s*-\s*Type\s+[A-Z0-9]+\s*$", re.IGNORECASE)


def _lenovo_split_series_generation(name: str) -> tuple[str | None, str | None]:
    """Split Lenovo product name into (series, generation).

    Examples:
      'ThinkPad E16 Gen 1'        → ('ThinkPad E16', 'Gen 1')
      'ThinkBook 14 G7 ARP'       → ('ThinkBook 14 ARP', 'G7')
      'ThinkBook 14 G7 ARP - Type 21MV' → ('ThinkBook 14 ARP', 'G7')
      'IdeaPad 5'                 → ('IdeaPad 5', None)
    """

    if not name:
        return (None, None)

    # Drop trailing "- Type XXXX" first
    cleaned = _LENOVO_TYPE_SUFFIX_RE.sub("", name).strip()

    m = _LENOVO_GEN_FULL_RE.search(cleaned)
    if m:
        before = cleaned[: m.start()].strip()
        after = cleaned[m.end():].strip()
        series = " ".join(p for p in (before, after) if p) or None
        return (series, f"Gen {m.group(1)}")

    m = _LENOVO_GEN_SHORT_RE.search(cleaned)
    if m:
        before = cleaned[: m.start()].strip()
        after = cleaned[m.end():].strip()
        series = " ".join(p for p in (before, after) if p) or None
        return (series, f"G{m.group(1)}")

    return (cleaned or None, None)


def _lenovo_name_to_type(name: str) -> str | None:
    """ThinkPad/IdeaPad → laptop, ThinkCentre → desktop, etc. Best-effort."""

    if not name:
        return None
    lower = name.lower()
    if "thinkpad" in lower or "ideapad" in lower or "yoga" in lower or "thinkbook" in lower:
        return "laptop"
    if "thinkcentre" in lower or "ideacentre" in lower:
        return "desktop"
    if "thinclient" in lower or "thin client" in lower:
        return "thin_client"
    return None


# ────────────────────────── Dell ────────────────────────────────────────


_DELL_HOST = "https://www.dell.com"
_DELL_REFERER = f"{_DELL_HOST}/support/contractservices/en-us"
_DELL_ENCVALUE_URL = (
    f"{_DELL_HOST}/support/components/detectproduct/encvalue/{{code}}?appname=warranty"
)
_DELL_PRODUCTDETAILS_URL = (
    f"{_DELL_HOST}/support/productsmfe/en-us/productdetails"
    "?selection={code}&assettype=svctag&appname=warranty&inccomponents=false&isolated=false"
)
_DELL_CONTRACT_URL = (
    f"{_DELL_HOST}/support/contractservices/en-us/entitlement/contractservicesapi/v1"
)

# Per-thread curl_cffi session. curl_cffi sessions wrap libcurl handles
# which aren't thread-safe, so each worker thread gets its own. Reused across
# calls within a thread so we don't re-warmup for every asset.
import threading as _threading

_dell_local = _threading.local()


def _dell_session():
    from curl_cffi import requests as cr  # lazy import

    s = getattr(_dell_local, "session", None)
    if s is None:
        s = cr.Session(impersonate="chrome", default_headers=False)
        _dell_local.session = s
        _dell_local.warmed = False
    return s


def _dell_reset_session() -> None:
    _dell_local.session = None
    _dell_local.warmed = False


def _dell_warmup(timeout: float) -> None:
    if getattr(_dell_local, "warmed", False):
        return
    s = _dell_session()
    s.get(
        _DELL_REFERER,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br, zstd",
        },
        timeout=timeout,
    )
    _dell_local.warmed = True


_DELL_PRODUCTNAME_RE = re.compile(
    r'<h4[^>]*class="[^"]*dds__text-lg-left[^"]*"[^>]*>\s*([^<]+?)\s*</h4>',
    re.IGNORECASE | re.DOTALL,
)
_DELL_PRODUCTNAME_TITLE_RE = re.compile(
    r'title="([^"]+)"\s+alt="\1"\s+class="dds__pt-2"',
    re.IGNORECASE,
)


def _dell_lookup(code: str, timeout: float, skip_product_name: bool = False) -> LookupResult:
    """Three-step Dell lookup (Akamai-bypassed via curl_cffi Chrome impersonation):
      1. GET detectproduct/encvalue/<svctag> → encrypted asset ID (plain text).
      2. POST contractservicesapi/v1 with encrypted ID → warranty JSON.
      3. GET productsmfe productdetails HTML → product name (best-effort).

    Cached in `device_lookups` under source="dell". The validate/asset POST
    used by the Dell HAR triggers Akamai's sec-cp-challenge and isn't usable
    without a real JS-capable browser, so we route around it.

    skip_product_name=True drops step 3 — useful for bulk refresh when the
    caller already has a model populated from Intune. Saves ~1 round trip.
    """

    settings = get_settings()
    if not getattr(settings, "dell_lookup_enabled", True):
        return _empty_result(code, "dell", "Dell lookup disabled in config.")

    common_hdrs = {
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br, zstd",
    }
    api_hdrs = {
        **common_hdrs,
        "Accept": "*/*",
        "Origin": _DELL_HOST,
        "Referer": _DELL_REFERER,
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        _dell_warmup(timeout)
        s = _dell_session()
    except Exception as e:
        return _empty_result(code, "dell", f"Dell session init failed: {e}")

    # Step 1: encrypted asset ID
    try:
        r1 = s.get(
            _DELL_ENCVALUE_URL.format(code=code),
            headers=api_hdrs,
            timeout=timeout,
        )
        if r1.status_code == 428:
            _dell_reset_session()
            return _empty_result(
                code, "dell", "Dell blocked by bot challenge (sec-cp). Retry later."
            )
        r1.raise_for_status()
        encrypted = r1.text.strip()
    except Exception as e:
        return _empty_result(code, "dell", f"Dell encvalue fetch failed: {e}")

    if not encrypted or len(encrypted) < 8 or "<" in encrypted:
        return _empty_result(code, "dell", "Dell did not return an encrypted asset ID.")

    # Step 2: warranty JSON
    try:
        r2 = s.post(
            _DELL_CONTRACT_URL,
            json={"assetFormat": "servicetag", "assetId": encrypted, "appName": "home"},
            headers={**api_hdrs, "Content-Type": "application/json"},
            timeout=timeout,
        )
        if r2.status_code == 428:
            _dell_reset_session()
            return _empty_result(
                code, "dell", "Dell blocked by bot challenge (sec-cp). Retry later."
            )
        r2.raise_for_status()
        warranty = r2.json()
    except Exception as e:
        return _empty_result(code, "dell", f"Dell warranty fetch failed: {e}")

    on_support = warranty.get("onSupport")
    warranty_active = bool(on_support) if on_support is not None else None
    end_dt = _parse_dell_end_date(
        warranty.get("warrantyEndDateUtc"), warranty.get("warrantyEndDate")
    )
    response_code = warranty.get("warrantyResponseCode")

    # Dell's "asset not found" comes back with isIssueWithAsset=true /
    # warrantyResponseCode != 200. Treat as not-found rather than error.
    if response_code is not None and str(response_code) != "200":
        return _empty_result(
            code,
            "dell",
            f"Dell did not recognize service tag (code {response_code}).",
        )

    # Step 3: product name (HTML scrape, best-effort, skippable)
    product_name: str | None = None
    if not skip_product_name:
        try:
            r3 = s.get(
                _DELL_PRODUCTDETAILS_URL.format(code=code),
                headers={**api_hdrs, "Accept": "*/*"},
                timeout=timeout,
            )
            if r3.status_code == 200:
                html = r3.text
                m = _DELL_PRODUCTNAME_TITLE_RE.search(html) or _DELL_PRODUCTNAME_RE.search(html)
                if m:
                    product_name = m.group(1).strip() or None
        except Exception:
            pass

    return LookupResult(
        code=code,
        source="dell",
        asset_type=_dell_name_to_type(product_name),
        manufacturer="Dell",
        model=product_name,
        series=None,
        generation=None,
        cpu=None,
        os=None,
        os_version=None,
        intune_id=None,
        assigned_upn=None,
        warranty_active=warranty_active,
        warranty_end_date=end_dt,
        raw={"warranty": warranty, "encrypted": encrypted},
        cached=False,
        error=None,
    )


def _dell_name_to_type(name: str | None) -> str | None:
    if not name:
        return None
    lower = name.lower()
    if any(k in lower for k in ("latitude", "xps", "vostro", "inspiron", "precision m", "precision 5", "precision 7")):
        return "laptop"
    if any(k in lower for k in ("optiplex", "precision tower", "precision desktop")):
        return "desktop"
    if "wyse" in lower or "thin client" in lower:
        return "thin_client"
    return None


def _try_dell(code: str, settings) -> LookupResult | None:
    """Best-effort Dell cascade for the live `lookup_device` path.
    Returns None if Dell is disabled, the call errors, or the result has no
    useful data — caller should fall back to whatever it had."""

    if not getattr(settings, "dell_lookup_enabled", True):
        return None
    timeout = getattr(settings, "dell_lookup_timeout_seconds", 20.0)
    try:
        result = _dell_lookup(code, timeout)
    except Exception:
        return None
    if (
        not result.model
        and result.warranty_active is None
        and result.warranty_end_date is None
    ):
        return None
    return result


def _parse_dell_end_date(utc_value: Any, friendly: Any) -> datetime | None:
    if utc_value:
        try:
            s = str(utc_value).replace("Z", "+00:00")
            parsed = datetime.fromisoformat(s)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except Exception:
            pass
    if friendly:
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(str(friendly), fmt)
            except Exception:
                continue
    return None


# ────────────────────────── Entry point ──────────────────────────────────


def lookup_device(db: Session, code: str) -> LookupResult:
    """Look up a scanned code, using cache when fresh."""

    code = (code or "").strip()
    if not code:
        return _empty_result("", "unknown", "Empty code.")

    settings = get_settings()
    cached = _read_cache(db, code)
    # Honor cache only when the cached model looks friendly (has a space).
    # Bogus short MTM-prefix values like "21MV" cached by older code, or
    # empty failures, all get retried.
    cached_model_friendly = bool(cached and cached.model and " " in cached.model)
    if (
        cached is not None
        and _is_fresh(cached, settings.lookup_cache_ttl_hours)
        and (cached_model_friendly or cached.manufacturer == "Meraki")
    ):
        return _row_to_result(cached)

    source = detect_source(code)
    timeout = settings.lookup_http_timeout_seconds

    if source == "meraki":
        result = _meraki_lookup(code, timeout)
    elif source == "lenovo":
        # Both Lenovo and Dell service tags fit the 7-8 alnum pattern. Try
        # Lenovo first (faster, no Akamai warmup); fall through to Dell if
        # Lenovo doesn't recognize the code.
        result = _lenovo_lookup(code, timeout)
        if not result.model:
            dell = _try_dell(code, settings)
            if dell is not None and dell.model:
                result = dell
        # Computers can also live in Intune. Merge any Intune match in.
        result = _merge_intune(result, code)
    elif source == "upc":
        result = _empty_result(code, "upc", "UPC lookup provider not implemented yet.")
    else:
        # Unknown format — try Dell as a last vendor option, then Intune.
        # Covers 7-char Dell tags that detect_source missed (rare) and
        # provides one cascade for non-standard codes.
        result = _empty_result(code, "unknown", "Code format not recognized.")
        dell = _try_dell(code, settings)
        if dell is not None and dell.model:
            result = dell
        result = _merge_intune(result, code)

    try:
        _write_cache(db, result)
    except Exception:
        logger.exception("Failed to cache lookup result for %s", code)

    return result


def _merge_intune(base: LookupResult, code: str) -> LookupResult:
    """Overlay Intune data onto an existing result. Intune fills only
    fields that are still empty so vendor data (Lenovo) wins on conflicts
    for fields it owns (model name, series). Intune-only fields (intune_id,
    assigned_upn, os, os_version) are always taken from Intune."""

    intune_result = intune_service.lookup_by_serial(code)
    if not intune_result.found or intune_result.device is None:
        # Don't surface "Intune not configured" / "not found" as an error
        # because the Lenovo half may have succeeded.
        return base

    d = intune_result.device

    return LookupResult(
        code=base.code,
        source=base.source,  # keep primary source label
        asset_type=base.asset_type
        or intune_service._chassis_to_asset_type(d.chassis_type)
        or None,
        manufacturer=base.manufacturer or d.manufacturer,
        model=base.model or d.model,
        series=base.series,
        generation=base.generation,
        cpu=base.cpu,
        os=d.operating_system,
        os_version=d.os_version,
        intune_id=d.intune_id,
        assigned_upn=d.assigned_upn,
        warranty_active=base.warranty_active,
        warranty_end_date=base.warranty_end_date,
        raw=base.raw,
        cached=False,
        error=base.error,
    )
