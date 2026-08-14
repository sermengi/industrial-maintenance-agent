from typing import Literal

from pydantic import BaseModel, Field

AgentStatus = Literal[
    "ok",
    "needs_approval",
    "insufficient_evidence",
    "unknown_asset",
    "error",
]
Confidence = Literal["confirmed", "hypothesis"]


class AgentQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="User maintenance question.")
    asset_id: str | None = Field(
        default=None,
        description="Optional explicit asset hint.",
    )
    fault_code: str | None = Field(default=None, description="Optional explicit fault code hint.")


class StructuredEvidence(BaseModel):
    source: str
    summary: str
    reference_id: str | None = None


class DocumentEvidence(BaseModel):
    document_id: str
    section: str
    excerpt: str


class PendingAction(BaseModel):
    action_type: str
    draft_id: str


class AgentError(BaseModel):
    code: str
    message: str


class AgentQueryResponse(BaseModel):
    request_id: str
    status: AgentStatus
    asset_id: str | None = None
    answer: str | None = None
    confidence: Confidence | None = None
    structured_evidence: list[StructuredEvidence] = Field(default_factory=list)
    document_evidence: list[DocumentEvidence] = Field(default_factory=list)
    pending_action: PendingAction | None = None
    error: AgentError | None = None
