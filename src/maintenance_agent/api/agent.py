import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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
from maintenance_agent.schemas.run_event import RunEvent, ToolCallSummary
from maintenance_agent.telemetry.run_events import EmitFn, noop_emit_run_event, record_run_event

router = APIRouter()
logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]


@router.post("/query", response_model=AgentQueryResponse)
async def query_agent(
    request: Request,
    body: AgentQueryRequest,
) -> AgentQueryResponse:
    clock = _route_clock(request)
    start = clock()
    request_id = str(uuid4())
    try:
        async with _request_session() as session:
            response, state = await _invoke_agent_graph(request, body, request_id, session)
            await _capture_run_event(
                request,
                start=start,
                end=clock(),
                run_id=request_id,
                request_text=body.query,
                state=state,
                response=response,
            )
            return response
    except Exception:
        logger.exception("Unhandled exception in /agent/query.")
        response = AgentQueryResponse(
            request_id=request_id,
            status="error",
            asset_id=await _asset_id_from_checkpoint(request, request_id),
            answer=None,
            confidence=None,
            structured_evidence=[],
            document_evidence=[],
            pending_action=None,
            error=AgentError(
                code="unhandled_exception",
                message="An unexpected error occurred. Please try again shortly.",
            ),
        )
        await _capture_run_event(
            request,
            start=start,
            end=clock(),
            run_id=request_id,
            request_text=body.query,
            state=None,
            response=response,
        )
        return response


@router.post("/approvals/{draft_id}", response_model=AgentQueryResponse)
async def resolve_pending_action(
    request: Request,
    draft_id: str,
    body: AgentApprovalRequest,
) -> AgentQueryResponse:
    clock = _route_clock(request)
    start = clock()
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
        await _capture_run_event(
            request,
            start=start,
            end=clock(),
            run_id=draft_id,
            request_text=body.decision,
            state=cast(GraphState, final_state),
            response=response,
        )
        return response


async def _invoke_agent_graph(
    request: Request,
    body: AgentQueryRequest,
    request_id: str,
    session: AsyncSession,
) -> tuple[AgentQueryResponse, GraphState]:
    graph = cast(Any, request.app.state.agent_graph)
    config = _thread_config(request_id, session)
    final_state = await graph.ainvoke(_initial_state(body, request_id, session), config=config)
    checkpoint = graph.get_state(config)
    if checkpoint.next:
        interrupted_state = cast(GraphState, checkpoint.values)
        return build_response(interrupted_state, status="needs_approval"), interrupted_state
    response = cast(AgentQueryResponse | None, final_state.get("response"))
    if response is None:
        raise RuntimeError("Agent graph completed without a response.")
    return response, cast(GraphState, final_state)


async def _asset_id_from_checkpoint(request: Request, request_id: str) -> str | None:
    try:
        graph = cast(Any, request.app.state.agent_graph)
        checkpoint = await graph.aget_state({"configurable": {"thread_id": request_id}})
    except Exception:
        return None
    values = getattr(checkpoint, "values", None) or {}
    asset = values.get("asset")
    return asset.asset_id if asset is not None else None


async def _capture_run_event(
    request: Request,
    *,
    start: datetime,
    end: datetime,
    run_id: str,
    request_text: str,
    state: GraphState | None,
    response: AgentQueryResponse,
) -> RunEvent:
    event = _build_run_event(
        start=start,
        end=end,
        run_id=run_id,
        request_text=request_text,
        state=state,
        response=response,
    )
    await record_run_event(_run_event_emitter(request), event)
    return event


def _build_run_event(
    *,
    start: datetime,
    end: datetime,
    run_id: str,
    request_text: str,
    state: GraphState | None,
    response: AgentQueryResponse,
) -> RunEvent:
    return RunEvent(
        event_id=uuid4(),
        run_id=run_id,
        emitted_at=end,
        latency_ms=_latency_ms(start, end),
        status=response.status,
        request=request_text,
        tool_calls=_tool_call_summaries(state),
        final_output=response,
        error=response.error,
    )


def _tool_call_summaries(state: GraphState | None) -> list[ToolCallSummary]:
    if state is None:
        return []
    return [
        ToolCallSummary(tool_name=record.tool_name, sequence=record.sequence)
        for record in sorted(state.get("tool_calls", []), key=lambda record: record.sequence)
    ]


def _latency_ms(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() * 1000))


def _route_clock(request: Request) -> Clock:
    clock = getattr(request.app.state, "run_event_clock", None)
    if clock is None:
        return _utc_now
    return cast(Clock, clock)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _run_event_emitter(request: Request) -> EmitFn:
    emit = getattr(request.app.state, "emit_run_event", None)
    if emit is None:
        return noop_emit_run_event
    return cast(EmitFn, emit)


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
