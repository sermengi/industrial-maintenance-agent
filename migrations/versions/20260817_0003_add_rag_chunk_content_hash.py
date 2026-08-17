"""add rag chunk content hash

Revision ID: 20260817_0003
Revises: 20260817_0002
Create Date: 2026-08-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0003"
down_revision: str | None = "20260817_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rag_chunks", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.execute("UPDATE rag_chunks SET content_hash = encode(sha256(text::bytea), 'hex')")
    op.alter_column("rag_chunks", "content_hash", nullable=False)


def downgrade() -> None:
    op.drop_column("rag_chunks", "content_hash")
