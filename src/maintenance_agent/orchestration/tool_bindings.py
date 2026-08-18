from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories.records import AssetRecord
from maintenance_agent.llm.client import LLMTool
from maintenance_agent.orchestration.state import GraphState, ToolResult
from maintenance_agent.tools.get_asset_status import get_asset_status
from maintenance_agent.tools.get_maintenance_history import get_maintenance_history
from maintenance_agent.tools.get_plant_policy import get_plant_policy
from maintenance_agent.tools.resolve_asset import resolve_asset
from maintenance_agent.tools.search_maintenance_docs import search_maintenance_docs

CanonicalToolName = Literal[
    "resolve_asset",
    "get_asset_status",
    "get_maintenance_history",
    "search_maintenance_docs",
    "get_plant_policy",
    "create_work_order_draft",
    "submit_work_order",
]
LLMOfferedToolName = Literal[
    "get_asset_status",
    "get_maintenance_history",
    "search_maintenance_docs",
    "get_plant_policy",
    "create_work_order_draft",
]

CANONICAL_TOOL_NAMES: tuple[CanonicalToolName, ...] = (
    "resolve_asset",
    "get_asset_status",
    "get_maintenance_history",
    "search_maintenance_docs",
    "get_plant_policy",
    "create_work_order_draft",
    "submit_work_order",
)
LLM_OFFERED_TOOL_NAMES: tuple[LLMOfferedToolName, ...] = (
    "get_asset_status",
    "get_maintenance_history",
    "search_maintenance_docs",
    "get_plant_policy",
    "create_work_order_draft",
)


class ToolInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResolveAssetInput(ToolInputModel):
    identifier: str


class GetAssetStatusInput(ToolInputModel):
    pass


class GetMaintenanceHistoryInput(ToolInputModel):
    pass


class SearchMaintenanceDocsInput(ToolInputModel):
    query: str


class GetPlantPolicyInput(ToolInputModel):
    policy_type: str


class CreateWorkOrderDraftInput(ToolInputModel):
    issue: str
    priority: str
    recommended_action: str | None = None


class SubmitWorkOrderInput(ToolInputModel):
    draft_id: str


TOOL_INPUT_MODELS: dict[CanonicalToolName, type[ToolInputModel]] = {
    "resolve_asset": ResolveAssetInput,
    "get_asset_status": GetAssetStatusInput,
    "get_maintenance_history": GetMaintenanceHistoryInput,
    "search_maintenance_docs": SearchMaintenanceDocsInput,
    "get_plant_policy": GetPlantPolicyInput,
    "create_work_order_draft": CreateWorkOrderDraftInput,
    "submit_work_order": SubmitWorkOrderInput,
}
TOOL_DESCRIPTIONS: dict[LLMOfferedToolName, str] = {
    "get_asset_status": "Fetch current telemetry, active faults, observations, and limits.",
    "get_maintenance_history": "Fetch maintenance, fault, work-order, and recurrence history.",
    "search_maintenance_docs": "Search maintenance documentation for relevant procedures.",
    "get_plant_policy": "Fetch plant policy records by policy type.",
    "create_work_order_draft": "Create a non-submitted work-order draft for approval.",
}


def build_llm_tools(
    tool_names: Sequence[CanonicalToolName] = LLM_OFFERED_TOOL_NAMES,
) -> list[LLMTool]:
    disallowed_tool_names = set(tool_names) - set(LLM_OFFERED_TOOL_NAMES)
    if disallowed_tool_names:
        raise ValueError(
            "These tools are not LLM-offered: "
            + ", ".join(sorted(disallowed_tool_names))
        )

    return [
        LLMTool(
            name=tool_name,
            description=TOOL_DESCRIPTIONS[cast(LLMOfferedToolName, tool_name)],
            input_schema=TOOL_INPUT_MODELS[tool_name].model_json_schema(),
        )
        for tool_name in tool_names
    ]


async def invoke_tool_binding(
    tool_name: CanonicalToolName,
    args: dict[str, object],
    state: GraphState,
    session: AsyncSession,
) -> ToolResult:
    if tool_name == "resolve_asset":
        resolve_input = ResolveAssetInput.model_validate(args)
        return await resolve_asset(resolve_input.identifier, session)

    if tool_name == "get_asset_status":
        GetAssetStatusInput.model_validate(args)
        return await get_asset_status(_require_asset(state, tool_name), session)

    if tool_name == "get_maintenance_history":
        GetMaintenanceHistoryInput.model_validate(args)
        return await get_maintenance_history(_require_asset(state, tool_name), session)

    if tool_name == "search_maintenance_docs":
        search_input = SearchMaintenanceDocsInput.model_validate(args)
        return await search_maintenance_docs(search_input.query, session)

    if tool_name == "get_plant_policy":
        policy_input = GetPlantPolicyInput.model_validate(args)
        return await get_plant_policy(policy_input.policy_type, session)

    if tool_name == "create_work_order_draft":
        CreateWorkOrderDraftInput.model_validate(args)
        raise NotImplementedError("create_work_order_draft is reserved for Phase 6.")

    SubmitWorkOrderInput.model_validate(args)
    raise NotImplementedError("submit_work_order is reserved for the Phase 6 resume path.")


def _require_asset(state: GraphState, tool_name: str) -> AssetRecord:
    asset = state.get("asset")
    if asset is None:
        raise ValueError(f"{tool_name} requires a resolved asset in graph state.")
    return asset
