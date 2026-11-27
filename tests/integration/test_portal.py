"""Portal contracts against disposable PostgreSQL; zero live provider inference."""
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import insert

from agentic_gateway.api import create_app
from agentic_gateway.catalog.sync import claim_job
from agentic_gateway.providers.inference.deployments import attempts
from agentic_gateway.qualification import service
from test_deployments import target_db

AUTH = {"Authorization": "Bearer test-only-operator"}


@pytest.mark.parametrize("resource", ["summary", "deployments", "accounts", "qualifications", "routes", "attempts", "jobs"])
def test_portal_reads_are_operator_only_and_paginated(db, resource):
    with TestClient(create_app()) as client:
        path = f"/admin/portal/{resource}"
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        response = client.get(path, headers=AUTH)
        assert response.status_code == 200
        assert "test-only-operator" not in response.text
        if resource == "summary":
            data = response.json()
            assert data["capabilities"]["public_inference"] is False
            assert data["capabilities"]["agents"] is False
            assert data["counts"]["sources"] == 1
        else:
            assert response.json()["pagination"]["limit"] == 50
            assert client.get(path + "?limit=101", headers=AUTH).status_code == 422
            assert client.get(path + "?offset=-1", headers=AUTH).status_code == 422


def test_attempt_read_dto_does_not_leak_arbitrary_report_content(target_db):
    engine, _ = target_db
    with engine.begin() as connection:
        connection.execute(insert(attempts).values(id="sensitive-report", deployment_id="test-deployment",
            generation=1, mode="fixture", result_code="ok", provider_outcome="succeeded", report={
                "output_text": "do-not-expose-output", "prompt": "do-not-expose-prompt",
                "headers": {"Authorization": "fixture-secret-token"}, "api_key": "fixture-secret-token",
                "latency_ms": 12, "client_send_count": 1,
                "usage": {"status": "unknown", "total_tokens": None}}))
    with TestClient(create_app()) as client:
        response = client.get("/admin/portal/attempts?limit=1", headers=AUTH)
        assert response.status_code == 200
        for secret in ("do-not-expose-output", "do-not-expose-prompt", "fixture-secret-token", "api_key", "Authorization"):
            assert secret not in response.text
        row = response.json()["data"][0]
        assert row["latency_ms"] == 12
        assert row["usage"]["total_tokens"] is None
        assert row["agent_id"] is None and row["cost_usd"] is None


def test_existing_deployment_and_fixture_qualification_flow_is_visible(target_db):
    engine, offering_id = target_db
    with TestClient(create_app()) as client:
        records = client.get("/admin/portal/deployments", headers=AUTH).json()
        assert records["pagination"]["total"] == 1
        assert records["data"][0]["secret_ref_id"] == "env:NVIDIA_API_KEY"
        assert "fixture-secret-token" not in str(records)
        run = client.post("/admin/qualifications", headers=AUTH,
            json={"deployment_id": "test-deployment", "mode": "fixture", "max_probes": 2}).json()
        service.process(engine, claim_job(engine))
        result = client.get("/admin/portal/qualifications", headers=AUTH).json()["data"][0]
        assert result["id"] == run["run_id"]
        assert result["mode"] == "fixture" and result["decision"] == "eligible"
        assert client.post("/admin/pinned-models", headers=AUTH, json={
            "model_id": "ag/test", "verdict_id": result["verdict_id"], "expected_route_version": 0}).status_code == 409
        changed = client.put("/admin/portal/deployment-state/test-deployment", headers=AUTH, json={"enabled": False})
        assert changed.status_code == 200
        current = client.get("/admin/portal/deployments", headers=AUTH).json()["data"][0]
        assert current["enabled"] is False and current["qualification_epoch"] == 1
        # Portal configuration cannot bypass official LIVE evidence with fixture-only observations.
        assert client.post("/admin/portal/deployments", headers=AUTH, json={
            "deployment_id": "new-target", "offering_id": offering_id, "account_id": "test-account"}).status_code == 409
        assert client.post("/admin/portal/deployments", headers=AUTH, json={
            "deployment_id": "new-target", "offering_id": offering_id, "account_id": "test-account", "_fixture": True}).status_code == 422
        assert client.put("/admin/portal/deployment-state/test-deployment", json={"enabled": True}).status_code == 401
