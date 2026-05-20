"""Software asset CRUD + Intune mobileApps sync.

Source-of-truth for manual entries is the local DB. Intune-sourced rows
are upserted by `intune_app_id` from Graph's `/deviceAppManagement/mobileApps`
endpoint. A sync re-runs the upsert; rows that disappear from Intune are
left intact (the admin can archive them manually rather than having them
quietly vanish).

Requires `DeviceManagementApps.Read.All` application permission on the
shared Graph app reg."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.inventory import Software
from app.services import intune_service
from app.services._graph_paging import paginate


logger = logging.getLogger("software")


@dataclass
class IntuneMobileApp:
    id: str
    display_name: str
    description: str | None
    publisher: str | None
    information_url: str | None
    privacy_url: str | None
    app_type: str | None  # @odata.type without prefix, e.g. "win32LobApp"


_MOBILEAPP_SELECT_FIELDS = (
    "id,displayName,description,publisher,informationUrl,privacyInformationUrl"
)


def _app_type_from_odata(otype: str | None) -> str | None:
    if not otype:
        return None
    # "#microsoft.graph.win32LobApp" → "win32LobApp"
    last = otype.rsplit(".", 1)[-1]
    return last or None


def _app_from_payload(item: dict[str, Any]) -> IntuneMobileApp:
    return IntuneMobileApp(
        id=str(item.get("id", "")),
        display_name=item.get("displayName") or "",
        description=item.get("description"),
        publisher=item.get("publisher"),
        information_url=item.get("informationUrl"),
        privacy_url=item.get("privacyInformationUrl"),
        app_type=_app_type_from_odata(item.get("@odata.type")),
    )


def list_intune_mobile_apps(
    *, page_size: int = 100, max_pages: int = 100
) -> list[IntuneMobileApp]:
    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = intune_service._ensure_token(timeout)

    base = settings.intune_graph_base_url.rstrip("/")
    url = (
        f"{base}/deviceAppManagement/mobileApps"
        f"?$select={_MOBILEAPP_SELECT_FIELDS}"
        f"&$top={page_size}"
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _parse(payload: dict) -> Iterable[IntuneMobileApp]:
        for item in payload.get("value", []) or []:
            yield _app_from_payload(item)

    return paginate(
        intune_service._http(),
        url,
        headers=headers,
        timeout=timeout,
        parse=_parse,
        max_pages=max_pages,
    )


@dataclass
class IntuneSyncResult:
    created: int
    updated: int
    total_fetched: int


def sync_from_intune(db: Session) -> IntuneSyncResult:
    """Upsert mobileApps into the software table. Idempotent. Rows already
    flagged as manual stay manual even if their name collides — only rows
    keyed by `intune_app_id` are touched."""

    apps = list_intune_mobile_apps()
    now = datetime.now(timezone.utc)

    existing_rows = db.execute(
        select(Software).where(Software.intune_app_id.is_not(None))
    ).scalars().all()
    existing_by_id: dict[str, Software] = {
        s.intune_app_id: s for s in existing_rows if s.intune_app_id
    }

    created = 0
    updated = 0

    for app in apps:
        row = existing_by_id.get(app.id)
        if row is None:
            row = Software(
                name=app.display_name,
                description=app.description,
                link=app.information_url,
                vendor=app.publisher,
                source="intune",
                intune_app_id=app.id,
                intune_app_type=app.app_type,
                intune_publisher=app.publisher,
                intune_synced_at=now,
            )
            db.add(row)
            created += 1
        else:
            # Only refresh Intune-managed fields. Leave manual edits to
            # name/description/link alone? No — for Intune-sourced rows
            # the user expects Intune to win. They can convert to manual
            # by clearing the intune_app_id if they want detached.
            row.name = app.display_name
            row.description = app.description
            row.link = app.information_url
            row.vendor = app.publisher
            row.intune_app_type = app.app_type
            row.intune_publisher = app.publisher
            row.intune_synced_at = now
            updated += 1

    db.commit()
    return IntuneSyncResult(
        created=created, updated=updated, total_fetched=len(apps)
    )
