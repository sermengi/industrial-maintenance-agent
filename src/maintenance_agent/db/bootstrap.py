from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from importlib.resources import files
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.sql.schema import Table

from maintenance_agent.db.models import (
    Asset,
    FaultEvent,
    FaultTaxonomy,
    MaintenanceEvent,
    Observation,
    OperatingLimit,
    PlantPolicy,
    TelemetrySnapshot,
    WorkOrder,
)


class StrictFixtureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssetFixture(StrictFixtureModel):
    asset_id: str
    asset_type: str
    model: str
    location: str
    installation_date: date
    status: str


class TelemetrySnapshotFixture(StrictFixtureModel):
    snapshot_id: str
    asset_id: str
    timestamp: datetime
    vibration_mm_s: Decimal
    bearing_temperature_c: Decimal
    inlet_pressure_bar: Decimal
    discharge_pressure_bar: Decimal
    flow_rate_l_min: Decimal


class FaultEventFixture(StrictFixtureModel):
    event_id: str
    asset_id: str
    fault_code: str
    fault_name: str
    timestamp: datetime
    severity: str
    status: str


class MaintenanceEventFixture(StrictFixtureModel):
    maintenance_id: str
    asset_id: str
    date: date
    type: str
    component: str
    description: str


class ObservationFixture(StrictFixtureModel):
    observation_id: str
    asset_id: str
    timestamp: datetime
    type: str
    severity: str
    description: str
    reported_by: str


class WorkOrderFixture(StrictFixtureModel):
    work_order_id: str
    asset_id: str
    issue: str
    priority: str
    status: str
    created_at: date
    approved: bool


class FaultTaxonomyFixture(StrictFixtureModel):
    fault_code: str
    canonical_name: str
    description: str


class OperatingLimitFixture(StrictFixtureModel):
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


class PlantPolicyFixture(StrictFixtureModel):
    policy_id: str
    type: str
    condition: str
    required_action: str


@dataclass(frozen=True)
class FixtureSpec:
    filename: str
    table: Table
    adapter: TypeAdapter[Any]


FIXTURE_SPECS: tuple[FixtureSpec, ...] = (
    FixtureSpec("assets.json", cast(Table, Asset.__table__), TypeAdapter(list[AssetFixture])),
    FixtureSpec(
        "fault_taxonomy.json",
        cast(Table, FaultTaxonomy.__table__),
        TypeAdapter(list[FaultTaxonomyFixture]),
    ),
    FixtureSpec(
        "operating_limits.json",
        cast(Table, OperatingLimit.__table__),
        TypeAdapter(list[OperatingLimitFixture]),
    ),
    FixtureSpec(
        "plant_policies.json",
        cast(Table, PlantPolicy.__table__),
        TypeAdapter(list[PlantPolicyFixture]),
    ),
    FixtureSpec(
        "telemetry_snapshots.json",
        cast(Table, TelemetrySnapshot.__table__),
        TypeAdapter(list[TelemetrySnapshotFixture]),
    ),
    FixtureSpec(
        "fault_events.json",
        cast(Table, FaultEvent.__table__),
        TypeAdapter(list[FaultEventFixture]),
    ),
    FixtureSpec(
        "maintenance_events.json",
        cast(Table, MaintenanceEvent.__table__),
        TypeAdapter(list[MaintenanceEventFixture]),
    ),
    FixtureSpec(
        "observations.json",
        cast(Table, Observation.__table__),
        TypeAdapter(list[ObservationFixture]),
    ),
    FixtureSpec(
        "work_orders.json",
        cast(Table, WorkOrder.__table__),
        TypeAdapter(list[WorkOrderFixture]),
    ),
)

PHASE_1_TABLE_NAMES: tuple[str, ...] = tuple(spec.table.name for spec in FIXTURE_SPECS)


def load_fixture_records(spec: FixtureSpec) -> list[dict[str, Any]]:
    fixture_path = files("maintenance_agent.db").joinpath("fixtures", spec.filename)
    with fixture_path.open() as fixture_file:
        raw_records = json.load(fixture_file, parse_float=Decimal, parse_int=Decimal)

    validated_records = spec.adapter.validate_python(raw_records)
    return [
        record.model_dump()
        for record in validated_records
        if isinstance(record, StrictFixtureModel)
    ]


def load_all_fixture_records() -> dict[str, list[dict[str, Any]]]:
    return {spec.table.name: load_fixture_records(spec) for spec in FIXTURE_SPECS}


async def reset_database(database_url: str) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(_truncate_statement(PHASE_1_TABLE_NAMES)))
            for spec in FIXTURE_SPECS:
                records = load_fixture_records(spec)
                if records:
                    await connection.execute(spec.table.insert(), records)
    finally:
        await engine.dispose()


def _truncate_statement(table_names: Sequence[str]) -> str:
    table_list = ", ".join(table_names)
    return f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE"
