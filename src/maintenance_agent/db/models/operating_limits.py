from decimal import Decimal

from sqlalchemy import CheckConstraint, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from maintenance_agent.db.models.base import Base


class OperatingLimit(Base):
    __tablename__ = "operating_limits"
    __table_args__ = (
        CheckConstraint("model IN ('CP-200', 'CP-300')", name="ck_operating_limits_model"),
        CheckConstraint(
            "source_type IN ('synthetic_plant_config', 'manufacturer_reference_adopted')",
            name="ck_operating_limits_source_type",
        ),
    )

    operating_limit_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    model: Mapped[str] = mapped_column(String(32), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    normal_min: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    normal_max: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    warning_min: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    warning_max: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    critical_min: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    critical_max: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance_note: Mapped[str] = mapped_column(Text, nullable=False)
