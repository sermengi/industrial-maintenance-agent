"""create rag chunks table

Revision ID: 20260817_0002
Revises: 20260815_0001
Create Date: 2026-08-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from maintenance_agent.db.types import Vector

revision: str = "20260817_0002"
down_revision: str | None = "20260815_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "rag_chunks",
        sa.Column("chunk_id", sa.String(length=32), primary_key=True),
        sa.Column("document_id", sa.String(length=32), nullable=False),
        sa.Column("chunk_heading", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("manufacturer", sa.Text(), nullable=False),
        sa.Column("source_product_family", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("page", sa.Text(), nullable=False),
        sa.Column("equipment_type", sa.String(length=64), nullable=False),
        sa.Column("applicability", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("content_provenance", sa.String(length=64), nullable=False),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("linked_fault_codes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("embedding", Vector(512), nullable=False),
        sa.CheckConstraint(
            "equipment_type IN ('centrifugal_pump')",
            name="ck_rag_chunks_equipment_type",
        ),
        sa.CheckConstraint(
            "applicability IN ('generic_reference')",
            name="ck_rag_chunks_applicability",
        ),
        sa.CheckConstraint(
            "content_provenance IN ('authored_representative')",
            name="ck_rag_chunks_content_provenance",
        ),
        sa.CheckConstraint(
            "topic IN ("
            "'HIGH_VIBRATION', "
            "'HIGH_BEARING_TEMPERATURE', "
            "'LOW_DISCHARGE_PRESSURE', "
            "'INSPECTION_PROCEDURE'"
            ")",
            name="ck_rag_chunks_topic",
        ),
    )


def downgrade() -> None:
    op.drop_table("rag_chunks")
