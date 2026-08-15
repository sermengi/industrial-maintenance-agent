from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.models import FaultEvent
from maintenance_agent.db.repositories.records import FaultEventRecord


async def list_active_for_asset(
    session: AsyncSession,
    asset_id: str,
) -> list[FaultEventRecord]:
    result = await session.execute(
        select(FaultEvent)
        .where(FaultEvent.asset_id == asset_id, FaultEvent.status == "active")
        .order_by(FaultEvent.timestamp, FaultEvent.event_id)
    )
    return [FaultEventRecord.model_validate(row) for row in result.scalars()]


async def list_for_asset(session: AsyncSession, asset_id: str) -> list[FaultEventRecord]:
    result = await session.execute(
        select(FaultEvent)
        .where(FaultEvent.asset_id == asset_id)
        .order_by(FaultEvent.timestamp, FaultEvent.event_id)
    )
    return [FaultEventRecord.model_validate(row) for row in result.scalars()]


async def list_by_asset_and_code(
    session: AsyncSession,
    asset_id: str,
    fault_code: str,
) -> list[FaultEventRecord]:
    result = await session.execute(
        select(FaultEvent)
        .where(FaultEvent.asset_id == asset_id, FaultEvent.fault_code == fault_code)
        .order_by(FaultEvent.timestamp, FaultEvent.event_id)
    )
    return [FaultEventRecord.model_validate(row) for row in result.scalars()]
