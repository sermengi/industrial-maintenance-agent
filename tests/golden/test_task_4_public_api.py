from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator, Sequence
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import yaml
from alembic import command
from alembic.config import Config
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from maintenance_agent.core.config import get_settings
from maintenance_agent.db import session as db_session
from maintenance_agent.db.bootstrap import reset_database
from maintenance_agent.llm.client import AnthropicLLMClient, get_llm_client
from maintenance_agent.main import create_app
from maintenance_agent.orchestration.tool_bindings import CANONICAL_TOOL_NAMES, CanonicalToolName
from maintenance_agent.schemas.agent import AgentQueryResponse, AgentStatus
from maintenance_agent.schemas.run_event import RunEvent

SCENARIOS_PATH = Path(__file__).with_name("scenarios.yaml")
MANUAL_REVIEW_REPORT_PATH = Path(__file__).with_name("manual_review_report.md")
TROUBLESHOOTING_SCENARIO_IDS = {"GS-01", "GS-02", "GS-03", "GS-04", "GS-05"}
GOLDEN_RUN_FLAG = "RUN_GOLDEN_SCENARIOS"
_manual_review_rows: list[dict[str, str]] = []


class ApprovalStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: str
    expected_status: str
    required_tools_after_resume: list[CanonicalToolName]
    required_evidence_source_types_after_resume: list[str]


class GoldenScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    query: str
    asset_id_hint: str | None
    expected_intent: str
    expected_asset_id: str | None
    required_tools: list[CanonicalToolName]
    optional_tools: list[CanonicalToolName]
    expected_status: AgentStatus
    required_evidence_ids: list[str]
    hitl: bool
    approval_step: ApprovalStep | None


class GoldenApiHarness(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    client: httpx.AsyncClient
    emitted_events: list[RunEvent]


def load_golden_scenarios() -> list[GoldenScenario]:
    payload = yaml.safe_load(SCENARIOS_PATH.read_text(encoding="utf-8"))
    return [GoldenScenario.model_validate(item) for item in payload["scenarios"]]


@pytest_asyncio.fixture(scope="session")
async def golden_database_url() -> AsyncGenerator[str]:
    if os.getenv(GOLDEN_RUN_FLAG) != "1":
        pytest.skip(f"Set {GOLDEN_RUN_FLAG}=1 to run live golden public-API scenarios.")
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY is required for live golden public-API scenarios.")

    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for live golden public-API scenarios.")

    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    await reset_database(database_url)

    engine = create_async_engine(database_url, pool_pre_ping=True)
    db_session.engine = engine
    db_session.async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield database_url
    finally:
        await engine.dispose()
        db_session.engine = db_session.create_engine()
        db_session.async_session_factory = (
            async_sessionmaker(db_session.engine, expire_on_commit=False)
            if db_session.engine is not None
            else None
        )
        get_settings.cache_clear()


@pytest_asyncio.fixture
async def golden_api(golden_database_url: str) -> AsyncGenerator[GoldenApiHarness]:
    del golden_database_url
    emitted_events: list[RunEvent] = []
    app = create_app()

    async with app.router.lifespan_context(app):
        app.state.emit_run_event = _collecting_emitter(emitted_events)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield GoldenApiHarness(client=client, emitted_events=emitted_events)


@pytest.fixture(scope="module", autouse=True)
def _manual_review_report() -> Generator[None]:
    _manual_review_rows.clear()
    yield
    if _manual_review_rows:
        _write_manual_review_report(_manual_review_rows)


def test_task_4_live_golden_suite_uses_real_anthropic_client(
    golden_database_url: str,
) -> None:
    del golden_database_url

    assert isinstance(get_llm_client(), AnthropicLLMClient)


@pytest.mark.asyncio
async def test_task_4_seeded_work_orders_are_reset_once_before_scenarios(
    golden_database_url: str,
) -> None:
    assert await _work_order_ids(golden_database_url) == ["WO-001", "WO-002"]


@pytest.mark.parametrize("scenario", load_golden_scenarios(), ids=lambda scenario: scenario.id)
@pytest.mark.asyncio
async def test_task_4_golden_scenarios_run_through_public_api(
    scenario: GoldenScenario,
    golden_api: GoldenApiHarness,
) -> None:
    first_response = await golden_api.client.post("/agent/query", json=_request_payload(scenario))
    assert first_response.status_code == 200
    first_payload = AgentQueryResponse.model_validate(first_response.json())
    assert len(golden_api.emitted_events) == 1
    _assert_turn_1_contract(scenario, first_payload, golden_api.emitted_events[0])
    _capture_manual_review_row(scenario.id, "turn-1", first_payload)

    if scenario.approval_step is None:
        return

    assert first_payload.pending_action is not None
    draft_id = first_payload.pending_action.draft_id
    approval_response = await golden_api.client.post(
        f"/agent/approvals/{draft_id}",
        json={"decision": scenario.approval_step.decision},
    )
    assert approval_response.status_code == 200
    approval_payload = AgentQueryResponse.model_validate(approval_response.json())
    assert len(golden_api.emitted_events) == 2
    _assert_approval_contract(
        scenario.approval_step,
        approval_payload,
        golden_api.emitted_events[0],
        golden_api.emitted_events[1],
    )
    _capture_manual_review_row(scenario.id, "approve", approval_payload)


@pytest.mark.asyncio
async def test_task_4_gs_08_reject_path_runs_through_same_public_api_client(
    golden_api: GoldenApiHarness,
) -> None:
    scenario = next(scenario for scenario in load_golden_scenarios() if scenario.id == "GS-08")
    pause_response = await golden_api.client.post("/agent/query", json=_request_payload(scenario))
    assert pause_response.status_code == 200
    pause_payload = AgentQueryResponse.model_validate(pause_response.json())
    assert pause_payload.pending_action is not None

    reject_response = await golden_api.client.post(
        f"/agent/approvals/{pause_payload.pending_action.draft_id}",
        json={"decision": "reject"},
    )

    assert reject_response.status_code == 200
    reject_payload = AgentQueryResponse.model_validate(reject_response.json())
    assert reject_payload.status == "ok"
    assert reject_payload.pending_action is None
    assert reject_payload.answer is not None
    assert "No work order was created" in reject_payload.answer
    assert "WO-" not in reject_payload.answer
    assert "work_order" not in {
        evidence.source_type for evidence in reject_payload.structured_evidence
    }
    assert len(golden_api.emitted_events) == 2
    assert "submit_work_order" not in [
        tool.tool_name for tool in golden_api.emitted_events[1].tool_calls
    ]


@pytest.mark.asyncio
async def test_task_4_full_suite_creates_exactly_one_new_work_order(
    golden_database_url: str,
) -> None:
    assert await _work_order_ids(golden_database_url) == ["WO-001", "WO-002", "WO-003"]


def _request_payload(scenario: GoldenScenario) -> dict[str, str]:
    payload = {"query": scenario.query}
    if scenario.asset_id_hint is not None:
        payload["asset_id"] = scenario.asset_id_hint
    return payload


def _collecting_emitter(events: list[RunEvent]):
    async def emit(event: RunEvent) -> None:
        events.append(event)

    return emit


async def _work_order_ids(database_url: str) -> list[str]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT work_order_id FROM work_orders ORDER BY work_order_id")
            )
            return [row[0] for row in result]
    finally:
        await engine.dispose()


def _assert_turn_1_contract(
    scenario: GoldenScenario,
    response: AgentQueryResponse,
    event: RunEvent,
) -> None:
    assert response.asset_id == scenario.expected_asset_id
    assert response.status == scenario.expected_status
    assert (response.status == "needs_approval") is scenario.hitl
    assert (response.pending_action is not None) is scenario.hitl
    assert event.final_output == response
    assert event.status == response.status
    _assert_evidence_contract(scenario, response)
    _assert_behavior_contract(scenario, response)

    tool_names = [tool_call.tool_name for tool_call in event.tool_calls]
    assert tool_names[0] == "resolve_asset"
    if scenario.id == "GS-07":
        assert tool_names == ["resolve_asset"]
    _assert_tool_contract(
        tool_names,
        required_tools=scenario.required_tools,
        optional_tools=scenario.optional_tools,
    )


def _assert_approval_contract(
    approval_step: ApprovalStep,
    response: AgentQueryResponse,
    pause_event: RunEvent,
    resume_event: RunEvent,
) -> None:
    assert response.status == approval_step.expected_status
    assert response.pending_action is None
    assert pause_event.run_id == resume_event.run_id
    resume_tool_names = [tool_call.tool_name for tool_call in resume_event.tool_calls]
    for required_tool in approval_step.required_tools_after_resume:
        assert required_tool in resume_tool_names
    _assert_resume_evidence_contract(approval_step, pause_event.final_output, response)
    _assert_approval_behavior_contract(response)


def _assert_behavior_contract(
    scenario: GoldenScenario,
    response: AgentQueryResponse,
) -> None:
    if scenario.id in TROUBLESHOOTING_SCENARIO_IDS:
        assert response.confidence != "confirmed"
    if scenario.id == "GS-03":
        assert response.confidence == "hypothesis"
    if scenario.id == "GS-07":
        assert response.confidence is None
        assert response.answer is not None
        assert "PUMP-999" in response.answer
        assert "couldn't find an asset matching" in response.answer
    if scenario.id == "GS-08":
        assert response.confidence is None
        assert response.answer is not None
        assert "Priority: high" in response.answer


def _assert_approval_behavior_contract(response: AgentQueryResponse) -> None:
    work_order_items = [
        evidence
        for evidence in response.structured_evidence
        if evidence.source_type == "work_order"
    ]
    assert len(work_order_items) == 1
    work_order = work_order_items[0]
    assert work_order.source_id is not None
    assert response.answer is not None
    assert work_order.source_id in response.answer
    assert "priority: high" in response.answer


def _assert_evidence_contract(
    scenario: GoldenScenario,
    response: AgentQueryResponse,
) -> None:
    if scenario.id == "GS-07":
        assert response.structured_evidence == []
        assert response.document_evidence == []

    evidence_ids = {
        item.source_id for item in response.structured_evidence if item.source_id is not None
    } | {item.document_id for item in response.document_evidence}
    assert set(scenario.required_evidence_ids) <= evidence_ids


def _assert_resume_evidence_contract(
    approval_step: ApprovalStep,
    pause_response: AgentQueryResponse,
    resume_response: AgentQueryResponse,
) -> None:
    pause_items = {
        (item.source_type, item.source_id)
        for item in pause_response.structured_evidence
        if item.source_type is not None and item.source_id is not None
    }
    resume_items = {
        (item.source_type, item.source_id)
        for item in resume_response.structured_evidence
        if item.source_type is not None and item.source_id is not None
    }
    new_items = resume_items - pause_items

    for source_type in approval_step.required_evidence_source_types_after_resume:
        assert len([item for item in new_items if item[0] == source_type]) == 1


def _assert_tool_contract(
    observed_tool_names: Sequence[str],
    *,
    required_tools: Sequence[str],
    optional_tools: Sequence[str],
) -> None:
    allowed_tools = set(required_tools) | set(optional_tools)
    forbidden_tools = set(CANONICAL_TOOL_NAMES) - allowed_tools

    for required_tool in required_tools:
        assert required_tool in observed_tool_names
    assert not (set(observed_tool_names) & forbidden_tools)


def _capture_manual_review_row(
    scenario_id: str,
    turn: str,
    response: AgentQueryResponse,
) -> None:
    _manual_review_rows.append(
        {
            "scenario": scenario_id,
            "turn": turn,
            "status": response.status,
            "confidence": response.confidence or "",
            "evidence_ids": ", ".join(sorted(_response_evidence_ids(response))),
            "answer": response.answer or "",
        }
    )


def _response_evidence_ids(response: AgentQueryResponse) -> set[str]:
    return {
        item.source_id for item in response.structured_evidence if item.source_id is not None
    } | {item.document_id for item in response.document_evidence}


def _write_manual_review_report(rows: Sequence[dict[str, str]]) -> None:
    lines = [
        "# Golden Scenario Manual Review Report",
        "",
        "| Scenario | Turn | Status | Confidence | Evidence IDs | Answer |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        "| {scenario} | {turn} | {status} | {confidence} | {evidence_ids} | {answer} |".format(
            scenario=_markdown_cell(row["scenario"]),
            turn=_markdown_cell(row["turn"]),
            status=_markdown_cell(row["status"]),
            confidence=_markdown_cell(row["confidence"]),
            evidence_ids=_markdown_cell(row["evidence_ids"]),
            answer=_markdown_cell(row["answer"]),
        )
        for row in rows
    )
    MANUAL_REVIEW_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
