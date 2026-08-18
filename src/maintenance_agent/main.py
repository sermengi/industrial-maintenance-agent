import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from maintenance_agent.api.agent import router as agent_router
from maintenance_agent.api.health import router as health_router
from maintenance_agent.core.config import get_settings
from maintenance_agent.db.session import verify_database_connection
from maintenance_agent.llm.client import get_llm_client
from maintenance_agent.orchestration.graph import AgentGraphDependencies, build_agent_graph


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    await verify_database_connection()
    app.state.agent_graph = build_agent_graph(AgentGraphDependencies(llm_client=get_llm_client()))
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
