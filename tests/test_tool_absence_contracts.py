from datetime import date
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories.records import AssetRecord
from maintenance_agent.tools.get_asset_status import (
    ClassifiedReading,
    GetAssetStatusResult,
    get_asset_status,
)
from maintenance_agent.tools.get_maintenance_history import (
    GetMaintenanceHistoryResult,
    get_maintenance_history,
)
from maintenance_agent.tools.get_plant_policy import GetPlantPolicyResult, get_plant_policy
from maintenance_agent.tools.resolve_asset import ResolveAssetResult, resolve_asset

RESULT_MODELS = [
    ResolveAssetResult,
    GetAssetStatusResult,
    GetMaintenanceHistoryResult,
    GetPlantPolicyResult,
]

LIST_FIELD_NAMES = {
    GetAssetStatusResult: [
        "classified_readings",
        "active_faults",
        "observations",
        "operating_limits",
    ],
    GetMaintenanceHistoryResult: [
        "maintenance_events",
        "fault_events",
        "work_orders",
        "recurrence",
    ],
    GetPlantPolicyResult: ["policies"],
}


@pytest.fixture
def session() -> AsyncSession:
    return cast(AsyncSession, object())


@pytest.fixture
def asset() -> AssetRecord:
    return AssetRecord(
        asset_id="PUMP-EMPTY",
        asset_type="centrifugal_pump",
        model="CP-200",
        location="Test Area",
        installation_date=date(2021, 1, 1),
        status="operational",
    )


def test_resolve_asset_is_the_only_tool_result_with_top_level_status() -> None:
    models_with_status = [
        model.__name__
        for model in RESULT_MODELS
        if "status" in model.model_fields
    ]

    assert models_with_status == ["ResolveAssetResult"]


@pytest.mark.asyncio
async def test_expected_business_absence_returns_typed_results_without_raising(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    asset: AssetRecord,
) -> None:
    async def missing_asset(_session: AsyncSession, _asset_id: str) -> None:
        return None

    async def missing_telemetry(_session: AsyncSession, _asset_id: str) -> None:
        return None

    async def empty_list(*_args: object) -> list[object]:
        return []

    monkeypatch.setattr(
        "maintenance_agent.tools.resolve_asset.assets.get_by_id",
        missing_asset,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.get_asset_status.telemetry.get_latest_for_asset",
        missing_telemetry,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.get_asset_status.fault_events.list_active_for_asset",
        empty_list,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.get_asset_status.observations.list_for_asset",
        empty_list,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.get_asset_status.operating_limits.list_for_model",
        empty_list,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.get_maintenance_history.maintenance_events.list_for_asset",
        empty_list,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.get_maintenance_history.fault_events.list_for_asset",
        empty_list,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.get_maintenance_history.work_orders.list_for_asset",
        empty_list,
    )
    monkeypatch.setattr(
        "maintenance_agent.tools.get_plant_policy.plant_policies.list_by_type",
        empty_list,
    )

    assert await resolve_asset("PUMP-999", session) == ResolveAssetResult(status="not_found")

    status = await get_asset_status(asset, session)
    assert status.telemetry is None
    assert status.classified_readings == []
    assert status.active_faults == []
    assert status.observations == []
    assert status.operating_limits == []

    history = await get_maintenance_history(asset, session)
    assert history.maintenance_events == []
    assert history.fault_events == []
    assert history.work_orders == []
    assert history.recurrence == []

    assert await get_plant_policy("missing_type", session) == GetPlantPolicyResult(
        policy_type="missing_type",
        policies=[],
    )


def test_missing_metric_classification_uses_nullable_fields_not_normal_default() -> None:
    reading = ClassifiedReading(
        metric="inlet_pressure_bar",
        value="2.40",
        unit="bar",
        tier=None,
        operating_limit_id=None,
        rule_text=None,
    )

    assert reading.tier is None
    assert reading.operating_limit_id is None
    assert reading.rule_text is None


@pytest.mark.asyncio
async def test_infrastructure_failures_propagate_as_exceptions(asset: AssetRecord) -> None:
    broken_session = cast(AsyncSession, object())

    with pytest.raises(AttributeError):
        await resolve_asset("PUMP-101", broken_session)

    with pytest.raises(AttributeError):
        await get_asset_status(asset, broken_session)

    with pytest.raises(AttributeError):
        await get_maintenance_history(asset, broken_session)

    with pytest.raises(AttributeError):
        await get_plant_policy("recurring_fault", broken_session)


def test_list_fields_have_no_redundant_empty_boolean_or_status_flags() -> None:
    redundant_prefixes = ("has_", "is_empty_", "has_no_")
    redundant_suffixes = ("_empty", "_status")

    for model, list_field_names in LIST_FIELD_NAMES.items():
        scalar_field_names = set(model.model_fields) - set(list_field_names)

        for list_field_name in list_field_names:
            assert f"{list_field_name}_status" not in scalar_field_names
            assert f"has_{list_field_name}" not in scalar_field_names
            assert f"is_empty_{list_field_name}" not in scalar_field_names

        assert all(
            not field_name.startswith(redundant_prefixes)
            and not field_name.endswith(redundant_suffixes)
            for field_name in scalar_field_names
        )
