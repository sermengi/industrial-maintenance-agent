import ast
import inspect
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any, Literal, cast

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
    ASSET_RESOLUTION_NODE,
    EVIDENCE_GATHERING_NODE,
    MAX_EVIDENCE_GATHERING_ITERATIONS,
    SYNTHESIS_NODE,
    AgentGraphDependencies,
    build_agent_graph,
    evidence_gathering_node,
    request_interpretation_node,
    synthesis_node,
    terminal_response_node,
)
from maintenance_agent.orchestration.retry import RetryExhaustedError, with_retry
from maintenance_agent.orchestration.state import GraphState, WorkOrderDraft
from maintenance_agent.tools.get_asset_status import ClassifiedReading, GetAssetStatusResult
from maintenance_agent.tools.get_maintenance_history import GetMaintenanceHistoryResult
from maintenance_agent.tools.resolve_asset import ResolveAssetResult
from maintenance_agent.tools.search_maintenance_docs import (
    DocSearchHit,
    SearchMaintenanceDocsResult,
)


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
    assert final_state["response"].asset_id is None
    assert final_state["response"].error is None
    assert final_state["response"].answer == (
        "I couldn't find an asset matching 'PUMP-999'. Please provide a valid asset ID."
    )
    assert final_state["response"].confidence is None
    assert final_state["tool_calls"][-1].args["identifier"] == "PUMP-999"
    assert final_state["structured_evidence"] == []
    assert final_state["document_evidence"] == []
    assert final_state["synthesis_answer"] is None
    assert llm_client.tool_names_by_call == [["interpret_request"]]


def test_unknown_asset_branch_has_no_edge_to_evidence_or_synthesis() -> None:
    compiled_graph = build_agent_graph(
        AgentGraphDependencies(
            llm_client=_RecordingLLMClient([LLMResponse()]),
        )
    )

    asset_resolution_edges = [
        edge for edge in compiled_graph.get_graph().edges if edge.source == ASSET_RESOLUTION_NODE
    ]

    assert (SYNTHESIS_NODE, True) not in {
        (edge.target, edge.conditional) for edge in asset_resolution_edges
    }
    assert {(edge.target, edge.conditional) for edge in asset_resolution_edges} == {
        ("terminal_response", True),
        (EVIDENCE_GATHERING_NODE, True),
    }


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


@pytest.mark.asyncio
async def test_invalid_interpretation_structured_output_routes_to_terminal_error() -> None:
    llm_client = _RecordingLLMClient(
        [
            LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="interpret-1",
                        name="interpret_request",
                        input={"intent": "unsupported", "asset_identifier": "PUMP-103"},
                    )
                ]
            )
        ]
    )
    compiled_graph = build_agent_graph(
        AgentGraphDependencies(llm_client=llm_client, max_retry_attempts=1)
    )

    final_state = await compiled_graph.ainvoke(_state())

    assert final_state["response"].status == "error"
    assert final_state["response"].error.code == "structured_output_invalid"
    assert final_state["errors"][-1].recoverable is False
    assert final_state["errors"][-1].node == "request_interpretation"
    assert final_state["tool_calls"] == []


@pytest.mark.asyncio
async def test_invalid_synthesis_structured_output_returns_recoverable_error() -> None:
    llm_client = _RecordingLLMClient(
        [
            LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="synthesis-1",
                        name="synthesize_response",
                        input={
                            "answer": "Inspect the asset.",
                            "confidence": 0.8,
                            "evidence_used": [],
                        },
                    )
                ]
            )
        ]
    )

    update = await synthesis_node(
        _state(intent="troubleshooting", asset=_asset()),
        llm_client,
        max_retry_attempts=1,
    )

    assert update["errors"][0].code == "structured_output_invalid"
    assert update["errors"][0].recoverable is False
    assert update["errors"][0].node == "synthesis"


@pytest.mark.asyncio
async def test_synthesis_retries_nonexistent_evidence_citation() -> None:
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    llm_client = _RecordingLLMClient(
        [
            LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="synthesis-1",
                        name="synthesize_response",
                        input={
                            "answer": "Inspect the asset.",
                            "confidence": "hypothesis",
                            "evidence_used": ["FE-999"],
                        },
                    )
                ]
            ),
            _synthesis_response(),
        ]
    )
    state = _state(intent="troubleshooting", asset=_asset())
    state["document_evidence"] = [_doc_hit()]

    update = await synthesis_node(
        state,
        llm_client,
        max_retry_attempts=2,
        retry_delay_seconds=0.5,
        sleep=fake_sleep,
    )

    assert update["synthesis_answer"] == "Inspect the bearing and follow the maintenance procedure."
    assert update["errors"][0].code == "structured_output_invalid"
    assert sleep_calls == [0.5]
    retry_message = llm_client.messages_by_call[1][-1].content
    assert "FE-999" in retry_message
    assert "DOC-03" in retry_message


@pytest.mark.asyncio
async def test_synthesis_rejects_empty_evidence_used_when_evidence_exists() -> None:
    llm_client = _RecordingLLMClient(
        [
            LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="synthesis-1",
                        name="synthesize_response",
                        input={
                            "answer": "Inspect the asset.",
                            "confidence": "hypothesis",
                            "evidence_used": [],
                        },
                    )
                ]
            )
        ]
    )
    state = _state(intent="troubleshooting", asset=_asset())
    state["document_evidence"] = [_doc_hit()]

    update = await synthesis_node(state, llm_client, max_retry_attempts=1)

    assert update["errors"][0].code == "structured_output_invalid"
    assert update["errors"][0].recoverable is False
    assert "at least one citation" in update["errors"][0].message


@pytest.mark.asyncio
async def test_synthesis_accepts_valid_evidence_citation_without_retry() -> None:
    llm_client = _RecordingLLMClient([_synthesis_response()])
    state = _state(intent="troubleshooting", asset=_asset())
    state["document_evidence"] = [_doc_hit()]

    update = await synthesis_node(state, llm_client, max_retry_attempts=2)

    assert update["synthesis_evidence_used"] == ["DOC-03"]
    assert "errors" not in update
    assert len(llm_client.messages_by_call) == 1


def test_terminal_response_surfaces_structured_evidence_provenance() -> None:
    state = _state(intent="troubleshooting", asset=_asset())
    state["asset_resolution_status"] = "resolved"
    state["structured_evidence"] = [_classified_reading()]
    state["synthesis_answer"] = "Inspect the bearing."
    state["synthesis_confidence"] = "hypothesis"
    state["synthesis_evidence_used"] = ["TS-001"]

    response = terminal_response_node(state)["response"]

    assert response.status == "ok"
    assert response.evidence_used == ["TS-001"]
    assert response.structured_evidence[0].source_type == "telemetry_snapshot"
    assert response.structured_evidence[0].source_id == "TS-001"
    assert response.structured_evidence[0].reference_id == "TS-001"


def test_terminal_response_exposes_cited_subset_without_filtering_retrieved_evidence() -> None:
    state = _state(intent="procedure_lookup", asset=_asset(asset_id="PUMP-104"))
    state["asset_resolution_status"] = "resolved"
    state["document_evidence"] = [
        _doc_hit(document_id="DOC-01"),
        _doc_hit(document_id="DOC-02"),
    ]
    state["synthesis_answer"] = "Inspect the mechanical seal."
    state["synthesis_confidence"] = "hypothesis"
    state["synthesis_evidence_used"] = ["DOC-01"]

    response = terminal_response_node(state)["response"]

    assert response.status == "ok"
    assert response.evidence_used == ["DOC-01"]
    assert [hit.document_id for hit in response.document_evidence] == ["DOC-01", "DOC-02"]


@pytest.mark.asyncio
async def test_tool_call_retries_transient_failures_and_merges_attempt_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    async def flaky_invoke_tool_binding(
        tool_name: str,
        args: dict[str, object],
        state: GraphState,
        session: AsyncSession,
    ) -> object:
        nonlocal call_count
        del args, state, session
        call_count += 1
        assert tool_name == "get_asset_status"
        if call_count < 3:
            raise RuntimeError(f"temporary tool failure {call_count}")
        return GetAssetStatusResult(
            asset=_asset(),
            telemetry=None,
            classified_readings=[_classified_reading()],
        )

    monkeypatch.setattr(graph_module, "invoke_tool_binding", flaky_invoke_tool_binding)
    update = await evidence_gathering_node(
        _state(intent="troubleshooting", asset=_asset()),
        _RecordingLLMClient(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCallRequest(
                            id="status-1",
                            name="get_asset_status",
                            input={},
                        )
                    ]
                )
            ]
        ),
        cast(AsyncSession, object()),
        max_retry_attempts=3,
        retry_delay_seconds=0.5,
        sleep=fake_sleep,
    )

    assert call_count == 3
    assert sleep_calls == [0.5, 0.5]
    assert [error.code for error in update["errors"]] == [
        "tool_execution_failed",
        "tool_execution_failed",
    ]
    assert all(error.recoverable is True for error in update["errors"])
    assert update["tool_calls"][0].tool_name == "get_asset_status"


@pytest.mark.asyncio
async def test_retry_helper_exhaustion_raises_after_configured_attempt_count() -> None:
    call_count = 0

    async def fake_sleep(delay: float) -> None:
        del delay

    async def failing_call() -> object:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("database unavailable")

    with pytest.raises(RetryExhaustedError) as exc_info:
        await with_retry(
            failing_call,
            max_attempts=3,
            delay_seconds=0.5,
            sleep=fake_sleep,
            error_code="tool_execution_failed",
            node=ASSET_RESOLUTION_NODE,
        )

    assert call_count == 3
    assert len(exc_info.value.attempts) == 3
    assert all(error.code == "tool_execution_failed" for error in exc_info.value.attempts)


@pytest.mark.asyncio
async def test_evidence_tool_retry_exhaustion_hard_aborts_with_sanitized_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_calls: list[str] = []
    raw_marker = "RAW_TOOL_FAILURE_MARKER"

    async def fake_sleep(delay: float) -> None:
        del delay

    async def failing_second_tool(
        tool_name: str,
        args: dict[str, object],
        state: GraphState,
        session: AsyncSession,
    ) -> object:
        del args, state, session
        tool_calls.append(tool_name)
        if tool_name == "resolve_asset":
            return ResolveAssetResult(status="resolved", asset=_asset())
        if tool_name == "get_asset_status":
            return GetAssetStatusResult(
                asset=_asset(),
                telemetry=None,
                classified_readings=[_classified_reading()],
            )
        if tool_name == "search_maintenance_docs":
            raise RuntimeError(raw_marker)
        raise AssertionError(f"Unexpected tool call: {tool_name}")

    monkeypatch.setattr(graph_module, "invoke_tool_binding", failing_second_tool)
    graph = build_agent_graph(
        AgentGraphDependencies(
            llm_client=_RecordingLLMClient(
                [
                    _interpret_response("troubleshooting", "PUMP-103"),
                    LLMResponse(
                        tool_calls=[
                            ToolCallRequest(
                                id="status-1",
                                name="get_asset_status",
                                input={},
                            )
                        ]
                    ),
                    LLMResponse(
                        tool_calls=[
                            ToolCallRequest(
                                id="search-1",
                                name="search_maintenance_docs",
                                input={"query": "bearing overheating"},
                            )
                        ]
                    ),
                ]
            ),
            max_retry_attempts=2,
            retry_delay_seconds=0.5,
            sleep=fake_sleep,
        )
    )

    final_state = await graph.ainvoke(_state())

    assert final_state["response"].status == "error"
    assert final_state["response"].asset_id == "PUMP-103"
    assert final_state["response"].confidence is None
    assert final_state["response"].structured_evidence == []
    assert final_state["response"].document_evidence == []
    assert final_state["response"].error.code == "tool_execution_failed"
    assert final_state["response"].error.message == (
        "A tool call failed after multiple attempts. Please try again shortly."
    )
    assert raw_marker not in final_state["response"].error.message
    assert raw_marker in final_state["errors"][-1].message
    assert final_state["errors"][-1].recoverable is False
    assert len(final_state["structured_evidence"]) == 1
    assert [record.tool_name for record in final_state["tool_calls"]] == [
        "resolve_asset",
        "get_asset_status",
    ]
    assert tool_calls == [
        "resolve_asset",
        "get_asset_status",
        "search_maintenance_docs",
        "search_maintenance_docs",
    ]


@pytest.mark.asyncio
async def test_evidence_llm_retry_exhaustion_hard_aborts_with_sanitized_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_marker = "RAW_LLM_FAILURE_MARKER"

    async def fake_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr(graph_module, "invoke_tool_binding", _fake_invoke_tool_binding)
    llm_client = _FailingLLMClient(
        [_interpret_response("troubleshooting", "PUMP-103")],
        raw_marker,
    )
    graph = build_agent_graph(
        AgentGraphDependencies(
            llm_client=llm_client,
            max_retry_attempts=2,
            retry_delay_seconds=0.5,
            sleep=fake_sleep,
        )
    )

    final_state = await graph.ainvoke(_state())

    assert final_state["response"].status == "error"
    assert final_state["response"].asset_id == "PUMP-103"
    assert final_state["response"].error.code == "llm_call_failed"
    assert final_state["response"].error.message == (
        "The AI service is temporarily unavailable. Please try again shortly."
    )
    assert raw_marker not in final_state["response"].error.message
    assert raw_marker in final_state["errors"][-1].message
    assert final_state["errors"][-1].recoverable is False
    assert len(llm_client.messages_by_call) == 3


@pytest.mark.asyncio
async def test_resolve_asset_retry_exhaustion_is_error_not_unknown_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_marker = "RAW_RESOLVE_DB_FAILURE"

    async def fake_sleep(delay: float) -> None:
        del delay

    async def failing_resolve_asset(
        tool_name: str,
        args: dict[str, object],
        state: GraphState,
        session: AsyncSession,
    ) -> object:
        del args, state, session
        assert tool_name == "resolve_asset"
        raise RuntimeError(raw_marker)

    monkeypatch.setattr(graph_module, "invoke_tool_binding", failing_resolve_asset)
    graph = build_agent_graph(
        AgentGraphDependencies(
            llm_client=_RecordingLLMClient([_interpret_response("troubleshooting", "PUMP-103")]),
            max_retry_attempts=2,
            retry_delay_seconds=0.5,
            sleep=fake_sleep,
        )
    )

    final_state = await graph.ainvoke(_state())

    assert final_state["response"].status == "error"
    assert final_state["response"].asset_id is None
    assert final_state["response"].error.code == "tool_execution_failed"
    assert raw_marker not in final_state["response"].error.message
    assert raw_marker in final_state["errors"][-1].message
    assert final_state["asset_resolution_status"] is None
    assert final_state["tool_calls"] == []


@pytest.mark.asyncio
async def test_structured_output_retry_adds_corrective_validation_message() -> None:
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    llm_client = _RecordingLLMClient(
        [
            LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="synthesis-1",
                        name="synthesize_response",
                        input={
                            "answer": "Inspect the asset.",
                            "confidence": 0.8,
                            "evidence_used": ["DOC-03"],
                        },
                    )
                ]
            ),
            _synthesis_response(),
        ]
    )

    update = await synthesis_node(
        _state_with_document_evidence(),
        llm_client,
        max_retry_attempts=2,
        retry_delay_seconds=0.5,
        sleep=fake_sleep,
    )

    assert update["synthesis_answer"] == "Inspect the bearing and follow the maintenance procedure."
    assert len(update["errors"]) == 1
    assert update["errors"][0].code == "structured_output_invalid"
    assert sleep_calls == [0.5]
    assert "Validation error:" in llm_client.messages_by_call[1][-1].content
    assert "confidence" in llm_client.messages_by_call[1][-1].content


def test_retry_settings_are_loaded_by_agent_graph_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maintenance_agent.core.config import get_settings

    with monkeypatch.context() as scoped_monkeypatch:
        get_settings.cache_clear()
        scoped_monkeypatch.setenv("MAX_RETRY_ATTEMPTS", "4")
        scoped_monkeypatch.setenv("RETRY_DELAY_SECONDS", "0.25")
        dependencies = AgentGraphDependencies(llm_client=_RecordingLLMClient([]))
        get_settings.cache_clear()

    get_settings.cache_clear()
    assert dependencies.max_retry_attempts == 4
    assert dependencies.retry_delay_seconds == 0.25


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
async def test_empty_troubleshooting_evidence_routes_to_insufficient_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(graph_module, "invoke_tool_binding", _fake_invoke_tool_binding)
    llm_client = _RecordingLLMClient(
        [
            _interpret_response("troubleshooting", "PUMP-103"),
            LLMResponse(tool_calls=[]),
        ]
    )
    compiled_graph = build_agent_graph(AgentGraphDependencies(llm_client=llm_client))

    final_state = await compiled_graph.ainvoke(_state())

    assert final_state["response"].status == "insufficient_evidence"
    assert final_state["response"].asset_id == "PUMP-103"
    assert final_state["response"].confidence is None
    assert final_state["response"].error is None
    assert final_state["response"].answer == (
        "Not enough evidence was found to answer this request. "
        "Try rephrasing or providing more detail."
    )
    assert llm_client.tool_names_by_call == [
        ["interpret_request"],
        [
            "get_asset_status",
            "get_maintenance_history",
            "search_maintenance_docs",
            "get_plant_policy",
        ],
    ]


@pytest.mark.asyncio
async def test_empty_procedure_lookup_document_evidence_routes_to_insufficient_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(graph_module, "invoke_tool_binding", _fake_invoke_tool_binding)
    llm_client = _RecordingLLMClient(
        [
            _interpret_response("procedure_lookup", "PUMP-103"),
            LLMResponse(tool_calls=[]),
        ]
    )
    compiled_graph = build_agent_graph(AgentGraphDependencies(llm_client=llm_client))

    final_state = await compiled_graph.ainvoke(_state(query="Find procedure for PUMP-103."))

    assert final_state["response"].status == "insufficient_evidence"
    assert final_state["response"].asset_id == "PUMP-103"
    assert final_state["response"].confidence is None
    assert final_state["response"].error is None
    assert llm_client.tool_names_by_call == [["interpret_request"], ["search_maintenance_docs"]]


@pytest.mark.asyncio
async def test_procedure_lookup_document_evidence_routes_to_synthesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_invoke_tool_binding(
        tool_name: str,
        args: dict[str, object],
        state: GraphState,
        session: AsyncSession,
    ) -> object:
        del args, state, session
        if tool_name == "resolve_asset":
            return ResolveAssetResult(status="resolved", asset=_asset())
        if tool_name == "search_maintenance_docs":
            return SearchMaintenanceDocsResult(query="procedure", results=[_doc_hit()])
        raise AssertionError(f"Unexpected tool call: {tool_name}")

    monkeypatch.setattr(graph_module, "invoke_tool_binding", fake_invoke_tool_binding)
    llm_client = _RecordingLLMClient(
        [
            _interpret_response("procedure_lookup", "PUMP-103"),
            LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="search-1",
                        name="search_maintenance_docs",
                        input={"query": "procedure"},
                    )
                ]
            ),
            LLMResponse(tool_calls=[]),
            _synthesis_response(),
        ]
    )
    compiled_graph = build_agent_graph(AgentGraphDependencies(llm_client=llm_client))

    final_state = await compiled_graph.ainvoke(_state(query="Find procedure for PUMP-103."))

    assert final_state["response"].status == "ok"
    assert final_state["document_evidence"][0].document_id == "DOC-03"
    assert llm_client.tool_names_by_call[-1] == ["synthesize_response"]


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


class _FailingLLMClient(_RecordingLLMClient):
    def __init__(self, responses: Sequence[LLMResponse], failure_message: str) -> None:
        super().__init__(responses)
        self.failure_message = failure_message

    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[LLMTool] | None = None,
        tool_choice: LLMToolChoice | None = None,
    ) -> LLMResponse:
        if self._responses:
            return await super().generate(messages, tools=tools, tool_choice=tool_choice)
        self.messages_by_call.append(messages)
        self.tool_names_by_call.append([tool.name for tool in tools or []])
        self.schemas_by_call.append({tool.name: tool.input_schema for tool in tools or []})
        raise RuntimeError(self.failure_message)


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
        return GetAssetStatusResult(
            asset=_asset(),
            telemetry=None,
            classified_readings=[_classified_reading()],
        )
    if tool_name == "get_maintenance_history":
        return GetMaintenanceHistoryResult(asset=_asset())
    if tool_name == "search_maintenance_docs":
        return SearchMaintenanceDocsResult(query="bearing overheating", results=[_doc_hit()])
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


def _synthesis_response() -> LLMResponse:
    return LLMResponse(
        tool_calls=[
            ToolCallRequest(
                id="synthesis-1",
                name="synthesize_response",
                input={
                    "answer": "Inspect the bearing and follow the maintenance procedure.",
                    "confidence": "confirmed",
                    "evidence_used": ["DOC-03"],
                },
            )
        ]
    )


def _classified_reading() -> ClassifiedReading:
    return ClassifiedReading(
        source_id="TS-001",
        metric="bearing_temperature_c",
        value=Decimal("84.2"),
        unit="C",
        tier="critical",
        operating_limit_id="OL-002",
        rule_text="Normal < 82; high >= 82",
    )


def _doc_hit(document_id: str = "DOC-03") -> DocSearchHit:
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


def _state_with_document_evidence() -> GraphState:
    state = _state(intent="troubleshooting", asset=_asset())
    state["document_evidence"] = [_doc_hit()]
    return state


def _asset(asset_id: str = "PUMP-103") -> AssetRecord:
    return AssetRecord(
        asset_id=asset_id,
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
            "synthesis_evidence_used": [],
            "response": None,
        },
    )
