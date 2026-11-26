import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from agentic_gateway.config import Settings
from agentic_gateway.database import make_engine
from agentic_gateway.catalog.registry import register_source


@pytest.fixture
def db(monkeypatch):
    if os.getenv("REGISTRY_TEST_DATABASE") != "isolated":
        pytest.skip("Use scripts/test-registry.ps1 for a dedicated PostgreSQL project")
    settings = Settings.from_env()
    # Defense in depth: destructive resets are forbidden on the development DB.
    assert settings.database == "registry_test"
    engine = make_engine(settings)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    command.upgrade(Config("/app/alembic.ini"), "head")
    register_source(engine, "fixture/catalog-a", "catalog-a")
    monkeypatch.setenv("OPERATOR_API_KEY", "test-only-operator")
    yield engine
    engine.dispose()
