from fastapi.testclient import TestClient

from agentic_gateway.api import create_app


def test_liveness_survives_unreachable_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost:1/gateway")
    with TestClient(create_app()) as client:
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/live").json() == {"status": "live"}
