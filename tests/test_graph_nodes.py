import ast
import inspect
from collections.abc import Sequence
from datetime import date
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories.records import AssetRecord
from maintenance_agent.llm.client import (
    LLMMessage,
    LLMResponse,
    LLMTool,
    LLMToolChoice,
    ToolCallRequest,
)
from maintenance_agent.orchestration import graph as graph_module
from maintenance_agent.orchestration.graph import (
    EVIDENCE_GATHERING_NODE,
    MAX_EVIDENCE_GATHERING_ITERATIONS,
    AgentGraphDependencies,
    build_agent_graph,
    evidence_gathering_node,
    request_interpretation_node,
)
from maintenance_agent.orchestration.state import GraphState
from maintenance_agent.tools.get_asset_status import GetAssetStatusResult
from maintenance_agent.tools.get_maintenance_history import GetMaintenanceHistoryResult
from maintenance_agent.tools.resolve_asset import ResolveAssetResult
from maintenance_agent.tools.search_maintenance_docs import SearchMaintenanceDocsResult


def test_evidence_gathering_is_single_conditional_self_loop() -> None:
    compiled_graph = build_agent_graph(
        AgentGraphDependencies(
            llm_client=_RecordingLLMClient([LLMResponse()]),
        )
    )

    evidence_edges = [
        edge for edge in compiled_graph.get_graph().edges if edge.source == EVIDENCE_GATHERING_NODE
    ]

    assert {(edge.target, edge.conditional) for edge in evidence_edges} == {
        (EVIDENCE_GATHERING_NODE, True),
        ("synthesis", True),
        ("terminal_response", True),
    }


@pytest.mark.asyncio
async def test_procedure_lookup_only_offers_document_search_tool() -> None:
    llm_client = _RecordingLLMClient(
        [
            LLMResponse(tool_calls=[]),
        ]
    )

    await evidence_gathering_node(
        _state(intent="procedure_lookup", asset=_asset()),
        llm_client,
        cast(AsyncSession, object()),
    )

    assert llm_client.tool_names_by_call == [["search_maintenance_docs"]]


@pytest.mark.asyncio
async def test_unknown_asset_routes_to_terminal_without_evidence_or_synthesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_invoke_tool_binding(
        tool_name: str,
        args: dict[str, object],
        state: GraphState,
        session: AsyncSession,
    ) -> object:
        del state, session
        assert tool_name == "resolve_asset"
        assert args == {"identifier": "PUMP-999"}
        return ResolveAssetResult(status="not_found")

    monkeypatch.setattr(graph_module, "invoke_tool_binding", fake_invoke_tool_binding)
    llm_client = _RecordingLLMClient(
        [
            LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="interpret-1",
                        name="interpret_request",
                        input={
                            "intent": "troubleshooting",
                            "asset_identifier": "PUMP-999",
                        },
                    )
                ]
            )
        ]
    )
    compiled_graph = build_agent_graph(
        AgentGraphDependencies(
            llm_client=llm_client,
        )
    )

    final_state = await compiled_graph.ainvoke(_state(query="Diagnose PUMP-999."))

    assert final_state["response"].status == "unknown_asset"
    assert final_state["response"].asset_id == "PUMP-999"
    assert llm_client.tool_names_by_call == [["interpret_request"]]


@pytest.mark.asyncio
async def test_interpretation_with_asset_hint_uses_classification_only_schema() -> None:
    llm_client = _RecordingLLMClient(
        [
            LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="classify-1",
                        name="classify_request",
                        input={"intent": "procedure_lookup"},
                    )
                ]
            )
        ]
    )

    update = await request_interpretation_node(
        _state(
            query="For PUMP-103, show the lockout procedure.",
            asset_id_hint="PUMP-103",
        ),
        llm_client,
    )

    assert update == {"intent": "procedure_lookup"}
    assert llm_client.tool_names_by_call == [["classify_request"]]
    assert "asset_identifier" not in llm_client.schemas_by_call[0]["classify_request"]["properties"]
    assert "extract the asset identifier" not in llm_client.messages_by_call[0][0].content


def test_terminal_node_is_only_agent_query_response_constructor() -> None:
    source = inspect.getsource(graph_module)
    tree = ast.parse(source)
    constructors: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "AgentQueryResponse"
                for child in ast.walk(node)
            ):
                constructors.append(node.name)

    assert constructors == ["terminal_response_node"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence_tool_names",
    [
        ["search_maintenance_docs", "get_maintenance_history"],
        ["get_maintenance_history", "search_maintenance_docs"],
    ],
)
async def test_different_evidence_tool_orders_execute_through_same_graph(
    monkeypatch: pytest.MonkeyPatch,
    evidence_tool_names: list[str],
) -> None:
    monkeypatch.setattr(graph_module, "invoke_tool_binding", _fake_invoke_tool_binding)
    llm_client = _RecordingLLMClient(
        [
            _interpret_response("troubleshooting", "PUMP-103"),
            *[
                LLMResponse(
                    tool_calls=[
                        ToolCallRequest(
                            id=f"tool-{index}",
                            name=tool_name,
                            input={"query": "bearing overheating"}
                            if tool_name == "search_maintenance_docs"
                            else {},
                        )
                    ]
                )
                for index, tool_name in enumerate(evidence_tool_names, start=1)
            ],
            LLMResponse(tool_calls=[]),
            _synthesis_response(),
        ]
    )
    compiled_graph = build_agent_graph(
        AgentGraphDependencies(
            llm_client=llm_client,
        )
    )

    final_state = await compiled_graph.ainvoke(_state())

    assert final_state["response"].status == "ok"
    assert [record.tool_name for record in final_state["tool_calls"]] == [
        "resolve_asset",
        *evidence_tool_names,
    ]


@pytest.mark.asyncio
async def test_pathological_evidence_loop_stops_at_iteration_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(graph_module, "invoke_tool_binding", _fake_invoke_tool_binding)
    llm_client = _RecordingLLMClient(
        [
            _interpret_response("troubleshooting", "PUMP-103"),
            *[
                LLMResponse(
                    tool_calls=[
                        ToolCallRequest(
                            id=f"tool-{index}",
                            name="search_maintenance_docs",
                            input={"query": f"bearing overheating {index}"},
                        )
                    ]
                )
                for index in range(MAX_EVIDENCE_GATHERING_ITERATIONS)
            ],
            _synthesis_response(),
        ]
    )
    compiled_graph = build_agent_graph(
        AgentGraphDependencies(
            llm_client=llm_client,
        )
    )

    final_state = await compiled_graph.ainvoke(_state())

    assert final_state["response"].status == "ok"
    assert final_state["evidence_gathering_iterations"] == MAX_EVIDENCE_GATHERING_ITERATIONS
    assert [record.tool_name for record in final_state["tool_calls"]] == [
        "resolve_asset",
        *["search_maintenance_docs"] * MAX_EVIDENCE_GATHERING_ITERATIONS,
    ]


class _RecordingLLMClient:
    def __init__(self, responses: Sequence[LLMResponse]) -> None:
        self._responses = list(responses)
        self.messages_by_call: list[Sequence[LLMMessage]] = []
        self.tool_names_by_call: list[list[str]] = []
        self.schemas_by_call: list[dict[str, dict[str, Any]]] = []

    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[LLMTool] | None = None,
        tool_choice: LLMToolChoice | None = None,
    ) -> LLMResponse:
        del tool_choice
        self.messages_by_call.append(messages)
        self.tool_names_by_call.append([tool.name for tool in tools or []])
        self.schemas_by_call.append({tool.name: tool.input_schema for tool in tools or []})
        if not self._responses:
            raise AssertionError("Unexpected LLM call.")
        return self._responses.pop(0)


async def _fake_invoke_tool_binding(
    tool_name: str,
    args: dict[str, object],
    state: GraphState,
    session: AsyncSession,
) -> object:
    del args, state, session
    if tool_name == "resolve_asset":
        return ResolveAssetResult(status="resolved", asset=_asset())
    if tool_name == "get_asset_status":
        return GetAssetStatusResult(asset=_asset(), telemetry=None)
    if tool_name == "get_maintenance_history":
        return GetMaintenanceHistoryResult(asset=_asset())
    if tool_name == "search_maintenance_docs":
        return SearchMaintenanceDocsResult(query="bearing overheating")
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


def _synthesis_response() -> LLMResponse:
    return LLMResponse(
        tool_calls=[
            ToolCallRequest(
                id="synthesis-1",
                name="synthesize_response",
                input={
                    "answer": "Inspect the bearing and follow the maintenance procedure.",
                    "confidence": "confirmed",
                },
            )
        ]
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


def _state(
    *,
    query: str = "Diagnose PUMP-103.",
    asset_id_hint: str | None = None,
    intent: str | None = None,
    asset: AssetRecord | None = None,
) -> GraphState:
    return cast(
        GraphState,
        {
            "request_id": "test-request",
            "session": cast(AsyncSession, object()),
            "query": query,
            "asset_id_hint": asset_id_hint,
            "fault_code_hint": None,
            "intent": intent,
            "asset": asset,
            "asset_resolution_status": None,
            "tool_calls": [],
            "structured_evidence": [],
            "document_evidence": [],
            "work_order_draft": None,
            "approval_status": "none",
            "errors": [],
            "evidence_gathering_iterations": 0,
            "last_evidence_tool_call_count": 0,
            "synthesis_answer": None,
            "synthesis_confidence": None,
            "response": None,
        },
    )
