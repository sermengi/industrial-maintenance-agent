from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db import session as db_session
from maintenance_agent.orchestration.graph import build_response
from maintenance_agent.orchestration.state import GraphState
from maintenance_agent.schemas.agent import (
    AgentApprovalRequest,
    AgentError,
    AgentQueryRequest,
    AgentQueryResponse,
)

router = APIRouter()


@router.post("/query", response_model=AgentQueryResponse)
async def query_agent(
    request: Request,
    body: AgentQueryRequest,
) -> AgentQueryResponse:
    request_id = str(uuid4())
    try:
        async with _request_session() as session:
            return await _invoke_agent_graph(request, body, request_id, session)
    except Exception as exc:
        return AgentQueryResponse(
            request_id=request_id,
            status="error",
            asset_id=body.asset_id,
            answer=None,
            confidence=None,
            structured_evidence=[],
            document_evidence=[],
            pending_action=None,
            error=AgentError(
                code="unhandled_exception",
                message=str(exc),
            ),
        )


@router.post("/approvals/{draft_id}", response_model=AgentQueryResponse)
async def resolve_pending_action(
    request: Request,
    draft_id: str,
    body: AgentApprovalRequest,
) -> AgentQueryResponse:
    async with _request_session() as session:
        graph = cast(Any, request.app.state.agent_graph)
        config = _thread_config(draft_id, session)
        checkpoint = await graph.aget_state(config)
        if not getattr(checkpoint, "values", None):
            raise HTTPException(status_code=404, detail="Pending action not found.")
        if not getattr(checkpoint, "next", ()):
            raise HTTPException(status_code=409, detail="Pending action has already been resolved.")

        final_state = await graph.ainvoke(Command(resume=body.decision), config=config)
        response = cast(AgentQueryResponse | None, final_state.get("response"))
        if response is None:
            raise RuntimeError("Agent graph completed without a response.")
        return response


async def _invoke_agent_graph(
    request: Request,
    body: AgentQueryRequest,
    request_id: str,
    session: AsyncSession,
) -> AgentQueryResponse:
    graph = cast(Any, request.app.state.agent_graph)
    config = _thread_config(request_id, session)
    final_state = await graph.ainvoke(_initial_state(body, request_id, session), config=config)
    checkpoint = graph.get_state(config)
    if checkpoint.next:
        return build_response(cast(GraphState, checkpoint.values), status="needs_approval")
    response = cast(AgentQueryResponse | None, final_state.get("response"))
    if response is None:
        raise RuntimeError("Agent graph completed without a response.")
    return response


@asynccontextmanager
async def _request_session() -> AsyncGenerator[AsyncSession]:
    if db_session.async_session_factory is None:
        raise RuntimeError("DATABASE_URL is not configured.")
    async with db_session.async_session_factory() as session:
        yield session


def _initial_state(
    request: AgentQueryRequest,
    request_id: str,
    session: AsyncSession,
) -> GraphState:
    return GraphState(
        request_id=request_id,
        session=session,
        query=request.query,
        asset_id_hint=request.asset_id,
        fault_code_hint=request.fault_code,
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


def _thread_config(request_id: str, session: AsyncSession) -> dict[str, object]:
    return {"configurable": {"thread_id": request_id, "session": session}}
