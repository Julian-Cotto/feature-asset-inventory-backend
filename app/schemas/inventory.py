from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AssetType = Literal["laptop", "desktop", "thin_client"]
LocationType = Literal["warehouse", "site"]


# ---------- statuses ----------

class AssetStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    label: str
    is_terminal: bool
    sort_order: int
    is_active: bool


class AssetStatusCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=128)
    is_terminal: bool = False
    sort_order: int = 0
    is_active: bool = True


class AssetStatusUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=128)
    is_terminal: bool | None = None
    sort_order: int | None = None
    is_active: bool | None = None


# ---------- locations ----------

class LocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    type: LocationType
    address: str | None = None
    is_active: bool


class LocationCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    type: LocationType
    address: str | None = Field(default=None, max_length=1024)
    is_active: bool = True


class LocationUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    type: LocationType | None = None
    address: str | None = Field(default=None, max_length=1024)
    is_active: bool | None = None


# ---------- assets ----------

class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    asset_tag: str | None = None
    serial_number: str
    asset_type: AssetType
    manufacturer: str | None = None
    model: str | None = None
    os: str | None = None
    os_version: str | None = None
    status_code: str
    location_id: int | None = None
    assigned_upn: str | None = None
    assigned_at: datetime | None = None
    onboarded_at: datetime
    archived_at: datetime | None = None
    notes: str | None = None


class AssetCreate(BaseModel):
    asset_tag: str | None = Field(default=None, max_length=64)
    serial_number: str = Field(min_length=1, max_length=128)
    asset_type: AssetType
    manufacturer: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=255)
    os: str | None = Field(default=None, max_length=64)
    os_version: str | None = Field(default=None, max_length=64)
    status_code: str = "in_warehouse"
    location_id: int | None = None
    notes: str | None = None


class AssetUpdate(BaseModel):
    asset_tag: str | None = Field(default=None, max_length=64)
    manufacturer: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=255)
    os: str | None = Field(default=None, max_length=64)
    os_version: str | None = Field(default=None, max_length=64)
    notes: str | None = None


class AssetAssign(BaseModel):
    assigned_upn: str | None = Field(default=None, max_length=320)
    location_id: int | None = None
    notes: str | None = None


class AssetStatusChange(BaseModel):
    status_code: str
    notes: str | None = None


class AssetArchive(BaseModel):
    notes: str | None = None


# ---------- history ----------

class AssetHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    asset_id: int
    event_type: str
    from_value: str | None = None
    to_value: str | None = None
    performed_by_upn: str | None = None
    performed_at: datetime
    notes: str | None = None
