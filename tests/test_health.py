import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.api import agent as agent_api
from maintenance_agent.api.agent import query_agent, resolve_pending_action
from maintenance_agent.api.health import health
from maintenance_agent.core.config import get_settings
from maintenance_agent.db.repositories.records import WorkOrderRecord
from maintenance_agent.orchestration.state import ToolCallRecord, WorkOrderDraft
from maintenance_agent.schemas.agent import (
    AgentApprovalRequest,
    AgentError,
    AgentQueryRequest,
    AgentQueryResponse,
    StructuredEvidence,
)
from maintenance_agent.schemas.run_event import RunEvent
from maintenance_agent.telemetry.run_events import record_run_event
from maintenance_agent.tools.resolve_asset import ResolveAssetResult


def test_app_registers_phase_zero_routes(app: FastAPI) -> None:
    routes = set(app.openapi()["paths"])

    assert "/health" in routes
    assert "/agent/query" in routes
    assert "/agent/approvals/{draft_id}" in routes


@pytest.mark.asyncio
async def test_health() -> None:
    payload = await health(get_settings())

    assert payload["status"] == "ok"
    assert isinstance(payload["environment"], str)


@pytest.mark.asyncio
async def test_agent_query_returns_validated_graph_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _FakeGraph()
    request = AgentQueryRequest(query="Why is pump A vibrating?")
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    response = await query_agent(
        _request_with_graph(graph),
        request,
    )

    assert response.model_dump() == {
        "request_id": response.request_id,
        "status": "ok",
        "asset_id": "PUMP-103",
        "answer": "Inspect the bearing and follow the maintenance procedure.",
        "confidence": "confirmed",
        "evidence_used": [],
        "structured_evidence": [],
        "document_evidence": [],
        "pending_action": None,
        "error": None,
    }
    UUID(response.request_id)
    assert graph.state is not None
    assert graph.state["request_id"] == response.request_id
    assert graph.state["query"] == request.query
    assert graph.config is not None
    assert graph.config["configurable"]["thread_id"] == response.request_id
    assert graph.config["configurable"]["session"] is not None


@pytest.mark.asyncio
async def test_agent_query_captures_run_event_with_route_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _FakeGraph()
    request = _request_with_graph(graph)
    emitted_events: list[RunEvent] = []
    request.app.state.emit_run_event = _collecting_emitter(emitted_events)
    clock = _SequenceClock(
        [
            datetime(2026, 8, 21, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 10, 0, 1, 250000, tzinfo=UTC),
        ]
    )
    request.app.state.run_event_clock = clock
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    response = await query_agent(
        request,
        AgentQueryRequest(query="Why is pump A vibrating?"),
    )

    assert len(emitted_events) == 1
    event = emitted_events[0]
    assert event.run_id == response.request_id
    assert event.event_id
    assert event.emitted_at == datetime(2026, 8, 21, 10, 0, 1, 250000, tzinfo=UTC)
    assert event.latency_ms == 1250
    assert event.status == response.status
    assert event.request == "Why is pump A vibrating?"
    assert event.final_output is response
    assert event.error is None
    assert event.tool_calls == []


@pytest.mark.asyncio
async def test_agent_query_seeds_optional_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _FakeGraph()
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    await query_agent(
        _request_with_graph(graph),
        AgentQueryRequest(
            query="What does F-101 mean on pump PUMP-104?",
            asset_id="PUMP-104",
            fault_code="F-101",
        ),
    )

    assert graph.state is not None
    assert graph.state["asset_id_hint"] == "PUMP-104"
    assert graph.state["fault_code_hint"] == "F-101"


@pytest.mark.asyncio
async def test_agent_query_generates_request_id_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    first = await query_agent(
        _request_with_graph(_FakeGraph()),
        AgentQueryRequest(query="Check pump vibration."),
    )
    second = await query_agent(
        _request_with_graph(_FakeGraph()),
        AgentQueryRequest(query="Check pump vibration."),
    )

    UUID(first.request_id)
    UUID(second.request_id)
    assert first.request_id != second.request_id


@pytest.mark.asyncio
async def test_agent_query_builds_needs_approval_response_from_interrupted_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _InterruptedGraph()
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    response = await query_agent(
        _request_with_graph(graph),
        AgentQueryRequest(query="Create a work order for PUMP-103.", asset_id="PUMP-103"),
    )

    assert response.status == "needs_approval"
    assert response.pending_action is not None
    assert response.pending_action.action_type == "submit_work_order"
    assert response.pending_action.draft_id == response.request_id
    assert response.answer is not None
    assert "Recurring bearing overheating" in response.answer
    assert graph.config is not None
    assert graph.config["configurable"]["thread_id"] == response.request_id
    assert graph.config["configurable"]["session"] is not None


@pytest.mark.asyncio
async def test_agent_query_maps_unhandled_graph_exception_to_error_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request_with_graph(_FailingGraph())
    emitted_events: list[RunEvent] = []
    request.app.state.emit_run_event = _collecting_emitter(emitted_events)
    request.app.state.run_event_clock = _SequenceClock(
        [
            datetime(2026, 8, 21, 11, 0, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 11, 0, 0, 750000, tzinfo=UTC),
        ]
    )
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    response = await query_agent(
        request,
        AgentQueryRequest(query="Check pump vibration.", asset_id="PUMP-103"),
    )

    assert response.status == "error"
    assert response.asset_id == "PUMP-103"
    assert response.answer is None
    assert response.evidence_used == []
    assert response.structured_evidence == []
    assert response.document_evidence == []
    assert response.error == AgentError(
        code="unhandled_exception",
        message="An unexpected error occurred. Please try again shortly.",
    )
    UUID(response.request_id)
    assert len(emitted_events) == 1
    event = emitted_events[0]
    assert event.run_id == response.request_id
    assert event.status == "error"
    assert event.request == "Check pump vibration."
    assert event.latency_ms == 750
    assert event.final_output is response
    assert event.error == response.error


@pytest.mark.asyncio
async def test_agent_query_never_leaks_raw_exception_text_but_logs_it(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_marker = "RAW_ROUTE_FAILURE_MARKER"

    class _MarkerFailingGraph:
        async def ainvoke(
            self,
            state: dict[str, Any],
            config: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            del state, config
            raise RuntimeError(raw_marker)

    request = _request_with_graph(_MarkerFailingGraph())
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    with caplog.at_level(logging.ERROR, logger="maintenance_agent.api.agent"):
        response = await query_agent(
            request,
            AgentQueryRequest(query="Check pump vibration."),
        )

    assert response.status == "error"
    assert response.error is not None
    assert raw_marker not in response.error.message
    assert response.error.message == "An unexpected error occurred. Please try again shortly."
    assert raw_marker in caplog.text


@pytest.mark.asyncio
async def test_approval_endpoint_resumes_pending_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _ApprovalGraph(decision="approve")
    request = _request_with_graph(graph)
    emitted_events: list[RunEvent] = []
    request.app.state.emit_run_event = _collecting_emitter(emitted_events)
    request.app.state.run_event_clock = _SequenceClock(
        [
            datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 12, 0, 0, 125000, tzinfo=UTC),
        ]
    )
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    response = await resolve_pending_action(
        request,
        "draft-123",
        AgentApprovalRequest(decision="approve"),
    )

    assert response.status == "ok"
    assert response.pending_action is None
    assert response.structured_evidence == [
        StructuredEvidence(
            source="WorkOrderRecord",
            source_type="work_order",
            source_id="WO-003",
            summary=str(
                WorkOrderRecord(
                    work_order_id="WO-003",
                    asset_id="PUMP-103",
                    issue="Recurring bearing overheating",
                    priority="high",
                    status="submitted",
                    created_at=date(2026, 8, 20),
                    approved=True,
                )
            ),
            reference_id="WO-003",
        )
    ]
    assert graph.resume_value == "approve"
    assert graph.config is not None
    assert graph.config["configurable"]["thread_id"] == "draft-123"
    assert graph.config["configurable"]["session"] is not None
    assert len(emitted_events) == 1
    event = emitted_events[0]
    assert event.run_id == "draft-123"
    assert event.request == "approve"
    assert event.status == "ok"
    assert event.latency_ms == 125
    assert event.final_output is response


@pytest.mark.asyncio
async def test_failed_run_event_emission_is_logged_without_changing_response(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = _request_with_graph(_FakeGraph())
    request.app.state.emit_run_event = _failing_emitter
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    with caplog.at_level(logging.WARNING, logger="maintenance_agent.telemetry.run_events"):
        response = await query_agent(
            request,
            AgentQueryRequest(query="Why is pump A vibrating?"),
        )

    assert response.status == "ok"
    assert response.asset_id == "PUMP-103"
    assert "Failed to emit run event." in caplog.text


@pytest.mark.asyncio
async def test_record_run_event_never_raises_and_logs_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event = RunEvent(
        event_id=UUID("11111111-1111-4111-8111-111111111111"),
        run_id="req-123",
        emitted_at=datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        latency_ms=10,
        status="ok",
        request="Check PUMP-103.",
        tool_calls=[],
        final_output=AgentQueryResponse(request_id="req-123", status="ok"),
        error=None,
    )

    with caplog.at_level(logging.WARNING, logger="maintenance_agent.telemetry.run_events"):
        await record_run_event(_failing_emitter, event)

    assert "Failed to emit run event." in caplog.text


def test_run_event_tool_calls_are_sourced_from_state_sorted_by_sequence() -> None:
    response = AgentQueryResponse(request_id="req-123", status="ok")
    event = agent_api._build_run_event(
        start=datetime(2026, 8, 21, 10, 0, 0, tzinfo=UTC),
        end=datetime(2026, 8, 21, 10, 0, 0, 100000, tzinfo=UTC),
        run_id="req-123",
        request_text="Check PUMP-103.",
        state=cast(
            Any,
            {
                "tool_calls": [
                    _tool_call("get_asset_status", 2),
                    _tool_call("resolve_asset", 1),
                ]
            },
        ),
        response=response,
    )

    assert [(call.tool_name, call.sequence) for call in event.tool_calls] == [
        ("resolve_asset", 1),
        ("get_asset_status", 2),
    ]


@pytest.mark.asyncio
async def test_approval_endpoint_resumes_pending_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _ApprovalGraph(decision="reject")
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    response = await resolve_pending_action(
        _request_with_graph(graph),
        "draft-123",
        AgentApprovalRequest(decision="reject"),
    )

    assert response.status == "ok"
    assert response.pending_action is None
    assert response.structured_evidence == []
    assert "No work order was created" in cast(str, response.answer)
    assert graph.resume_value == "reject"


@pytest.mark.asyncio
async def test_approval_endpoint_returns_404_for_unknown_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    with pytest.raises(HTTPException) as exc_info:
        await resolve_pending_action(
            _request_with_graph(_NoCheckpointGraph()),
            "missing-draft",
            AgentApprovalRequest(decision="approve"),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_approval_endpoint_returns_409_for_resolved_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _ResolvedCheckpointGraph()
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    with pytest.raises(HTTPException) as exc_info:
        await resolve_pending_action(
            _request_with_graph(graph),
            "resolved-draft",
            AgentApprovalRequest(decision="approve"),
        )

    assert exc_info.value.status_code == 409
    assert graph.invoke_count == 0


class _FakeGraph:
    def __init__(self) -> None:
        self.state: dict[str, Any] | None = None
        self.config: dict[str, Any] | None = None

    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.state = state
        self.config = config
        return {
            "response": AgentQueryResponse(
                request_id=state["request_id"],
                status="ok",
                asset_id="PUMP-103",
                answer="Inspect the bearing and follow the maintenance procedure.",
                confidence="confirmed",
                structured_evidence=[],
                document_evidence=[],
                pending_action=None,
                error=None,
            )
        }

    def get_state(self, config: dict[str, Any]) -> Any:
        self.config = config
        return SimpleNamespace(next=(), values=self.state)


class _FailingGraph:
    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del state, config
        raise RuntimeError("graph failed")


class _InterruptedGraph:
    def __init__(self) -> None:
        self.state: dict[str, Any] | None = None
        self.config: dict[str, Any] | None = None

    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.state = {
            **state,
            "asset_resolution_status": "resolved",
            "work_order_draft": WorkOrderDraft(
                draft_id=state["request_id"],
                asset_id="PUMP-103",
                issue="Recurring bearing overheating",
                recommended_action="Investigate root cause.",
                priority="high",
                supporting_evidence=[],
            ),
            "approval_status": "pending_approval",
            "response": None,
        }
        self.config = config
        return {**self.state, "__interrupt__": []}

    def get_state(self, config: dict[str, Any]) -> Any:
        self.config = config
        return SimpleNamespace(next=("await_approval",), values=self.state)


class _ApprovalGraph:
    def __init__(self, decision: str) -> None:
        self.decision = decision
        self.resume_value: str | None = None
        self.config: dict[str, Any] | None = None

    async def aget_state(self, config: dict[str, Any]) -> Any:
        self.config = config
        return SimpleNamespace(next=("await_approval",), values={"request_id": "draft-123"})

    async def ainvoke(
        self,
        command: Any,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.config = config
        self.resume_value = cast(str, command.resume)
        if self.decision == "reject":
            return {
                "response": AgentQueryResponse(
                    request_id="draft-123",
                    status="ok",
                    asset_id="PUMP-103",
                    answer="Work order draft draft-123 was rejected. No work order was created.",
                    confidence=None,
                    structured_evidence=[],
                    document_evidence=[],
                    pending_action=None,
                    error=None,
                )
            }
        record = WorkOrderRecord(
            work_order_id="WO-003",
            asset_id="PUMP-103",
            issue="Recurring bearing overheating",
            priority="high",
            status="submitted",
            created_at=date(2026, 8, 20),
            approved=True,
        )
        return {
            "response": AgentQueryResponse(
                request_id="draft-123",
                status="ok",
                asset_id="PUMP-103",
                answer="Work order WO-003 has been submitted for PUMP-103 (priority: high).",
                confidence=None,
                structured_evidence=[
                    StructuredEvidence(
                        source="WorkOrderRecord",
                        source_type="work_order",
                        source_id="WO-003",
                        summary=str(record),
                        reference_id="WO-003",
                    )
                ],
                document_evidence=[],
                pending_action=None,
                error=None,
            )
        }


class _NoCheckpointGraph:
    async def aget_state(self, config: dict[str, Any]) -> Any:
        del config
        return SimpleNamespace(next=(), values={})


class _ResolvedCheckpointGraph:
    def __init__(self) -> None:
        self.invoke_count = 0

    async def aget_state(self, config: dict[str, Any]) -> Any:
        del config
        return SimpleNamespace(next=(), values={"request_id": "resolved-draft"})

    async def ainvoke(
        self,
        command: Any,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del command, config
        self.invoke_count += 1
        return {}


def _request_with_graph(graph: object) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(agent_graph=graph)),
        state=SimpleNamespace(),
    )


class _SequenceClock:
    def __init__(self, values: list[datetime]) -> None:
        self._values = values

    def __call__(self) -> datetime:
        if not self._values:
            raise AssertionError("Unexpected clock call.")
        return self._values.pop(0)


def _tool_call(tool_name: str, sequence: int) -> ToolCallRecord:
    return ToolCallRecord(
        tool_name=tool_name,
        args={},
        result=ResolveAssetResult(status="not_found"),
        timestamp=datetime(2026, 8, 21, 10, 0, sequence, tzinfo=UTC),
        sequence=sequence,
    )


def _collecting_emitter(events: list[RunEvent]) -> Any:
    async def emit(event: RunEvent) -> None:
        events.append(event)

    return emit


async def _failing_emitter(event: RunEvent) -> None:
    del event
    raise OSError("telemetry unavailable")


@asynccontextmanager
async def _fake_session_context() -> AsyncGenerator[AsyncSession]:
    yield cast(AsyncSession, object())
