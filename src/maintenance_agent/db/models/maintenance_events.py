from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from maintenance_agent.db.models.base import Base

if TYPE_CHECKING:
    from maintenance_agent.db.models.assets import Asset


class MaintenanceEvent(Base):
    __tablename__ = "maintenance_events"
    __table_args__ = (
        CheckConstraint(
            "type IN ('preventive', 'corrective', 'inspection')",
            name="ck_maintenance_events_type",
        ),
        Index("ix_maintenance_events_asset_id_date", "asset_id", "date"),
    )

    maintenance_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("assets.asset_id", name="fk_maintenance_events_asset_id"),
        nullable=False,
    )
    date: Mapped[date] = mapped_column(nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    component: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="maintenance_events")
