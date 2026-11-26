from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from agentic_gateway.api import create_app
from agentic_gateway.catalog.registry import enqueue, get_job, register_source
from agentic_gateway.catalog.schema import jobs, runs, sources
from agentic_gateway.catalog.sync import claim_job, process_job

AUTH = {"Authorization":"Bearer test-only-operator"}


def sync(db, revision="r1", source="fixture/catalog-a"):
    job_id = enqueue(db, source, revision)
    process_job(db, claim_job(db))
    return get_job(db, job_id)


def test_never_synced_and_queued_source_are_explicitly_null(db):
    with TestClient(create_app()) as client:
        source, = client.get("/admin/sources", headers=AUTH).json()["data"]
        for name in ("latest_attempt", "last_success_at", "last_successful_run_id", "active_snapshot_id", "active_snapshot", "next_sync_at"):
            assert source[name] is None
        assert source["connector"]["status"] == "available"
        enqueue(db, "fixture/catalog-a", "r1")
        source, = client.get("/admin/sources", headers=AUTH).json()["data"]
        assert source["latest_job"]["status"] == "queued" and source["latest_attempt"] is None


def test_failed_latest_attempt_preserves_success_and_current_catalog(db):
    success = sync(db)
    assert success["status"] == "succeeded"
    next_at = datetime.now(timezone.utc) + timedelta(hours=1)
    with TestClient(create_app()) as client:
        response = client.put("/admin/sources/fixture/catalog-a/schedule", headers=AUTH,
            json={"interval_seconds":3600, "next_sync_at":next_at.isoformat(), "revision":"last-page-timeout"})
        assert response.status_code == 200
        before = client.get("/admin/offerings", headers=AUTH).json()
        failed = sync(db, "last-page-timeout")
        source, = client.get("/admin/sources", headers=AUTH).json()["data"]
        run = client.get(f"/admin/sync-runs/{failed['sync_run_id']}", headers=AUTH).json()
        first = client.get(f"/admin/sync-runs/{success['sync_run_id']}", headers=AUTH).json()
        assert client.get("/admin/offerings", headers=AUTH).json() == before
    assert source["latest_attempt"]["id"] == failed["sync_run_id"]
    assert source["latest_attempt"]["status"] == "failed"
    assert source["last_successful_run_id"] == success["sync_run_id"]
    assert source["active_snapshot_id"] == first["snapshot_id"]
    assert source["active_snapshot"]["sync_run_id"] == success["sync_run_id"]
    assert datetime.fromisoformat(source["next_sync_at"].replace("Z", "+00:00")) == next_at
    assert run["trigger"] == "manual" and run["source_id"] == "fixture/catalog-a"
    assert run["pages_read"] == run["records_staged"] == 1
    assert run["records_published"] == 0 and run["snapshot_id"] is None
    assert run["diff"] is run["published_diff"] is None and run["published"] is False
    assert run["error"] == {"code":"fixture_page_timeout"}
    assert first["records_staged"] == first["records_published"] == 2 and first["published"]
    assert len(first["diff"]["added"]) == 2
    assert datetime.fromisoformat(run["started_at"].replace("Z", "+00:00")).utcoffset() == timedelta(0)


def test_filters_and_pagination_operate_on_offering_before_evidence(db):
    sync(db)
    register_source(db, "fixture/catalog-b", "catalog-b")
    sync(db, source="fixture/catalog-b")
    with TestClient(create_app()) as client:
        collected = []
        for offset in range(3):
            page = client.get(f"/admin/offerings?limit=1&offset={offset}", headers=AUTH).json()
            assert page["pagination"]["total"] == 3
            assert len(page["data"]) == 1
            collected.extend(page["data"])
        assert len({item["id"] for item in collected}) == 3
        assert [item["id"] for item in collected] == sorted(item["id"] for item in collected)
        alpha = next(item for item in collected if item["upstream_model"] == "alpha" and item["task_type"] == "chat")
        assert len(alpha["evidence"]) == len(alpha["source_ids"]) == 2
        assert alpha["evidence_url"].endswith(f"/{alpha['id']}/evidence")
        filtered = client.get("/admin/offerings", params={"provider":"demo-provider", "source":"fixture/catalog-a",
            "region":"global", "task_type":"chat"}, headers=AUTH).json()
        assert filtered["pagination"]["total"] == len(filtered["data"]) == 2
        assert len(next(item for item in filtered["data"] if item["upstream_model"] == "alpha")["evidence"]) == 2
        assert client.get("/admin/offerings?source=missing", headers=AUTH).json()["data"] == []
        assert client.get("/admin/offerings?task_type=embedding", headers=AUTH).json()["pagination"]["total"] == 1
        sync(db, "r3")
        assert not any(item["upstream_model"] == "alpha" for item in client.get(
            "/admin/offerings?source=fixture/catalog-a", headers=AUTH).json()["data"])
        for query in ("limit=0", "limit=101", "offset=-1"):
            assert client.get("/admin/offerings?" + query, headers=AUTH).status_code == 422


@pytest.mark.parametrize("headers", [{}, {"Authorization":"Bearer incorrect"}])
def test_all_management_endpoints_require_operator(db, headers):
    with TestClient(create_app()) as client:
        for path in ("/admin/sources", "/admin/offerings", "/admin/sync-runs/missing"):
            assert client.get(path, headers=headers).status_code == 401
        assert client.put("/admin/sources/fixture/catalog-a/schedule", headers=headers,
            json={"interval_seconds":None, "next_sync_at":None}).status_code == 401


def test_schedule_validation_and_missing_ids(db):
    with TestClient(create_app()) as client:
        assert client.get("/admin/sync-runs/missing", headers=AUTH).status_code == 404
        assert client.put("/admin/sources/missing/schedule", headers=AUTH,
            json={"interval_seconds":None,"next_sync_at":None}).status_code == 404
        for body in (
            {"interval_seconds":60,"next_sync_at":"2026-11-26T08:00:00"},
            {"interval_seconds":None,"next_sync_at":"2026-11-26T08:00:00Z"},
            {"interval_seconds":0,"next_sync_at":"2026-11-26T08:00:00Z"},
        ):
            assert client.put("/admin/sources/fixture/catalog-a/schedule", headers=AUTH, json=body).status_code == 422


def test_responses_do_not_echo_config_authorization_or_raw_errors(db):
    failed = sync(db, "last-page-timeout")
    secret = "DO_NOT_EXPOSE_OPERATOR_OR_UPSTREAM_SECRET"
    with db.begin() as connection:
        connection.execute(update(sources).values(config={"Authorization":secret, "password":secret},
            locator=f"https://user:{secret}@example.invalid/catalog?key={secret}"))
        connection.execute(update(runs).where(runs.c.id == failed["sync_run_id"]).values(
            error_code="HTTP 200 " + secret, fetch_metadata=[{"message":secret,"Authorization":secret,
                "locator":f"https://example.invalid/?key={secret}", "read_at":"malformed " + secret}]))
        connection.execute(update(jobs).where(jobs.c.id == failed["id"]).values(error_code=secret))
    with TestClient(create_app()) as client:
        for path in ("/admin/sources", "/admin/offerings", f"/admin/sync-runs/{failed['sync_run_id']}", f"/admin/jobs/{failed['id']}"):
            response = client.get(path, headers=AUTH)
            assert response.status_code == 200
            assert secret not in response.text and '"Authorization"' not in response.text


def test_official_source_filter_includes_exact_3c_match_without_new_offering(db, monkeypatch):
    from agentic_gateway.providers.catalog import connectors
    from agentic_gateway.providers.catalog.seeds import seed_providers
    from agentic_gateway.providers.catalog.transport import Document, NVIDIA
    seed_providers(db)
    with db.begin() as connection:
        connection.execute(insert(sources).values(id="fixture/github", connector="github_fixture"))
    sync(db, source="fixture/github")
    body = (Path(__file__).parents[1] / "fixtures/nvidia-hosted.html").read_text()
    monkeypatch.setattr(connectors, "fetch_document", lambda locator, credential=None:
        Document(NVIDIA,body,mode="fixture",fetched_at="2026-11-26T08:00:00+00:00"))
    sync(db, "latest", "official/nvidia-hosted")
    with TestClient(create_app()) as client:
        result = client.get("/admin/offerings?source=official%2Fnvidia-hosted&limit=1", headers=AUTH).json()
        assert result["pagination"]["total"] == len(result["data"]) == 1
        offering, = result["data"]
        assert offering["upstream_model"] == "meta/llama-3.3-70b-instruct"
        assert set(offering["source_ids"]) == {"fixture/github", "official/nvidia-hosted"}
        assert {item["association"] for item in offering["evidence"]} == {"direct","exact_official"}
