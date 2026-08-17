from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_revision_chain_includes_phase_3_rag_revision() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = list(script.walk_revisions())

    assert [revision.revision for revision in revisions] == ["20260817_0002", "20260815_0001"]
    assert revisions[0].down_revision == "20260815_0001"
    assert revisions[1].down_revision is None


def test_phase_1_initial_revision_creates_locked_tables_and_timestamptz_columns() -> None:
    migration_source = Path("migrations/versions/20260815_0001_create_phase1_schema.py").read_text()

    expected_tables = {
        "assets",
        "telemetry_snapshots",
        "fault_events",
        "maintenance_events",
        "observations",
        "work_orders",
        "fault_taxonomy",
        "operating_limits",
        "plant_policies",
    }

    assert migration_source.count("op.create_table(") == len(expected_tables)
    for table_name in expected_tables:
        assert f'op.create_table(\n        "{table_name}",' in migration_source

    assert migration_source.count("postgresql.TIMESTAMP(timezone=True)") == 3


def test_task_4_rag_migration_creates_pgvector_schema_without_ann_index() -> None:
    migration_source = Path("migrations/versions/20260817_0002_create_rag_chunks.py").read_text()

    assert 'op.create_table(\n        "rag_chunks",' in migration_source
    assert "CREATE EXTENSION IF NOT EXISTS vector" in migration_source
    assert "Vector(512)" in migration_source
    assert "postgresql.ARRAY(sa.Text())" in migration_source
    assert "JSON" not in migration_source
    assert "JSONB" not in migration_source
    assert "ivfflat" not in migration_source.lower()
    assert "hnsw" not in migration_source.lower()


def test_phase_1_test_database_configuration_is_documented_and_provisioned() -> None:
    env_example = Path(".env.example").read_text()
    compose = Path("docker-compose.yml").read_text()
    postgres_init = Path("docker/postgres-init/01-create-test-database.sh").read_text()

    assert "TEST_DATABASE_URL=" in env_example
    assert "POSTGRES_TEST_DB=maintenance_agent_test" in env_example
    assert "TEST_DATABASE_URL:" in compose
    assert "${POSTGRES_TEST_DB:-maintenance_agent_test}" in compose
    assert "POSTGRES_TEST_DB:" in compose
    assert "CREATE DATABASE ${POSTGRES_TEST_DB:-maintenance_agent_test}" in postgres_init
    assert "WHERE NOT EXISTS" in postgres_init
