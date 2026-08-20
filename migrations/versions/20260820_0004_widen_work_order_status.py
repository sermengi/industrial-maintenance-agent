"""widen work order status for submitted rows

Revision ID: 20260820_0004
Revises: 20260817_0003
Create Date: 2026-08-20 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260820_0004"
down_revision: str | None = "20260817_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_work_orders_status", "work_orders", type_="check")
    op.create_check_constraint(
        "ck_work_orders_status",
        "work_orders",
        "status IN ('completed', 'submitted')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_work_orders_status", "work_orders", type_="check")
    op.create_check_constraint(
        "ck_work_orders_status",
        "work_orders",
        "status IN ('completed')",
    )
