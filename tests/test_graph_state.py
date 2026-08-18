import operator
from typing import Annotated, get_args, get_origin, get_type_hints

from pydantic import BaseModel

from maintenance_agent.db.repositories.records import FaultEventRecord
from maintenance_agent.orchestration.state import (
    ApprovalStatus,
    AssetResolutionStatus,
    GraphState,
    Intent,
    StructuredEvidenceItem,
)
from maintenance_agent.schemas.agent import AgentQueryResponse
from maintenance_agent.tools.get_asset_status import ClassifiedReading
from maintenance_agent.tools.get_maintenance_history import FaultRecurrence
from maintenance_agent.tools.resolve_asset import ResolveAssetResult

GRAPH_STATE_HINTS = get_type_hints(GraphState, include_extras=True)


def test_graph_state_is_typed_dict_not_pydantic_model() -> None:
    assert issubclass(GraphState, dict)
    assert not issubclass(GraphState, BaseModel)


def test_intent_keeps_full_capability_taxonomy() -> None:
    assert set(get_args(Intent)) == {
        "troubleshooting",
        "maintenance_check",
        "history_query",
        "procedure_lookup",
        "work_order_request",
    }


def test_asset_resolution_status_mirrors_resolve_asset_result_status() -> None:
    assert get_args(AssetResolutionStatus) == get_args(
        ResolveAssetResult.model_fields["status"].annotation
    )


def test_only_append_only_fields_use_operator_add_reducers() -> None:
    reducer_fields = {
        field_name: _reducer(annotation)
        for field_name, annotation in GRAPH_STATE_HINTS.items()
        if get_origin(annotation) is Annotated
    }

    assert reducer_fields == {
        "tool_calls": operator.add,
        "structured_evidence": operator.add,
        "document_evidence": operator.add,
        "errors": operator.add,
    }


def test_response_field_uses_phase_zero_response_schema() -> None:
    response_annotation = GRAPH_STATE_HINTS["response"]

    assert AgentQueryResponse in get_args(response_annotation)


def test_structured_evidence_item_covers_synthesis_facing_sources() -> None:
    assert set(get_args(StructuredEvidenceItem)) == {
        ClassifiedReading,
        FaultEventRecord,
        FaultRecurrence,
    }


def test_approval_status_values_are_reserved_for_hitl_lifecycle() -> None:
    assert set(get_args(ApprovalStatus)) == {
        "none",
        "pending_approval",
        "approved",
        "rejected",
        "submitted",
    }


def _reducer(annotation: object) -> object:
    return get_args(annotation)[1]
