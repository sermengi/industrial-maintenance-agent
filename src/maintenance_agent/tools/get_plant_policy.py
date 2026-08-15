from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories import plant_policies
from maintenance_agent.db.repositories.records import PlantPolicyRecord


class GetPlantPolicyResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_type: str
    policies: list[PlantPolicyRecord] = Field(default_factory=list)


async def get_plant_policy(
    policy_type: str,
    session: AsyncSession,
) -> GetPlantPolicyResult:
    policies = await plant_policies.list_by_type(session, policy_type)
    return GetPlantPolicyResult(policy_type=policy_type, policies=policies)
