from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories import fault_events, maintenance_events, work_orders
from maintenance_agent.db.repositories.records import (
    AssetRecord,
    FaultEventRecord,
    MaintenanceEventRecord,
    WorkOrderRecord,
)
from maintenance_agent.tools.fault_recurrence import (
    FaultRecurrence,
    compute_fault_recurrence,
)


class GetMaintenanceHistoryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset: AssetRecord
    maintenance_events: list[MaintenanceEventRecord] = Field(default_factory=list)
    fault_events: list[FaultEventRecord] = Field(default_factory=list)
    work_orders: list[WorkOrderRecord] = Field(default_factory=list)
    recurrence: list[FaultRecurrence] = Field(default_factory=list)


async def get_maintenance_history(
    asset: AssetRecord,
    session: AsyncSession,
) -> GetMaintenanceHistoryResult:
    asset_maintenance_events = await maintenance_events.list_for_asset(session, asset.asset_id)
    asset_fault_events = await fault_events.list_for_asset(session, asset.asset_id)
    asset_work_orders = await work_orders.list_for_asset(session, asset.asset_id)
    recurrence = await compute_fault_recurrence(asset.asset_id, asset_fault_events, session)

    return GetMaintenanceHistoryResult(
        asset=asset,
        maintenance_events=asset_maintenance_events,
        fault_events=asset_fault_events,
        work_orders=asset_work_orders,
        recurrence=recurrence,
    )
