from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories import fault_events
from maintenance_agent.db.repositories.records import AssetRecord
from maintenance_agent.orchestration.state import WorkOrderDraft
from maintenance_agent.tools.fault_recurrence import compute_fault_recurrence
from maintenance_agent.tools.search_maintenance_docs import DocSearchHit

WorkOrderPriority = Literal["low", "high"]


async def create_work_order_draft(
    *,
    issue: str,
    recommended_action: str,
    priority: WorkOrderPriority,
    asset: AssetRecord,
    request_id: str,
    structured_evidence: Sequence[object],
    document_evidence: Sequence[DocSearchHit],
    session: AsyncSession,
) -> WorkOrderDraft:
    active_faults = await fault_events.list_active_for_asset(session, asset.asset_id)
    recurrence = await compute_fault_recurrence(asset.asset_id, active_faults, session)

    return WorkOrderDraft(
        draft_id=request_id,
        asset_id=asset.asset_id,
        issue=issue,
        recommended_action=recommended_action,
        priority="high"
        if any(item.meets_recurrence_threshold for item in recurrence)
        else priority,
        supporting_evidence=_supporting_evidence_ids(
            structured_evidence=structured_evidence,
            document_evidence=document_evidence,
        ),
    )


def _supporting_evidence_ids(
    *,
    structured_evidence: Sequence[object],
    document_evidence: Sequence[DocSearchHit],
) -> list[str]:
    evidence_ids = [
        str(source_id)
        for item in structured_evidence
        if (source_id := getattr(item, "source_id", None)) is not None
    ]
    evidence_ids.extend(hit.document_id for hit in document_evidence)
    return list(dict.fromkeys(evidence_ids))
