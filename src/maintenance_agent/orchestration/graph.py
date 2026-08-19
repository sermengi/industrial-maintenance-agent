from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.core.config import get_settings
from maintenance_agent.llm.client import (
    LLMClient,
    LLMMessage,
    LLMResponse,
    LLMTool,
    LLMToolChoice,
    ToolCallRequest,
)
from maintenance_agent.orchestration.retry import (
    AsyncSleep,
    RetryExhaustedError,
    RetryResult,
    with_retry,
)
from maintenance_agent.orchestration.state import (
    ErrorRecord,
    GraphState,
    Intent,
    StructuredEvidenceItem,
    ToolCallRecord,
    ToolResult,
)
from maintenance_agent.orchestration.tool_bindings import (
    LLMOfferedToolName,
    build_llm_tools,
    invoke_tool_binding,
)
from maintenance_agent.schemas.agent import (
    AgentError,
    AgentQueryResponse,
    AgentStatus,
    Confidence,
    DocumentEvidence,
    StructuredEvidence,
)
from maintenance_agent.tools.get_asset_status import GetAssetStatusResult
from maintenance_agent.tools.get_maintenance_history import GetMaintenanceHistoryResult
from maintenance_agent.tools.resolve_asset import ResolveAssetResult
from maintenance_agent.tools.search_maintenance_docs import SearchMaintenanceDocsResult

REQUEST_INTERPRETATION_NODE = "request_interpretation"
ASSET_RESOLUTION_NODE = "asset_resolution"
EVIDENCE_GATHERING_NODE = "evidence_gathering"
SYNTHESIS_NODE = "synthesis"
TERMINAL_RESPONSE_NODE = "terminal_response"

PostInterpretationRoute = Literal["asset_resolution", "terminal"]
InterpretationRoute = Literal["terminal", "evidence_gathering"]
EvidenceGatheringRoute = Literal["evidence_gathering", "synthesis", "terminal"]

INTERPRET_REQUEST_TOOL_NAME = "interpret_request"
CLASSIFY_REQUEST_TOOL_NAME = "classify_request"
SYNTHESIZE_RESPONSE_TOOL_NAME = "synthesize_response"

DEFAULT_REQUEST_ID = "graph-local-request"
MAX_EVIDENCE_GATHERING_ITERATIONS = 6

TOOLS_BY_INTENT: dict[Intent, tuple[LLMOfferedToolName, ...]] = {
    "procedure_lookup": ("search_maintenance_docs",),
    "troubleshooting": (
        "get_asset_status",
        "get_maintenance_history",
        "search_maintenance_docs",
        "get_plant_policy",
    ),
    "maintenance_check": (
        "get_asset_status",
        "get_maintenance_history",
        "search_maintenance_docs",
        "get_plant_policy",
    ),
    "history_query": (
        "get_asset_status",
        "get_maintenance_history",
        "search_maintenance_docs",
        "get_plant_policy",
    ),
    "work_order_request": (
        "get_asset_status",
        "get_maintenance_history",
        "search_maintenance_docs",
        "get_plant_policy",
        "create_work_order_draft",
    ),
}


class StructuredOutputValidationError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        node: str | None,
        attempts: list[ErrorRecord] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.node = node
        self.attempts = attempts or []


class IntentExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Intent
    asset_identifier: str | None = None


class RequestIntentOnly(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Intent


class SynthesisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str
    confidence: Confidence | None = None
    evidence_used: list[str]


@dataclass(frozen=True)
class AgentGraphDependencies:
    llm_client: LLMClient
    max_retry_attempts: int = field(default_factory=lambda: get_settings().max_retry_attempts)
    retry_delay_seconds: float = field(default_factory=lambda: get_settings().retry_delay_seconds)
    sleep: AsyncSleep = asyncio.sleep


def build_agent_graph(dependencies: AgentGraphDependencies) -> Any:
    async def interpret(state: GraphState) -> dict[str, object]:
        return await request_interpretation_node(
            state,
            dependencies.llm_client,
            max_retry_attempts=dependencies.max_retry_attempts,
            retry_delay_seconds=dependencies.retry_delay_seconds,
            sleep=dependencies.sleep,
        )

    async def resolve_asset(state: GraphState) -> dict[str, object]:
        return await asset_resolution_node(
            state,
            _session_from_state(state),
            max_retry_attempts=dependencies.max_retry_attempts,
            retry_delay_seconds=dependencies.retry_delay_seconds,
            sleep=dependencies.sleep,
        )

    async def gather_evidence(state: GraphState) -> dict[str, object]:
        return await evidence_gathering_node(
            state,
            dependencies.llm_client,
            _session_from_state(state),
            max_retry_attempts=dependencies.max_retry_attempts,
            retry_delay_seconds=dependencies.retry_delay_seconds,
            sleep=dependencies.sleep,
        )

    async def synthesize(state: GraphState) -> dict[str, object]:
        return await synthesis_node(
            state,
            dependencies.llm_client,
            max_retry_attempts=dependencies.max_retry_attempts,
            retry_delay_seconds=dependencies.retry_delay_seconds,
            sleep=dependencies.sleep,
        )

    async def terminal(state: GraphState) -> dict[str, AgentQueryResponse]:
        return terminal_response_node(state)

    graph = StateGraph(GraphState)
    graph.add_node(REQUEST_INTERPRETATION_NODE, interpret)
    graph.add_node(ASSET_RESOLUTION_NODE, resolve_asset)
    graph.add_node(EVIDENCE_GATHERING_NODE, gather_evidence)
    graph.add_node(SYNTHESIS_NODE, synthesize)
    graph.add_node(TERMINAL_RESPONSE_NODE, terminal)

    graph.add_edge(START, REQUEST_INTERPRETATION_NODE)
    graph.add_conditional_edges(
        REQUEST_INTERPRETATION_NODE,
        route_after_request_interpretation,
        {
            "asset_resolution": ASSET_RESOLUTION_NODE,
            "terminal": TERMINAL_RESPONSE_NODE,
        },
    )
    graph.add_conditional_edges(
        ASSET_RESOLUTION_NODE,
        route_after_asset_resolution,
        {
            "terminal": TERMINAL_RESPONSE_NODE,
            "evidence_gathering": EVIDENCE_GATHERING_NODE,
        },
    )
    graph.add_conditional_edges(
        EVIDENCE_GATHERING_NODE,
        route_after_evidence_gathering,
        {
            "evidence_gathering": EVIDENCE_GATHERING_NODE,
            "synthesis": SYNTHESIS_NODE,
            "terminal": TERMINAL_RESPONSE_NODE,
        },
    )
    graph.add_edge(SYNTHESIS_NODE, TERMINAL_RESPONSE_NODE)
    graph.add_edge(TERMINAL_RESPONSE_NODE, END)
    return graph.compile()


async def request_interpretation_node(
    state: GraphState,
    llm_client: LLMClient,
    *,
    max_retry_attempts: int | None = None,
    retry_delay_seconds: float | None = None,
    sleep: AsyncSleep = asyncio.sleep,
) -> dict[str, object]:
    max_retry_attempts = max_retry_attempts or get_settings().max_retry_attempts
    retry_delay_seconds = (
        get_settings().retry_delay_seconds
        if retry_delay_seconds is None
        else retry_delay_seconds
    )
    if state.get("asset_id_hint"):
        try:
            classification_result = await generate_structured(
                llm_client,
                [
                    LLMMessage(
                        role="user",
                        content=(
                            "Classify this maintenance request into exactly one supported "
                            f"intent.\n\nRequest: {state['query']}"
                        ),
                    )
                ],
                RequestIntentOnly,
                CLASSIFY_REQUEST_TOOL_NAME,
                node=REQUEST_INTERPRETATION_NODE,
                max_attempts=max_retry_attempts,
                delay_seconds=retry_delay_seconds,
                sleep=sleep,
            )
        except StructuredOutputValidationError as exc:
            return {"errors": exc.attempts}
        classification_update: dict[str, object] = {
            "intent": classification_result.value.intent
        }
        if classification_result.attempts:
            classification_update["errors"] = classification_result.attempts
        return classification_update

    try:
        interpretation_result = await generate_structured(
            llm_client,
            [
                LLMMessage(
                    role="user",
                    content=(
                        "Classify this maintenance request and extract the asset identifier "
                        f"if one is present.\n\nRequest: {state['query']}"
                    ),
                ),
            ],
            IntentExtractionOutput,
            INTERPRET_REQUEST_TOOL_NAME,
            node=REQUEST_INTERPRETATION_NODE,
            max_attempts=max_retry_attempts,
            delay_seconds=retry_delay_seconds,
            sleep=sleep,
        )
    except StructuredOutputValidationError as exc:
        return {"errors": exc.attempts}
    interpretation_update: dict[str, object] = {
        "intent": interpretation_result.value.intent,
        "asset_id_hint": interpretation_result.value.asset_identifier,
    }
    if interpretation_result.attempts:
        interpretation_update["errors"] = interpretation_result.attempts
    return interpretation_update


async def route_after_request_interpretation(state: GraphState) -> PostInterpretationRoute:
    if state.get("intent") is None:
        return "terminal"
    return "asset_resolution"


async def asset_resolution_node(
    state: GraphState,
    session: AsyncSession,
    *,
    max_retry_attempts: int | None = None,
    retry_delay_seconds: float | None = None,
    sleep: AsyncSleep = asyncio.sleep,
) -> dict[str, object]:
    max_retry_attempts = max_retry_attempts or get_settings().max_retry_attempts
    retry_delay_seconds = (
        get_settings().retry_delay_seconds
        if retry_delay_seconds is None
        else retry_delay_seconds
    )
    identifier = state.get("asset_id_hint") or ""
    retry_result = await with_retry(
        lambda: invoke_tool_binding(
            "resolve_asset", {"identifier": identifier}, state, session
        ),
        max_attempts=max_retry_attempts,
        delay_seconds=retry_delay_seconds,
        sleep=sleep,
        error_code="tool_execution_failed",
        node=ASSET_RESOLUTION_NODE,
    )
    result = cast(ResolveAssetResult, retry_result.value)
    update: dict[str, object] = {
        "asset": result.asset,
        "asset_resolution_status": result.status,
        "tool_calls": [
            _tool_call_record("resolve_asset", {"identifier": identifier}, result, state)
        ],
    }
    if retry_result.attempts:
        update["errors"] = retry_result.attempts
    return update


async def route_after_asset_resolution(state: GraphState) -> InterpretationRoute:
    if state.get("asset_resolution_status") == "not_found":
        return "terminal"
    return "evidence_gathering"


async def route_after_evidence_gathering(state: GraphState) -> EvidenceGatheringRoute:
    if state.get("approval_status") == "pending_approval":
        return "terminal"
    loop_complete = (
        not state.get("last_evidence_tool_call_count", 0)
        or state.get("evidence_gathering_iterations", 0) >= MAX_EVIDENCE_GATHERING_ITERATIONS
    )
    if loop_complete and _has_insufficient_evidence(state):
        return "terminal"
    if not state.get("last_evidence_tool_call_count", 0):
        return "synthesis"
    if state.get("evidence_gathering_iterations", 0) >= MAX_EVIDENCE_GATHERING_ITERATIONS:
        return "synthesis"
    return "evidence_gathering"


async def evidence_gathering_node(
    state: GraphState,
    llm_client: LLMClient,
    session: AsyncSession,
    *,
    max_retry_attempts: int | None = None,
    retry_delay_seconds: float | None = None,
    sleep: AsyncSleep = asyncio.sleep,
) -> dict[str, object]:
    max_retry_attempts = max_retry_attempts or get_settings().max_retry_attempts
    retry_delay_seconds = (
        get_settings().retry_delay_seconds
        if retry_delay_seconds is None
        else retry_delay_seconds
    )
    offered_tool_names = TOOLS_BY_INTENT[state["intent"] or "troubleshooting"]
    llm_result = await with_retry(
        lambda: llm_client.generate(
            [_evidence_message(state)],
            tools=build_llm_tools(offered_tool_names),
            tool_choice=LLMToolChoice(type="auto"),
        ),
        max_attempts=max_retry_attempts,
        delay_seconds=retry_delay_seconds,
        sleep=sleep,
        error_code="llm_call_failed",
        node=EVIDENCE_GATHERING_NODE,
    )
    response = llm_result.value

    tool_call_records: list[ToolCallRecord] = []
    structured_evidence: list[StructuredEvidenceItem] = []
    document_evidence = []
    errors = list(llm_result.attempts)
    next_iteration = state.get("evidence_gathering_iterations", 0) + 1

    for tool_call in response.tool_calls:
        if tool_call.name == "create_work_order_draft":
            draft_update: dict[str, object] = {
                "approval_status": "pending_approval",
                "evidence_gathering_iterations": next_iteration,
                "last_evidence_tool_call_count": 1,
            }
            if errors:
                draft_update["errors"] = errors
            return draft_update

        async def invoke_requested_tool(
            requested_tool_call: ToolCallRequest = tool_call,
        ) -> ToolResult:
            return await invoke_tool_binding(
                cast(LLMOfferedToolName, requested_tool_call.name),
                requested_tool_call.input,
                state,
                session,
            )

        tool_result = await with_retry(
            invoke_requested_tool,
            max_attempts=max_retry_attempts,
            delay_seconds=retry_delay_seconds,
            sleep=sleep,
            error_code="tool_execution_failed",
            node=EVIDENCE_GATHERING_NODE,
        )
        errors.extend(tool_result.attempts)
        result = tool_result.value
        tool_call_records.append(
            _tool_call_record(
                tool_call.name,
                tool_call.input,
                result,
                state,
                len(tool_call_records),
            )
        )
        if isinstance(result, GetAssetStatusResult):
            structured_evidence.extend(result.classified_readings)
            structured_evidence.extend(result.active_faults)
        elif isinstance(result, GetMaintenanceHistoryResult):
            structured_evidence.extend(result.fault_events)
            structured_evidence.extend(result.recurrence)
        elif isinstance(result, SearchMaintenanceDocsResult):
            document_evidence.extend(result.results)

    evidence_update: dict[str, object] = {
        "tool_calls": tool_call_records,
        "structured_evidence": structured_evidence,
        "document_evidence": document_evidence,
        "evidence_gathering_iterations": next_iteration,
        "last_evidence_tool_call_count": len(response.tool_calls),
    }
    if errors:
        evidence_update["errors"] = errors
    return evidence_update


async def synthesis_node(
    state: GraphState,
    llm_client: LLMClient,
    *,
    max_retry_attempts: int | None = None,
    retry_delay_seconds: float | None = None,
    sleep: AsyncSleep = asyncio.sleep,
) -> dict[str, object]:
    max_retry_attempts = max_retry_attempts or get_settings().max_retry_attempts
    retry_delay_seconds = (
        get_settings().retry_delay_seconds
        if retry_delay_seconds is None
        else retry_delay_seconds
    )
    try:
        result = await generate_structured(
            llm_client,
            [_synthesis_message(state)],
            SynthesisOutput,
            SYNTHESIZE_RESPONSE_TOOL_NAME,
            node=SYNTHESIS_NODE,
            max_attempts=max_retry_attempts,
            delay_seconds=retry_delay_seconds,
            sleep=sleep,
        )
    except StructuredOutputValidationError as exc:
        return {"errors": exc.attempts}
    update: dict[str, object] = {
        "synthesis_answer": result.value.answer,
        "synthesis_confidence": result.value.confidence,
        "synthesis_evidence_used": result.value.evidence_used,
    }
    if result.attempts:
        update["errors"] = result.attempts
    return update


def terminal_response_node(state: GraphState) -> dict[str, AgentQueryResponse]:
    error = state.get("errors", [])[-1] if state.get("errors") else None
    status = _terminal_status(state)
    response = AgentQueryResponse(
        request_id=state.get("request_id", DEFAULT_REQUEST_ID),
        status=status,
        asset_id=_response_asset_id(state, status),
        answer=_response_answer(state, status),
        confidence=None
        if status in {"unknown_asset", "insufficient_evidence", "error"}
        else state.get("synthesis_confidence"),
        structured_evidence=_response_structured_evidence(state),
        document_evidence=[
            DocumentEvidence(
                document_id=hit.document_id,
                section=hit.section,
                excerpt=hit.evidence_text,
            )
            for hit in state.get("document_evidence", [])
        ],
        pending_action=None,
        error=AgentError(code=error.code, message=error.message)
        if status == "error" and error is not None
        else None,
    )
    return {"response": response}


def _terminal_status(state: GraphState) -> AgentStatus:
    if state.get("asset_resolution_status") == "not_found":
        return "unknown_asset"
    if state.get("approval_status") == "pending_approval":
        return "needs_approval"
    if _has_insufficient_evidence(state):
        return "insufficient_evidence"
    if state.get("errors") and state.get("synthesis_answer") is None:
        return "error"
    return "ok"


def _response_asset_id(state: GraphState, status: AgentStatus) -> str | None:
    if status == "unknown_asset":
        return None
    asset = state.get("asset")
    if asset is not None:
        return asset.asset_id
    return state.get("asset_id_hint")


def _response_answer(state: GraphState, status: AgentStatus) -> str | None:
    if status == "unknown_asset":
        return _unknown_asset_answer(state)
    if status == "insufficient_evidence":
        return _insufficient_evidence_answer()
    if status == "error":
        return None
    return state.get("synthesis_answer")


def _unknown_asset_answer(state: GraphState) -> str:
    identifier = _attempted_asset_identifier(state)
    if identifier:
        return (
            f"I couldn't find an asset matching '{identifier}'. "
            "Please provide a valid asset ID."
        )
    return "I couldn't find a matching asset. Please provide a valid asset ID."


def _attempted_asset_identifier(state: GraphState) -> str | None:
    for record in reversed(state.get("tool_calls", [])):
        if record.tool_name != "resolve_asset":
            continue
        identifier = record.args.get("identifier")
        return str(identifier) if identifier is not None else None
    return None


def _has_insufficient_evidence(state: GraphState) -> bool:
    if state.get("asset_resolution_status") != "resolved":
        return False
    if state.get("intent") == "procedure_lookup":
        return not state.get("document_evidence", [])
    return not state.get("structured_evidence", []) and not state.get("document_evidence", [])


def _insufficient_evidence_answer() -> str:
    return (
        "Not enough evidence was found to answer this request. "
        "Try rephrasing or providing more detail."
    )


def _session_from_state(state: GraphState) -> AsyncSession:
    session = state.get("session")
    if session is None:
        raise RuntimeError("Graph state is missing the request-scoped database session.")
    return session


def _structured_tool(name: str, model: type[BaseModel]) -> LLMTool:
    return LLMTool(
        name=name,
        description=f"Return structured data for {name}.",
        input_schema=model.model_json_schema(),
    )


async def generate_structured[StructuredOutputT: BaseModel](
    client: LLMClient,
    messages: Sequence[LLMMessage],
    output_model: type[StructuredOutputT],
    tool_name: str,
    *,
    node: str | None = None,
    extra_validator: Callable[[StructuredOutputT], None] | None = None,
    max_attempts: int | None = None,
    delay_seconds: float | None = None,
    sleep: AsyncSleep = asyncio.sleep,
) -> RetryResult[StructuredOutputT]:
    max_attempts = max_attempts or get_settings().max_retry_attempts
    delay_seconds = get_settings().retry_delay_seconds if delay_seconds is None else delay_seconds
    attempt_messages = list(messages)
    attempts: list[ErrorRecord] = []

    for attempt_number in range(1, max_attempts + 1):
        try:
            response = await client.generate(
                attempt_messages,
                tools=[_structured_tool(tool_name, output_model)],
                tool_choice=LLMToolChoice(type="tool", name=tool_name),
            )
            tool_call = _single_structured_tool_call(response, tool_name)
            output = output_model.model_validate(tool_call.input)
            if extra_validator is not None:
                try:
                    extra_validator(output)
                except Exception as exc:
                    raise ValueError(str(exc)) from exc
            return RetryResult(value=output, attempts=attempts)
        except (ValidationError, ValueError) as exc:
            message = str(exc)
            error_record = ErrorRecord(
                code="structured_output_invalid",
                message=message,
                node=node,
                recoverable=True,
            )
            if attempt_number >= max_attempts:
                raise StructuredOutputValidationError(
                    code="structured_output_invalid",
                    message=message,
                    node=node,
                    attempts=[*attempts, error_record],
                ) from exc
            attempts.append(error_record)
            attempt_messages = [
                *attempt_messages,
                _structured_retry_message(message),
            ]
            await sleep(delay_seconds)
        except Exception as exc:
            message = str(exc)
            attempts.append(
                ErrorRecord(
                    code="llm_call_failed",
                    message=message,
                    node=node,
                    recoverable=True,
                )
            )
            if attempt_number >= max_attempts:
                raise RetryExhaustedError(message, attempts, exc) from exc
            await sleep(delay_seconds)

    raise StructuredOutputValidationError(
        code="structured_output_invalid",
        message="Structured output retry loop exhausted.",
        node=node,
        attempts=attempts,
    )


def _structured_retry_message(validation_error: str) -> LLMMessage:
    return LLMMessage(
        role="user",
        content=(
            "Your last structured response was invalid. Regenerate it using the "
            "required tool schema exactly.\n\n"
            f"Validation error: {validation_error}"
        ),
    )


def _single_structured_tool_call(response: LLMResponse, tool_name: str) -> Any:
    if len(response.tool_calls) != 1:
        raise ValueError(
            f"Expected exactly one structured tool call named {tool_name}, "
            f"got {len(response.tool_calls)}."
        )
    tool_call = response.tool_calls[0]
    if tool_call.name != tool_name:
        raise ValueError(
            f"Expected structured tool call named {tool_name}, got {tool_call.name}."
        )
    return tool_call


def _evidence_message(state: GraphState) -> LLMMessage:
    return LLMMessage(
        role="user",
        content=(
            "Choose any maintenance tools needed to gather evidence. "
            "Return no tool calls when no more evidence is needed.\n\n"
            f"Intent: {state.get('intent')}\n"
            f"Asset: {state.get('asset')}\n"
            f"Request: {state['query']}"
        ),
    )


def _synthesis_message(state: GraphState) -> LLMMessage:
    return LLMMessage(
        role="user",
        content=(
            "Synthesize the final maintenance answer from the accumulated evidence. "
            "Set confidence to confirmed only when the cited evidence directly supports "
            "the answer; set confidence to hypothesis when evidence is relevant but "
            "does not prove a root cause or diagnosis.\n\n"
            f"Request: {state['query']}\n"
            f"Asset: {state.get('asset')}\n"
            f"Structured evidence: {state.get('structured_evidence', [])}\n"
            f"Document evidence: {state.get('document_evidence', [])}\n"
            f"Tool trace: {state.get('tool_calls', [])}"
        ),
    )


def _tool_call_record(
    tool_name: str,
    args: dict[str, object],
    result: ToolResult,
    state: GraphState,
    offset: int = 0,
) -> ToolCallRecord:
    return ToolCallRecord(
        tool_name=tool_name,
        args=args,
        result=result,
        timestamp=datetime.now(UTC),
        sequence=len(state.get("tool_calls", [])) + offset + 1,
    )


def _response_structured_evidence(state: GraphState) -> list[StructuredEvidence]:
    return [
        StructuredEvidence(
            source=item.__class__.__name__,
            summary=str(item),
            reference_id=_reference_id(item),
        )
        for item in state.get("structured_evidence", [])
    ]


def _reference_id(item: object) -> str | None:
    for field_name in (
        "event_id",
        "fault_code",
        "operating_limit_id",
        "maintenance_id",
        "work_order_id",
    ):
        value = getattr(item, field_name, None)
        if value is not None:
            return str(value)
    return None
