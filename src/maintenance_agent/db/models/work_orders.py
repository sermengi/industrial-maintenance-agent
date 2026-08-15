from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from maintenance_agent.db.models.base import Base

if TYPE_CHECKING:
    from maintenance_agent.db.models.assets import Asset


class WorkOrder(Base):
    __tablename__ = "work_orders"
    __table_args__ = (
        CheckConstraint("priority IN ('low', 'high')", name="ck_work_orders_priority"),
        CheckConstraint("status IN ('completed')", name="ck_work_orders_status"),
        Index("ix_work_orders_asset_id_created_at", "asset_id", "created_at"),
    )

    work_order_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("assets.asset_id", name="fk_work_orders_asset_id"),
        nullable=False,
    )
    issue: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[date] = mapped_column(nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="work_orders")
