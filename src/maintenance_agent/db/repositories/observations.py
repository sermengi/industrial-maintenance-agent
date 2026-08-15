from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.models import Observation
from maintenance_agent.db.repositories.records import ObservationRecord


async def list_for_asset(session: AsyncSession, asset_id: str) -> list[ObservationRecord]:
    result = await session.execute(
        select(Observation)
        .where(Observation.asset_id == asset_id)
        .order_by(Observation.timestamp, Observation.observation_id)
    )
    return [ObservationRecord.model_validate(row) for row in result.scalars()]
