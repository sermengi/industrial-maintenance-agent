from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from maintenance_agent.schemas.agent import AgentError, AgentQueryResponse, AgentStatus


class ToolCallSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    sequence: int


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    run_id: str
    emitted_at: datetime
    latency_ms: int
    status: AgentStatus
    request: str
    tool_calls: list[ToolCallSummary]
    final_output: AgentQueryResponse
    error: AgentError | None
