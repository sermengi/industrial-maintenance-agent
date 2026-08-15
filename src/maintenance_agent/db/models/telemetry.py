from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from maintenance_agent.db.models.base import Base

if TYPE_CHECKING:
    from maintenance_agent.db.models.assets import Asset


class TelemetrySnapshot(Base):
    __tablename__ = "telemetry_snapshots"
    __table_args__ = (Index("ix_telemetry_snapshots_asset_id_timestamp", "asset_id", "timestamp"),)

    snapshot_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("assets.asset_id", name="fk_telemetry_asset_id"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    vibration_mm_s: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    bearing_temperature_c: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    inlet_pressure_bar: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    discharge_pressure_bar: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    flow_rate_l_min: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="telemetry_snapshots")
