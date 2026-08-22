import operator
from typing import Annotated, Literal, get_args, get_origin, get_type_hints

import pytest
from pydantic import BaseModel

from maintenance_agent.db.repositories.records import (
    FaultEventRecord,
    MaintenanceEventRecord,
    ObservationRecord,
    OperatingLimitRecord,
    PlantPolicyRecord,
    WorkOrderRecord,
)
from maintenance_agent.orchestration.graph import IntentExtractionOutput
from maintenance_agent.orchestration.state import (
    ApprovalStatus,
    AssetResolutionStatus,
    GraphState,
    Intent,
    StructuredEvidenceItem,
    WorkOrderDraft,
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
        MaintenanceEventRecord,
        ObservationRecord,
        OperatingLimitRecord,
        PlantPolicyRecord,
        WorkOrderRecord,
    }


def test_plant_policy_record_exposes_evidence_source_identity() -> None:
    policy = PlantPolicyRecord(
        policy_id="PP-002",
        type="consequential_action",
        condition="Work-order submission changes system state",
        required_action="Human approval is required before final submission",
    )

    assert policy.source_type == "plant_policy"
    assert policy.source_id == "PP-002"


def test_work_order_draft_schema_has_no_lifecycle_status_and_forbids_extras() -> None:
    assert "status" not in WorkOrderDraft.model_fields
    assert WorkOrderDraft.model_config["extra"] == "forbid"
    assert WorkOrderDraft.model_fields["priority"].annotation == Literal["low", "high"]


def test_llm_facing_models_cannot_set_approval_status() -> None:
    assert "approval_status" not in IntentExtractionOutput.model_fields
    assert "approved" not in IntentExtractionOutput.model_fields
    assert "approval_status" not in WorkOrderDraft.model_fields
    assert "approved" not in WorkOrderDraft.model_fields


def test_work_order_draft_rejects_approval_injection_field() -> None:
    with pytest.raises(ValueError):
        WorkOrderDraft.model_validate(
            {
                "draft_id": "draft-123",
                "asset_id": "PUMP-103",
                "issue": "Recurring bearing overheating",
                "recommended_action": "Investigate root cause.",
                "priority": "high",
                "approved": True,
            }
        )


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
