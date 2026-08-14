from uuid import UUID

import pytest
from fastapi import FastAPI

from maintenance_agent.api.agent import query_agent
from maintenance_agent.api.health import health
from maintenance_agent.core.config import get_settings
from maintenance_agent.schemas.agent import AgentQueryRequest


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
async def test_agent_query_stub() -> None:
    response = await query_agent(AgentQueryRequest(query="Why is pump A vibrating?"))

    assert response.model_dump() == {
        "request_id": response.request_id,
        "status": "ok",
        "asset_id": None,
        "answer": "Agent query handling is not implemented yet.",
        "confidence": None,
        "structured_evidence": [],
        "document_evidence": [],
        "pending_action": None,
        "error": None,
    }
    UUID(response.request_id)


@pytest.mark.asyncio
async def test_agent_query_accepts_optional_hints() -> None:
    response = await query_agent(
        AgentQueryRequest(
            query="What does F-101 mean on pump PUMP-104?",
            asset_id="PUMP-104",
            fault_code="F-101",
        )
    )

    assert response.status == "ok"
    assert response.asset_id is None
    assert response.answer


@pytest.mark.asyncio
async def test_agent_query_generates_request_id_per_call() -> None:
    first = await query_agent(AgentQueryRequest(query="Check pump vibration."))
    second = await query_agent(AgentQueryRequest(query="Check pump vibration."))

    UUID(first.request_id)
    UUID(second.request_id)
    assert first.request_id != second.request_id
