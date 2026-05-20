from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


ASSET_TYPES = (
    "laptop",
    "desktop",
    "thin_client",
    "ap",
    "switch",
    "gateway",
)
LOCATION_TYPES = ("warehouse", "site")
ASSET_HISTORY_EVENTS = (
    "onboard",
    "assign",
    "unassign",
    "status_change",
    "location_change",
    "archive",
    "note",
    "update",
)

DEFAULT_STATUSES = [
    {"code": "active",    "label": "Active",    "is_terminal": False, "sort_order": 10},
    {"code": "in_repair", "label": "In Repair", "is_terminal": False, "sort_order": 30},
    {"code": "lost",      "label": "Lost",      "is_terminal": True,  "sort_order": 90},
    {"code": "retired",   "label": "Retired",   "is_terminal": True,  "sort_order": 99},
]
# Codes retired by the assignment-from-Intune refactor. Migration in main
# rewrites assets pointing at these to status="active". Rows kept (FKs
# from asset_history) but flipped is_active=False so they're hidden.
DEPRECATED_STATUS_CODES = {"in_warehouse", "assigned"}

# Manually-seeded locations that don't come from Snowflake. Snowflake sync
# leaves these alone (no update, no deactivate). Add new entries here when
# we need a non-corporate site (e.g. internal warehouse).
DEFAULT_LOCATIONS = [
    {
        "code": "BRENTWOOD-WH",
        "name": "Brentwood - Warehouse",
        "type": "warehouse",
        "address": None,
    },
]
PROTECTED_LOCATION_CODES = {entry["code"] for entry in DEFAULT_LOCATIONS}


class AssetStatus(Base):
    __tablename__ = "asset_statuses"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    is_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("code", name="uq_locations_code"),
        CheckConstraint(f"type IN {LOCATION_TYPES!r}", name="chk_locations_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    address: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    created_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    updated_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("asset_tag", name="uq_assets_asset_tag"),
        UniqueConstraint("serial_number", name="uq_assets_serial_number"),
        CheckConstraint(f"asset_type IN {ASSET_TYPES!r}", name="chk_assets_asset_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    serial_number: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    override_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    series: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generation: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cpu: Mapped[str | None] = mapped_column(String(128), nullable=True)
    os: Mapped[str | None] = mapped_column(String(64), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("asset_statuses.code"), nullable=False
    )
    location_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("locations.id"), nullable=True
    )
    assigned_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    onboarded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    intune_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    intune_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    intune_device_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    intune_managed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intune_ownership: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intune_compliance: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intune_last_check_in: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Azure AD device id — bridges Intune (`managedDevice.azureADDeviceId`)
    # and Defender (`machine.aadDeviceId`). Populated from Intune sync.
    aad_device_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Microsoft Defender for Endpoint — populated when the matching machine
    # is onboarded to Defender. All fields null on non-Defender devices.
    defender_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    defender_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    defender_health_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    defender_risk_score: Mapped[str | None] = mapped_column(String(32), nullable=True)
    defender_exposure_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    defender_last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    defender_onboarding_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    defender_av_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    defender_os_build: Mapped[str | None] = mapped_column(String(64), nullable=True)
    defender_last_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    defender_tags: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    # Canonical MAC (colon-separated lowercase). Sourced from Intune's
    # `wiFiMacAddress` first, falling back to `ethernetMacAddress`. Used to
    # match Meraki client appearances (`meraki_clients.mac`) so we can show
    # "which networks has this asset shown up on".
    mac_address: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # Network membership — set on networking gear via Meraki serial match
    # during sync, and on client devices (laptop/desktop) via Defender IP
    # subnet match. Nullable so unmanaged assets remain valid.
    network_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("networks.id"), nullable=True, index=True
    )
    warranty_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    warranty_end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warranty_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    created_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    updated_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)

    status: Mapped[AssetStatus] = relationship(AssetStatus, lazy="joined")
    location: Mapped[Location | None] = relationship(Location, lazy="joined")
    network: Mapped["Network | None"] = relationship(
        "Network", lazy="joined", foreign_keys=[network_id]
    )

    @property
    def location_name(self) -> str | None:
        return self.location.name if self.location is not None else None

    @property
    def location_code(self) -> str | None:
        return self.location.code if self.location is not None else None

    @property
    def network_name(self) -> str | None:
        return self.network.display_name if self.network is not None else None
    history: Mapped[list["AssetHistory"]] = relationship(
        "AssetHistory",
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="AssetHistory.performed_at.desc()",
    )


class AssetHistory(Base):
    __tablename__ = "asset_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(Integer, ForeignKey("assets.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    from_value: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    to_value: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    performed_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    performed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    asset: Mapped[Asset] = relationship(Asset, back_populates="history")


DEPLOYMENT_STATUSES = ("planning", "in_progress", "completed", "cancelled")
# Type is free-form text (suggested values: acquisition, new_build, expansion,
# relocation, ...); no enum constraint so teams can use whatever fits.

SHIPMENT_DIRECTIONS = ("outbound", "inbound")
SHIPMENT_CARRIERS = ("ups", "fedex", "other")
SHIPMENT_CARRIER_STATUSES = (
    "pending",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "exception",
    "unknown",
)
SHIPMENT_RESOLUTIONS = ("open", "resolved", "cancelled")


class Shipment(Base):
    """A tracked package containing one or more assets, in or outbound."""

    __tablename__ = "shipments"
    __table_args__ = (
        CheckConstraint(
            f"direction IN {SHIPMENT_DIRECTIONS!r}", name="chk_shipments_direction"
        ),
        CheckConstraint(
            f"carrier IN {SHIPMENT_CARRIERS!r}", name="chk_shipments_carrier"
        ),
        CheckConstraint(
            f"carrier_status IN {SHIPMENT_CARRIER_STATUSES!r}",
            name="chk_shipments_carrier_status",
        ),
        CheckConstraint(
            f"resolution IN {SHIPMENT_RESOLUTIONS!r}", name="chk_shipments_resolution"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tracking_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    carrier: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    deployment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("deployments.id"), nullable=True, index=True
    )

    carrier_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    resolution: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", server_default="open"
    )

    # "From" — pick saved location OR free-form address fields
    from_location_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("locations.id"), nullable=True
    )
    from_address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    from_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    from_postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    from_country: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # "To"
    to_location_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("locations.id"), nullable=True
    )
    to_address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_country: Mapped[str | None] = mapped_column(String(64), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_poll_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    updated_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)

    items: Mapped[list["ShipmentItem"]] = relationship(
        "ShipmentItem",
        back_populates="shipment",
        cascade="all, delete-orphan",
        order_by="ShipmentItem.id",
    )
    events: Mapped[list["ShipmentEvent"]] = relationship(
        "ShipmentEvent",
        back_populates="shipment",
        cascade="all, delete-orphan",
        order_by="ShipmentEvent.occurred_at.desc()",
    )
    from_location: Mapped[Location | None] = relationship(
        Location, foreign_keys=[from_location_id], lazy="joined"
    )
    to_location: Mapped[Location | None] = relationship(
        Location, foreign_keys=[to_location_id], lazy="joined"
    )


class ShipmentItem(Base):
    """One asset on one shipment. Asset-only for v1."""

    __tablename__ = "shipment_items"
    __table_args__ = (
        UniqueConstraint(
            "shipment_id", "asset_id", name="uq_shipment_items_shipment_asset"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shipments.id"), nullable=False
    )
    asset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assets.id"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    shipment: Mapped[Shipment] = relationship(Shipment, back_populates="items")
    asset: Mapped[Asset] = relationship(Asset, lazy="joined")


class ShipmentEvent(Base):
    """Append-only carrier event timeline. Populated by refresh."""

    __tablename__ = "shipment_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shipments.id"), nullable=False, index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    shipment: Mapped[Shipment] = relationship(Shipment, back_populates="events")


class Deployment(Base):
    """A planned rollout to a location — acquisition / new build / expansion /
    relocation / etc. Owns assigned assets (reserved while active) and
    optionally a list of shipments."""

    __tablename__ = "deployments"
    __table_args__ = (
        CheckConstraint(
            f"status IN {DEPLOYMENT_STATUSES!r}", name="chk_deployments_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="planning", server_default="planning"
    )

    target_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Target — pick existing location OR enter free-form planned address
    target_location_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("locations.id"), nullable=True
    )
    target_address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_country: Mapped[str | None] = mapped_column(String(64), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    cancelled_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    archived_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    updated_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)

    items: Mapped[list["DeploymentItem"]] = relationship(
        "DeploymentItem",
        back_populates="deployment",
        cascade="all, delete-orphan",
        order_by="DeploymentItem.id",
    )
    shipments: Mapped[list["Shipment"]] = relationship(
        "Shipment",
        primaryjoin="Deployment.id == Shipment.deployment_id",
        viewonly=True,
        order_by="Shipment.created_at.desc()",
    )
    target_location: Mapped[Location | None] = relationship(
        Location, foreign_keys=[target_location_id], lazy="joined"
    )


class DeploymentItem(Base):
    """One asset assigned to a deployment. Asset-only; reserved while
    deployment is active (planning / in_progress)."""

    __tablename__ = "deployment_items"
    __table_args__ = (
        UniqueConstraint(
            "deployment_id", "asset_id", name="uq_deployment_items_deployment_asset"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deployment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("deployments.id"), nullable=False
    )
    asset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assets.id"), nullable=False
    )
    role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    deployment: Mapped[Deployment] = relationship(Deployment, back_populates="items")
    asset: Mapped[Asset] = relationship(Asset, lazy="joined")


class DeviceLookup(Base):
    """Cached vendor-API lookups by scanned code (serial / UPC).

    One row per unique code. Refreshed when stale; provider failures store an
    empty payload so we don't hammer external APIs on repeated misses.
    """

    __tablename__ = "device_lookups"
    __table_args__ = (
        UniqueConstraint("code", name="uq_device_lookups_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    series: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generation: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cpu: Mapped[str | None] = mapped_column(String(128), nullable=True)
    os: Mapped[str | None] = mapped_column(String(64), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intune_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assigned_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    warranty_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    warranty_end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class IntuneUser(Base):
    """Cached Microsoft Graph user row. One row per active member.

    Source of truth is Microsoft Graph; this table is a denormalized cache
    refreshed by the `/users/sync` (bulk) and `/users/{id}/sync` (single)
    endpoints. Device assignments are NOT stored here — they live in Intune
    as `managedDevice.userPrincipalName` and are queried live."""

    __tablename__ = "intune_users"

    # Use Microsoft Graph object id as PK (immutable UUID).
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    user_principal_name: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mail: Mapped[str | None] = mapped_column(String(320), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    office_location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    user_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "Member" / "Guest"
    last_sign_in_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Why `last_sign_in_at` may be null. One of: "ok" (date or genuine
    # never-signed-in), "permission_missing" (Graph 403d on signInActivity
    # — app needs AuditLog.Read.All), "license_unavailable" (200 but field
    # null tenant-wide — likely missing Entra ID P1/P2). UI branches on this.
    sign_in_status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    manager_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manager_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Identity / org
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employee_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    employee_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    employee_hire_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # `employeeOrgData` is a composite Graph object — flatten to two columns.
    employee_org_division: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employee_org_cost_center: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Contact — addresses
    street_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str | None] = mapped_column(String(128), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Contact — phones (mobile is scalar; business is array → JSON string)
    mobile_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_phones_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    fax_number: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Contact — mail / IM
    mail_nickname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    other_mails_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy_addresses_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    im_addresses_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


SOFTWARE_SOURCES = ("manual", "intune")
ASSIGNMENT_PRINCIPAL_TYPES = ("group", "user")


class Software(Base):
    """A piece of software the org tracks (licensed app, internal tool, SaaS
    subscription, etc.). Rows are either manually entered or pulled from
    Intune mobileApps. Identity for Intune-sourced rows is the Graph
    `mobileApp.id` stored on `intune_app_id`."""

    __tablename__ = "software"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Stored as cents to avoid float drift. Annual or per-seat — UI decides.
    license_cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seat_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    internal_owner_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)

    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    intune_app_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    intune_app_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intune_publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    intune_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            f"source IN {SOFTWARE_SOURCES}",
            name="software_source_check",
        ),
    )


class EntraGroup(Base):
    """Cached Entra ID group row. Source of truth = Microsoft Graph. Bulk
    sync upserts metadata only; membership is fetched lazily on demand and
    NOT persisted. `is_managed` lets admins curate which groups appear in
    the default list (since tenants often have many auto-created M365
    groups that are noise here)."""

    __tablename__ = "entra_groups"

    # Graph objectId
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    mail_nickname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mail: Mapped[str | None] = mapped_column(String(320), nullable=True)

    security_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mail_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # CSV of group types (e.g. "Unified" for M365). Empty for plain security.
    group_types: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_managed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Cached on last members fetch; null until first lazy load. Used for
    # display only — never authoritative.
    member_count_cached: Mapped[int | None] = mapped_column(Integer, nullable=True)
    members_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    last_synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class SoftwareAssignment(Base):
    """Many-to-many between software and a principal (Entra group OR Intune
    user). Principal is stored as `(principal_type, principal_id)` rather
    than a real FK so a group/user can be deleted upstream without
    cascading damage — the row turns into a tombstone and the UI shows
    "Unknown principal".

    `principal_display` caches the human-readable label at the time of
    assignment so the row still renders something useful if the upstream
    object disappears."""

    __tablename__ = "software_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    software_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("software.id", ondelete="CASCADE"), nullable=False
    )
    principal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    principal_display: Mapped[str | None] = mapped_column(String(320), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    created_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "software_id", "principal_type", "principal_id",
            name="software_assignments_unique_principal",
        ),
        CheckConstraint(
            f"principal_type IN {ASSIGNMENT_PRINCIPAL_TYPES}",
            name="software_assignments_principal_type_check",
        ),
    )


class Network(Base):
    """A Meraki network (e.g. one office's LAN). Source of truth = Meraki
    Dashboard. We cache the network record plus a single subnet/CIDR + the
    primary firewall (MX) and switch (MS) management IPs so we can:
      - render an inventory of networks for the asset directory
      - back-link Meraki-managed networking gear (gateway/switch/AP) to its
        parent network by serial → networkId on sync
      - resolve client devices (laptop/desktop) to a network by matching
        `assets.defender_last_ip` against `subnet_cidr`.

    Mostly read-only — admin can override `name_override`, attach a
    `location_id`, and add notes. Everything else flows from Meraki sync."""

    __tablename__ = "networks"
    __table_args__ = (
        UniqueConstraint("meraki_network_id", name="uq_networks_meraki_network_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Meraki Dashboard network id (e.g. "L_123456789012345678")
    meraki_network_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    meraki_org_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Live name pulled from Meraki on sync. `name_override` lets admins use
    # an internal name without rewriting Meraki.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_override: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Optional parent location ("which office is this network in?"). One
    # location can have multiple networks (corp / guest / IoT). Nullable
    # because the sync can't auto-resolve location — admin sets after.
    location_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("locations.id"), nullable=True
    )

    # Default MX appliance VLAN's subnet (lowest-id VLAN, usually management).
    # Null if no MX or no VLANs configured.
    subnet_cidr: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Public uplink IP from `/organizations/{org}/appliance/uplink/statuses`.
    wan_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # MX LAN gateway IP for the default VLAN.
    firewall_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # "Corp VLAN" — the actual client subnet (VLAN 30 by convention here).
    # This is the CIDR Defender IPs get matched against for asset→network
    # linking, since end-user devices live on this VLAN, not the mgmt one.
    corp_vlan_subnet: Mapped[str | None] = mapped_column(String(64), nullable=True)
    corp_vlan_gateway_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # JSON list of MS switch management IPs (str list).
    switch_ips_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # CSV of Meraki productTypes (e.g. "appliance,switch,wireless"). Helps
    # filter networks that have no MX (no subnet) from the UI.
    product_types_csv: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meraki_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    created_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    updated_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)

    location: Mapped[Location | None] = relationship(Location, lazy="joined")
    vlans: Mapped[list["NetworkVlan"]] = relationship(
        "NetworkVlan",
        back_populates="network",
        cascade="all, delete-orphan",
        order_by="NetworkVlan.meraki_vlan_id.asc()",
    )

    @property
    def display_name(self) -> str:
        return self.name_override or self.name

    @property
    def location_name(self) -> str | None:
        return self.location.name if self.location is not None else None


class NetworkVlan(Base):
    """One Meraki VLAN row per (network, VLAN id). Full per-VLAN subnet
    inventory so the asset locator can match any device IP into any VLAN
    on any network — not just the "default" + "corp" picks we cached on
    the Network row.

    Network.subnet_cidr / firewall_ip / corp_vlan_subnet / corp_vlan_gateway_ip
    remain as convenience columns for list views, but the full picture lives
    here. Refreshed on every Meraki sync."""

    __tablename__ = "network_vlans"
    __table_args__ = (
        UniqueConstraint(
            "network_id",
            "meraki_vlan_id",
            name="uq_network_vlans_network_vlan_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    network_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("networks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    meraki_vlan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subnet_cidr: Mapped[str | None] = mapped_column(String(64), nullable=True)
    appliance_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    network: Mapped[Network] = relationship(Network, back_populates="vlans")


class MerakiClient(Base):
    """Cached snapshot of clients Meraki has seen on a network. Refreshed
    on Meraki sync via `GET /networks/{id}/clients` (per-network) so we can
    answer "which networks has asset X been seen on" without hitting the
    Meraki API on every page load.

    Matched against `Asset.mac_address` to derive cross-network sightings."""

    __tablename__ = "meraki_clients"
    __table_args__ = (
        UniqueConstraint(
            "network_id", "mac", name="uq_meraki_clients_network_mac"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    network_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("networks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Normalised colon-form MAC (aa:bb:cc:dd:ee:ff lowercase). Indexed for
    # the asset → appearances lookup.
    mac: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vlan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user: Mapped[str | None] = mapped_column(String(320), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    os: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    network: Mapped[Network] = relationship(Network)
