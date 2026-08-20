from __future__ import annotations

from collections.abc import Callable
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories import work_orders
from maintenance_agent.db.repositories.records import WorkOrderRecord
from maintenance_agent.orchestration.state import WorkOrderDraft


class ConsequentialActionGuardError(RuntimeError):
    pass


async def submit_work_order(
    draft: WorkOrderDraft,
    *,
    approval_status: str,
    session: AsyncSession,
    clock: Callable[[], date] = date.today,
) -> WorkOrderRecord:
    if approval_status != "approved":
        raise ConsequentialActionGuardError(
            "submit_work_order requires approval_status='approved'."
        )
    record = await work_orders.create_submitted(
        session,
        asset_id=draft.asset_id,
        issue=draft.issue,
        priority=draft.priority,
        created_at=clock(),
    )
    await session.commit()
    return record
