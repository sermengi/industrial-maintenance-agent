from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.models import WorkOrder
from maintenance_agent.db.repositories.records import WorkOrderRecord


async def list_for_asset(session: AsyncSession, asset_id: str) -> list[WorkOrderRecord]:
    result = await session.execute(
        select(WorkOrder)
        .where(WorkOrder.asset_id == asset_id)
        .order_by(WorkOrder.created_at, WorkOrder.work_order_id)
    )
    return [WorkOrderRecord.model_validate(row) for row in result.scalars()]


async def create_submitted(
    session: AsyncSession,
    *,
    asset_id: str,
    issue: str,
    priority: str,
    created_at: date,
) -> WorkOrderRecord:
    work_order = WorkOrder(
        work_order_id=await _next_work_order_id(session),
        asset_id=asset_id,
        issue=issue,
        priority=priority,
        status="submitted",
        created_at=created_at,
        approved=True,
    )
    session.add(work_order)
    await session.flush()
    return WorkOrderRecord.model_validate(work_order)


async def _next_work_order_id(session: AsyncSession) -> str:
    result = await session.execute(select(WorkOrder.work_order_id))
    suffixes = [
        suffix
        for work_order_id in result.scalars()
        if (suffix := _work_order_numeric_suffix(work_order_id)) is not None
    ]
    return f"WO-{max(suffixes, default=0) + 1:03d}"


def _work_order_numeric_suffix(work_order_id: str) -> int | None:
    prefix = "WO-"
    if not work_order_id.startswith(prefix):
        return None
    suffix = work_order_id.removeprefix(prefix)
    if not suffix.isdecimal():
        return None
    return int(suffix)
