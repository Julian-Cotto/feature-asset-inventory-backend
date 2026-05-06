from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import jwt
from fastapi import HTTPException, status

from app.config import get_settings

logger = logging.getLogger("auth")


@dataclass(slots=True)
class RequestAuthContext:
    is_authenticated: bool
    user_id: str | None = None
    user_name: str | None = None
    email: str | None = None
    roles: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    raw_token: str | None = None
    auth_mode: str = "none"
    claims: dict[str, Any] = field(default_factory=dict)


def _log_auth_event(event: str, context: RequestAuthContext, **extra: Any) -> None:
    logger.info(
        event,
        extra={
            "event": event,
            "user_id": context.user_id,
            "user_name": context.user_name,
            "email": context.email,
            "roles": context.roles,
            "groups": context.groups,
            "permissions": context.permissions,
            "auth_mode": context.auth_mode,
            **extra,
        },
    )


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _extract_bearer_token(authorization_header: str | None) -> str | None:
    if not authorization_header:
        return None

    prefix = "bearer "
    if not authorization_header.lower().startswith(prefix):
        return None

    token = authorization_header[len(prefix):].strip()
    return token or None


def _normalize_roles(claims: dict[str, Any]) -> list[str]:
    roles: list[str] = []

    raw_roles = claims.get("roles")
    if isinstance(raw_roles, list):
        roles.extend(str(item).strip() for item in raw_roles if str(item).strip())

    scp = claims.get("scp")
    if isinstance(scp, str):
        roles.extend(item.strip() for item in scp.split(" ") if item.strip())

    return sorted(set(roles))


def _normalize_groups(claims: dict[str, Any]) -> list[str]:
    raw_groups = claims.get("groups")
    if not isinstance(raw_groups, list):
        return []

    return sorted(
        set(str(item).strip() for item in raw_groups if str(item).strip())
    )


def _normalize_direct_permissions(claims: dict[str, Any]) -> list[str]:
    permissions: list[str] = []

    raw_permissions = claims.get("permissions")
    if isinstance(raw_permissions, list):
        permissions.extend(
            str(item).strip()
            for item in raw_permissions
            if str(item).strip()
        )

    raw_permissions_csv = claims.get("permissions_csv")
    if isinstance(raw_permissions_csv, str):
        permissions.extend(_split_csv(raw_permissions_csv))

    return permissions


def _extract_scopes(claims: dict[str, Any]) -> list[str]:
    scp = claims.get("scp")
    if not isinstance(scp, str):
        return []
    return [item.strip() for item in scp.split(" ") if item.strip()]


def _expand_permissions_from_values(
    values: list[str],
    permission_map: dict[str, list[str]],
) -> list[str]:
    permissions: list[str] = []

    for value in values:
        if value in permission_map:
            permissions.extend(permission_map[value])

        if value == "*" or "." in value:
            permissions.append(value)

    return permissions


def _normalize_permissions(
    claims: dict[str, Any],
    roles: list[str],
) -> list[str]:
    settings = get_settings()

    permissions: list[str] = []
    permissions.extend(_normalize_direct_permissions(claims))
    permissions.extend(
        _expand_permissions_from_values(
            roles,
            settings.auth_role_permission_map,
        )
    )
    permissions.extend(
        _expand_permissions_from_values(
            _extract_scopes(claims),
            settings.auth_scope_permission_map,
        )
    )

    return sorted(set(permission for permission in permissions if permission))


def _build_context_from_claims(
    *,
    claims: dict[str, Any],
    raw_token: str,
    auth_mode: str,
) -> RequestAuthContext:
    roles = _normalize_roles(claims)

    user_id = (
        claims.get("oid")
        or claims.get("sub")
        or claims.get("preferred_username")
        or claims.get("unique_name")
        or claims.get("appid")
    )
    user_name = (
        claims.get("name")
        or claims.get("preferred_username")
        or claims.get("upn")
        or claims.get("unique_name")
        or claims.get("appid")
    )
    email = (
        claims.get("preferred_username")
        or claims.get("email")
        or claims.get("upn")
        or claims.get("unique_name")
    )

    return RequestAuthContext(
        is_authenticated=True,
        user_id=str(user_id) if user_id else None,
        user_name=str(user_name) if user_name else None,
        email=str(email) if email else None,
        roles=roles,
        groups=_normalize_groups(claims),
        permissions=_normalize_permissions(claims, roles),
        raw_token=raw_token,
        auth_mode=auth_mode,
        claims=claims,
    )


def _extract_none_context() -> RequestAuthContext:
    ctx = RequestAuthContext(
        is_authenticated=True,
        user_id="anonymous",
        user_name="anonymous",
        auth_mode="none",
    )
    _log_auth_event("auth_context_created_none", ctx)
    return ctx


def _extract_mock_context(
    *,
    authorization_header: str | None,
    debug_user_id: str | None,
    debug_user_name: str | None,
    debug_email: str | None,
    debug_roles: str | None,
) -> RequestAuthContext:
    settings = get_settings()
    token = _extract_bearer_token(authorization_header)

    if settings.auth_debug_headers_enabled:
        user_id = debug_user_id or settings.auth_default_dev_user_id
        user_name = debug_user_name or settings.auth_default_dev_user_name
        email = debug_email or settings.auth_default_dev_email
        roles = _split_csv(debug_roles) or settings.auth_default_dev_roles
    else:
        user_id = settings.auth_default_dev_user_id
        user_name = settings.auth_default_dev_user_name
        email = settings.auth_default_dev_email
        roles = settings.auth_default_dev_roles

    claims = {
        "dev_mode": True,
        "roles": roles,
    }

    ctx = RequestAuthContext(
        is_authenticated=True,
        user_id=user_id,
        user_name=user_name,
        email=email,
        roles=roles,
        groups=[],
        permissions=_normalize_permissions(claims, roles),
        raw_token=token,
        auth_mode="mock",
        claims=claims,
    )
    _log_auth_event("auth_context_created_mock", ctx)
    return ctx


def _get_signing_key_for_token(raw_token: str):
    settings = get_settings()
    jwks_url = settings.effective_entra_jwks_url

    if not jwks_url:
        logger.error("auth_entra_jwks_missing")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Entra JWKS configuration is missing.",
        )

    jwk_client = jwt.PyJWKClient(jwks_url)
    return jwk_client.get_signing_key_from_jwt(raw_token).key


def _validate_issuer(claims: dict[str, Any]) -> None:
    settings = get_settings()
    allowed_issuers = settings.effective_entra_issuers

    if not allowed_issuers:
        logger.error("auth_entra_issuer_missing")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Entra issuer configuration is missing.",
        )

    issuer = claims.get("iss")
    if issuer not in allowed_issuers:
        logger.warning(
            "auth_entra_invalid_issuer",
            extra={
                "issuer": issuer,
                "allowed_issuers": allowed_issuers,
            },
        )
        raise jwt.InvalidIssuerError("Access token issuer is invalid.")


def _validate_entra_token(raw_token: str) -> dict[str, Any]:
    settings = get_settings()

    audiences = settings.effective_entra_audiences
    if not audiences:
        logger.error("auth_entra_audience_missing")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Entra audience configuration is missing.",
        )

    try:
        signing_key = _get_signing_key_for_token(raw_token)

        claims = jwt.decode(
            raw_token,
            signing_key,
            algorithms=["RS256"],
            audience=audiences,
            leeway=settings.entra_clock_skew_seconds,
            options={
                "require": ["exp", "iat", "iss", "aud"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_aud": True,
                "verify_iss": False,
            },
        )

        _validate_issuer(claims)
        return claims

    except jwt.ExpiredSignatureError as exc:
        logger.warning("auth_entra_token_expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token has expired.",
        ) from exc
    except jwt.InvalidAudienceError as exc:
        logger.warning("auth_entra_invalid_audience")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token audience is invalid.",
        ) from exc
    except jwt.InvalidIssuerError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token issuer is invalid.",
        ) from exc
    except jwt.InvalidTokenError as exc:
        logger.warning("auth_entra_invalid_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token is invalid.",
        ) from exc


def _extract_entra_context(authorization_header: str | None) -> RequestAuthContext:
    raw_token = _extract_bearer_token(authorization_header)

    if not raw_token:
        logger.warning("auth_entra_missing_bearer_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token is required.",
        )

    claims = _validate_entra_token(raw_token)

    ctx = _build_context_from_claims(
        claims=claims,
        raw_token=raw_token,
        auth_mode="entra",
    )
    _log_auth_event("auth_context_created_entra", ctx)
    return ctx


def extract_auth_context(
    *,
    authorization_header: str | None,
    debug_user_id: str | None = None,
    debug_user_name: str | None = None,
    debug_email: str | None = None,
    debug_roles: str | None = None,
) -> RequestAuthContext:
    mode = get_settings().auth_mode.strip().lower()

    if mode == "none":
        return _extract_none_context()

    if mode == "mock":
        return _extract_mock_context(
            authorization_header=authorization_header,
            debug_user_id=debug_user_id,
            debug_user_name=debug_user_name,
            debug_email=debug_email,
            debug_roles=debug_roles,
        )

    if mode == "entra":
        return _extract_entra_context(authorization_header)

    logger.error("auth_unsupported_mode", extra={"auth_mode": mode})
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Unsupported auth mode: {mode}",
    )


def require_authenticated_user(
    auth_context: RequestAuthContext,
) -> RequestAuthContext:
    if not auth_context.is_authenticated:
        logger.warning("auth_denied_unauthenticated")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required.",
        )

    _log_auth_event("auth_authenticated", auth_context)
    return auth_context


def require_any_role(
    auth_context: RequestAuthContext,
    required_roles: set[str],
) -> RequestAuthContext:
    if not required_roles:
        _log_auth_event("auth_role_allowed_no_requirement", auth_context)
        return auth_context

    user_roles = set(auth_context.roles)
    if user_roles.intersection(required_roles):
        _log_auth_event(
            "auth_role_allowed",
            auth_context,
            required_roles=sorted(required_roles),
            matched_roles=sorted(user_roles.intersection(required_roles)),
        )
        return auth_context

    _log_auth_event(
        "auth_role_denied",
        auth_context,
        required_roles=sorted(required_roles),
        user_roles=sorted(user_roles),
    )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to access this resource.",
    )


def require_any_permission(
    auth_context: RequestAuthContext,
    required_permissions: set[str],
) -> RequestAuthContext:
    if not required_permissions:
        _log_auth_event("auth_permission_allowed_no_requirement", auth_context)
        return auth_context

    user_permissions = set(auth_context.permissions)
    if "*" in user_permissions:
        _log_auth_event(
            "auth_permission_allowed_wildcard",
            auth_context,
            required_permissions=sorted(required_permissions),
        )
        return auth_context

    matched_permissions = user_permissions.intersection(required_permissions)
    if matched_permissions:
        _log_auth_event(
            "auth_permission_allowed",
            auth_context,
            required_permissions=sorted(required_permissions),
            matched_permissions=sorted(matched_permissions),
        )
        return auth_context

    _log_auth_event(
        "auth_permission_denied",
        auth_context,
        required_permissions=sorted(required_permissions),
        user_permissions=sorted(user_permissions),
    )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to access this resource.",
    )