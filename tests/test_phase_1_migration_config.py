from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_phase_1_has_single_initial_alembic_revision() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = list(script.walk_revisions())

    assert len(revisions) == 1
    revision = revisions[0]
    assert revision.revision == "20260815_0001"
    assert revision.down_revision is None


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
