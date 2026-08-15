"""create phase 1 schema

Revision ID: 20260815_0001
Revises:
Create Date: 2026-08-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("asset_id", sa.String(length=32), primary_key=True),
        sa.Column("asset_type", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=32), nullable=False),
        sa.Column("location", sa.String(length=128), nullable=False),
        sa.Column("installation_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.CheckConstraint("asset_type IN ('centrifugal_pump')", name="ck_assets_asset_type"),
        sa.CheckConstraint("model IN ('CP-200', 'CP-300')", name="ck_assets_model"),
        sa.CheckConstraint(
            "status IN ('operational', 'degraded', 'maintenance_required')",
            name="ck_assets_status",
        ),
    )

    op.create_table(
        "fault_taxonomy",
        sa.Column("fault_code", sa.String(length=32), primary_key=True),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
    )

    op.create_table(
        "operating_limits",
        sa.Column("operating_limit_id", sa.String(length=32), primary_key=True),
        sa.Column("model", sa.String(length=32), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("normal_min", sa.Numeric(8, 2), nullable=True),
        sa.Column("normal_max", sa.Numeric(8, 2), nullable=True),
        sa.Column("warning_min", sa.Numeric(8, 2), nullable=True),
        sa.Column("warning_max", sa.Numeric(8, 2), nullable=True),
        sa.Column("critical_min", sa.Numeric(8, 2), nullable=True),
        sa.Column("critical_max", sa.Numeric(8, 2), nullable=True),
        sa.Column("rule_text", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("provenance_note", sa.Text(), nullable=False),
        sa.CheckConstraint("model IN ('CP-200', 'CP-300')", name="ck_operating_limits_model"),
        sa.CheckConstraint(
            "source_type IN ('synthetic_plant_config', 'manufacturer_reference_adopted')",
            name="ck_operating_limits_source_type",
        ),
    )

    op.create_table(
        "plant_policies",
        sa.Column("policy_id", sa.String(length=32), primary_key=True),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("condition", sa.Text(), nullable=False),
        sa.Column("required_action", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "type IN ('recurring_fault', 'consequential_action')",
            name="ck_plant_policies_type",
        ),
    )

    op.create_table(
        "telemetry_snapshots",
        sa.Column("snapshot_id", sa.String(length=32), primary_key=True),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("timestamp", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("vibration_mm_s", sa.Numeric(6, 2), nullable=False),
        sa.Column("bearing_temperature_c", sa.Numeric(6, 2), nullable=False),
        sa.Column("inlet_pressure_bar", sa.Numeric(6, 2), nullable=False),
        sa.Column("discharge_pressure_bar", sa.Numeric(6, 2), nullable=False),
        sa.Column("flow_rate_l_min", sa.Numeric(8, 2), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.asset_id"], name="fk_telemetry_asset_id"),
    )
    op.create_index(
        "ix_telemetry_snapshots_asset_id_timestamp",
        "telemetry_snapshots",
        ["asset_id", "timestamp"],
    )

    op.create_table(
        "fault_events",
        sa.Column("event_id", sa.String(length=32), primary_key=True),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("fault_code", sa.String(length=32), nullable=False),
        sa.Column("fault_name", sa.Text(), nullable=False),
        sa.Column("timestamp", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.CheckConstraint("severity IN ('medium', 'high')", name="ck_fault_events_severity"),
        sa.CheckConstraint("status IN ('active', 'resolved')", name="ck_fault_events_status"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.asset_id"], name="fk_fault_events_asset_id"),
    )
    op.create_index(
        "ix_fault_events_asset_id_timestamp",
        "fault_events",
        ["asset_id", "timestamp"],
    )
    op.create_index(
        "ix_fault_events_asset_id_fault_code",
        "fault_events",
        ["asset_id", "fault_code"],
    )

    op.create_table(
        "maintenance_events",
        sa.Column("maintenance_id", sa.String(length=32), primary_key=True),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("component", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "type IN ('preventive', 'corrective', 'inspection')",
            name="ck_maintenance_events_type",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.asset_id"],
            name="fk_maintenance_events_asset_id",
        ),
    )
    op.create_index(
        "ix_maintenance_events_asset_id_date",
        "maintenance_events",
        ["asset_id", "date"],
    )

    op.create_table(
        "observations",
        sa.Column("observation_id", sa.String(length=32), primary_key=True),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("timestamp", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reported_by", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "type IN ('seal_leak', 'abnormal_vibration')",
            name="ck_observations_type",
        ),
        sa.CheckConstraint(
            "severity IN ('minor', 'moderate')",
            name="ck_observations_severity",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.asset_id"], name="fk_observations_asset_id"),
    )
    op.create_index(
        "ix_observations_asset_id_timestamp",
        "observations",
        ["asset_id", "timestamp"],
    )

    op.create_table(
        "work_orders",
        sa.Column("work_order_id", sa.String(length=32), primary_key=True),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("issue", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.Date(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.CheckConstraint("priority IN ('low', 'high')", name="ck_work_orders_priority"),
        sa.CheckConstraint("status IN ('completed')", name="ck_work_orders_status"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.asset_id"], name="fk_work_orders_asset_id"),
    )
    op.create_index(
        "ix_work_orders_asset_id_created_at",
        "work_orders",
        ["asset_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_work_orders_asset_id_created_at", table_name="work_orders")
    op.drop_table("work_orders")
    op.drop_index("ix_observations_asset_id_timestamp", table_name="observations")
    op.drop_table("observations")
    op.drop_index("ix_maintenance_events_asset_id_date", table_name="maintenance_events")
    op.drop_table("maintenance_events")
    op.drop_index("ix_fault_events_asset_id_fault_code", table_name="fault_events")
    op.drop_index("ix_fault_events_asset_id_timestamp", table_name="fault_events")
    op.drop_table("fault_events")
    op.drop_index("ix_telemetry_snapshots_asset_id_timestamp", table_name="telemetry_snapshots")
    op.drop_table("telemetry_snapshots")
    op.drop_table("plant_policies")
    op.drop_table("operating_limits")
    op.drop_table("fault_taxonomy")
    op.drop_table("assets")
