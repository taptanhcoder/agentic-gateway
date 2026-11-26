import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

from agentic_gateway import api
from agentic_gateway.config import Settings
from agentic_gateway.database import migration_heads


@pytest.fixture
def engine(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    monkeypatch.setattr(api, "make_engine", lambda settings: engine)
    yield engine
    engine.dispose()


def test_liveness_and_missing_schema(engine):
    with TestClient(api.create_app()) as client:
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "live"}
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}


@pytest.mark.parametrize("revisions, status", [
    ([], 503), (["old_revision"], 503),
    (["0001_baseline"], 503), (list(migration_heads()), 200),
    ([*migration_heads(), "unexpected"], 503),
])
def test_schema_must_exactly_match_image(engine, revisions, status):
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        for revision in revisions:
            connection.execute(
                text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": revision}
            )
    with TestClient(api.create_app()) as client:
        assert client.get("/health/ready").status_code == status


def test_engine_disposed_on_shutdown(engine):
    disposed = []
    event.listen(engine, "engine_disposed", lambda engine: disposed.append(True))
    with TestClient(api.create_app()) as client:
        assert client.get("/health/live").status_code == 200
    assert disposed == [True]


def test_password_is_not_in_settings_repr_or_display_url():
    settings = Settings(password="private@:/password")
    assert settings.database_url.password == "private@:/password"
    assert "private" not in repr(settings)
    assert "private" not in str(settings.database_url)
