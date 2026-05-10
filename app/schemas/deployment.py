from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.inventory import AssetOut
from app.schemas.shipment import AddressIn, AutoAssignRequest, ShipmentOut


DeploymentStatus = Literal["planning", "in_progress", "completed", "cancelled"]


class DeploymentItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    deployment_id: int
    asset_id: int
    asset: AssetOut | None = None
    role: str | None = None
    notes: str | None = None
    created_at: datetime


class DeploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    type: str | None = None
    status: DeploymentStatus
    description: str | None = None
    notes: str | None = None
    target_date: datetime | None = None

    target_location_id: int | None = None
    target_address_line1: str | None = None
    target_address_line2: str | None = None
    target_city: str | None = None
    target_state: str | None = None
    target_postal_code: str | None = None
    target_country: str | None = None

    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    completed_by_upn: str | None = None
    cancelled_by_upn: str | None = None

    created_at: datetime
    updated_at: datetime
    created_by_upn: str | None = None
    updated_by_upn: str | None = None

    items: list[DeploymentItemOut] = Field(default_factory=list)
    shipments: list[ShipmentOut] = Field(default_factory=list)


class DeploymentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=1024)
    notes: str | None = None
    target_date: datetime | None = None

    target_location_id: int | None = None
    target_address: AddressIn | None = None

    asset_ids: list[int] = Field(default_factory=list)
    auto_assign: list[AutoAssignRequest] = Field(default_factory=list)


class DeploymentUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    type: str | None = Field(default=None, max_length=64)
    description: str | None = None
    notes: str | None = None
    target_date: datetime | None = None
    target_location_id: int | None = None
    target_address: AddressIn | None = None


class DeploymentItemCreate(BaseModel):
    asset_id: int
    role: str | None = Field(default=None, max_length=128)
    notes: str | None = None
    force: bool = False


class DeploymentItemAddResponse(BaseModel):
    item: DeploymentItemOut
    released_from_deployment_id: int | None = None
