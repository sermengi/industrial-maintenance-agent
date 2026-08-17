from __future__ import annotations

from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from maintenance_agent.db.models.base import Base
from maintenance_agent.db.types import Vector
from maintenance_agent.rag.embeddings import EMBEDDING_DIMENSION


class RagChunk(Base):
    __tablename__ = "rag_chunks"
    __table_args__ = (
        CheckConstraint(
            "equipment_type IN ('centrifugal_pump')",
            name="ck_rag_chunks_equipment_type",
        ),
        CheckConstraint(
            "applicability IN ('generic_reference')",
            name="ck_rag_chunks_applicability",
        ),
        CheckConstraint(
            "content_provenance IN ('authored_representative')",
            name="ck_rag_chunks_content_provenance",
        ),
        CheckConstraint(
            "topic IN ("
            "'HIGH_VIBRATION', "
            "'HIGH_BEARING_TEMPERATURE', "
            "'LOW_DISCHARGE_PRESSURE', "
            "'INSPECTION_PROCEDURE'"
            ")",
            name="ck_rag_chunks_topic",
        ),
    )

    chunk_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(32), nullable=False)
    chunk_heading: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    manufacturer: Mapped[str] = mapped_column(Text, nullable=False)
    source_product_family: Mapped[str] = mapped_column(Text, nullable=False)
    section: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[str] = mapped_column(Text, nullable=False)
    equipment_type: Mapped[str] = mapped_column(String(64), nullable=False)
    applicability: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_provenance: Mapped[str] = mapped_column(String(64), nullable=False)
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    linked_fault_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=False)
