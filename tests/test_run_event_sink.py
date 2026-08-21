from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.api import agent as agent_api
from maintenance_agent.api.agent import router as agent_router
from maintenance_agent.core.config import Settings, get_settings
from maintenance_agent.db.repositories.records import AssetRecord, WorkOrderRecord
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
from maintenance_agent.schemas.agent import AgentQueryResponse
from maintenance_agent.schemas.run_event import RunEvent
from maintenance_agent.telemetry.run_events import make_jsonl_emitter, read_run_events
from maintenance_agent.tools.get_asset_status import ClassifiedReading, GetAssetStatusResult
from maintenance_agent.tools.get_maintenance_history import GetMaintenanceHistoryResult
from maintenance_agent.tools.get_plant_policy import GetPlantPolicyResult
from maintenance_agent.tools.resolve_asset import ResolveAssetResult
from maintenance_agent.tools.search_maintenance_docs import (
    DocSearchHit,
    SearchMaintenanceDocsResult,
)


@pytest.mark.asyncio
async def test_jsonl_emitter_creates_parent_directory_and_appends_events(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "events.jsonl"
    emit = make_jsonl_emitter(path)

    await emit(_run_event("event-1"))
    await emit(_run_event("event-2"))

    assert path.exists()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    assert [event.run_id for event in read_run_events(path)] == ["event-1", "event-2"]


@pytest.mark.asyncio
async def test_jsonl_emitter_raises_write_failures(tmp_path: Path) -> None:
    path = tmp_path / "events-as-directory"
    path.mkdir()
    emit = make_jsonl_emitter(path)

    with pytest.raises(OSError):
        await emit(_run_event("event-1"))


def test_read_run_events_validates_each_jsonl_line(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    event = _run_event("event-1")
    path.write_text(event.model_dump_json() + "\n", encoding="utf-8")

    assert read_run_events(path) == [event]


def test_settings_include_run_events_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    get_settings.cache_clear()
    configured_path = tmp_path / "configured" / "events.jsonl"
    monkeypatch.delenv("RUN_EVENTS_PATH", raising=False)

    try:
        assert Settings(RUN_EVENTS_PATH=str(configured_path)).run_events_path == configured_path
        assert get_settings().run_events_path.parent == Path("run-events")
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_agent_query_writes_run_event_to_real_jsonl_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "events" / "run-events.jsonl"
    app = FastAPI()
    app.state.agent_graph = _FakeGraph()
    app.state.emit_run_event = make_jsonl_emitter(path)
    app.state.run_event_clock = _SequenceClock(
        [
            datetime(2026, 8, 21, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 10, 0, 0, 250000, tzinfo=UTC),
        ]
    )
    app.include_router(agent_router, prefix="/agent")
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/agent/query", json={"query": "Check pump vibration."})

    assert response.status_code == 200
    events = read_run_events(path)
    assert len(events) == 1
    assert events[0].request == "Check pump vibration."
    assert events[0].status == "ok"
    assert events[0].latency_ms == 250
    assert events[0].final_output == AgentQueryResponse.model_validate(response.json())


@pytest.mark.parametrize(
    ("query", "asset_id", "intent", "evidence_tools", "expected_status"),
    [
        (
            "PUMP-102 has an active high-vibration fault. What should I inspect first?",
            "PUMP-102",
            "troubleshooting",
            ["get_asset_status", "search_maintenance_docs", "get_maintenance_history"],
            "ok",
        ),
        (
            "PUMP-102 is vibrating much more than usual. What could be wrong?",
            "PUMP-102",
            "troubleshooting",
            ["get_asset_status", "search_maintenance_docs", "get_maintenance_history"],
            "ok",
        ),
        (
            "PUMP-101 seems to be overheating. What maintenance should we perform?",
            "PUMP-101",
            "troubleshooting",
            ["get_asset_status"],
            "ok",
        ),
        (
            "PUMP-103 is overheating again. What should we do?",
            "PUMP-103",
            "troubleshooting",
            [
                "get_asset_status",
                "get_maintenance_history",
                "search_maintenance_docs",
                "get_plant_policy",
            ],
            "ok",
        ),
        (
            "Why is PUMP-104 producing low discharge pressure?",
            "PUMP-104",
            "troubleshooting",
            ["get_asset_status", "get_maintenance_history", "search_maintenance_docs"],
            "ok",
        ),
        (
            "How should I inspect the mechanical seal on PUMP-104?",
            "PUMP-104",
            "procedure_lookup",
            ["search_maintenance_docs"],
            "ok",
        ),
        (
            "PUMP-999 has high vibration. Diagnose it.",
            "PUMP-999",
            "troubleshooting",
            [],
            "unknown_asset",
        ),
    ],
)
@pytest.mark.asyncio
async def test_non_hitl_golden_scenarios_emit_one_run_event_to_jsonl_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    query: str,
    asset_id: str,
    intent: str,
    evidence_tools: list[str],
    expected_status: str,
) -> None:
    path = tmp_path / "events.jsonl"
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)
    monkeypatch.setattr(graph_module, "invoke_tool_binding", _fake_invoke_tool_binding)
    llm_client = _RecordingLLMClient(
        [
            _interpret_response(intent, asset_id),
            *[_evidence_response(tool_name) for tool_name in evidence_tools],
            *(
                []
                if expected_status == "unknown_asset"
                else [
                    LLMResponse(tool_calls=[]),
                    _synthesis_response(
                        "DOC-03" if "search_maintenance_docs" in evidence_tools else "TS-001"
                    ),
                ]
            ),
        ]
    )
    app = _app_with_graph_and_jsonl_sink(
        build_agent_graph(AgentGraphDependencies(llm_client=llm_client)),
        path,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/agent/query", json={"query": query})

    assert response.status_code == 200
    events = read_run_events(path)
    assert len(events) == 1
    assert events[0].run_id == response.json()["request_id"]
    assert events[0].status == expected_status
    assert events[0].request == query
    assert events[0].final_output == AgentQueryResponse.model_validate(response.json())


@pytest.mark.parametrize("decision", ["approve", "reject"])
@pytest.mark.asyncio
async def test_hitl_golden_scenario_emits_pause_and_resume_events_to_jsonl_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    decision: Literal["approve", "reject"],
) -> None:
    path = tmp_path / "events.jsonl"
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)
    monkeypatch.setattr(graph_module, "invoke_tool_binding", _fake_invoke_tool_binding)
    monkeypatch.setattr(graph_module, "submit_work_order", _fake_submit_work_order)
    graph = build_agent_graph(
        AgentGraphDependencies(
            llm_client=_RecordingLLMClient(
                [
                    _interpret_response("work_order_request", "PUMP-103"),
                    _evidence_response("get_asset_status"),
                    _evidence_response("get_maintenance_history"),
                    _evidence_response("search_maintenance_docs"),
                    _evidence_response("get_plant_policy", policy_type="consequential_action"),
                    _draft_response(),
                ]
            )
        )
    )
    app = _app_with_graph_and_jsonl_sink(graph, path)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        pause_response = await client.post(
            "/agent/query",
            json={"query": "Create and submit a work order for PUMP-103."},
        )
        draft_id = pause_response.json()["pending_action"]["draft_id"]
        resume_response = await client.post(
            f"/agent/approvals/{draft_id}",
            json={"decision": decision},
        )

    assert pause_response.status_code == 200
    assert resume_response.status_code == 200
    events = read_run_events(path)
    assert len(events) == 2
    assert [event.status for event in events] == ["needs_approval", "ok"]
    assert events[0].run_id == events[1].run_id == draft_id
    assert events[0].event_id != events[1].event_id
    assert events[0].request == "Create and submit a work order for PUMP-103."
    assert events[1].request == decision
    if decision == "approve":
        assert events[1].final_output.structured_evidence[-1].source_type == "work_order"
    else:
        assert "work_order" not in {
            evidence.source_type for evidence in events[1].final_output.structured_evidence
        }


@pytest.mark.asyncio
async def test_unhandled_internal_error_still_emits_one_error_run_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)
    app = _app_with_graph_and_jsonl_sink(_FailingGraph(), path)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/agent/query",
            json={"query": "Check pump vibration.", "asset_id": "PUMP-103"},
        )

    assert response.status_code == 200
    events = read_run_events(path)
    assert len(events) == 1
    assert events[0].status == "error"
    assert events[0].error == AgentQueryResponse.model_validate(response.json()).error
    assert events[0].error is not None
    assert events[0].error.code == "unhandled_exception"


def _run_event(run_id: str) -> RunEvent:
    return RunEvent(
        event_id=UUID("11111111-1111-4111-8111-111111111111"),
        run_id=run_id,
        emitted_at=datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        latency_ms=10,
        status="ok",
        request="Check PUMP-103.",
        tool_calls=[],
        final_output=AgentQueryResponse(request_id=run_id, status="ok"),
        error=None,
    )


class _FakeGraph:
    def __init__(self) -> None:
        self.state: dict[str, Any] | None = None

    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del config
        self.state = state
        return {
            "response": AgentQueryResponse(
                request_id=state["request_id"],
                status="ok",
                asset_id="PUMP-103",
                answer="Inspect the bearing and follow the maintenance procedure.",
                confidence="confirmed",
            )
        }

    def get_state(self, config: dict[str, Any]) -> Any:
        del config
        return SimpleNamespace(next=(), values=self.state)


class _FailingGraph:
    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del state, config
        raise RuntimeError("graph failed")


class _RecordingLLMClient:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        tools: list[LLMTool] | None = None,
        tool_choice: LLMToolChoice | None = None,
    ) -> LLMResponse:
        del messages, tools, tool_choice
        if not self._responses:
            raise AssertionError("Unexpected LLM call.")
        return self._responses.pop(0)


class _SequenceClock:
    def __init__(self, values: list[datetime]) -> None:
        self._values = values

    def __call__(self) -> datetime:
        if not self._values:
            raise AssertionError("Unexpected clock call.")
        return self._values.pop(0)


@asynccontextmanager
async def _fake_session_context() -> AsyncGenerator[AsyncSession]:
    yield cast(AsyncSession, object())


def _app_with_graph_and_jsonl_sink(graph: object, path: Path) -> FastAPI:
    app = FastAPI()
    app.state.agent_graph = graph
    app.state.emit_run_event = make_jsonl_emitter(path)
    app.include_router(agent_router, prefix="/agent")
    return app


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
        return GetAssetStatusResult(
            asset=_asset("PUMP-103"),
            telemetry=None,
            classified_readings=[_classified_reading()],
        )
    if tool_name == "get_maintenance_history":
        return GetMaintenanceHistoryResult(asset=_asset("PUMP-103"))
    if tool_name == "search_maintenance_docs":
        return SearchMaintenanceDocsResult(query="maintenance docs", results=[_doc_hit()])
    if tool_name == "get_plant_policy":
        policy_type = cast(str, args["policy_type"])
        return GetPlantPolicyResult(policy_type=policy_type)
    if tool_name == "create_work_order_draft":
        return WorkOrderDraft(
            draft_id=state.get("request_id", "test-request"),
            asset_id="PUMP-103",
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


def _evidence_response(tool_name: str, *, policy_type: str = "recurring_fault") -> LLMResponse:
    return LLMResponse(
        tool_calls=[
            ToolCallRequest(
                id=f"{tool_name}-1",
                name=tool_name,
                input={"query": "maintenance docs"}
                if tool_name == "search_maintenance_docs"
                else {"policy_type": policy_type}
                if tool_name == "get_plant_policy"
                else {},
            )
        ]
    )


def _draft_response() -> LLMResponse:
    return LLMResponse(
        tool_calls=[
            ToolCallRequest(
                id="draft-1",
                name="create_work_order_draft",
                input={
                    "issue": "Recurring bearing overheating",
                    "recommended_action": "Investigate root cause.",
                    "priority": "high",
                },
            )
        ]
    )


def _synthesis_response(evidence_id: str = "DOC-03") -> LLMResponse:
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


def _classified_reading() -> ClassifiedReading:
    return ClassifiedReading(
        source_id="TS-001",
        metric="bearing_temperature_c",
        value=Decimal("78.0"),
        unit="C",
        tier="normal",
        operating_limit_id="OL-002",
        rule_text="Normal < 82; high >= 82",
    )


def _doc_hit() -> DocSearchHit:
    return DocSearchHit(
        chunk_id="DOC-03-C1",
        document_id="DOC-03",
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


def _asset(asset_id: str) -> AssetRecord:
    return AssetRecord(
        asset_id=asset_id,
        asset_type="centrifugal_pump",
        model="CP-200",
        location="Line 3",
        installation_date=date(2021, 6, 1),
        status="operational",
    )
