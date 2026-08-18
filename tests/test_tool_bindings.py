from datetime import date
from typing import cast

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories.records import AssetRecord
from maintenance_agent.orchestration.state import GraphState
from maintenance_agent.orchestration.tool_bindings import (
    CANONICAL_TOOL_NAMES,
    LLM_OFFERED_TOOL_NAMES,
    TOOL_INPUT_MODELS,
    CreateWorkOrderDraftInput,
    GetAssetStatusInput,
    GetMaintenanceHistoryInput,
    GetPlantPolicyInput,
    ResolveAssetInput,
    SearchMaintenanceDocsInput,
    SubmitWorkOrderInput,
    build_llm_tools,
    invoke_tool_binding,
)
from maintenance_agent.tools.get_asset_status import GetAssetStatusResult
from maintenance_agent.tools.get_maintenance_history import GetMaintenanceHistoryResult
from maintenance_agent.tools.get_plant_policy import GetPlantPolicyResult
from maintenance_agent.tools.resolve_asset import ResolveAssetResult
from maintenance_agent.tools.search_maintenance_docs import SearchMaintenanceDocsResult


def test_all_seven_canonical_tool_names_are_declared() -> None:
    assert CANONICAL_TOOL_NAMES == (
        "resolve_asset",
        "get_asset_status",
        "get_maintenance_history",
        "search_maintenance_docs",
        "get_plant_policy",
        "create_work_order_draft",
        "submit_work_order",
    )


def test_each_canonical_tool_has_a_dedicated_input_model() -> None:
    assert TOOL_INPUT_MODELS == {
        "resolve_asset": ResolveAssetInput,
        "get_asset_status": GetAssetStatusInput,
        "get_maintenance_history": GetMaintenanceHistoryInput,
        "search_maintenance_docs": SearchMaintenanceDocsInput,
        "get_plant_policy": GetPlantPolicyInput,
        "create_work_order_draft": CreateWorkOrderDraftInput,
        "submit_work_order": SubmitWorkOrderInput,
    }


def test_resolve_asset_and_submit_work_order_are_not_llm_offered() -> None:
    assert "resolve_asset" not in LLM_OFFERED_TOOL_NAMES
    assert "submit_work_order" not in LLM_OFFERED_TOOL_NAMES

    llm_tool_names = [tool.name for tool in build_llm_tools()]
    assert llm_tool_names == list(LLM_OFFERED_TOOL_NAMES)
    assert "resolve_asset" not in llm_tool_names
    assert "submit_work_order" not in llm_tool_names


def test_disallowed_tool_names_cannot_be_built_as_llm_tools() -> None:
    with pytest.raises(ValueError, match="resolve_asset"):
        build_llm_tools(["resolve_asset"])

    with pytest.raises(ValueError, match="submit_work_order"):
        build_llm_tools(["submit_work_order"])


def test_llm_tool_schemas_are_generated_from_dedicated_input_models() -> None:
    tools = {tool.name: tool for tool in build_llm_tools()}

    for tool_name in LLM_OFFERED_TOOL_NAMES:
        assert tools[tool_name].input_schema == TOOL_INPUT_MODELS[tool_name].model_json_schema()


def test_asset_scoped_llm_input_models_have_zero_fields() -> None:
    assert GetAssetStatusInput.model_fields == {}
    assert GetMaintenanceHistoryInput.model_fields == {}
    assert GetAssetStatusInput.model_json_schema()["properties"] == {}
    assert GetMaintenanceHistoryInput.model_json_schema()["properties"] == {}


def test_llm_input_models_reject_extra_llm_supplied_context() -> None:
    with pytest.raises(ValidationError):
        GetAssetStatusInput.model_validate({"asset_id": "PUMP-103"})

    with pytest.raises(ValidationError):
        GetMaintenanceHistoryInput.model_validate({"asset_id": "PUMP-103"})


@pytest.mark.asyncio
async def test_resolve_asset_binding_maps_identifier_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, AsyncSession]] = []
    session = cast(AsyncSession, object())

    async def fake_resolve_asset(identifier: str, session: AsyncSession) -> ResolveAssetResult:
        calls.append((identifier, session))
        return ResolveAssetResult(status="not_found")

    monkeypatch.setattr(
        "maintenance_agent.orchestration.tool_bindings.resolve_asset",
        fake_resolve_asset,
    )

    result = await invoke_tool_binding(
        "resolve_asset",
        {"identifier": "PUMP-999"},
        _state(),
        session,
    )

    assert result == ResolveAssetResult(status="not_found")
    assert calls == [("PUMP-999", session)]


@pytest.mark.asyncio
async def test_asset_scoped_bindings_inject_asset_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = _asset()
    session = cast(AsyncSession, object())
    calls: list[tuple[str, AssetRecord, AsyncSession]] = []

    async def fake_get_asset_status(
        asset: AssetRecord,
        session: AsyncSession,
    ) -> GetAssetStatusResult:
        calls.append(("get_asset_status", asset, session))
        return GetAssetStatusResult(asset=asset, telemetry=None)

    async def fake_get_maintenance_history(
        asset: AssetRecord,
        session: AsyncSession,
    ) -> GetMaintenanceHistoryResult:
        calls.append(("get_maintenance_history", asset, session))
        return GetMaintenanceHistoryResult(asset=asset)

    monkeypatch.setattr(
        "maintenance_agent.orchestration.tool_bindings.get_asset_status",
        fake_get_asset_status,
    )
    monkeypatch.setattr(
        "maintenance_agent.orchestration.tool_bindings.get_maintenance_history",
        fake_get_maintenance_history,
    )

    status = await invoke_tool_binding("get_asset_status", {}, _state(asset), session)
    history = await invoke_tool_binding("get_maintenance_history", {}, _state(asset), session)

    assert status == GetAssetStatusResult(asset=asset, telemetry=None)
    assert history == GetMaintenanceHistoryResult(asset=asset)
    assert calls == [
        ("get_asset_status", asset, session),
        ("get_maintenance_history", asset, session),
    ]


@pytest.mark.asyncio
async def test_query_scoped_bindings_map_llm_args_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(AsyncSession, object())
    calls: list[tuple[str, str, AsyncSession]] = []

    async def fake_search_maintenance_docs(
        query: str,
        session: AsyncSession,
    ) -> SearchMaintenanceDocsResult:
        calls.append(("search_maintenance_docs", query, session))
        return SearchMaintenanceDocsResult(query=query)

    async def fake_get_plant_policy(
        policy_type: str,
        session: AsyncSession,
    ) -> GetPlantPolicyResult:
        calls.append(("get_plant_policy", policy_type, session))
        return GetPlantPolicyResult(policy_type=policy_type)

    monkeypatch.setattr(
        "maintenance_agent.orchestration.tool_bindings.search_maintenance_docs",
        fake_search_maintenance_docs,
    )
    monkeypatch.setattr(
        "maintenance_agent.orchestration.tool_bindings.get_plant_policy",
        fake_get_plant_policy,
    )

    docs = await invoke_tool_binding(
        "search_maintenance_docs",
        {"query": "bearing overheating"},
        _state(),
        session,
    )
    policy = await invoke_tool_binding(
        "get_plant_policy",
        {"policy_type": "recurring_fault"},
        _state(),
        session,
    )

    assert docs == SearchMaintenanceDocsResult(query="bearing overheating")
    assert policy == GetPlantPolicyResult(policy_type="recurring_fault")
    assert calls == [
        ("search_maintenance_docs", "bearing overheating", session),
        ("get_plant_policy", "recurring_fault", session),
    ]


@pytest.mark.asyncio
async def test_phase_six_tools_are_reserved_not_executed_via_current_bindings() -> None:
    session = cast(AsyncSession, object())

    with pytest.raises(NotImplementedError, match="Phase 6"):
        await invoke_tool_binding(
            "create_work_order_draft",
            {"issue": "Recurring bearing overheating", "priority": "high"},
            _state(_asset()),
            session,
        )

    with pytest.raises(NotImplementedError, match="Phase 6"):
        await invoke_tool_binding(
            "submit_work_order",
            {"draft_id": "DRAFT-001"},
            _state(_asset()),
            session,
        )


def _asset() -> AssetRecord:
    return AssetRecord(
        asset_id="PUMP-103",
        asset_type="centrifugal_pump",
        model="CP-200",
        location="Line 3",
        installation_date=date(2021, 6, 1),
        status="operational",
    )


def _state(asset: AssetRecord | None = None) -> GraphState:
    return cast(
        GraphState,
        {
            "query": "Diagnose PUMP-103.",
            "asset_id_hint": None,
            "fault_code_hint": None,
            "asset": asset,
        },
    )
