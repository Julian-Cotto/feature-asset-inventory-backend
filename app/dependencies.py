import logging
from collections.abc import Callable

from fastapi import Depends, Header

from app.platform.auth_context import (
    RequestAuthContext,
    extract_auth_context,
    require_any_permission,
    require_any_role,
    require_authenticated_user,
)

logger = logging.getLogger("auth")


def get_auth_context(
    authorization: str | None = Header(default=None),
    x_debug_user_id: str | None = Header(default=None),
    x_debug_user_name: str | None = Header(default=None),
    x_debug_email: str | None = Header(default=None),
    x_debug_roles: str | None = Header(default=None),
) -> RequestAuthContext:
    ctx = extract_auth_context(
        authorization_header=authorization,
        debug_user_id=x_debug_user_id,
        debug_user_name=x_debug_user_name,
        debug_email=x_debug_email,
        debug_roles=x_debug_roles,
    )

    logger.info(
        "auth_context_available",
        extra={
            "event": "auth_context_available",
            "user_id": ctx.user_id,
            "user_name": ctx.user_name,
            "email": ctx.email,
            "roles": ctx.roles,
            "groups": ctx.groups,
            "permissions": ctx.permissions,
            "auth_mode": ctx.auth_mode,
        },
    )

    return ctx


def require_auth(
    auth_context: RequestAuthContext = Depends(get_auth_context),
) -> RequestAuthContext:
    return require_authenticated_user(auth_context)


def require_roles(*roles: str) -> Callable[..., RequestAuthContext]:
    required_roles = {role.strip() for role in roles if role.strip()}

    def _dependency(
        auth_context: RequestAuthContext = Depends(get_auth_context),
    ) -> RequestAuthContext:
        auth_context = require_authenticated_user(auth_context)
        return require_any_role(auth_context, required_roles)

    return _dependency


def require_permissions(*permissions: str) -> Callable[..., RequestAuthContext]:
    required_permissions = {
        permission.strip()
        for permission in permissions
        if permission.strip()
    }

    def _dependency(
        auth_context: RequestAuthContext = Depends(get_auth_context),
    ) -> RequestAuthContext:
        auth_context = require_authenticated_user(auth_context)
        return require_any_permission(auth_context, required_permissions)

    return _dependency


def require_admin() -> Callable[..., RequestAuthContext]:
    return require_roles("admin")


def require_developer() -> Callable[..., RequestAuthContext]:
    return require_roles("admin", "developer")


def require_reader() -> Callable[..., RequestAuthContext]:
    return require_roles("admin", "developer", "reader")


def require_operator() -> Callable[..., RequestAuthContext]:
    return require_roles("admin", "operator")