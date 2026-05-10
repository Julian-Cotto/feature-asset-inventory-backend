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
    warranty_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    warranty_end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warranty_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    created_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    updated_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)

    status: Mapped[AssetStatus] = relationship(AssetStatus, lazy="joined")
    location: Mapped[Location | None] = relationship(Location, lazy="joined")

    @property
    def location_name(self) -> str | None:
        return self.location.name if self.location is not None else None

    @property
    def location_code(self) -> str | None:
        return self.location.code if self.location is not None else None
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
    completed_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    cancelled_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)

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
