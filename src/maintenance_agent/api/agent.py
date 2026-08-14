from uuid import uuid4

from fastapi import APIRouter

from maintenance_agent.schemas.agent import AgentQueryRequest, AgentQueryResponse

router = APIRouter()


@router.post("/query", response_model=AgentQueryResponse)
async def query_agent(request: AgentQueryRequest) -> AgentQueryResponse:
    return AgentQueryResponse(
        request_id=str(uuid4()),
        status="ok",
        asset_id=None,
        answer="Agent query handling is not implemented yet.",
        confidence=None,
        structured_evidence=[],
        document_evidence=[],
        pending_action=None,
        error=None,
    )
