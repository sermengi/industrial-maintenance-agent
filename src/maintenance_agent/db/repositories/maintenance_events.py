from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.models import MaintenanceEvent
from maintenance_agent.db.repositories.records import MaintenanceEventRecord


async def list_for_asset(session: AsyncSession, asset_id: str) -> list[MaintenanceEventRecord]:
    result = await session.execute(
        select(MaintenanceEvent)
        .where(MaintenanceEvent.asset_id == asset_id)
        .order_by(MaintenanceEvent.date, MaintenanceEvent.maintenance_id)
    )
    return [MaintenanceEventRecord.model_validate(row) for row in result.scalars()]
