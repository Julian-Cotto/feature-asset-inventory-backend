"""Microsoft Graph groups lookup.

Bulk-syncs Entra ID group metadata (id, displayName, type, etc.) and
fetches group membership on demand. Membership is NOT persisted —
each detail-view open hits Graph live so we always show current
members without paying the storage + reconciliation cost.

Auth: reuses the Graph token from `intune_service` (same Azure AD app
registration, same client-credentials flow). Requires the following
application permissions on the app reg:

  - Group.Read.All
  - GroupMember.Read.All
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from app.config import get_settings
from app.services import intune_service
from app.services._graph_paging import paginate


logger = logging.getLogger("groups")


@dataclass
class EntraGroupRow:
    id: str
    display_name: str
    description: str | None
    mail_nickname: str | None
    mail: str | None
    security_enabled: bool
    mail_enabled: bool
    group_types: list[str]


@dataclass
class EntraGroupMember:
    """A single member entry. Graph returns @odata.type telling us if the
    member is a user, group, or device — we surface the type so the UI
    can render an icon and link appropriately."""
    id: str
    member_type: str  # "user" | "group" | "device" | "other"
    display_name: str | None
    user_principal_name: str | None
    mail: str | None


_GROUP_SELECT_FIELDS = (
    "id,displayName,description,mailNickname,mail,"
    "securityEnabled,mailEnabled,groupTypes"
)


def _row_from_payload(item: dict[str, Any]) -> EntraGroupRow:
    return EntraGroupRow(
        id=str(item.get("id", "")),
        display_name=item.get("displayName") or "",
        description=item.get("description"),
        mail_nickname=item.get("mailNickname"),
        mail=item.get("mail"),
        security_enabled=bool(item.get("securityEnabled", False)),
        mail_enabled=bool(item.get("mailEnabled", False)),
        group_types=list(item.get("groupTypes") or []),
    )


def list_all_groups(*, page_size: int = 100, max_pages: int = 500) -> list[EntraGroupRow]:
    """Page through every group in the tenant. No filter — we sync all and
    let the admin curate via `is_managed` rather than guess what's relevant."""

    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = intune_service._ensure_token(timeout)

    base = settings.intune_graph_base_url.rstrip("/")
    url = f"{base}/groups?$select={_GROUP_SELECT_FIELDS}&$top={page_size}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _parse(payload: dict) -> Iterable[EntraGroupRow]:
        for item in payload.get("value", []) or []:
            yield _row_from_payload(item)

    return paginate(
        intune_service._http(),
        url,
        headers=headers,
        timeout=timeout,
        parse=_parse,
        max_pages=max_pages,
    )


def get_group(group_id: str) -> EntraGroupRow | None:
    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = intune_service._ensure_token(timeout)

    base = settings.intune_graph_base_url.rstrip("/")
    url = f"{base}/groups/{group_id}?$select={_GROUP_SELECT_FIELDS}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    resp = intune_service._http().get(url, headers=headers, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return _row_from_payload(resp.json())


def _member_type_from_odata(otype: str | None) -> str:
    if not otype:
        return "other"
    t = otype.lower()
    if t.endswith(".user"):
        return "user"
    if t.endswith(".group"):
        return "group"
    if t.endswith(".device"):
        return "device"
    return "other"


def list_group_members(
    group_id: str,
    *,
    page_size: int = 100,
    max_pages: int = 100,
) -> list[EntraGroupMember]:
    """Lazy-fetch members for one group. Called on detail-view open."""

    settings = get_settings()
    timeout = settings.intune_http_timeout_seconds
    token = intune_service._ensure_token(timeout)

    base = settings.intune_graph_base_url.rstrip("/")
    url = (
        f"{base}/groups/{group_id}/members"
        f"?$select=id,displayName,userPrincipalName,mail"
        f"&$top={page_size}"
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _parse(payload: dict) -> Iterable[EntraGroupMember]:
        for item in payload.get("value", []) or []:
            yield EntraGroupMember(
                id=str(item.get("id", "")),
                member_type=_member_type_from_odata(item.get("@odata.type")),
                display_name=item.get("displayName"),
                user_principal_name=item.get("userPrincipalName"),
                mail=item.get("mail"),
            )

    try:
        return paginate(
            intune_service._http(),
            url,
            headers=headers,
            timeout=timeout,
            parse=_parse,
            max_pages=max_pages,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (403, 404):
            logger.warning(
                "graph_group_members_failed",
                extra={
                    "event": "graph_group_members_failed",
                    "group_id": group_id,
                    "status_code": e.response.status_code,
                },
            )
            return []
        raise
