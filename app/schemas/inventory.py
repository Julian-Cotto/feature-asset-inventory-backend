from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AssetType = Literal[
    "laptop",
    "desktop",
    "thin_client",
    "ap",
    "switch",
    "gateway",
]
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
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
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
    override_model: str | None = None
    series: str | None = None
    generation: str | None = None
    cpu: str | None = None
    os: str | None = None
    os_version: str | None = None
    status_code: str
    location_id: int | None = None
    location_name: str | None = None
    location_code: str | None = None
    assigned_upn: str | None = None
    assigned_at: datetime | None = None
    onboarded_at: datetime
    archived_at: datetime | None = None
    notes: str | None = None
    intune_id: str | None = None
    intune_synced_at: datetime | None = None
    intune_device_name: str | None = None
    intune_managed_by: str | None = None
    intune_ownership: str | None = None
    intune_compliance: str | None = None
    intune_last_check_in: datetime | None = None
    aad_device_id: str | None = None
    defender_id: str | None = None
    defender_synced_at: datetime | None = None
    defender_health_status: str | None = None
    defender_risk_score: str | None = None
    defender_exposure_level: str | None = None
    defender_last_seen_at: datetime | None = None
    defender_onboarding_status: str | None = None
    defender_av_status: str | None = None
    defender_os_build: str | None = None
    defender_last_ip: str | None = None
    defender_tags: str | None = None  # JSON array (string)
    warranty_active: bool | None = None
    warranty_end_date: datetime | None = None
    warranty_synced_at: datetime | None = None
    mac_address: str | None = None
    network_id: int | None = None
    network_name: str | None = None
    reserved_by_kind: str | None = None  # "deployment" | "shipment" | None
    reserved_by_id: int | None = None
    reserved_by_label: str | None = None


class AssetCreate(BaseModel):
    asset_tag: str | None = Field(default=None, max_length=64)
    serial_number: str = Field(min_length=1, max_length=128)
    asset_type: AssetType
    manufacturer: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=255)
    override_model: str | None = Field(default=None, max_length=255)
    series: str | None = Field(default=None, max_length=128)
    generation: str | None = Field(default=None, max_length=64)
    cpu: str | None = Field(default=None, max_length=128)
    os: str | None = Field(default=None, max_length=64)
    os_version: str | None = Field(default=None, max_length=64)
    status_code: str = "active"
    location_id: int | None = None
    notes: str | None = None
    intune_id: str | None = Field(default=None, max_length=64)


class AssetUpdate(BaseModel):
    asset_tag: str | None = Field(default=None, max_length=64)
    manufacturer: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=255)
    override_model: str | None = Field(default=None, max_length=255)
    series: str | None = Field(default=None, max_length=128)
    generation: str | None = Field(default=None, max_length=64)
    cpu: str | None = Field(default=None, max_length=128)
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


class AssetBulkLocation(BaseModel):
    asset_ids: list[int] = Field(min_length=1, max_length=500)
    location_id: int | None = None


# ---------- dashboard stats ----------

class StatusCount(BaseModel):
    code: str
    count: int


class TypeCount(BaseModel):
    type: str
    count: int


class AssetsStats(BaseModel):
    total: int
    by_status: list[StatusCount]
    by_type: list[TypeCount]


class WarrantyStats(BaseModel):
    on: int
    off: int
    unknown: int
    expiring_30d: int
    expiring_60d: int
    expiring_90d: int


class IntuneStats(BaseModel):
    last_bulk_sync_at: datetime | None = None
    stale_7d_count: int
    synced_count: int


class ShipmentsStats(BaseModel):
    open: int
    in_transit: int
    exception: int


class DeploymentsStats(BaseModel):
    planning: int
    in_progress: int
    completed_30d: int


class SeriesPoint(BaseModel):
    date: str  # YYYY-MM-DD
    count: int


class ReservationRow(BaseModel):
    asset_id: int
    asset_tag: str | None = None
    serial_number: str
    asset_type: str
    manufacturer: str | None = None
    model: str | None = None
    intune_device_name: str | None = None
    assigned_upn: str | None = None
    kind: str  # "deployment" | "shipment"
    source_id: int
    source_label: str  # deployment name or shipment tracking #
    source_status: str  # planning/in_progress | carrier_status
    destination: str | None = None  # target / to location or address


class DashboardStats(BaseModel):
    assets: AssetsStats
    warranty: WarrantyStats
    intune: IntuneStats
    shipments: ShipmentsStats
    deployments: DeploymentsStats
    onboards_30d: list[SeriesPoint]
    warranty_changes_30d: list[SeriesPoint]


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
