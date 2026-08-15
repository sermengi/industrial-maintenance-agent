import pytest

from maintenance_agent.core.config import get_settings
from maintenance_agent.db.cli import resolve_database_url


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_resolve_database_url_selects_dev_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://dev")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://test")

    assert resolve_database_url("dev") == "postgresql+asyncpg://dev"


def test_resolve_database_url_selects_test_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://dev")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://test")

    assert resolve_database_url("test") == "postgresql+asyncpg://test"


def test_resolve_database_url_requires_configured_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL is required"):
        resolve_database_url("test")
