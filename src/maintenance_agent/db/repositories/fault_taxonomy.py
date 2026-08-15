from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.models import FaultTaxonomy
from maintenance_agent.db.repositories.records import FaultTaxonomyRecord


async def get_by_code(session: AsyncSession, fault_code: str) -> FaultTaxonomyRecord | None:
    taxonomy = await session.get(FaultTaxonomy, fault_code)
    if taxonomy is None:
        return None
    return FaultTaxonomyRecord.model_validate(taxonomy)


async def list_all(session: AsyncSession) -> list[FaultTaxonomyRecord]:
    result = await session.execute(select(FaultTaxonomy).order_by(FaultTaxonomy.fault_code))
    return [FaultTaxonomyRecord.model_validate(row) for row in result.scalars()]
