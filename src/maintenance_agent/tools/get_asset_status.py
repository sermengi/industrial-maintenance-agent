from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories import (
    fault_events,
    observations,
    operating_limits,
    telemetry,
)
from maintenance_agent.db.repositories.records import (
    AssetRecord,
    FaultEventRecord,
    ObservationRecord,
    OperatingLimitRecord,
    TelemetrySnapshotRecord,
)

TelemetryTier = Literal["normal", "warning", "critical"]


class ClassifiedReading(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: str
    value: Decimal
    unit: str
    tier: TelemetryTier | None
    operating_limit_id: str | None
    rule_text: str | None


class GetAssetStatusResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset: AssetRecord
    telemetry: TelemetrySnapshotRecord | None
    classified_readings: list[ClassifiedReading] = Field(default_factory=list)
    active_faults: list[FaultEventRecord] = Field(default_factory=list)
    observations: list[ObservationRecord] = Field(default_factory=list)
    operating_limits: list[OperatingLimitRecord] = Field(default_factory=list)


METRIC_UNITS = {
    "vibration_mm_s": "mm/s",
    "bearing_temperature_c": "C",
    "inlet_pressure_bar": "bar",
    "discharge_pressure_bar": "bar",
    "flow_rate_l_min": "L/min",
}


async def get_asset_status(
    asset: AssetRecord,
    session: AsyncSession,
) -> GetAssetStatusResult:
    telemetry_snapshot = await telemetry.get_latest_for_asset(session, asset.asset_id)
    active_faults = await fault_events.list_active_for_asset(session, asset.asset_id)
    asset_observations = await observations.list_for_asset(session, asset.asset_id)
    asset_operating_limits = await operating_limits.list_for_model(session, asset.model)

    classified_readings = (
        _classify_telemetry(telemetry_snapshot, asset_operating_limits)
        if telemetry_snapshot is not None
        else []
    )

    return GetAssetStatusResult(
        asset=asset,
        telemetry=telemetry_snapshot,
        classified_readings=classified_readings,
        active_faults=active_faults,
        observations=asset_observations,
        operating_limits=asset_operating_limits,
    )


def _classify_telemetry(
    telemetry_snapshot: TelemetrySnapshotRecord,
    asset_operating_limits: list[OperatingLimitRecord],
) -> list[ClassifiedReading]:
    limits_by_metric = {limit.metric: limit for limit in asset_operating_limits}

    return [
        _classify_reading(
            metric=metric,
            value=getattr(telemetry_snapshot, metric),
            limit=limits_by_metric.get(metric),
        )
        for metric in METRIC_UNITS
    ]


def _classify_reading(
    metric: str,
    value: Decimal,
    limit: OperatingLimitRecord | None,
) -> ClassifiedReading:
    if limit is None:
        return ClassifiedReading(
            metric=metric,
            value=value,
            unit=METRIC_UNITS[metric],
            tier=None,
            operating_limit_id=None,
            rule_text=None,
        )

    return ClassifiedReading(
        metric=metric,
        value=value,
        unit=limit.unit,
        tier=_classify_tier(value, limit),
        operating_limit_id=limit.operating_limit_id,
        rule_text=limit.rule_text,
    )


def _classify_tier(value: Decimal, limit: OperatingLimitRecord) -> TelemetryTier | None:
    if _is_critical(value, limit):
        return "critical"
    if _is_warning(value, limit):
        return "warning"
    if _is_normal(value, limit):
        return "normal"
    return None


def _is_critical(value: Decimal, limit: OperatingLimitRecord) -> bool:
    if limit.critical_min is not None:
        if limit.warning_max is not None and limit.critical_min == limit.warning_max:
            return value > limit.critical_min
        return value >= limit.critical_min

    if limit.critical_max is not None:
        if limit.warning_min is not None and limit.critical_max == limit.warning_min:
            return value < limit.critical_max
        return value <= limit.critical_max

    return False


def _is_warning(value: Decimal, limit: OperatingLimitRecord) -> bool:
    if limit.warning_min is not None and value < limit.warning_min:
        return False
    if limit.warning_max is not None:
        if limit.normal_min is not None and limit.warning_max == limit.normal_min:
            return value < limit.warning_max
        return value <= limit.warning_max
    return limit.warning_min is not None or limit.warning_max is not None


def _is_normal(value: Decimal, limit: OperatingLimitRecord) -> bool:
    if limit.normal_min is not None and value < limit.normal_min:
        return False
    if limit.normal_max is not None and value >= limit.normal_max:
        return False
    return limit.normal_min is not None or limit.normal_max is not None
