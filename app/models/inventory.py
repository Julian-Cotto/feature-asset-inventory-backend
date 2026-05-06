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


ASSET_TYPES = ("laptop", "desktop", "thin_client")
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
    {"code": "in_warehouse", "label": "In Warehouse", "is_terminal": False, "sort_order": 10},
    {"code": "assigned",     "label": "Assigned",      "is_terminal": False, "sort_order": 20},
    {"code": "in_repair",    "label": "In Repair",     "is_terminal": False, "sort_order": 30},
    {"code": "lost",         "label": "Lost",          "is_terminal": True,  "sort_order": 90},
    {"code": "retired",      "label": "Retired",       "is_terminal": True,  "sort_order": 99},
]


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
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    created_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)
    updated_by_upn: Mapped[str | None] = mapped_column(String(320), nullable=True)

    status: Mapped[AssetStatus] = relationship(AssetStatus, lazy="joined")
    location: Mapped[Location | None] = relationship(Location, lazy="joined")
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
