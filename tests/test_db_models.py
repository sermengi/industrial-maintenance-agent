from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Numeric
from sqlalchemy.orm import configure_mappers

from maintenance_agent.db.models import Base
from maintenance_agent.db.types import Vector


def test_phase_1_model_mappers_configure() -> None:
    configure_mappers()


def test_phase_1_models_define_expected_tables() -> None:
    assert set(Base.metadata.tables) == {
        "assets",
        "telemetry_snapshots",
        "fault_events",
        "maintenance_events",
        "observations",
        "work_orders",
        "fault_taxonomy",
        "operating_limits",
        "plant_policies",
        "rag_chunks",
    }


def test_phase_1_models_use_natural_primary_keys() -> None:
    expected_primary_keys = {
        "assets": ["asset_id"],
        "telemetry_snapshots": ["snapshot_id"],
        "fault_events": ["event_id"],
        "maintenance_events": ["maintenance_id"],
        "observations": ["observation_id"],
        "work_orders": ["work_order_id"],
        "fault_taxonomy": ["fault_code"],
        "operating_limits": ["operating_limit_id"],
        "plant_policies": ["policy_id"],
        "rag_chunks": ["chunk_id"],
    }

    for table_name, expected_columns in expected_primary_keys.items():
        table = Base.metadata.tables[table_name]
        assert [column.name for column in table.primary_key.columns] == expected_columns


def test_phase_1_models_define_asset_foreign_keys() -> None:
    for table_name in {
        "telemetry_snapshots",
        "fault_events",
        "maintenance_events",
        "observations",
        "work_orders",
    }:
        table = Base.metadata.tables[table_name]
        foreign_keys = {
            constraint.name: constraint
            for constraint in table.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }

        assert len(foreign_keys) == 1
        constraint = next(iter(foreign_keys.values()))
        assert [element.parent.name for element in constraint.elements] == ["asset_id"]
        assert [element.column.table.name for element in constraint.elements] == ["assets"]
        assert [element.column.name for element in constraint.elements] == ["asset_id"]


def test_phase_1_models_define_expected_checks_and_indexes() -> None:
    expected_checks = {
        "assets": {"ck_assets_asset_type", "ck_assets_model", "ck_assets_status"},
        "fault_events": {"ck_fault_events_severity", "ck_fault_events_status"},
        "maintenance_events": {"ck_maintenance_events_type"},
        "observations": {"ck_observations_type", "ck_observations_severity"},
        "work_orders": {"ck_work_orders_priority", "ck_work_orders_status"},
        "operating_limits": {
            "ck_operating_limits_model",
            "ck_operating_limits_source_type",
        },
        "plant_policies": {"ck_plant_policies_type"},
        "rag_chunks": {
            "ck_rag_chunks_equipment_type",
            "ck_rag_chunks_applicability",
            "ck_rag_chunks_content_provenance",
            "ck_rag_chunks_topic",
        },
    }

    for table_name, check_names in expected_checks.items():
        table = Base.metadata.tables[table_name]
        actual_check_names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert actual_check_names == check_names

    index_names = {
        index.name
        for table in Base.metadata.tables.values()
        for index in table.indexes
        if isinstance(index, Index)
    }
    assert index_names == {
        "ix_telemetry_snapshots_asset_id_timestamp",
        "ix_fault_events_asset_id_timestamp",
        "ix_fault_events_asset_id_fault_code",
        "ix_maintenance_events_asset_id_date",
        "ix_observations_asset_id_timestamp",
        "ix_work_orders_asset_id_created_at",
    }


def test_work_order_status_check_accepts_completed_and_submitted_only() -> None:
    work_orders = Base.metadata.tables["work_orders"]
    status_checks = [
        str(constraint.sqltext)
        for constraint in work_orders.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_work_orders_status"
    ]

    assert status_checks == ["status IN ('completed', 'submitted')"]


def test_task_4_rag_chunks_schema_uses_typed_metadata_columns_and_vector() -> None:
    rag_chunks = Base.metadata.tables["rag_chunks"]

    expected_columns = {
        "chunk_id",
        "document_id",
        "chunk_heading",
        "text",
        "content_hash",
        "manufacturer",
        "source_product_family",
        "section",
        "page",
        "equipment_type",
        "applicability",
        "source_url",
        "content_provenance",
        "topic",
        "linked_fault_codes",
        "embedding",
    }

    assert set(rag_chunks.c.keys()) == expected_columns
    assert "metadata" not in rag_chunks.c
    assert "json" not in {column.type.__class__.__name__.lower() for column in rag_chunks.c}
    assert rag_chunks.c.linked_fault_codes.type.item_type.__class__.__name__ == "Text"
    assert isinstance(rag_chunks.c.embedding.type, Vector)
    assert rag_chunks.c.embedding.type.dimension == 512
    assert rag_chunks.indexes == set()


def test_phase_1_models_keep_selected_fields_unconstrained() -> None:
    maintenance_events = Base.metadata.tables["maintenance_events"]
    observations = Base.metadata.tables["observations"]

    maintenance_check_sql = [
        str(constraint.sqltext)
        for constraint in maintenance_events.constraints
        if isinstance(constraint, CheckConstraint)
    ]
    observation_check_sql = [
        str(constraint.sqltext)
        for constraint in observations.constraints
        if isinstance(constraint, CheckConstraint)
    ]

    assert all("component" not in sql for sql in maintenance_check_sql)
    assert all("reported_by" not in sql for sql in observation_check_sql)


def test_phase_1_measurement_models_use_numeric_columns() -> None:
    telemetry_snapshots = Base.metadata.tables["telemetry_snapshots"]
    operating_limits = Base.metadata.tables["operating_limits"]

    telemetry_columns = {
        "vibration_mm_s": (6, 2),
        "bearing_temperature_c": (6, 2),
        "inlet_pressure_bar": (6, 2),
        "discharge_pressure_bar": (6, 2),
        "flow_rate_l_min": (8, 2),
    }
    limit_columns = {
        "normal_min": (8, 2),
        "normal_max": (8, 2),
        "warning_min": (8, 2),
        "warning_max": (8, 2),
        "critical_min": (8, 2),
        "critical_max": (8, 2),
    }

    for column_name, expected_precision_scale in telemetry_columns.items():
        column_type = telemetry_snapshots.c[column_name].type
        assert isinstance(column_type, Numeric)
        assert (column_type.precision, column_type.scale) == expected_precision_scale

    for column_name, expected_precision_scale in limit_columns.items():
        column_type = operating_limits.c[column_name].type
        assert isinstance(column_type, Numeric)
        assert (column_type.precision, column_type.scale) == expected_precision_scale


def test_phase_1_timestamp_columns_are_timezone_aware() -> None:
    expected_timestamp_columns = {
        "telemetry_snapshots": ["timestamp"],
        "fault_events": ["timestamp"],
        "observations": ["timestamp"],
    }

    for table_name, column_names in expected_timestamp_columns.items():
        table = Base.metadata.tables[table_name]
        for column_name in column_names:
            column_type = table.c[column_name].type
            assert isinstance(column_type, DateTime)
            assert column_type.timezone is True
