from datetime import UTC, datetime
from typing import get_args
from uuid import UUID

import pytest
from pydantic import ValidationError

from maintenance_agent.schemas.agent import AgentError, AgentQueryResponse, AgentStatus
from maintenance_agent.schemas.run_event import RunEvent, ToolCallSummary


def test_tool_call_summary_forbids_extra_fields() -> None:
    assert ToolCallSummary.model_config["extra"] == "forbid"

    with pytest.raises(ValidationError):
        ToolCallSummary.model_validate(
            {
                "tool_name": "resolve_asset",
                "sequence": 1,
                "status": "ok",
            }
        )


def test_run_event_forbids_extra_fields() -> None:
    assert RunEvent.model_config["extra"] == "forbid"

    payload = _run_event_payload()
    payload["unexpected"] = "not allowed"

    with pytest.raises(ValidationError):
        RunEvent.model_validate(payload)


def test_run_event_uses_agent_response_contracts() -> None:
    assert RunEvent.model_fields["status"].annotation == AgentStatus
    assert RunEvent.model_fields["final_output"].annotation == AgentQueryResponse
    assert AgentError in get_args(RunEvent.model_fields["error"].annotation)


def test_run_event_accepts_full_agent_query_response_and_error_envelope() -> None:
    response = AgentQueryResponse(
        request_id="req-123",
        status="error",
        error=AgentError(code="internal_error", message="Synthetic failure"),
    )
    event = RunEvent.model_validate(
        {
            **_run_event_payload(),
            "status": "error",
            "final_output": response,
            "error": response.error,
        }
    )

    assert event.final_output is response
    assert event.error == response.error
    assert event.status == response.status


def test_run_event_serializes_to_single_json_object() -> None:
    event = RunEvent.model_validate(_run_event_payload())

    serialized = event.model_dump_json()

    assert "\n" not in serialized
    assert '"tool_calls":[{"tool_name":"resolve_asset","sequence":1}]' in serialized
    assert '"final_output":' in serialized


def _run_event_payload() -> dict[str, object]:
    return {
        "event_id": UUID("11111111-1111-4111-8111-111111111111"),
        "run_id": "req-123",
        "emitted_at": datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        "latency_ms": 25,
        "status": "ok",
        "request": "Why is PUMP-103 overheating?",
        "tool_calls": [{"tool_name": "resolve_asset", "sequence": 1}],
        "final_output": {
            "request_id": "req-123",
            "status": "ok",
            "asset_id": "PUMP-103",
            "answer": "PUMP-103 has recurring bearing temperature faults.",
            "confidence": "confirmed",
            "evidence_used": ["FE-004"],
            "structured_evidence": [],
            "document_evidence": [],
            "pending_action": None,
            "error": None,
        },
        "error": None,
    }
