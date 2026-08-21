import ast
import inspect
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.api import agent as agent_api
from maintenance_agent.api.agent import router as agent_router
from maintenance_agent.db.repositories.records import AssetRecord
from maintenance_agent.llm.client import (
    LLMMessage,
    LLMResponse,
    LLMTool,
    LLMToolChoice,
    ToolCallRequest,
)
from maintenance_agent.main import lifespan
from maintenance_agent.orchestration import graph as graph_module
from maintenance_agent.orchestration.graph import (
    AgentGraphDependencies,
    build_agent_graph,
)
from maintenance_agent.orchestration.state import GraphState, WorkOrderDraft
from maintenance_agent.tools.get_asset_status import ClassifiedReading, GetAssetStatusResult
from maintenance_agent.tools.get_maintenance_history import GetMaintenanceHistoryResult
from maintenance_agent.tools.get_plant_policy import GetPlantPolicyResult
from maintenance_agent.tools.resolve_asset import ResolveAssetResult
from maintenance_agent.tools.search_maintenance_docs import (
    DocSearchHit,
    SearchMaintenanceDocsResult,
)


@pytest.mark.asyncio
async def test_lifespan_compiles_graph_once_for_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compiled_graph = object()
    app = FastAPI()
    build_calls = 0

    async def fake_verify_database_connection() -> None:
        return None

    def fake_get_llm_client() -> object:
        return object()

    def fake_build_agent_graph(dependencies: object) -> object:
        nonlocal build_calls
        build_calls += 1
        return compiled_graph

    def fake_get_settings() -> object:
        return SimpleNamespace(run_events_path=tmp_path / "events.jsonl")

    monkeypatch.setattr(
        "maintenance_agent.main.verify_database_connection", fake_verify_database_connection
    )
    monkeypatch.setattr("maintenance_agent.main.get_settings", fake_get_settings)
    monkeypatch.setattr("maintenance_agent.main.get_llm_client", fake_get_llm_client)
    monkeypatch.setattr("maintenance_agent.main.build_agent_graph", fake_build_agent_graph)

    async with lifespan(app):
        assert app.state.agent_graph is compiled_graph
        assert app.state.agent_graph is compiled_graph
        assert callable(app.state.emit_run_event)

    assert build_calls == 1


def test_graph_compile_uses_phase_6_memory_saver_checkpointer() -> None:
    source = inspect.getsource(graph_module.build_agent_graph)
    tree = ast.parse(source)
    compile_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compile"
    ]

    assert len(compile_calls) == 1
    assert compile_calls[0].args == []
    assert [keyword.arg for keyword in compile_calls[0].keywords] == ["checkpointer"]
    assert ast.unparse(compile_calls[0].keywords[0].value) == "build_graph_checkpointer()"


def test_graph_has_no_langchain_chat_model_dependency() -> None:
    source = inspect.getsource(graph_module)

    assert "langchain" not in source.lower()
    assert "ChatAnthropic" not in source


@pytest.mark.asyncio
async def test_work_order_draft_branch_is_reserved_for_phase_6_hitl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(graph_module, "invoke_tool_binding", _fake_invoke_tool_binding)
    graph = build_agent_graph(
        AgentGraphDependencies(
            llm_client=_RecordingLLMClient(
                [
                    _interpret_response("work_order_request", "PUMP-103"),
                    LLMResponse(
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
                    ),
                ]
            )
        )
    )

    final_state = await graph.ainvoke(
        _state(query="Create a work order for PUMP-103."),
        config={"configurable": {"thread_id": "test-request"}},
    )
    checkpoint = graph.get_state({"configurable": {"thread_id": "test-request"}})

    assert "__interrupt__" in final_state
    assert checkpoint.next == ("await_approval",)
    assert final_state["approval_status"] == "pending_approval"
    assert final_state["response"] is None


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
async def test_non_hitl_golden_scenarios_run_through_agent_query_api(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    asset_id: str,
    intent: str,
    evidence_tools: list[str],
    expected_status: str,
) -> None:
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
    app = FastAPI()
    app.state.agent_graph = build_agent_graph(AgentGraphDependencies(llm_client=llm_client))
    app.include_router(agent_router, prefix="/agent")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/agent/query", json={"query": query})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == expected_status
    if expected_status == "unknown_asset":
        assert payload["asset_id"] is None
        assert asset_id in payload["answer"]
        assert payload["error"] is None
    else:
        assert payload["asset_id"] == asset_id
    if asset_id == "PUMP-101":
        assert payload["status"] == "ok"
        assert payload["confidence"] == "hypothesis"
    assert [record.name for record in llm_client.tool_records] == evidence_tools


class _RecordingLLMClient:
    def __init__(self, responses: Sequence[LLMResponse]) -> None:
        self._responses = list(responses)
        self.tool_records: list[ToolCallRequest] = []

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
        response = self._responses.pop(0)
        self.tool_records.extend(
            tool_call
            for tool_call in response.tool_calls
            if tool_call.name
            not in {
                "interpret_request",
                "classify_request",
                "synthesize_response",
            }
        )
        return response


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
        return GetPlantPolicyResult(policy_type="recurring_fault")
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


def _evidence_response(tool_name: str) -> LLMResponse:
    return LLMResponse(
        tool_calls=[
            ToolCallRequest(
                id=f"{tool_name}-1",
                name=tool_name,
                input={"query": "maintenance docs"}
                if tool_name == "search_maintenance_docs"
                else {"policy_type": "recurring_fault"}
                if tool_name == "get_plant_policy"
                else {},
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


def _state(query: str) -> GraphState:
    return GraphState(
        request_id="phase-4-success-test",
        session=cast(AsyncSession, object()),
        query=query,
        asset_id_hint=None,
        fault_code_hint=None,
        intent=None,
        asset=None,
        asset_resolution_status=None,
        tool_calls=[],
        structured_evidence=[],
        document_evidence=[],
        work_order_draft=None,
        approval_status="none",
        errors=[],
        evidence_gathering_iterations=0,
        last_evidence_tool_call_count=0,
        synthesis_answer=None,
        synthesis_confidence=None,
        synthesis_evidence_used=[],
        response=None,
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


@asynccontextmanager
async def _fake_session_context() -> AsyncGenerator[AsyncSession]:
    yield cast(AsyncSession, object())
