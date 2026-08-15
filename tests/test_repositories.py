import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from inspect import signature

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from maintenance_agent.core.config import get_settings
from maintenance_agent.db.bootstrap import reset_database
from maintenance_agent.db.models import TelemetrySnapshot
from maintenance_agent.db.repositories import (
    assets,
    fault_events,
    fault_taxonomy,
    maintenance_events,
    observations,
    operating_limits,
    plant_policies,
    telemetry,
    work_orders,
)
from maintenance_agent.db.repositories.records import AssetRecord, FaultEventRecord


async def dump_queryable_repository_records(
    session: AsyncSession,
) -> dict[str, list[dict[str, object]]]:
    asset_ids = ["PUMP-101", "PUMP-102", "PUMP-103", "PUMP-104"]

    asset_records = [await assets.get_by_id(session, asset_id) for asset_id in asset_ids]
    telemetry_records = [
        await telemetry.get_latest_for_asset(session, asset_id) for asset_id in asset_ids
    ]
    fault_records = [
        fault
        for asset_id in asset_ids
        for fault in await fault_events.list_for_asset(session, asset_id)
    ]
    maintenance_records = [
        event
        for asset_id in asset_ids
        for event in await maintenance_events.list_for_asset(session, asset_id)
    ]
    observation_records = [
        observation
        for asset_id in asset_ids
        for observation in await observations.list_for_asset(session, asset_id)
    ]
    work_order_records = [
        order
        for asset_id in asset_ids
        for order in await work_orders.list_for_asset(session, asset_id)
    ]
    taxonomy_records = await fault_taxonomy.list_all(session)
    limit_records = [
        *await operating_limits.list_for_model(session, "CP-200"),
        *await operating_limits.list_for_model(session, "CP-300"),
    ]
    policy_records = [
        *await plant_policies.list_by_type(session, "recurring_fault"),
        *await plant_policies.list_by_type(session, "consequential_action"),
    ]

    assert all(record is not None for record in asset_records)
    assert all(record is not None for record in telemetry_records)

    return {
        "assets": [record.model_dump(mode="json") for record in asset_records if record],
        "telemetry_snapshots": [
            record.model_dump(mode="json") for record in telemetry_records if record
        ],
        "fault_events": [record.model_dump(mode="json") for record in fault_records],
        "maintenance_events": [record.model_dump(mode="json") for record in maintenance_records],
        "observations": [record.model_dump(mode="json") for record in observation_records],
        "work_orders": [record.model_dump(mode="json") for record in work_order_records],
        "fault_taxonomy": [record.model_dump(mode="json") for record in taxonomy_records],
        "operating_limits": [record.model_dump(mode="json") for record in limit_records],
        "plant_policies": [record.model_dump(mode="json") for record in policy_records],
    }


async def count_queryable_repository_records(session: AsyncSession) -> int:
    records_by_table = await dump_queryable_repository_records(session)
    return sum(
        len(records) for records in records_by_table.values()
    )


def test_repository_modules_expose_locked_function_surface() -> None:
    expected_functions = {
        assets: {"get_by_id"},
        telemetry: {"get_latest_for_asset"},
        fault_events: {
            "list_active_for_asset",
            "list_for_asset",
            "list_by_asset_and_code",
        },
        maintenance_events: {"list_for_asset"},
        observations: {"list_for_asset"},
        work_orders: {"list_for_asset"},
        fault_taxonomy: {"get_by_code", "list_all"},
        operating_limits: {"list_for_model"},
        plant_policies: {"get_by_id", "list_by_type"},
    }

    for module, function_names in expected_functions.items():
        assert {
            name
            for name in dir(module)
            if not name.startswith("_") and callable(getattr(module, name))
        } >= function_names

        for function_name in function_names:
            parameters = signature(getattr(module, function_name)).parameters
            first_parameter = next(iter(parameters.values()))
            assert first_parameter.name == "session"
            assert first_parameter.annotation is AsyncSession


@pytest.fixture(scope="module")
def test_database_url() -> str:
    if os.getenv("RUN_DB_INTEGRATION") != "1":
        pytest.skip("Set RUN_DB_INTEGRATION=1 to run repository integration tests.")

    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")

    return database_url


@pytest_asyncio.fixture
async def session(test_database_url: str) -> AsyncIterator[AsyncSession]:
    await reset_database(test_database_url)
    engine = create_async_engine(test_database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db_session:
            yield db_session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_asset_repository_returns_read_model_and_unknown_asset_none(
    session: AsyncSession,
) -> None:
    asset = await assets.get_by_id(session, "PUMP-101")
    missing_asset = await assets.get_by_id(session, "PUMP-999")

    assert isinstance(asset, AssetRecord)
    assert asset.asset_id == "PUMP-101"
    assert asset.status == "operational"
    assert missing_asset is None


@pytest.mark.asyncio
async def test_repository_queries_match_asset_ground_truth(session: AsyncSession) -> None:
    pump_101_faults = await fault_events.list_active_for_asset(session, "PUMP-101")
    pump_101_telemetry = await telemetry.get_latest_for_asset(session, "PUMP-101")
    pump_101_maintenance = await maintenance_events.list_for_asset(session, "PUMP-101")

    assert pump_101_faults == []
    assert pump_101_telemetry is not None
    assert pump_101_telemetry.vibration_mm_s == Decimal("2.10")
    assert pump_101_telemetry.bearing_temperature_c == Decimal("54.00")
    assert [event.maintenance_id for event in pump_101_maintenance] == ["ME-001", "ME-002"]

    pump_102_faults = await fault_events.list_active_for_asset(session, "PUMP-102")
    pump_102_telemetry = await telemetry.get_latest_for_asset(session, "PUMP-102")
    pump_102_observations = await observations.list_for_asset(session, "PUMP-102")
    pump_102_maintenance = await maintenance_events.list_for_asset(session, "PUMP-102")

    assert [fault.event_id for fault in pump_102_faults] == ["FE-001"]
    assert pump_102_faults[0].fault_code == "F101"
    assert pump_102_telemetry is not None
    assert pump_102_telemetry.vibration_mm_s == Decimal("8.10")
    assert [observation.observation_id for observation in pump_102_observations] == ["OBS-002"]
    assert pump_102_observations[0].type == "abnormal_vibration"
    pump_102_maintenance_by_id = {
        event.maintenance_id: event for event in pump_102_maintenance
    }
    assert "ME-003" in pump_102_maintenance_by_id
    assert pump_102_maintenance_by_id["ME-003"].type == "corrective"
    assert pump_102_maintenance_by_id["ME-003"].component == "coupling"

    pump_103_f102_faults = await fault_events.list_by_asset_and_code(
        session,
        "PUMP-103",
        "F102",
    )
    pump_103_telemetry = await telemetry.get_latest_for_asset(session, "PUMP-103")
    pump_103_maintenance = await maintenance_events.list_for_asset(session, "PUMP-103")
    cp_200_limits = await operating_limits.list_for_model(session, "CP-200")

    assert isinstance(pump_103_f102_faults[0], FaultEventRecord)
    assert [fault.event_id for fault in pump_103_f102_faults] == ["FE-002", "FE-003", "FE-004"]
    assert [fault.status for fault in pump_103_f102_faults] == ["resolved", "resolved", "active"]
    assert pump_103_telemetry is not None
    assert pump_103_telemetry.bearing_temperature_c == Decimal("91.00")
    ol_002 = next(limit for limit in cp_200_limits if limit.operating_limit_id == "OL-002")
    assert ol_002.critical_min == Decimal("82.00")
    assert [
        event.maintenance_id
        for event in pump_103_maintenance
        if event.type == "corrective" and event.component == "bearing"
    ] == ["ME-006", "ME-007"]

    pump_104_faults = await fault_events.list_active_for_asset(session, "PUMP-104")
    pump_104_telemetry = await telemetry.get_latest_for_asset(session, "PUMP-104")
    pump_104_observations = await observations.list_for_asset(session, "PUMP-104")
    all_faults = await fault_events.list_for_asset(session, "PUMP-104")
    cp_300_limits = await operating_limits.list_for_model(session, "CP-300")

    assert [fault.event_id for fault in pump_104_faults] == ["FE-005"]
    assert pump_104_faults[0].fault_code == "F103"
    assert pump_104_telemetry is not None
    assert pump_104_telemetry.discharge_pressure_bar == Decimal("3.90")
    assert pump_104_telemetry.flow_rate_l_min == Decimal("61.00")
    limits_by_id = {limit.operating_limit_id: limit for limit in cp_300_limits}
    assert limits_by_id["OL-003"].critical_max == Decimal("4.00")
    assert limits_by_id["OL-004"].critical_max == Decimal("70.00")
    assert [observation.observation_id for observation in pump_104_observations] == ["OBS-001"]
    assert pump_104_observations[0].type == "seal_leak"
    assert all(fault.fault_code != "F104" for fault in all_faults)


@pytest.mark.asyncio
async def test_reference_repositories_return_expected_rows(session: AsyncSession) -> None:
    taxonomy = await fault_taxonomy.list_all(session)
    f104 = await fault_taxonomy.get_by_code(session, "F104")
    cp_200_limits = await operating_limits.list_for_model(session, "CP-200")
    recurring_policies = await plant_policies.list_by_type(session, "recurring_fault")
    policy = await plant_policies.get_by_id(session, "PP-002")
    pump_103_work_orders = await work_orders.list_for_asset(session, "PUMP-103")

    assert [record.fault_code for record in taxonomy] == ["F101", "F102", "F103", "F104"]
    assert f104 is not None
    assert f104.canonical_name == "SEAL_LEAK_DETECTED"
    assert [limit.operating_limit_id for limit in cp_200_limits] == ["OL-001", "OL-002"]
    assert cp_200_limits[1].source_type == "manufacturer_reference_adopted"
    assert (
        "manufacturer reference adopted by the synthetic plant"
        in cp_200_limits[1].provenance_note
    )
    assert (
        "not presented as a literal CP-200 manufacturer specification"
        in cp_200_limits[1].provenance_note
    )
    assert [record.policy_id for record in recurring_policies] == ["PP-001"]
    assert policy is not None
    assert policy.type == "consequential_action"
    assert [order.work_order_id for order in pump_103_work_orders] == ["WO-002"]


@pytest.mark.asyncio
async def test_latest_telemetry_orders_by_timestamp(session: AsyncSession) -> None:
    session.add(
        TelemetrySnapshot(
            snapshot_id="TS-999",
            asset_id="PUMP-101",
            timestamp=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
            vibration_mm_s=Decimal("3.30"),
            bearing_temperature_c=Decimal("55.00"),
            inlet_pressure_bar=Decimal("2.40"),
            discharge_pressure_bar=Decimal("6.80"),
            flow_rate_l_min=Decimal("98.00"),
        )
    )
    await session.commit()

    latest_snapshot = await telemetry.get_latest_for_asset(session, "PUMP-101")

    assert latest_snapshot is not None
    assert latest_snapshot.snapshot_id == "TS-999"
    assert latest_snapshot.vibration_mm_s == Decimal("3.30")


@pytest.mark.asyncio
async def test_repository_layer_exposes_all_frozen_phase_1_records(
    session: AsyncSession,
) -> None:
    assert await count_queryable_repository_records(session) == 37


@pytest.mark.asyncio
async def test_reset_database_is_idempotent_for_seeded_test_database(
    session: AsyncSession,
    test_database_url: str,
) -> None:
    seeded_state = await dump_queryable_repository_records(session)
    assert sum(len(records) for records in seeded_state.values()) == 37

    await session.rollback()
    await reset_database(test_database_url)
    assert await dump_queryable_repository_records(session) == seeded_state

    await session.rollback()
    await reset_database(test_database_url)
    assert await dump_queryable_repository_records(session) == seeded_state
