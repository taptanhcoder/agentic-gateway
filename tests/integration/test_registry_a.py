import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from agentic_gateway.api import create_app
from agentic_gateway.catalog.registry import register_source
from agentic_gateway.catalog.schema import jobs, sources
from agentic_gateway.database import migration_heads

AUTH = {"Authorization": "Bearer test-only-operator"}


def test_enqueue_is_durable_and_seed_is_idempotent(db):
    register_source(db, "fixture/catalog-a", "catalog-a")
    with TestClient(create_app()) as client:
        response = client.post("/admin/sources/fixture/catalog-a/sync", json={"revision": "r1"}, headers=AUTH)
        assert response.status_code == 202
        job_id = response.json()["job_id"]
    # A different app instance/connection sees the queued row with no worker.
    with TestClient(create_app()) as client:
        job = client.get(f"/admin/jobs/{job_id}", headers=AUTH).json()
        assert job["status"] == "queued"
        assert job["source_id"] == "fixture/catalog-a"
        assert job["sync_run_id"] is None and job["pages_read"] == 0
    with db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(sources)) == 1
        assert connection.scalar(select(func.count()).select_from(jobs)) == 1


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_operator_auth_for_every_admin_endpoint(db, headers):
    with TestClient(create_app()) as client:
        for method, path, body in [
            ("POST", "/admin/sources/fixture/catalog-a/sync", {"revision": "r1"}),
            ("GET", "/admin/jobs/unknown", None), ("GET", "/admin/sources", None),
            ("GET", "/admin/sync-runs/unknown", None),
            ("GET", "/admin/offerings", None),
            ("POST", "/admin/sources", {"id": "fixture/catalog-b", "fixture_name": "catalog-b"}),
        ]:
            response = client.request(method, path, json=body, headers=headers)
            assert response.status_code == 401
            assert "test-only-operator" not in response.text


def test_missing_resources_and_invalid_fixture(db):
    with TestClient(create_app()) as client:
        assert client.post("/admin/sources/missing/sync", json={}, headers=AUTH).status_code == 404
        assert client.get("/admin/jobs/missing", headers=AUTH).status_code == 404
        assert client.get("/admin/sync-runs/missing", headers=AUTH).status_code == 404
        assert client.post("/admin/sources/fixture/catalog-a/sync", json={"revision":"missing"}, headers=AUTH).status_code == 400
        assert client.post("/admin/sources", json={"id":"bad","fixture_name":"../../etc/passwd"}, headers=AUTH).status_code == 400


def test_upgrade_existing_baseline_and_health(db):
    config = Config("/app/alembic.ini")
    command.downgrade(config, "0001_baseline")
    with db.begin() as connection:
        connection.execute(text("CREATE TABLE foundation_marker (value text)"))
        connection.execute(text("INSERT INTO foundation_marker VALUES ('preserved')"))
    with TestClient(create_app()) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        assert client.get("/health/ready").status_code == 200
    with db.connect() as connection:
        assert connection.scalar(text("SELECT value FROM foundation_marker")) == "preserved"
        assert frozenset(connection.execute(text("SELECT version_num FROM alembic_version")).scalars()) == migration_heads()
