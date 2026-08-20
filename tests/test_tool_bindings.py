from contextlib import asynccontextmanager
from datetime import date
from typing import Literal, cast

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.api import agent as agent_api
from maintenance_agent.api.agent import router as agent_router
from maintenance_agent.db.repositories.records import AssetRecord
from maintenance_agent.orchestration.state import GraphState, WorkOrderDraft
from maintenance_agent.orchestration.tool_bindings import (
    CANONICAL_TOOL_NAMES,
    LLM_OFFERED_TOOL_NAMES,
    TOOL_BINDINGS,
    TOOL_INPUT_MODELS,
    CreateWorkOrderDraftInput,
    GetAssetStatusInput,
    GetMaintenanceHistoryInput,
    GetPlantPolicyInput,
    ResolveAssetInput,
    SearchMaintenanceDocsInput,
    SubmitWorkOrderInput,
    ToolBinding,
    build_llm_tools,
    invoke_tool_binding,
)
from maintenance_agent.tools.get_asset_status import GetAssetStatusResult
from maintenance_agent.tools.get_maintenance_history import GetMaintenanceHistoryResult
from maintenance_agent.tools.get_plant_policy import GetPlantPolicyResult
from maintenance_agent.tools.resolve_asset import ResolveAssetResult
from maintenance_agent.tools.search_maintenance_docs import SearchMaintenanceDocsResult
from maintenance_agent.tools.submit_work_order import (
    ConsequentialActionGuardError,
    submit_work_order,
)


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


def test_only_submit_work_order_binding_is_consequential() -> None:
    consequential_bindings = [
        tool_name for tool_name, binding in TOOL_BINDINGS.items() if binding.consequential
    ]

    assert consequential_bindings == ["submit_work_order"]


def test_consequential_bindings_are_filtered_from_llm_tools() -> None:
    bindings = {
        **TOOL_BINDINGS,
        "get_asset_status": ToolBinding(
            GetAssetStatusInput,
            llm_description="Misconfigured consequential status tool.",
            consequential=True,
        ),
    }

    llm_tool_names = [
        tool.name
        for tool in build_llm_tools(
            ["get_asset_status", "search_maintenance_docs"],
            bindings=bindings,
        )
    ]

    assert llm_tool_names == ["search_maintenance_docs"]


def test_disallowed_tool_names_cannot_be_built_as_llm_tools() -> None:
    with pytest.raises(ValueError, match="resolve_asset"):
        build_llm_tools(["resolve_asset"])

    assert build_llm_tools(["submit_work_order"]) == []


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


def test_create_work_order_draft_input_rejects_invalid_priority_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CreateWorkOrderDraftInput.model_validate(
            {
                "issue": "Recurring bearing overheating",
                "recommended_action": "Investigate root cause.",
                "priority": "medium",
            }
        )

    with pytest.raises(ValidationError):
        CreateWorkOrderDraftInput.model_validate(
            {
                "issue": "Recurring bearing overheating",
                "recommended_action": "Investigate root cause.",
                "priority": "high",
                "approved": True,
            }
        )


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
async def test_create_work_order_draft_binding_injects_state_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(AsyncSession, object())
    asset = _asset()
    calls: list[tuple[str, str, str, AssetRecord, str, AsyncSession]] = []

    async def fake_create_work_order_draft(
        *,
        issue: str,
        recommended_action: str,
        priority: str,
        asset: AssetRecord,
        request_id: str,
        structured_evidence: list[object],
        document_evidence: list[object],
        session: AsyncSession,
    ) -> object:
        assert structured_evidence == ["structured"]
        assert document_evidence == ["document"]
        calls.append((issue, recommended_action, priority, asset, request_id, session))
        return WorkOrderDraft(
            draft_id=request_id,
            asset_id=asset.asset_id,
            issue=issue,
            recommended_action=recommended_action,
            priority=cast(Literal["low", "high"], priority),
            supporting_evidence=[],
        )

    monkeypatch.setattr(
        "maintenance_agent.orchestration.tool_bindings.create_work_order_draft",
        fake_create_work_order_draft,
    )

    result = await invoke_tool_binding(
        "create_work_order_draft",
        {
            "issue": "Recurring bearing overheating",
            "recommended_action": "Investigate root cause.",
            "priority": "high",
        },
        cast(
            GraphState,
            {
                **_state(asset),
                "request_id": "REQ-123",
                "structured_evidence": ["structured"],
                "document_evidence": ["document"],
            },
        ),
        session,
    )

    assert result is not None
    assert calls == [
        (
            "Recurring bearing overheating",
            "Investigate root cause.",
            "high",
            asset,
            "REQ-123",
            session,
        )
    ]


@pytest.mark.asyncio
async def test_submit_work_order_binding_keeps_guard_on_unapproved_state() -> None:
    draft = WorkOrderDraft(
        draft_id="DRAFT-001",
        asset_id="PUMP-103",
        issue="Recurring bearing overheating",
        recommended_action="Investigate root cause.",
        priority="high",
        supporting_evidence=[],
    )

    with pytest.raises(ConsequentialActionGuardError):
        await invoke_tool_binding(
            "submit_work_order",
            {"draft_id": "DRAFT-001"},
            cast(GraphState, {**_state(_asset()), "work_order_draft": draft}),
            cast(AsyncSession, object()),
        )


@pytest.mark.asyncio
async def test_submit_work_order_guard_requires_approved_status() -> None:
    with pytest.raises(ConsequentialActionGuardError, match="approval_status='approved'"):
        await submit_work_order(
            WorkOrderDraft(
                draft_id="DRAFT-001",
                asset_id="PUMP-103",
                issue="Recurring bearing overheating",
                recommended_action="Investigate root cause.",
                priority="high",
                supporting_evidence=[],
            ),
            approval_status="pending_approval",
            session=cast(AsyncSession, object()),
        )


@pytest.mark.asyncio
async def test_create_work_order_draft_does_not_use_consequential_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_work_order_draft(**kwargs: object) -> WorkOrderDraft:
        del kwargs
        return WorkOrderDraft(
            draft_id="REQ-000",
            asset_id="PUMP-103",
            issue="Recurring bearing overheating",
            recommended_action="Investigate root cause.",
            priority="high",
            supporting_evidence=[],
        )

    monkeypatch.setattr(
        "maintenance_agent.orchestration.tool_bindings.create_work_order_draft",
        fake_create_work_order_draft,
    )

    result = await invoke_tool_binding(
        "create_work_order_draft",
        {
            "issue": "Recurring bearing overheating",
            "recommended_action": "Investigate root cause.",
            "priority": "high",
        },
        _state(_asset()),
        cast(AsyncSession, object()),
    )

    assert result is not None


@pytest.mark.asyncio
async def test_consequential_guard_error_reaches_api_unhandled_exception_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class GuardFailingGraph:
        async def ainvoke(self, state: GraphState) -> GraphState:
            del state
            raise ConsequentialActionGuardError("approval guard tripped")

    @asynccontextmanager
    async def fake_request_session() -> object:
        yield cast(AsyncSession, object())

    monkeypatch.setattr(agent_api, "_request_session", fake_request_session)
    app = FastAPI()
    app.state.agent_graph = GuardFailingGraph()
    app.include_router(agent_router, prefix="/agent")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/agent/query",
            json={"query": "Submit the work order.", "asset_id": "PUMP-103"},
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "unhandled_exception"
    assert payload["error"]["code"] != "tool_execution_failed"


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
            "approval_status": "none",
        },
    )
