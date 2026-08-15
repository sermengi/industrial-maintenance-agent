from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.models import OperatingLimit
from maintenance_agent.db.repositories.records import OperatingLimitRecord


async def list_for_model(session: AsyncSession, model: str) -> list[OperatingLimitRecord]:
    result = await session.execute(
        select(OperatingLimit)
        .where(OperatingLimit.model == model)
        .order_by(OperatingLimit.operating_limit_id)
    )
    return [OperatingLimitRecord.model_validate(row) for row in result.scalars()]
