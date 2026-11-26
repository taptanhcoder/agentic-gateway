from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from agentic_gateway.api import create_app
from agentic_gateway.catalog.registry import enqueue, get_job
from agentic_gateway.catalog.reconciliation import offering_evidence
from agentic_gateway.catalog.schema import documents, memberships, observations, offerings, sources
from agentic_gateway.catalog.sync import claim_job, process_job, stage_page, publish_snapshot
from agentic_gateway.providers.catalog import connectors
from agentic_gateway.providers.catalog.seeds import seed_providers
from agentic_gateway.providers.catalog.transport import Document, NVIDIA, SF_API, SF_DOCS, SF_WEB

FIXTURES = Path(__file__).parents[1] / "fixtures"
AUTH = {"Authorization":"Bearer test-only-operator"}


def setup(db, monkeypatch):
    seed_providers(db)
    seed_providers(db)
    with db.begin() as connection:
        connection.execute(insert(sources).values(id="fixture/github", connector="github_fixture"))
    sync(db, "fixture/github", "r1")
    monkeypatch.setenv("SILICONFLOW_CN_CATALOG_API_KEY", "fixture-only-secret")
    bodies = {NVIDIA:(FIXTURES / "nvidia-hosted.html").read_text(),
              SF_API:(FIXTURES / "siliconflow-models.json").read_text(),
              SF_DOCS:(FIXTURES / "siliconflow-cline.html").read_text()}
    calls = []

    def fetch(locator, credential=None):
        calls.append(locator)
        if credential:
            assert locator == SF_API
        timestamp = datetime(2026,11,26,tzinfo=timezone.utc) + timedelta(seconds=len(calls))
        return Document(locator, bodies[locator], mode="fixture", fetched_at=timestamp.isoformat())

    monkeypatch.setattr(connectors, "fetch_document", fetch)
    return bodies, calls


def sync(db, source_id, revision="latest"):
    job_id = enqueue(db, source_id, revision)
    claim = claim_job(db)
    assert claim.job_id == job_id
    process_job(db, claim)
    return get_job(db, job_id)


def model_id(db, model="meta/llama-3.3-70b-instruct"):
    with db.connect() as connection:
        return connection.scalar(select(offerings.c.id).where(offerings.c.upstream_model == model))


def source_state(db, source_id):
    with db.connect() as connection:
        pointer = connection.scalar(select(sources.c.current_snapshot_id).where(sources.c.id == source_id))
        ids = set(connection.execute(select(memberships.c.observation_id).where(memberships.c.snapshot_id == pointer)).scalars())
        return pointer, ids


def test_exact_model_has_two_evidence_sources_but_free_and_quota_unverified(db, monkeypatch):
    setup(db, monkeypatch)
    assert sync(db, "official/nvidia-hosted")["status"] == "succeeded"
    offering_id = model_id(db)
    with TestClient(create_app()) as client:
        assert client.get(f"/admin/offerings/{offering_id}/evidence").status_code == 401
        assert client.get("/admin/offerings/missing/evidence", headers=AUTH).status_code == 404
        response = client.get(f"/admin/offerings/{offering_id}/evidence", headers=AUTH)
        assert response.status_code == 200
        proof = response.json()
    assert proof["match_status"] == "matched"
    assert proof["fields"]["upstream_model"]["status"] == "fixture_confirmed"
    for field in ("pricing", "quota", "tool_calling"):
        assert proof["fields"][field]["status"] == "unverified"
        assert proof["fields"][field]["value"] == "unknown"
    assert any(c["value"] == "free" and c["origin"] == "discovery" for c in proof["fields"]["pricing"]["claims"])
    assert proof["fields"]["base_url"]["status"] == "provider_scope_only"
    github = proof["discovery_evidence"][0]["provenance"]
    assert set(github["github"]) >= {"owner","repo","path","commit_sha"}
    assert github["mode"] == "fixture" and proof["github_live_available"] is False
    official = proof["official_evidence"][0]
    assert official["provenance"]["locator"] == NVIDIA
    assert official["position"].startswith("table[")
    assert official["provenance"]["content_hash"] and official["provenance"]["read_at"]
    with db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(offerings).where(offerings.c.id == offering_id)) == 1
        assert connection.scalar(select(func.count()).select_from(documents)) == 1


def test_different_prefix_and_display_only_never_merge_or_confirm(db, monkeypatch):
    bodies, _ = setup(db, monkeypatch)
    sync(db, "official/nvidia-hosted")
    different = model_id(db, "other-prefix/llama-3.3-70b-instruct")
    assert different != model_id(db)
    assert offering_evidence(db, different)["match_status"] == "pending"
    bodies[NVIDIA] = bodies[NVIDIA].replace("meta / llama-3.3-70b-instruct", "Llama Display Name")
    sync(db, "official/nvidia-hosted")
    proof = offering_evidence(db, model_id(db))
    assert proof["match_status"] == "pending"
    assert proof["fields"]["upstream_model"]["status"] == "unverified"
    assert any(row["reason_code"] == "exact_model_id_missing" for row in proof["unresolved_catalog_records"] if row["current"])
    assert all(row["associated_offering_id"] is None for row in proof["unresolved_catalog_records"])
    assert all(row["fields"].get("upstream_model") for row in proof["official_evidence"])


def test_same_content_reuses_observations_for_both_providers(db, monkeypatch):
    setup(db, monkeypatch)
    for source in ("official/nvidia-hosted", "official/siliconflow-cn", "official/siliconflow-cn-docs"):
        assert sync(db, source)["status"] == "succeeded"
    with db.connect() as connection:
        before = connection.scalar(select(func.count()).select_from(observations))
    for source in ("official/nvidia-hosted", "official/siliconflow-cn", "official/siliconflow-cn-docs"):
        job = sync(db, source)
        assert job["status"] == "succeeded"
        assert job["diff"]["observations_added"] == job["diff"]["observations_removed"] == []
    with db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(observations)) == before
        assert connection.scalar(select(func.count()).select_from(offerings)) == 3


def test_parser_error_after_success_preserves_snapshot_and_reports_latest_failure(db, monkeypatch):
    bodies, _ = setup(db, monkeypatch)
    sync(db, "official/nvidia-hosted")
    before = source_state(db, "official/nvidia-hosted")
    assert len(before[1]) == 1
    bodies[NVIDIA] = '<html><h1>Error</h1><p>HTTP 200 but not a catalog</p></html>'
    job = sync(db, "official/nvidia-hosted")
    assert job["status"] == "failed" and job["error_code"] == "nvidia_catalog_structure_changed"
    assert source_state(db, "official/nvidia-hosted") == before
    proof = offering_evidence(db, model_id(db))
    attempt = next(row for row in proof["reconciliation"] if row["source_id"] == "official/nvidia-hosted")
    assert attempt["latest_attempt"]["status"] == "failed"
    assert attempt["latest_attempt"]["fetch_metadata"][0]["content_hash"]
    assert proof["fields"]["upstream_model"]["status"] == "fixture_confirmed"


def test_cn_login_preserves_snapshot_and_nvidia_job_still_succeeds(db, monkeypatch):
    bodies, calls = setup(db, monkeypatch)
    sync(db, "official/siliconflow-cn")
    before = source_state(db, "official/siliconflow-cn")
    assert len(before[1]) == 1
    monkeypatch.delenv("SILICONFLOW_CN_CATALOG_API_KEY")
    bodies[SF_WEB] = '<html><title>Sign in</title><h1>登录</h1><input type="password"></html>'
    failed_id = enqueue(db, "official/siliconflow-cn", "latest")
    good_id = enqueue(db, "official/nvidia-hosted", "latest")
    process_job(db, claim_job(db))
    process_job(db, claim_job(db))
    failed, good = get_job(db, failed_id), get_job(db, good_id)
    assert failed["status"] == "blocked" and failed["error_code"] == "catalog_login_required"
    assert good["status"] == "succeeded"
    assert source_state(db, "official/siliconflow-cn") == before
    assert calls[-2:] == [SF_WEB, NVIDIA]
    assert sync(db, "official/siliconflow-cn-docs")["status"] == "succeeded"
    proof = offering_evidence(db, model_id(db, "deepseek-ai/DeepSeek-V4-Flash"))
    assert proof["fields"]["base_url"]["status"] == "provider_scope_only"
    assert proof["fields"]["pricing"]["status"] == "unverified"


def test_valid_empty_api_catalog_removes_current_official_evidence_not_history(db, monkeypatch):
    bodies, _ = setup(db, monkeypatch)
    sync(db, "official/siliconflow-cn")
    before = source_state(db, "official/siliconflow-cn")
    bodies[SF_API] = '{"object":"list","data":[]}'
    job = sync(db, "official/siliconflow-cn")
    assert job["status"] == "succeeded"
    after = source_state(db, "official/siliconflow-cn")
    assert before[0] != after[0] and not after[1]
    proof = offering_evidence(db, model_id(db, "deepseek-ai/DeepSeek-V4-Flash"))
    assert proof["fields"]["upstream_model"]["status"] == "unverified"
    assert proof["official_evidence"] and all(not row["current"] for row in proof["official_evidence"])


def test_ambiguous_endpoint_is_pending_not_auto_linked(db, monkeypatch):
    setup(db, monkeypatch)
    with db.begin() as connection:
        connection.execute(insert(sources).values(id="fixture/ambiguous", connector="github_fixture"))
    enqueue(db, "fixture/ambiguous", "r1")
    claim = claim_job(db)
    from agentic_gateway.catalog.github_fixture import read_fixture
    record = read_fixture(claim)[0]
    record["offering"]["base_url"] = "https://other-endpoint.example/v1"
    record["claims"]["base_url"]["value"] = "https://other-endpoint.example/v1"
    stage_page(db, claim, 1, 1, [record])
    assert publish_snapshot(db, claim)
    sync(db, "official/nvidia-hosted")
    with db.connect() as connection:
        ids = connection.execute(select(offerings.c.id).where(offerings.c.upstream_model == "meta/llama-3.3-70b-instruct")).scalars().all()
    assert len(ids) == 2
    for offering_id in ids:
        proof = offering_evidence(db, offering_id)
        assert proof["match_status"] == "pending"
        assert any(row["reason_code"] == "ambiguous_offering" for row in proof["official_evidence"])


def test_conflicting_task_values_remain_visible(db, monkeypatch):
    setup(db, monkeypatch)
    parser = connectors.parse_nvidia

    def conflicting(document):
        records = parser(document)
        records[0]["claims"]["task"]["value"] = "embedding"
        return records

    monkeypatch.setattr(connectors, "parse_nvidia", conflicting)
    sync(db, "official/nvidia-hosted")
    proof = offering_evidence(db, model_id(db))
    assert proof["fields"]["task"]["status"] == "conflicting"
    assert {claim["value"] for claim in proof["fields"]["task"]["claims"]} == {"chat","embedding"}
    assert proof["match_status"] == "pending"
