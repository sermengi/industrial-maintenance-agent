from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from maintenance_agent.db.models.base import Base

if TYPE_CHECKING:
    from maintenance_agent.db.models.assets import Asset


class FaultEvent(Base):
    __tablename__ = "fault_events"
    __table_args__ = (
        CheckConstraint("severity IN ('medium', 'high')", name="ck_fault_events_severity"),
        CheckConstraint("status IN ('active', 'resolved')", name="ck_fault_events_status"),
        Index("ix_fault_events_asset_id_timestamp", "asset_id", "timestamp"),
        Index("ix_fault_events_asset_id_fault_code", "asset_id", "fault_code"),
    )

    event_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("assets.asset_id", name="fk_fault_events_asset_id"),
        nullable=False,
    )
    fault_code: Mapped[str] = mapped_column(String(32), nullable=False)
    fault_name: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="fault_events")
