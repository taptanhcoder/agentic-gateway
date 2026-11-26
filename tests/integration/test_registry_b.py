from fastapi.testclient import TestClient
from sqlalchemy import func, select

from agentic_gateway.api import create_app
from agentic_gateway.catalog.registry import enqueue, get_job, register_source
from agentic_gateway.catalog.schema import observations, offerings, runs, snapshots, sources
from agentic_gateway.catalog.sync import claim_job, process_job

AUTH = {"Authorization": "Bearer test-only-operator"}


def sync(db, revision, source="fixture/catalog-a"):
    job_id = enqueue(db, source, revision)
    claim = claim_job(db)
    assert claim.job_id == job_id
    process_job(db, claim)
    job = get_job(db, job_id)
    assert job["status"] == "succeeded", job
    return job


def count(db, table):
    with db.connect() as connection:
        return connection.scalar(select(func.count()).select_from(table))


def test_same_revision_is_idempotent_but_keeps_run_history(db):
    first = sync(db, "r1")
    before = (count(db, offerings), count(db, observations))
    second = sync(db, "r1")
    assert before == (2, 2) == (count(db, offerings), count(db, observations))
    assert first["sync_run_id"] != second["sync_run_id"]
    assert count(db, runs) == count(db, snapshots) == 2
    assert second["diff"]["added"] == second["diff"]["removed"] == []
    assert len(second["diff"]["unchanged"]) == 2


def test_revision_two_adds_exactly_one_offering(db):
    sync(db, "r1")
    second = sync(db, "r2")
    assert len(second["diff"]["added"]) == 1
    assert second["diff"]["removed"] == []
    with db.connect() as connection:
        assert connection.scalar(select(offerings.c.upstream_model)
            .where(offerings.c.id == second["diff"]["added"][0])) == "gamma"


def test_two_sources_share_offering_but_not_evidence_or_task(db):
    sync(db, "r1")
    register_source(db, "fixture/catalog-b", "catalog-b")
    sync(db, "r1", "fixture/catalog-b")
    assert count(db, offerings) == 3  # alpha/chat, beta/chat, alpha/embedding
    with TestClient(create_app()) as client:
        catalog = client.get("/admin/offerings", headers=AUTH).json()["data"]
        alpha = next(item for item in catalog if item["upstream_model"] == "alpha" and item["task"] == "chat")
        assert {e["source_id"] for e in alpha["evidence"]} == {"fixture/catalog-a", "fixture/catalog-b"}
        assert alpha["inference_eligible"] is False
        assert len([item for item in catalog if item["upstream_model"] == "alpha"]) == 2
        old_observations = count(db, observations)
        sync(db, "r3")  # A stops mentioning alpha; B must remain.
        catalog = client.get("/admin/offerings", headers=AUTH).json()["data"]
        alpha = next(item for item in catalog if item["upstream_model"] == "alpha" and item["task"] == "chat")
        assert [e["source_id"] for e in alpha["evidence"]] == ["fixture/catalog-b"]
        assert count(db, observations) > old_observations  # historical evidence retained
