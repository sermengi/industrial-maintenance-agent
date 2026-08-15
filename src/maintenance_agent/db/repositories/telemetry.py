from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.models import TelemetrySnapshot
from maintenance_agent.db.repositories.records import TelemetrySnapshotRecord


async def get_latest_for_asset(
    session: AsyncSession,
    asset_id: str,
) -> TelemetrySnapshotRecord | None:
    result = await session.execute(
        select(TelemetrySnapshot)
        .where(TelemetrySnapshot.asset_id == asset_id)
        .order_by(TelemetrySnapshot.timestamp.desc(), TelemetrySnapshot.snapshot_id.desc())
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    if snapshot is None:
        return None
    return TelemetrySnapshotRecord.model_validate(snapshot)
