from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

import httpx
import pytest
import yaml
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.api import agent as agent_api
from maintenance_agent.api.agent import router as agent_router
from maintenance_agent.db.repositories.records import (
    AssetRecord,
    FaultEventRecord,
    MaintenanceEventRecord,
    ObservationRecord,
    OperatingLimitRecord,
    PlantPolicyRecord,
    WorkOrderRecord,
)
from maintenance_agent.llm.client import (
    LLMMessage,
    LLMResponse,
    LLMTool,
    LLMToolChoice,
    ToolCallRequest,
)
from maintenance_agent.orchestration import graph as graph_module
from maintenance_agent.orchestration.graph import AgentGraphDependencies, build_agent_graph
from maintenance_agent.orchestration.state import GraphState, WorkOrderDraft
from maintenance_agent.orchestration.tool_bindings import CANONICAL_TOOL_NAMES, CanonicalToolName
from maintenance_agent.schemas.agent import AgentQueryResponse, AgentStatus
from maintenance_agent.schemas.run_event import RunEvent
from maintenance_agent.tools.fault_recurrence import FaultRecurrence
from maintenance_agent.tools.get_asset_status import ClassifiedReading, GetAssetStatusResult
from maintenance_agent.tools.get_maintenance_history import GetMaintenanceHistoryResult
from maintenance_agent.tools.get_plant_policy import GetPlantPolicyResult
from maintenance_agent.tools.resolve_asset import ResolveAssetResult
from maintenance_agent.tools.search_maintenance_docs import (
    DocSearchHit,
    SearchMaintenanceDocsResult,
)

SCENARIOS_PATH = Path(__file__).with_name("scenarios.yaml")


class ApprovalStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["approve", "reject"]
    expected_status: Literal["ok"]
    required_tools_after_resume: list[CanonicalToolName]
    required_evidence_source_types_after_resume: list[str]


class GoldenScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    query: str
    asset_id_hint: str | None
    expected_intent: Literal[
        "troubleshooting",
        "maintenance_check",
        "history_query",
        "procedure_lookup",
        "work_order_request",
    ]
    expected_asset_id: str | None
    required_tools: list[CanonicalToolName]
    optional_tools: list[CanonicalToolName]
    expected_status: AgentStatus
    required_evidence_ids: list[str]
    hitl: bool
    approval_step: ApprovalStep | None


def load_golden_scenarios() -> list[GoldenScenario]:
    payload = yaml.safe_load(SCENARIOS_PATH.read_text(encoding="utf-8"))
    return [GoldenScenario.model_validate(item) for item in payload["scenarios"]]


@pytest.fixture
def emitted_events() -> list[RunEvent]:
    return []


@pytest.fixture(autouse=True)
def _stub_graph_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)
    monkeypatch.setattr(graph_module, "invoke_tool_binding", _fake_invoke_tool_binding)
    monkeypatch.setattr(graph_module, "submit_work_order", _fake_submit_work_order)


def test_scenarios_yaml_loads_and_validates_against_task_1_schema() -> None:
    scenarios = load_golden_scenarios()

    assert [scenario.id for scenario in scenarios] == [
        "GS-01",
        "GS-02",
        "GS-03",
        "GS-04",
        "GS-05",
        "GS-06",
        "GS-07",
        "GS-08",
    ]
    assert [scenario.id for scenario in scenarios if scenario.approval_step is not None] == [
        "GS-08"
    ]


@pytest.mark.parametrize("scenario", load_golden_scenarios(), ids=lambda scenario: scenario.id)
@pytest.mark.asyncio
async def test_task_1_golden_scenario_contracts(
    scenario: GoldenScenario,
    emitted_events: list[RunEvent],
) -> None:
    app = _app_for_scenario(scenario, emitted_events)
    request_payload = {"query": scenario.query}
    if scenario.asset_id_hint is not None:
        request_payload["asset_id"] = scenario.asset_id_hint

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        first_response = await client.post("/agent/query", json=request_payload)
        assert first_response.status_code == 200
        first_payload = AgentQueryResponse.model_validate(first_response.json())
        assert len(emitted_events) == 1
        _assert_turn_1_contract(scenario, first_payload, emitted_events[0])

        if scenario.approval_step is None:
            return

        assert first_payload.pending_action is not None
        draft_id = first_payload.pending_action.draft_id
        approval_response = await client.post(
            f"/agent/approvals/{draft_id}",
            json={"decision": scenario.approval_step.decision},
        )

    assert approval_response.status_code == 200
    approval_payload = AgentQueryResponse.model_validate(approval_response.json())
    assert len(emitted_events) == 2
    _assert_approval_contract(
        scenario.approval_step,
        approval_payload,
        emitted_events[0],
        emitted_events[1],
    )


def _assert_turn_1_contract(
    scenario: GoldenScenario,
    response: AgentQueryResponse,
    event: RunEvent,
) -> None:
    assert response.asset_id == scenario.expected_asset_id
    assert response.status == scenario.expected_status
    assert (response.status == "needs_approval") is scenario.hitl
    assert (response.pending_action is not None) is scenario.hitl
    assert event.final_output == response
    assert event.status == response.status
    _assert_evidence_contract(scenario, response)

    tool_names = [tool_call.tool_name for tool_call in event.tool_calls]
    assert tool_names[0] == "resolve_asset"
    if scenario.id == "GS-07":
        assert tool_names == ["resolve_asset"]
    _assert_tool_contract(
        tool_names,
        required_tools=scenario.required_tools,
        optional_tools=scenario.optional_tools,
    )


def _assert_approval_contract(
    approval_step: ApprovalStep,
    response: AgentQueryResponse,
    pause_event: RunEvent,
    resume_event: RunEvent,
) -> None:
    assert response.status == approval_step.expected_status
    assert response.pending_action is None
    assert pause_event.run_id == resume_event.run_id
    resume_tool_names = [tool_call.tool_name for tool_call in resume_event.tool_calls]
    for required_tool in approval_step.required_tools_after_resume:
        assert required_tool in resume_tool_names
    _assert_resume_evidence_contract(approval_step, pause_event.final_output, response)


def _assert_evidence_contract(
    scenario: GoldenScenario,
    response: AgentQueryResponse,
) -> None:
    if scenario.id == "GS-07":
        assert response.structured_evidence == []
        assert response.document_evidence == []

    evidence_ids = {
        item.source_id for item in response.structured_evidence if item.source_id is not None
    } | {item.document_id for item in response.document_evidence}
    assert set(scenario.required_evidence_ids) <= evidence_ids


def _assert_resume_evidence_contract(
    approval_step: ApprovalStep,
    pause_response: AgentQueryResponse,
    resume_response: AgentQueryResponse,
) -> None:
    pause_items = {
        (item.source_type, item.source_id)
        for item in pause_response.structured_evidence
        if item.source_type is not None and item.source_id is not None
    }
    resume_items = {
        (item.source_type, item.source_id)
        for item in resume_response.structured_evidence
        if item.source_type is not None and item.source_id is not None
    }
    new_items = resume_items - pause_items

    for source_type in approval_step.required_evidence_source_types_after_resume:
        assert len([item for item in new_items if item[0] == source_type]) == 1


def _assert_tool_contract(
    observed_tool_names: Sequence[str],
    *,
    required_tools: Sequence[str],
    optional_tools: Sequence[str],
) -> None:
    allowed_tools = set(required_tools) | set(optional_tools)
    forbidden_tools = set(CANONICAL_TOOL_NAMES) - allowed_tools

    for required_tool in required_tools:
        assert required_tool in observed_tool_names
    assert not (set(observed_tool_names) & forbidden_tools)


def _app_for_scenario(
    scenario: GoldenScenario,
    emitted_events: list[RunEvent],
) -> FastAPI:
    app = FastAPI()
    app.state.agent_graph = build_agent_graph(
        AgentGraphDependencies(
            llm_client=_RecordingLLMClient(_llm_responses_for_scenario(scenario)),
            max_retry_attempts=1,
        )
    )
    app.state.emit_run_event = _collecting_emitter(emitted_events)
    app.include_router(agent_router, prefix="/agent")
    return app


def _llm_responses_for_scenario(scenario: GoldenScenario) -> list[LLMResponse]:
    asset_identifier = scenario.expected_asset_id or "PUMP-999"
    responses = [_interpret_response(scenario.expected_intent, asset_identifier)]
    evidence_tools = _evidence_tools_for_scenario(scenario)
    if scenario.expected_status == "unknown_asset":
        return responses
    responses.extend(_evidence_response(tool_name, scenario) for tool_name in evidence_tools)
    if scenario.hitl:
        return responses
    return [
        *responses,
        LLMResponse(tool_calls=[]),
        _synthesis_response(_synthesis_evidence_id(scenario)),
    ]


def _evidence_tools_for_scenario(scenario: GoldenScenario) -> list[CanonicalToolName]:
    evidence_tools = [
        tool_name for tool_name in scenario.required_tools if tool_name != "resolve_asset"
    ]
    if scenario.id == "GS-02":
        evidence_tools.append("get_maintenance_history")
    return evidence_tools


def _synthesis_evidence_id(scenario: GoldenScenario) -> str:
    if scenario.required_evidence_ids:
        return scenario.required_evidence_ids[0]
    return "TS-001"


def _collecting_emitter(events: list[RunEvent]):
    async def emit(event: RunEvent) -> None:
        events.append(event)

    return emit


class _RecordingLLMClient:
    def __init__(self, responses: Sequence[LLMResponse]) -> None:
        self._responses = list(responses)

    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[LLMTool] | None = None,
        tool_choice: LLMToolChoice | None = None,
    ) -> LLMResponse:
        del messages, tools, tool_choice
        if not self._responses:
            raise AssertionError("Unexpected LLM call.")
        return self._responses.pop(0)


@asynccontextmanager
async def _fake_session_context() -> AsyncGenerator[AsyncSession]:
    yield cast(AsyncSession, object())


async def _fake_invoke_tool_binding(
    tool_name: str,
    args: dict[str, object],
    state: GraphState,
    session: AsyncSession,
) -> object:
    del session
    if tool_name == "resolve_asset":
        identifier = cast(str, args["identifier"])
        if identifier == "PUMP-999":
            return ResolveAssetResult(status="not_found")
        return ResolveAssetResult(status="resolved", asset=_asset(identifier))
    if tool_name == "get_asset_status":
        asset = cast(AssetRecord, state["asset"])
        return GetAssetStatusResult(
            asset=asset,
            telemetry=None,
            classified_readings=_classified_readings(asset.asset_id),
            active_faults=_active_faults(asset.asset_id),
            observations=_observations(asset.asset_id),
            operating_limits=_operating_limits(asset.asset_id),
        )
    if tool_name == "get_maintenance_history":
        asset = cast(AssetRecord, state["asset"])
        return GetMaintenanceHistoryResult(
            asset=asset,
            maintenance_events=_maintenance_events(asset.asset_id),
            fault_events=_history_faults(asset.asset_id),
            recurrence=_recurrence(asset.asset_id),
        )
    if tool_name == "search_maintenance_docs":
        return SearchMaintenanceDocsResult(
            query="maintenance docs",
            results=_doc_hits(cast(str, state["query"])),
        )
    if tool_name == "get_plant_policy":
        policy_type = cast(str, args["policy_type"])
        return GetPlantPolicyResult(policy_type=policy_type, policies=[_plant_policy(policy_type)])
    if tool_name == "create_work_order_draft":
        return WorkOrderDraft(
            draft_id=state.get("request_id", "test-request"),
            asset_id=cast(AssetRecord, state["asset"]).asset_id,
            issue=cast(str, args["issue"]),
            recommended_action=cast(str, args["recommended_action"]),
            priority=cast(Literal["low", "high"], args["priority"]),
            supporting_evidence=[],
        )
    raise AssertionError(f"Unexpected tool call: {tool_name}")


async def _fake_submit_work_order(
    draft: WorkOrderDraft,
    *,
    approval_status: str,
    session: AsyncSession,
) -> WorkOrderRecord:
    del session
    assert approval_status == "approved"
    return WorkOrderRecord(
        work_order_id="WO-003",
        asset_id=draft.asset_id,
        issue=draft.issue,
        priority=draft.priority,
        status="submitted",
        created_at=date(2026, 8, 20),
        approved=True,
    )


def _interpret_response(intent: str, asset_identifier: str) -> LLMResponse:
    return LLMResponse(
        tool_calls=[
            ToolCallRequest(
                id="interpret-1",
                name="interpret_request",
                input={
                    "intent": intent,
                    "asset_identifier": asset_identifier,
                },
            )
        ]
    )


def _evidence_response(tool_name: str, scenario: GoldenScenario) -> LLMResponse:
    return LLMResponse(
        tool_calls=[
            ToolCallRequest(
                id=f"{tool_name}-1",
                name=tool_name,
                input=_tool_input(tool_name, scenario),
            )
        ]
    )


def _tool_input(tool_name: str, scenario: GoldenScenario) -> dict[str, object]:
    if tool_name == "search_maintenance_docs":
        return {"query": "maintenance docs"}
    if tool_name == "get_plant_policy":
        return {
            "policy_type": "consequential_action"
            if scenario.id == "GS-08"
            else "recurring_fault"
        }
    if tool_name == "create_work_order_draft":
        return {
            "issue": "Recurring bearing overheating",
            "recommended_action": "Investigate recurring overheating before replacement.",
            "priority": "high",
        }
    return {}


def _synthesis_response(evidence_id: str) -> LLMResponse:
    return LLMResponse(
        tool_calls=[
            ToolCallRequest(
                id="synthesis-1",
                name="synthesize_response",
                input={
                    "answer": "Use the gathered evidence to inspect the asset.",
                    "confidence": "hypothesis",
                    "evidence_used": [evidence_id],
                },
            )
        ]
    )


def _asset(asset_id: str) -> AssetRecord:
    return AssetRecord(
        asset_id=asset_id,
        asset_type="centrifugal_pump",
        model="CP-200",
        location="Line 3",
        installation_date=date(2021, 6, 1),
        status="operational",
    )


def _classified_readings(asset_id: str) -> list[ClassifiedReading]:
    source_id = {
        "PUMP-101": "TS-001",
        "PUMP-102": "TS-002",
        "PUMP-103": "TS-003",
        "PUMP-104": "TS-004",
    }[asset_id]
    metric = "discharge_pressure_bar" if asset_id == "PUMP-104" else "bearing_temperature_c"
    return [
        ClassifiedReading(
            source_id=source_id,
            metric=metric,
            value=Decimal("3.9") if asset_id == "PUMP-104" else Decimal("91.0"),
            unit="bar" if asset_id == "PUMP-104" else "C",
            tier="critical" if asset_id in {"PUMP-103", "PUMP-104"} else "normal",
            operating_limit_id="OL-002" if asset_id == "PUMP-103" else "OL-001",
            rule_text="Task 2 golden evidence fixture.",
        )
    ]


def _active_faults(asset_id: str) -> list[FaultEventRecord]:
    return {
        "PUMP-102": [_fault("FE-001", "PUMP-102", "F101", "active")],
        "PUMP-103": [_fault("FE-004", "PUMP-103", "F102", "active")],
        "PUMP-104": [_fault("FE-005", "PUMP-104", "F103", "active")],
    }.get(asset_id, [])


def _history_faults(asset_id: str) -> list[FaultEventRecord]:
    return {
        "PUMP-103": [
            _fault("FE-002", "PUMP-103", "F102", "resolved"),
            _fault("FE-003", "PUMP-103", "F102", "resolved"),
            _fault("FE-004", "PUMP-103", "F102", "active"),
        ],
        "PUMP-104": [_fault("FE-005", "PUMP-104", "F103", "active")],
    }.get(asset_id, _active_faults(asset_id))


def _maintenance_events(asset_id: str) -> list[MaintenanceEventRecord]:
    return {
        "PUMP-102": [_maintenance("ME-003", "PUMP-102", "coupling")],
        "PUMP-103": [
            _maintenance("ME-006", "PUMP-103", "bearing"),
            _maintenance("ME-007", "PUMP-103", "bearing"),
            _maintenance("ME-008", "PUMP-103", "lubrication_system"),
        ],
        "PUMP-104": [
            _maintenance("ME-009", "PUMP-104", "mechanical_seal"),
            _maintenance("ME-010", "PUMP-104", "discharge_line"),
        ],
    }.get(asset_id, [])


def _observations(asset_id: str) -> list[ObservationRecord]:
    return {
        "PUMP-102": [_observation("OBS-002", "PUMP-102", "vibration")],
        "PUMP-104": [_observation("OBS-001", "PUMP-104", "seal_leak")],
    }.get(asset_id, [])


def _operating_limits(asset_id: str) -> list[OperatingLimitRecord]:
    if asset_id == "PUMP-102":
        return [_operating_limit("OL-001", "vibration_mm_s")]
    if asset_id == "PUMP-103":
        return [_operating_limit("OL-002", "bearing_temperature_c")]
    return []


def _recurrence(asset_id: str) -> list[FaultRecurrence]:
    if asset_id == "PUMP-103":
        return [
            FaultRecurrence(
                source_id="F102",
                fault_code="F102",
                total_occurrences=3,
                occurrences_within_window=3,
                meets_recurrence_threshold=True,
            )
        ]
    return []


def _doc_hits(query: str) -> list[DocSearchHit]:
    if "PUMP-103" in query:
        return [_doc_hit("DOC-04")]
    if "How should I inspect" in query:
        return [_doc_hit("DOC-01")]
    if "PUMP-104" in query:
        return [_doc_hit("DOC-01"), _doc_hit("DOC-02"), _doc_hit("DOC-05")]
    return [_doc_hit("DOC-03")]


def _doc_hit(document_id: str) -> DocSearchHit:
    return DocSearchHit(
        chunk_id=f"{document_id}-C1",
        document_id=document_id,
        section="Mechanical seal inspection",
        page="1",
        topic="seal inspection",
        manufacturer="Synthetic",
        source_product_family="CP",
        applicability="PUMP-103",
        source_url="synthetic://DOC-03",
        content_provenance="synthetic",
        linked_fault_codes=["F102"],
        evidence_text="Inspect the seal and bearing assembly.",
        similarity_score=0.9,
    )


def _fault(
    event_id: str,
    asset_id: str,
    fault_code: str,
    status: Literal["active", "resolved"],
) -> FaultEventRecord:
    return FaultEventRecord(
        event_id=event_id,
        asset_id=asset_id,
        fault_code=fault_code,
        fault_name="Golden scenario fault",
        timestamp=datetime(2026, 8, 13, 16, 40, tzinfo=UTC),
        severity="high",
        status=status,
    )


def _maintenance(
    maintenance_id: str,
    asset_id: str,
    component: str,
) -> MaintenanceEventRecord:
    return MaintenanceEventRecord(
        maintenance_id=maintenance_id,
        asset_id=asset_id,
        date=date(2026, 6, 1),
        type="corrective",
        component=component,
        description="Golden scenario maintenance event.",
    )


def _observation(
    observation_id: str,
    asset_id: str,
    observation_type: str,
) -> ObservationRecord:
    return ObservationRecord(
        observation_id=observation_id,
        asset_id=asset_id,
        timestamp=datetime(2026, 8, 14, 8, 15, tzinfo=UTC),
        type=observation_type,
        severity="medium",
        description="Golden scenario observation.",
        reported_by="operator",
    )


def _operating_limit(
    operating_limit_id: str,
    metric: str,
) -> OperatingLimitRecord:
    return OperatingLimitRecord(
        operating_limit_id=operating_limit_id,
        model="CP-200",
        metric=metric,
        unit="C" if metric == "bearing_temperature_c" else "mm/s",
        normal_min=None,
        normal_max=Decimal("82.0"),
        warning_min=None,
        warning_max=Decimal("90.0"),
        critical_min=Decimal("90.0"),
        critical_max=None,
        rule_text="Golden scenario operating limit.",
        source_type="manufacturer_reference_adopted",
        provenance_note="Golden scenario fixture.",
    )


def _plant_policy(policy_type: str) -> PlantPolicyRecord:
    if policy_type == "consequential_action":
        return PlantPolicyRecord(
            policy_id="PP-002",
            type=policy_type,
            condition="Work-order submission changes system state.",
            required_action="Human approval is required before final submission.",
        )
    return PlantPolicyRecord(
        policy_id="PP-001",
        type=policy_type,
        condition="Recurring faults require escalation.",
        required_action="Escalate recurring faults to root-cause review.",
    )
