import pytest

from maintenance_agent.core.config import get_settings
from maintenance_agent.rag.cli import _format_result, build_parser, main
from maintenance_agent.rag.ingestion import RagIngestionResult


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_task_6_rag_cli_exposes_ingest_command_with_force() -> None:
    args = build_parser().parse_args(["ingest", "--database", "test", "--force"])

    assert args.command == "ingest"
    assert args.database == "test"
    assert args.force is True


def test_task_6_rag_cli_formats_ingest_summary() -> None:
    result = RagIngestionResult(
        desired_chunks=14,
        embedded_chunks=1,
        skipped_chunks=13,
        pruned_chunks=2,
    )

    assert _format_result(result) == (
        "RAG ingest complete: desired=14, embedded=1, skipped=13, pruned=2"
    )


def test_task_6_rag_cli_runs_migrations_and_ingests_selected_database(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[object] = []
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://test")
    monkeypatch.setattr(
        "maintenance_agent.rag.cli.command.upgrade",
        lambda config, revision: calls.append(("upgrade", revision)),
    )

    async def fake_ingest_database(database_url: str, *, force: bool) -> RagIngestionResult:
        calls.append(("ingest", database_url, force))
        return RagIngestionResult(
            desired_chunks=2,
            embedded_chunks=0,
            skipped_chunks=2,
            pruned_chunks=0,
        )

    monkeypatch.setattr("maintenance_agent.rag.cli.ingest_database", fake_ingest_database)

    main(["ingest", "--database", "test"])

    assert calls == [
        ("upgrade", "head"),
        ("ingest", "postgresql+asyncpg://test", False),
    ]
    assert "desired=2, embedded=0, skipped=2, pruned=0" in capsys.readouterr().out
