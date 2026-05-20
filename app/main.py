import contextvars
import json
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.deployments import router as deployments_router
from app.api.feature import router as feature_router
from app.api.groups import router as groups_router
from app.api.health import router as health_router
from app.api.intune import router as intune_router
from app.api.meraki import router as meraki_router
from app.api.inventory import router as inventory_router
from app.api.lookup import router as lookup_router
from app.api.networks import router as networks_router
from app.api.shipments import router as shipments_router
from app.api.software import router as software_router
from app.api.users import router as users_router
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import inventory as _inventory_models  # noqa: F401  register ORM tables
from app.services.inventory_service import (
    seed_default_locations,
    seed_default_statuses,
)

# NOTE: scaffold-generated app/api/platform_capabilities.py and
# app/api/cache_capabilities.py contain unrendered Jinja placeholders
# (scaffold template bug). Not wired here. Fix or delete those files
# before re-introducing them.

request_id_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id",
    default=None,
)


def _migrate_legacy_statuses(db) -> None:
    """One-shot data migration. Status now tracks operational state only
    (active / in_repair / lost / retired). Assignment is derived from
    `assigned_upn` (Intune-driven). Idempotent — runs every boot, no-op
    once data is migrated."""

    from sqlalchemy import text
    from app.models import Asset, AssetStatus, Location

    legacy_codes = ("in_warehouse", "assigned")
    legacy_count = (
        db.query(Asset).filter(Asset.status_code.in_(legacy_codes)).count()
    )
    if legacy_count == 0:
        # Still flip is_active=False on legacy rows in case seed re-added them
        db.execute(
            text(
                "UPDATE asset_statuses SET is_active = 0 "
                "WHERE code IN ('in_warehouse','assigned')"
            )
        )
        db.commit()
        return

    # Tie former 'in_warehouse' assets to BRENTWOOD-WH if no location set
    brentwood = (
        db.query(Location).filter(Location.code == "BRENTWOOD-WH").first()
    )
    if brentwood is not None:
        db.execute(
            text(
                "UPDATE assets SET location_id = :loc "
                "WHERE status_code = 'in_warehouse' AND location_id IS NULL"
            ),
            {"loc": brentwood.id},
        )

    # Flip any legacy status_code → 'active'
    db.execute(
        text(
            "UPDATE assets SET status_code = 'active' "
            "WHERE status_code IN ('in_warehouse','assigned')"
        )
    )
    # Hide legacy status rows from dropdowns; keep them so AssetHistory
    # foreign-key-style references stay valid.
    db.execute(
        text(
            "UPDATE asset_statuses SET is_active = 0 "
            "WHERE code IN ('in_warehouse','assigned')"
        )
    )
    db.commit()


def _migrate_stock_to_brentwood(db) -> None:
    """One-shot, idempotent. Normalize empty `assigned_upn` to NULL, then
    park anything matching the "stock" UPN rule (NULL or 'join@hv.ltd')
    at Brentwood-WH if it has no location yet."""

    from sqlalchemy import text
    from app.models import Location

    # Empty string → NULL
    db.execute(
        text("UPDATE assets SET assigned_upn = NULL WHERE assigned_upn = ''")
    )
    brentwood = (
        db.query(Location).filter(Location.code == "BRENTWOOD-WH").first()
    )
    if brentwood is None:
        db.commit()
        return
    db.execute(
        text(
            "UPDATE assets SET location_id = :loc "
            "WHERE archived_at IS NULL "
            "AND location_id IS NULL "
            "AND (assigned_upn IS NULL OR assigned_upn = 'join@hv.ltd')"
        ),
        {"loc": brentwood.id},
    )
    db.commit()


def _apply_lightweight_migrations() -> None:
    """SQLite create_all is a no-op for existing tables. When we add new
    columns to ORM models we have to ALTER manually. Idempotent: each ADD
    COLUMN is wrapped to ignore "duplicate column" errors."""

    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    additions: dict[str, list[tuple[str, str]]] = {
        "assets": [
            ("warranty_active", "BOOLEAN"),
            ("warranty_end_date", "DATETIME"),
            ("warranty_synced_at", "DATETIME"),
            ("aad_device_id", "VARCHAR(64)"),
            ("defender_id", "VARCHAR(64)"),
            ("defender_synced_at", "DATETIME"),
            ("defender_health_status", "VARCHAR(64)"),
            ("defender_risk_score", "VARCHAR(32)"),
            ("defender_exposure_level", "VARCHAR(32)"),
            ("defender_last_seen_at", "DATETIME"),
            ("defender_onboarding_status", "VARCHAR(32)"),
            ("defender_av_status", "VARCHAR(32)"),
            ("defender_os_build", "VARCHAR(64)"),
            ("defender_last_ip", "VARCHAR(64)"),
            ("defender_tags", "TEXT"),
            ("network_id", "INTEGER"),
            ("mac_address", "VARCHAR(32)"),
        ],
        "device_lookups": [
            ("warranty_active", "BOOLEAN"),
            ("warranty_end_date", "DATETIME"),
        ],
        "locations": [
            ("address_line1", "VARCHAR(255)"),
            ("address_line2", "VARCHAR(255)"),
            ("city", "VARCHAR(128)"),
            ("state", "VARCHAR(64)"),
            ("postal_code", "VARCHAR(32)"),
            ("country", "VARCHAR(64)"),
        ],
        "shipments": [
            ("archived_at", "DATETIME"),
            ("archived_by_upn", "VARCHAR(320)"),
        ],
        "deployments": [
            ("archived_at", "DATETIME"),
            ("archived_by_upn", "VARCHAR(320)"),
        ],
        "networks": [
            ("corp_vlan_subnet", "VARCHAR(64)"),
            ("corp_vlan_gateway_ip", "VARCHAR(64)"),
        ],
        "intune_users": [
            ("sign_in_status", "VARCHAR(32) NOT NULL DEFAULT 'ok'"),
            ("company_name", "VARCHAR(255)"),
            ("employee_id", "VARCHAR(64)"),
            ("employee_type", "VARCHAR(64)"),
            ("employee_hire_date", "DATETIME"),
            ("employee_org_division", "VARCHAR(255)"),
            ("employee_org_cost_center", "VARCHAR(128)"),
            ("street_address", "VARCHAR(255)"),
            ("city", "VARCHAR(128)"),
            ("state", "VARCHAR(128)"),
            ("postal_code", "VARCHAR(32)"),
            ("country", "VARCHAR(128)"),
            ("mobile_phone", "VARCHAR(64)"),
            ("business_phones_json", "TEXT"),
            ("fax_number", "VARCHAR(64)"),
            ("mail_nickname", "VARCHAR(255)"),
            ("other_mails_json", "TEXT"),
            ("proxy_addresses_json", "TEXT"),
            ("im_addresses_json", "TEXT"),
        ],
    }
    with engine.begin() as conn:
        for table, cols in additions.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, decl in cols:
                if name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {decl}"))

logger = logging.getLogger("request")


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "IT Asset Inventory Service",
            "feature_key": "asset-inventory",
        }

        request_id = getattr(record, "request_id", None) or request_id_context.get()
        if request_id:
            payload["request_id"] = request_id

        for key in (
            "event",
            "method",
            "path",
            "query",
            "status_code",
            "elapsed_ms",
            "user_id",
            "user_name",
            "email",
            "roles",
            "groups",
            "permissions",
            "auth_mode",
            "required_roles",
            "matched_roles",
            "user_roles",
            "required_permissions",
            "matched_permissions",
            "user_permissions",
            "issuer",
            "allowed_issuers",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers = [handler]


def create_app() -> FastAPI:
    configure_logging()

    settings = get_settings()
    app = FastAPI(title="IT Asset Inventory Service", version="0.1.0")

    if settings.db_create_all_on_startup:
        Base.metadata.create_all(bind=engine)
        _apply_lightweight_migrations()
        if settings.db_seed_default_statuses:
            with SessionLocal() as db:
                seed_default_statuses(db)
                seed_default_locations(db)
                _migrate_legacy_statuses(db)
                _migrate_stock_to_brentwood(db)

    @app.middleware("http")
    async def add_request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_context.set(request_id)
        start_time = time.perf_counter()

        logger.info(
            "request_start",
            extra={
                "event": "request_start",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "query": request.url.query,
            },
        )

        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.exception(
                "request_error",
                extra={
                    "event": "request_error",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "elapsed_ms": elapsed_ms,
                },
            )
            raise
        finally:
            request_id_context.reset(token)

        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

        logger.info(
            "request_end",
            extra={
                "event": "request_end",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "elapsed_ms": elapsed_ms,
            },
        )

        response.headers["X-Request-ID"] = request_id
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router, prefix=settings.api_base_path)
    app.include_router(feature_router, prefix=settings.api_base_path)
    app.include_router(inventory_router, prefix=settings.api_base_path)
    app.include_router(lookup_router, prefix=settings.api_base_path)
    app.include_router(shipments_router, prefix=settings.api_base_path)
    app.include_router(intune_router, prefix=settings.api_base_path)
    app.include_router(meraki_router, prefix=settings.api_base_path)
    app.include_router(deployments_router, prefix=settings.api_base_path)
    app.include_router(users_router, prefix=settings.api_base_path)
    app.include_router(groups_router, prefix=settings.api_base_path)
    app.include_router(software_router, prefix=settings.api_base_path)
    app.include_router(networks_router, prefix=settings.api_base_path)

    # Background Defender cache warmer. Refreshes every CACHE_TTL_SECONDS
    # (30 min). On startup, kicks off an initial refresh after a short
    # delay so the first request never blocks on the bulk fetch.
    @app.on_event("startup")
    def _start_defender_cache_warmer() -> None:
        import threading
        from app.services import defender_service

        log = logging.getLogger("defender")
        stop_event = threading.Event()
        app.state.defender_warmer_stop = stop_event

        def _loop() -> None:
            # Brief initial delay so app finishes booting first.
            if stop_event.wait(timeout=10):
                return
            while not stop_event.is_set():
                try:
                    idx = defender_service.refresh_cache()
                    log.info(
                        "defender_warmer_refreshed",
                        extra={
                            "event": "defender_warmer_refreshed",
                            "machines": len(idx),
                        },
                    )
                except Exception as e:
                    log.warning(
                        "defender_warmer_failed",
                        extra={"event": "defender_warmer_failed", "error": str(e)},
                    )
                # Wait the TTL; `wait` returns early if stop_event is set.
                if stop_event.wait(timeout=defender_service.CACHE_TTL_SECONDS):
                    return

        t = threading.Thread(
            target=_loop, name="defender-cache-warmer", daemon=True
        )
        t.start()
        app.state.defender_warmer_thread = t

    @app.on_event("shutdown")
    def _stop_defender_cache_warmer() -> None:
        stop = getattr(app.state, "defender_warmer_stop", None)
        if stop is not None:
            stop.set()

    return app


app = create_app()