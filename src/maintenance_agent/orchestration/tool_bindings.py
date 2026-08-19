from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
from maintenance_agent.tools.submit_work_order import submit_work_order

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


@dataclass(frozen=True)
class ToolBinding:
    input_model: type[ToolInputModel]
    llm_description: str | None = None
    consequential: bool = False


TOOL_BINDINGS: dict[CanonicalToolName, ToolBinding] = {
    "resolve_asset": ToolBinding(ResolveAssetInput),
    "get_asset_status": ToolBinding(
        GetAssetStatusInput,
        llm_description="Fetch current telemetry, active faults, observations, and limits.",
    ),
    "get_maintenance_history": ToolBinding(
        GetMaintenanceHistoryInput,
        llm_description="Fetch maintenance, fault, work-order, and recurrence history.",
    ),
    "search_maintenance_docs": ToolBinding(
        SearchMaintenanceDocsInput,
        llm_description="Search maintenance documentation for relevant procedures.",
    ),
    "get_plant_policy": ToolBinding(
        GetPlantPolicyInput,
        llm_description="Fetch plant policy records by policy type.",
    ),
    "create_work_order_draft": ToolBinding(
        CreateWorkOrderDraftInput,
        llm_description="Create a non-submitted work-order draft for approval.",
    ),
    "submit_work_order": ToolBinding(
        SubmitWorkOrderInput,
        consequential=True,
    ),
}
TOOL_INPUT_MODELS: dict[CanonicalToolName, type[ToolInputModel]] = {
    tool_name: binding.input_model for tool_name, binding in TOOL_BINDINGS.items()
}
TOOL_DESCRIPTIONS: dict[LLMOfferedToolName, str] = {
    cast(LLMOfferedToolName, tool_name): binding.llm_description
    for tool_name, binding in TOOL_BINDINGS.items()
    if binding.llm_description is not None and not binding.consequential
}


def build_llm_tools(
    tool_names: Sequence[CanonicalToolName] = LLM_OFFERED_TOOL_NAMES,
    *,
    bindings: Mapping[CanonicalToolName, ToolBinding] = TOOL_BINDINGS,
) -> list[LLMTool]:
    disallowed_tool_names = {
        tool_name
        for tool_name in tool_names
        if tool_name not in bindings
        or (bindings[tool_name].llm_description is None and not bindings[tool_name].consequential)
    }
    if disallowed_tool_names:
        raise ValueError(
            "These tools are not LLM-offered: " + ", ".join(sorted(disallowed_tool_names))
        )

    return [
        LLMTool(
            name=tool_name,
            description=cast(str, bindings[tool_name].llm_description),
            input_schema=bindings[tool_name].input_model.model_json_schema(),
        )
        for tool_name in tool_names
        if not bindings[tool_name].consequential
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

    submit_input = SubmitWorkOrderInput.model_validate(args)
    return await submit_work_order(
        submit_input.draft_id,
        approval_status=state.get("approval_status", "none"),
        session=session,
    )


def _require_asset(state: GraphState, tool_name: str) -> AssetRecord:
    asset = state.get("asset")
    if asset is None:
        raise ValueError(f"{tool_name} requires a resolved asset in graph state.")
    return asset
