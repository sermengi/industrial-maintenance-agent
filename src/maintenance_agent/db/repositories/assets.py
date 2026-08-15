from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.models import Asset
from maintenance_agent.db.repositories.records import AssetRecord


async def get_by_id(session: AsyncSession, asset_id: str) -> AssetRecord | None:
    asset = await session.get(Asset, asset_id)
    if asset is None:
        return None
    return AssetRecord.model_validate(asset)
