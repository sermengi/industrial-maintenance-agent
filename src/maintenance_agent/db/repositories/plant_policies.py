from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.models import PlantPolicy
from maintenance_agent.db.repositories.records import PlantPolicyRecord


async def get_by_id(session: AsyncSession, policy_id: str) -> PlantPolicyRecord | None:
    policy = await session.get(PlantPolicy, policy_id)
    if policy is None:
        return None
    return PlantPolicyRecord.model_validate(policy)


async def list_by_type(session: AsyncSession, policy_type: str) -> list[PlantPolicyRecord]:
    result = await session.execute(
        select(PlantPolicy).where(PlantPolicy.type == policy_type).order_by(PlantPolicy.policy_id)
    )
    return [PlantPolicyRecord.model_validate(row) for row in result.scalars()]
