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
