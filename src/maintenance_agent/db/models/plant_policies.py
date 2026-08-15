from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from maintenance_agent.db.models.base import Base


class PlantPolicy(Base):
    __tablename__ = "plant_policies"
    __table_args__ = (
        CheckConstraint(
            "type IN ('recurring_fault', 'consequential_action')",
            name="ck_plant_policies_type",
        ),
    )

    policy_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    condition: Mapped[str] = mapped_column(Text, nullable=False)
    required_action: Mapped[str] = mapped_column(Text, nullable=False)
