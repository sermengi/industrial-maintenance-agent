from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories.records import AssetRecord, FaultEventRecord
from maintenance_agent.schemas.agent import AgentQueryResponse, Confidence
from maintenance_agent.tools.get_asset_status import (
    ClassifiedReading,
    GetAssetStatusResult,
)
from maintenance_agent.tools.get_maintenance_history import (
    FaultRecurrence,
    GetMaintenanceHistoryResult,
)
from maintenance_agent.tools.get_plant_policy import GetPlantPolicyResult
from maintenance_agent.tools.resolve_asset import ResolveAssetResult
from maintenance_agent.tools.search_maintenance_docs import (
    DocSearchHit,
    SearchMaintenanceDocsResult,
)

Intent = Literal[
    "troubleshooting",
    "maintenance_check",
    "history_query",
    "procedure_lookup",
    "work_order_request",
]
AssetResolutionStatus = Literal["resolved", "not_found"]
ApprovalStatus = Literal["none", "pending_approval", "approved", "rejected", "submitted"]

ToolResult = (
    ResolveAssetResult
    | GetAssetStatusResult
    | GetMaintenanceHistoryResult
    | SearchMaintenanceDocsResult
    | GetPlantPolicyResult
)
StructuredEvidenceItem = ClassifiedReading | FaultEventRecord | FaultRecurrence


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: str
    args: dict[str, object] = Field(default_factory=dict)
    result: ToolResult
    timestamp: datetime
    sequence: int


class WorkOrderDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft_id: str
    asset_id: str
    issue: str
    priority: str


class ErrorRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    node: str | None
    recoverable: bool


class GraphState(TypedDict):
    request_id: NotRequired[str]
    session: NotRequired[AsyncSession]
    query: str
    asset_id_hint: str | None
    fault_code_hint: str | None
    intent: Intent | None
    asset: AssetRecord | None
    asset_resolution_status: AssetResolutionStatus | None
    tool_calls: Annotated[list[ToolCallRecord], operator.add]
    structured_evidence: Annotated[list[StructuredEvidenceItem], operator.add]
    document_evidence: Annotated[list[DocSearchHit], operator.add]
    work_order_draft: WorkOrderDraft | None
    approval_status: ApprovalStatus
    errors: Annotated[list[ErrorRecord], operator.add]
    evidence_gathering_iterations: NotRequired[int]
    last_evidence_tool_call_count: NotRequired[int]
    synthesis_answer: NotRequired[str | None]
    synthesis_confidence: NotRequired[Confidence | None]
    synthesis_evidence_used: NotRequired[list[str]]
    response: AgentQueryResponse | None
