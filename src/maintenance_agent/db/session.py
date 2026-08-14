import logging
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from maintenance_agent.core.config import get_settings

logger = logging.getLogger(__name__)


def create_engine() -> AsyncEngine | None:
    settings = get_settings()
    if not settings.database_url:
        return None
    return create_async_engine(settings.database_url, pool_pre_ping=True)


engine = create_engine()
async_session_factory = (
    async_sessionmaker(engine, expire_on_commit=False) if engine is not None else None
)


async def get_session() -> AsyncIterator[AsyncSession]:
    if async_session_factory is None:
        raise RuntimeError("DATABASE_URL is not configured.")
    async with async_session_factory() as session:
        yield session


async def check_database_connection() -> bool:
    if engine is None:
        return False

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def verify_database_connection() -> None:
    if engine is None:
        logger.info("DATABASE_URL is not configured; skipping startup database check.")
        return

    logger.info("Checking startup database connectivity.")
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    logger.info("Startup database connectivity check succeeded.")
