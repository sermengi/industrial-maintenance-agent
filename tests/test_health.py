from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.api import agent as agent_api
from maintenance_agent.api.agent import query_agent
from maintenance_agent.api.health import health
from maintenance_agent.core.config import get_settings
from maintenance_agent.orchestration.state import WorkOrderDraft
from maintenance_agent.schemas.agent import AgentError, AgentQueryRequest, AgentQueryResponse


def test_app_registers_phase_zero_routes(app: FastAPI) -> None:
    routes = set(app.openapi()["paths"])

    assert "/health" in routes
    assert "/agent/query" in routes


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
    monkeypatch.setattr(agent_api, "_request_session", _fake_session_context)

    response = await query_agent(
        _request_with_graph(_FailingGraph()),
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
        message="graph failed",
    )
    UUID(response.request_id)


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


def _request_with_graph(graph: object) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent_graph=graph)))


@asynccontextmanager
async def _fake_session_context() -> AsyncGenerator[AsyncSession]:
    yield cast(AsyncSession, object())
