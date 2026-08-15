from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from maintenance_agent.db.models.base import Base

if TYPE_CHECKING:
    from maintenance_agent.db.models.assets import Asset


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        CheckConstraint(
            "type IN ('seal_leak', 'abnormal_vibration')",
            name="ck_observations_type",
        ),
        CheckConstraint(
            "severity IN ('minor', 'moderate')",
            name="ck_observations_severity",
        ),
        Index("ix_observations_asset_id_timestamp", "asset_id", "timestamp"),
    )

    observation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("assets.asset_id", name="fk_observations_asset_id"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reported_by: Mapped[str] = mapped_column(String(128), nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="observations")
