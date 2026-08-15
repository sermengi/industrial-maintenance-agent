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
    pump_102_observations = await observations.list_for_asset(session, "PUMP-102")
    pump_102_maintenance = await maintenance_events.list_for_asset(session, "PUMP-102")

    assert [fault.event_id for fault in pump_102_faults] == ["FE-001"]
    assert pump_102_faults[0].fault_code == "F101"
    assert [observation.observation_id for observation in pump_102_observations] == ["OBS-002"]
    assert "ME-003" in [event.maintenance_id for event in pump_102_maintenance]

    pump_103_f102_faults = await fault_events.list_by_asset_and_code(
        session,
        "PUMP-103",
        "F102",
    )
    assert isinstance(pump_103_f102_faults[0], FaultEventRecord)
    assert [fault.event_id for fault in pump_103_f102_faults] == ["FE-002", "FE-003", "FE-004"]

    pump_104_faults = await fault_events.list_active_for_asset(session, "PUMP-104")
    pump_104_observations = await observations.list_for_asset(session, "PUMP-104")
    all_faults = await fault_events.list_for_asset(session, "PUMP-104")

    assert [fault.event_id for fault in pump_104_faults] == ["FE-005"]
    assert [observation.observation_id for observation in pump_104_observations] == ["OBS-001"]
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
