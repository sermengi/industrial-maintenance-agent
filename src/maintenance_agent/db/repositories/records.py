from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class RepositoryRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


class AssetRecord(RepositoryRecord):
    asset_id: str
    asset_type: str
    model: str
    location: str
    installation_date: date
    status: str


class TelemetrySnapshotRecord(RepositoryRecord):
    snapshot_id: str
    asset_id: str
    timestamp: datetime
    vibration_mm_s: Decimal
    bearing_temperature_c: Decimal
    inlet_pressure_bar: Decimal
    discharge_pressure_bar: Decimal
    flow_rate_l_min: Decimal

    @property
    def source_type(self) -> Literal["telemetry_snapshot"]:
        return "telemetry_snapshot"

    @property
    def source_id(self) -> str:
        return self.snapshot_id


class FaultEventRecord(RepositoryRecord):
    event_id: str
    asset_id: str
    fault_code: str
    fault_name: str
    timestamp: datetime
    severity: str
    status: str

    @property
    def source_type(self) -> Literal["fault_event"]:
        return "fault_event"

    @property
    def source_id(self) -> str:
        return self.event_id


class MaintenanceEventRecord(RepositoryRecord):
    maintenance_id: str
    asset_id: str
    date: date
    type: str
    component: str
    description: str

    @property
    def source_type(self) -> Literal["maintenance_event"]:
        return "maintenance_event"

    @property
    def source_id(self) -> str:
        return self.maintenance_id


class ObservationRecord(RepositoryRecord):
    observation_id: str
    asset_id: str
    timestamp: datetime
    type: str
    severity: str
    description: str
    reported_by: str

    @property
    def source_type(self) -> Literal["observation"]:
        return "observation"

    @property
    def source_id(self) -> str:
        return self.observation_id


class WorkOrderRecord(RepositoryRecord):
    work_order_id: str
    asset_id: str
    issue: str
    priority: str
    status: str
    created_at: date
    approved: bool

    @property
    def source_type(self) -> Literal["work_order"]:
        return "work_order"

    @property
    def source_id(self) -> str:
        return self.work_order_id


class FaultTaxonomyRecord(RepositoryRecord):
    fault_code: str
    canonical_name: str
    description: str


class OperatingLimitRecord(RepositoryRecord):
    operating_limit_id: str
    model: str
    metric: str
    unit: str
    normal_min: Decimal | None
    normal_max: Decimal | None
    warning_min: Decimal | None
    warning_max: Decimal | None
    critical_min: Decimal | None
    critical_max: Decimal | None
    rule_text: str
    source_type: str
    provenance_note: str

    @property
    def source_id(self) -> str:
        return self.operating_limit_id


class PlantPolicyRecord(RepositoryRecord):
    policy_id: str
    type: str
    condition: str
    required_action: str
