from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from maintenance_agent.db.models.base import Base

if TYPE_CHECKING:
    from maintenance_agent.db.models.fault_events import FaultEvent
    from maintenance_agent.db.models.maintenance_events import MaintenanceEvent
    from maintenance_agent.db.models.observations import Observation
    from maintenance_agent.db.models.telemetry import TelemetrySnapshot
    from maintenance_agent.db.models.work_orders import WorkOrder


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint("asset_type IN ('centrifugal_pump')", name="ck_assets_asset_type"),
        CheckConstraint("model IN ('CP-200', 'CP-300')", name="ck_assets_model"),
        CheckConstraint(
            "status IN ('operational', 'degraded', 'maintenance_required')",
            name="ck_assets_status",
        ),
    )

    asset_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(32), nullable=False)
    location: Mapped[str] = mapped_column(String(128), nullable=False)
    installation_date: Mapped[date] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    telemetry_snapshots: Mapped[list[TelemetrySnapshot]] = relationship(back_populates="asset")
    fault_events: Mapped[list[FaultEvent]] = relationship(back_populates="asset")
    maintenance_events: Mapped[list[MaintenanceEvent]] = relationship(back_populates="asset")
    observations: Mapped[list[Observation]] = relationship(back_populates="asset")
    work_orders: Mapped[list[WorkOrder]] = relationship(back_populates="asset")
