import asyncio
import os

import pytest
from sqlalchemy import text

from maintenance_agent.core.config import get_settings
from maintenance_agent.db.session import async_session_factory, check_database_connection


@pytest.mark.asyncio
async def test_database_connectivity() -> None:
    if os.getenv("RUN_DB_INTEGRATION") != "1":
        pytest.skip("Set RUN_DB_INTEGRATION=1 to run the database integration test.")

    if get_settings().database_url is None:
        pytest.skip("DATABASE_URL is not configured.")

    database_available = await asyncio.wait_for(check_database_connection(), timeout=3)
    if not database_available:
        pytest.skip("Configured database is not reachable.")

    assert async_session_factory is not None
    async with async_session_factory() as session:
        result = await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=3)

    assert result.scalar_one() == 1
