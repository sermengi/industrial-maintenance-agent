from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from maintenance_agent.db.models.base import Base


class FaultTaxonomy(Base):
    __tablename__ = "fault_taxonomy"

    fault_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
