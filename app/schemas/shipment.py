from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.inventory import AssetOut


ShipmentDirection = Literal["outbound", "inbound"]
ShipmentCarrier = Literal["ups", "fedex", "other"]
ShipmentCarrierStatus = Literal[
    "pending",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "exception",
    "unknown",
]
ShipmentResolution = Literal["open", "resolved", "cancelled"]


class AddressIn(BaseModel):
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=128)
    state: str | None = Field(default=None, max_length=64)
    postal_code: str | None = Field(default=None, max_length=32)
    country: str | None = Field(default=None, max_length=64)


class AutoAssignRequest(BaseModel):
    asset_type: str = Field(min_length=1, max_length=32)
    quantity: int = Field(ge=1, le=100)
    # Optional finer-grained filters — narrow the auto-assign pool
    model: str | None = Field(default=None, max_length=255)
    manufacturer: str | None = Field(default=None, max_length=128)


class ShipmentItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    shipment_id: int
    asset_id: int
    asset: AssetOut | None = None
    notes: str | None = None
    created_at: datetime


class ShipmentEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    shipment_id: int
    occurred_at: datetime
    status: ShipmentCarrierStatus
    location: str | None = None
    description: str | None = None
    created_at: datetime


class ShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tracking_number: str
    carrier: ShipmentCarrier
    direction: ShipmentDirection
    description: str | None = None
    carrier_status: ShipmentCarrierStatus
    resolution: ShipmentResolution

    from_location_id: int | None = None
    from_address_line1: str | None = None
    from_address_line2: str | None = None
    from_city: str | None = None
    from_state: str | None = None
    from_postal_code: str | None = None
    from_country: str | None = None

    to_location_id: int | None = None
    to_address_line1: str | None = None
    to_address_line2: str | None = None
    to_city: str | None = None
    to_state: str | None = None
    to_postal_code: str | None = None
    to_country: str | None = None

    notes: str | None = None
    last_polled_at: datetime | None = None
    last_poll_error: str | None = None
    resolved_at: datetime | None = None
    resolved_by_upn: str | None = None
    cancelled_at: datetime | None = None
    cancelled_by_upn: str | None = None
    archived_at: datetime | None = None
    archived_by_upn: str | None = None

    created_at: datetime
    updated_at: datetime
    created_by_upn: str | None = None
    updated_by_upn: str | None = None

    items: list[ShipmentItemOut] = Field(default_factory=list)
    events: list[ShipmentEventOut] = Field(default_factory=list)


class ShipmentCreate(BaseModel):
    tracking_number: str = Field(min_length=1, max_length=64)
    carrier: ShipmentCarrier
    direction: ShipmentDirection
    description: str | None = Field(default=None, max_length=512)
    notes: str | None = None

    from_location_id: int | None = None
    from_address: AddressIn | None = None
    to_location_id: int | None = None
    to_address: AddressIn | None = None

    asset_ids: list[int] = Field(default_factory=list)
    auto_assign: list[AutoAssignRequest] = Field(default_factory=list)


class ShipmentUpdate(BaseModel):
    description: str | None = None
    notes: str | None = None
    direction: ShipmentDirection | None = None


class ShipmentItemCreate(BaseModel):
    asset_id: int


class CarrierDetectResult(BaseModel):
    carrier: ShipmentCarrier
