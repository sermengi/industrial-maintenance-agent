from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories import assets
from maintenance_agent.db.repositories.records import AssetRecord


class ResolveAssetResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["resolved", "not_found"]
    asset: AssetRecord | None = None


async def resolve_asset(identifier: str, session: AsyncSession) -> ResolveAssetResult:
    if not isinstance(identifier, str):
        return ResolveAssetResult(status="not_found")

    normalized_identifier = identifier.strip().upper()
    if not normalized_identifier:
        return ResolveAssetResult(status="not_found")

    asset = await assets.get_by_id(session, normalized_identifier)
    if asset is None:
        return ResolveAssetResult(status="not_found")

    return ResolveAssetResult(status="resolved", asset=asset)
