import pytest
from fastapi import FastAPI

from maintenance_agent.main import create_app


@pytest.fixture
def app() -> FastAPI:
    return create_app()
