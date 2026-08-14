import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from maintenance_agent.api.agent import router as agent_router
from maintenance_agent.api.health import router as health_router
from maintenance_agent.core.config import get_settings
from maintenance_agent.db.session import verify_database_connection


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    del app
    await verify_database_connection()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(agent_router, prefix="/agent", tags=["agent"])
    return app


app = create_app()
