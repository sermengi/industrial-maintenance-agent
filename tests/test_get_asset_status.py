from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories.records import (
    AssetRecord,
    FaultEventRecord,
    ObservationRecord,
    OperatingLimitRecord,
    TelemetrySnapshotRecord,
)
from maintenance_agent.tools.get_asset_status import (
    ClassifiedReading,
    GetAssetStatusResult,
    get_asset_status,
)


@pytest.fixture
def asset_records() -> dict[str, AssetRecord]:
    return {
        "PUMP-101": _asset("PUMP-101", "CP-200", "Building A / Line 1", "operational"),
        "PUMP-102": _asset("PUMP-102", "CP-200", "Building A / Line 2", "warning"),
        "PUMP-103": _asset("PUMP-103", "CP-200", "Building B / Line 1", "fault_active"),
        "PUMP-104": _asset("PUMP-104", "CP-300", "Building B / Line 2", "maintenance_required"),
    }


@pytest.fixture
def telemetry_records() -> dict[str, TelemetrySnapshotRecord]:
    return {
        "PUMP-101": _telemetry("TS-001", "PUMP-101", "2.10", "54.00", "2.40", "6.80", "98.00"),
        "PUMP-102": _telemetry("TS-002", "PUMP-102", "8.10", "58.00", "2.30", "6.40", "94.00"),
        "PUMP-103": _telemetry("TS-003", "PUMP-103", "4.20", "91.00", "2.50", "6.60", "96.00"),
        "PUMP-104": _telemetry("TS-004", "PUMP-104", "2.80", "61.00", "2.20", "3.90", "61.00"),
    }


@pytest.fixture
def active_fault_records() -> dict[str, list[FaultEventRecord]]:
    return {
        "PUMP-101": [],
        "PUMP-102": [_fault("FE-001", "PUMP-102", "F101", "HIGH_VIBRATION", "medium", "active")],
        "PUMP-103": [
            _fault("FE-004", "PUMP-103", "F102", "HIGH_BEARING_TEMPERATURE", "high", "active")
        ],
        "PUMP-104": [
            _fault("FE-005", "PUMP-104", "F103", "LOW_DISCHARGE_PRESSURE", "medium", "active")
        ],
    }


@pytest.fixture
def observation_records() -> dict[str, list[ObservationRecord]]:
    return {
        "PUMP-101": [],
        "PUMP-102": [_observation("OBS-002", "PUMP-102", "abnormal_vibration", "moderate")],
        "PUMP-103": [],
        "PUMP-104": [_observation("OBS-001", "PUMP-104", "seal_leak", "minor")],
    }


@pytest.fixture
def operating_limit_records() -> dict[str, list[OperatingLimitRecord]]:
    return {
        "CP-200": [
            _limit(
                "OL-001",
                "CP-200",
                "vibration_mm_s",
                "mm/s",
                normal_max="4.50",
                warning_min="4.50",
                warning_max="7.00",
                critical_min="7.00",
                rule_text="Normal < 4.5; warning 4.5-7.0; critical > 7.0",
            ),
            _limit(
                "OL-002",
                "CP-200",
                "bearing_temperature_c",
                "C",
                normal_max="82.00",
                critical_min="82.00",
                rule_text="Normal < 82; high >= 82",
            ),
        ],
        "CP-300": [
            _limit(
                "OL-003",
                "CP-300",
                "discharge_pressure_bar",
                "bar",
                normal_min="5.00",
                warning_min="4.00",
                warning_max="5.00",
                critical_max="4.00",
                rule_text="Normal >= 5.0; warning 4.0-<5.0; critical < 4.0",
            ),
            _limit(
                "OL-004",
                "CP-300",
                "flow_rate_l_min",
                "L/min",
                normal_min="85.00",
                warning_min="70.00",
                warning_max="85.00",
                critical_max="70.00",
                rule_text="Normal >= 85; warning 70-<85; low < 70",
            ),
        ],
    }


@pytest.fixture
def session() -> AsyncSession:
    return cast(AsyncSession, object())


@pytest.fixture
def repository_calls(
    monkeypatch: pytest.MonkeyPatch,
    telemetry_records: dict[str, TelemetrySnapshotRecord],
    active_fault_records: dict[str, list[FaultEventRecord]],
    observation_records: dict[str, list[ObservationRecord]],
    operating_limit_records: dict[str, list[OperatingLimitRecord]],
) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def fake_get_latest_for_asset(
        _session: AsyncSession,
        asset_id: str,
    ) -> TelemetrySnapshotRecord | None:
        calls.append(("telemetry", asset_id))
        return telemetry_records.get(asset_id)

    async def fake_list_active_for_asset(
        _session: AsyncSession,
        asset_id: str,
    ) -> list[FaultEventRecord]:
        calls.append(("active_faults", asset_id))
        return active_fault_records[asset_id]

    async def fake_list_observations_for_asset(
        _session: AsyncSession,
        asset_id: str,
    ) -> list[ObservationRecord]:
        calls.append(("observations", asset_id))
        return observation_records[asset_id]

    async def fake_list_limits_for_model(
        _session: AsyncSession,
        model: str,
    ) -> list[OperatingLimitRecord]:
        calls.append(("operating_limits", model))
        return operating_limit_records[model]

    monkeypatch.setattr(
        "maintenance_agent.tools.get_asset_status.telemetry.get_latest_for_asset",
        fake_get_latest_for_asset,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.get_asset_status.fault_events.list_active_for_asset",
        fake_list_active_for_asset,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.get_asset_status.observations.list_for_asset",
        fake_list_observations_for_asset,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.get_asset_status.operating_limits.list_for_model",
        fake_list_limits_for_model,
    )

    return calls


@pytest.mark.asyncio
async def test_get_asset_status_returns_raw_asset_status_collections(
    session: AsyncSession,
    repository_calls: list[tuple[str, str]],
    asset_records: dict[str, AssetRecord],
) -> None:
    result = await get_asset_status(asset_records["PUMP-102"], session)

    assert isinstance(result, GetAssetStatusResult)
    assert result.asset == asset_records["PUMP-102"]
    assert result.telemetry is not None
    assert result.telemetry.snapshot_id == "TS-002"
    assert [fault.event_id for fault in result.active_faults] == ["FE-001"]
    assert [observation.observation_id for observation in result.observations] == ["OBS-002"]
    assert [limit.operating_limit_id for limit in result.operating_limits] == ["OL-001", "OL-002"]
    assert repository_calls == [
        ("telemetry", "PUMP-102"),
        ("active_faults", "PUMP-102"),
        ("observations", "PUMP-102"),
        ("operating_limits", "CP-200"),
    ]


@pytest.mark.asyncio
async def test_get_asset_status_classifies_cp_200_assets(
    session: AsyncSession,
    repository_calls: list[tuple[str, str]],
    asset_records: dict[str, AssetRecord],
) -> None:
    pump_101 = await get_asset_status(asset_records["PUMP-101"], session)
    pump_102 = await get_asset_status(asset_records["PUMP-102"], session)
    pump_103 = await get_asset_status(asset_records["PUMP-103"], session)

    pump_101_readings = _readings_by_metric(pump_101)
    assert pump_101_readings["vibration_mm_s"].tier == "normal"
    assert pump_101_readings["bearing_temperature_c"].tier == "normal"
    assert pump_101.active_faults == []

    pump_102_readings = _readings_by_metric(pump_102)
    assert pump_102_readings["vibration_mm_s"].tier == "critical"
    assert pump_102_readings["vibration_mm_s"].operating_limit_id == "OL-001"
    assert [fault.event_id for fault in pump_102.active_faults] == ["FE-001"]
    assert [observation.observation_id for observation in pump_102.observations] == ["OBS-002"]

    pump_103_readings = _readings_by_metric(pump_103)
    assert pump_103_readings["bearing_temperature_c"].tier == "critical"
    assert pump_103_readings["bearing_temperature_c"].operating_limit_id == "OL-002"
    assert pump_103_readings["bearing_temperature_c"].rule_text == "Normal < 82; high >= 82"
    assert [fault.event_id for fault in pump_103.active_faults] == ["FE-004"]

    assert _all_inlet_pressure_readings_are_unclassified(pump_101, pump_102, pump_103)
    assert all(
        [limit.operating_limit_id for limit in result.operating_limits] == ["OL-001", "OL-002"]
        for result in [pump_101, pump_102, pump_103]
    )
    assert ("active_faults", "PUMP-103") in repository_calls


@pytest.mark.asyncio
async def test_get_asset_status_classifies_cp_300_asset(
    session: AsyncSession,
    repository_calls: list[tuple[str, str]],
    asset_records: dict[str, AssetRecord],
) -> None:
    result = await get_asset_status(asset_records["PUMP-104"], session)
    readings = _readings_by_metric(result)

    assert readings["discharge_pressure_bar"].tier == "critical"
    assert readings["discharge_pressure_bar"].operating_limit_id == "OL-003"
    assert readings["flow_rate_l_min"].tier == "critical"
    assert readings["flow_rate_l_min"].operating_limit_id == "OL-004"
    assert readings["inlet_pressure_bar"].tier is None
    assert [observation.observation_id for observation in result.observations] == ["OBS-001"]
    assert result.observations[0].type == "seal_leak"
    assert [fault.event_id for fault in result.active_faults] == ["FE-005"]
    assert all(fault.fault_code != "F104" for fault in result.active_faults)
    assert [limit.operating_limit_id for limit in result.operating_limits] == ["OL-003", "OL-004"]
    assert ("operating_limits", "CP-300") in repository_calls


@pytest.mark.asyncio
async def test_get_asset_status_returns_empty_classifications_without_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    repository_calls: list[tuple[str, str]],
    asset_records: dict[str, AssetRecord],
) -> None:
    async def fake_get_latest_for_asset(
        _session: AsyncSession,
        asset_id: str,
    ) -> TelemetrySnapshotRecord | None:
        repository_calls.append(("telemetry", asset_id))
        return None

    monkeypatch.setattr(
        "maintenance_agent.tools.get_asset_status.telemetry.get_latest_for_asset",
        fake_get_latest_for_asset,
    )

    result = await get_asset_status(asset_records["PUMP-101"], session)

    assert result.telemetry is None
    assert result.classified_readings == []
    assert result.active_faults == []
    assert [limit.operating_limit_id for limit in result.operating_limits] == ["OL-001", "OL-002"]


@pytest.mark.asyncio
async def test_get_asset_status_handles_structured_threshold_boundaries(
    session: AsyncSession,
    repository_calls: list[tuple[str, str]],
    asset_records: dict[str, AssetRecord],
    telemetry_records: dict[str, TelemetrySnapshotRecord],
) -> None:
    telemetry_records["PUMP-102"] = _telemetry(
        "TS-102-B",
        "PUMP-102",
        "7.00",
        "82.00",
        "2.30",
        "6.40",
        "94.00",
    )
    telemetry_records["PUMP-104"] = _telemetry(
        "TS-104-B",
        "PUMP-104",
        "2.80",
        "61.00",
        "2.20",
        "5.00",
        "85.00",
    )

    pump_102 = await get_asset_status(asset_records["PUMP-102"], session)
    pump_104 = await get_asset_status(asset_records["PUMP-104"], session)
    pump_102_readings = _readings_by_metric(pump_102)
    pump_104_readings = _readings_by_metric(pump_104)

    assert pump_102_readings["vibration_mm_s"].tier == "warning"
    assert pump_102_readings["bearing_temperature_c"].tier == "critical"
    assert pump_104_readings["discharge_pressure_bar"].tier == "normal"
    assert pump_104_readings["flow_rate_l_min"].tier == "normal"
    assert ("telemetry", "PUMP-104") in repository_calls


def _asset(asset_id: str, model: str, location: str, status: str) -> AssetRecord:
    return AssetRecord(
        asset_id=asset_id,
        asset_type="centrifugal_pump",
        model=model,
        location=location,
        installation_date=date(2021, 1, 1),
        status=status,
    )


def _telemetry(
    snapshot_id: str,
    asset_id: str,
    vibration_mm_s: str,
    bearing_temperature_c: str,
    inlet_pressure_bar: str,
    discharge_pressure_bar: str,
    flow_rate_l_min: str,
) -> TelemetrySnapshotRecord:
    return TelemetrySnapshotRecord(
        snapshot_id=snapshot_id,
        asset_id=asset_id,
        timestamp=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
        vibration_mm_s=Decimal(vibration_mm_s),
        bearing_temperature_c=Decimal(bearing_temperature_c),
        inlet_pressure_bar=Decimal(inlet_pressure_bar),
        discharge_pressure_bar=Decimal(discharge_pressure_bar),
        flow_rate_l_min=Decimal(flow_rate_l_min),
    )


def _fault(
    event_id: str,
    asset_id: str,
    fault_code: str,
    fault_name: str,
    severity: str,
    status: str,
) -> FaultEventRecord:
    return FaultEventRecord(
        event_id=event_id,
        asset_id=asset_id,
        fault_code=fault_code,
        fault_name=fault_name,
        timestamp=datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
        severity=severity,
        status=status,
    )


def _observation(
    observation_id: str,
    asset_id: str,
    observation_type: str,
    severity: str,
) -> ObservationRecord:
    return ObservationRecord(
        observation_id=observation_id,
        asset_id=asset_id,
        timestamp=datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
        type=observation_type,
        severity=severity,
        description="Observation description.",
        reported_by="operator",
    )


def _limit(
    operating_limit_id: str,
    model: str,
    metric: str,
    unit: str,
    *,
    normal_min: str | None = None,
    normal_max: str | None = None,
    warning_min: str | None = None,
    warning_max: str | None = None,
    critical_min: str | None = None,
    critical_max: str | None = None,
    rule_text: str,
) -> OperatingLimitRecord:
    return OperatingLimitRecord(
        operating_limit_id=operating_limit_id,
        model=model,
        metric=metric,
        unit=unit,
        normal_min=_decimal_or_none(normal_min),
        normal_max=_decimal_or_none(normal_max),
        warning_min=_decimal_or_none(warning_min),
        warning_max=_decimal_or_none(warning_max),
        critical_min=_decimal_or_none(critical_min),
        critical_max=_decimal_or_none(critical_max),
        rule_text=rule_text,
        source_type="synthetic_plant_config",
        provenance_note="Synthetic plant operating limit for the debug environment.",
    )


def _decimal_or_none(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _readings_by_metric(result: GetAssetStatusResult) -> dict[str, ClassifiedReading]:
    return {reading.metric: reading for reading in result.classified_readings}


def _all_inlet_pressure_readings_are_unclassified(*results: GetAssetStatusResult) -> bool:
    return all(
        _readings_by_metric(result)["inlet_pressure_bar"]
        == ClassifiedReading(
            metric="inlet_pressure_bar",
            value=cast(TelemetrySnapshotRecord, result.telemetry).inlet_pressure_bar,
            unit="bar",
            tier=None,
            operating_limit_id=None,
            rule_text=None,
        )
        for result in results
    )
