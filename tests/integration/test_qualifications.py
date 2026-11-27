"""Real PostgreSQL and counted TLS upstream; all provider responses are simulated.

Tests of the live-only publisher use an isolated simulated-live run and an
injected evidence check/transport. They are not evidence of a provider live pass.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import subprocess
import sys
import threading
import time

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text, update

from agentic_gateway.api import create_app
from agentic_gateway.catalog.schema import jobs
from agentic_gateway.catalog.sync import claim_job, LostLease
from agentic_gateway.database import is_ready, migration_heads
from agentic_gateway.providers.inference import adapter
from agentic_gateway.providers.inference.deployments import accounts, attempts, configure, deployments, set_enabled
from agentic_gateway.qualification import routes, service
from agentic_gateway.qualification.schema import observations, pins, policy, runs, snapshots, verdicts
from inference_upstream import LoopbackTransport, upstream
from test_deployments import target_db

AUTH = {"Authorization": "Bearer test-only-operator"}
MODEL = "ag/pinned-text-01"


def enqueue(db, mode="fixture", scenario="ok", max_probes=2):
    return service.enqueue(db, "test-deployment", mode, max_probes, "operator:test", scenario)


def complete(db, *, mode="fixture", scenario="ok", max_probes=2, transport_factory=None):
    job = enqueue(db, mode, scenario, max_probes)
    service.process(db, claim_job(db), _transport_factory=transport_factory)
    return service.detail(db, job["run_id"])


@pytest.fixture
def simulated_live(target_db, monkeypatch):
    db, _ = target_db
    monkeypatch.setenv("QUALIFICATION_LIVE_MAX_PROBES", "2")
    monkeypatch.setenv("QUALIFICATION_LIVE_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("QUALIFICATION_LIVE_ACK_ACCOUNT_TERMS", "1")
    # Explicit test seam: target_db has fixture catalog evidence, never live proof.
    monkeypatch.setattr(service, "verified_evidence", lambda *args, **kwargs: None)
    return db


def live_verdict(db):
    with upstream() as server:
        result = complete(db, mode="live", transport_factory=lambda: LoopbackTransport(server.url))
        assert server.count == 2
    assert result["verdict"]["decision"] == "eligible"
    return result


def test_http_enqueue_is_durable_auth_and_fixture_evidence_cannot_publish(target_db, monkeypatch):
    db, _ = target_db
    monkeypatch.delenv("NVIDIA_API_KEY")  # Offline fixture does not need provider secrets.
    with TestClient(create_app()) as client:
        for path in ("/admin/qualifications/missing", "/admin/pinned-models/ag/missing", "/admin/route-snapshots/missing"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
            assert client.get(path, headers=AUTH).status_code == 404
        for path in ("/admin/qualifications", "/admin/pinned-models", "/admin/pinned-models/ag/missing/rollback"):
            assert client.post(path, json={}).status_code == 401
        assert client.post("/admin/qualifications", headers=AUTH, json={"deployment_id": "missing"}).status_code == 404
        assert client.post("/admin/qualifications", headers=AUTH,
            json={"deployment_id": "test-deployment", "max_probes": 3}).status_code == 422
        queued = client.post("/admin/qualifications", headers=AUTH, json={"deployment_id": "test-deployment"})
        assert queued.status_code == 202
        run_id = queued.json()["run_id"]
        before = client.get(f"/admin/qualifications/{run_id}", headers=AUTH).json()
        assert before["status"] == "queued" and before["attempt_count"] == 0 and before["verdict"] is None
    # Restart API, then execute through the actual worker entry point.
    process = subprocess.Popen([sys.executable, "-m", "agentic_gateway.worker"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        until = time.monotonic() + 12
        while time.monotonic() < until:
            result = service.detail(db, run_id)
            if result["status"] == "completed":
                break
            time.sleep(0.05)
        assert result["status"] == "completed"
    finally:
        process.terminate()
        logs, _ = process.communicate(timeout=5)
    assert process.returncode == 0 and '"event": "worker_stopped"' in logs
    with TestClient(create_app()) as client:
        result = client.get(f"/admin/qualifications/{run_id}", headers=AUTH).json()
        assert result["mode"] == "fixture" and result["verdict"]["decision"] == "eligible"
        assert result["attempt_count"] == result["client_send_count"] == 2
        assert all(row["result"] == "pass" and all(row["assertions"].values()) for row in result["observations"])
        assert result["requested_by"].startswith("operator:sha256:")
        rejected = client.post("/admin/pinned-models", headers=AUTH,
            json={"model_id": MODEL, "verdict_id": result["verdict"]["id"], "expected_route_version": 0})
        assert rejected.status_code == 409 and rejected.json()["detail"] == "live_verdict_required"
        assert "test-only-operator" not in json.dumps(result)


@pytest.mark.parametrize("reason", ["credential_missing", "live_budget_not_authorized", "deployment_disabled", "invalid_target"])
def test_preflight_blocks_with_zero_attempts_and_zero_requests(simulated_live, monkeypatch, reason):
    db = simulated_live
    if reason == "credential_missing":
        monkeypatch.delenv("NVIDIA_API_KEY")
    elif reason == "live_budget_not_authorized":
        monkeypatch.setenv("QUALIFICATION_LIVE_MAX_PROBES", "0")
    elif reason == "deployment_disabled":
        set_enabled(db, "test-deployment", False)
    else:
        from agentic_gateway.providers.inference.deployments import versions
        with db.begin() as c:
            c.execute(update(versions).values(endpoint="https://untrusted.invalid/"))
    with upstream() as server:
        result = complete(db, mode="live", transport_factory=lambda: LoopbackTransport(server.url))
        assert server.count == 0
    assert result["status"] == "blocked" and result["attempt_count"] == result["client_send_count"] == 0
    assert result["verdict"]["decision"] == "inconclusive" and result["reason_codes"] == [reason]


@pytest.mark.parametrize("scenario,code", [("timeout", "timeout"), ("429", "rate_limited")])
def test_inconclusive_real_tls_count_usage_and_total_deadline(simulated_live, scenario, code):
    db = simulated_live
    # Configure a bounded test profile, no real-time TTL wait.
    with db.begin() as c:
        spec = dict(c.scalar(select(policy.c.spec)))
        spec.update(deadline_seconds=1, probe_deadline_seconds=0.3)
        c.execute(update(policy).values(spec=spec))
    with upstream(scenario) as server:
        started = time.monotonic()
        result = complete(db, mode="live", transport_factory=lambda: LoopbackTransport(server.url))
        assert server.count == result["client_send_count"] == 1
        assert time.monotonic() - started < 1.3
    assert result["status"] == "completed" and result["verdict"]["decision"] == "inconclusive"
    row, = result["observations"]
    assert row["reason_code"] == code and row["usage"]["status"] == "unknown"
    if scenario == "timeout":
        assert row["report"]["provider_outcome"] == "unknown"


@pytest.mark.parametrize("scenario,reason", [("empty", "text_nonempty"), ("invalid", "invalid_response")])
def test_mandatory_failures_are_ineligible_with_case_reason(target_db, scenario, reason):
    db, _ = target_db
    result = complete(db, scenario=scenario)
    assert result["verdict"]["decision"] == "ineligible"
    assert result["reason_codes"] == ["short-text:" + reason]
    assert result["observations"][0]["result"] == "fail"


def test_no_usage_remains_unknown_and_text_has_no_exact_match(target_db):
    db, _ = target_db
    result = complete(db, scenario="no_usage")
    assert result["verdict"]["decision"] == "eligible"
    assert all(row["usage"]["status"] == "unknown" and row["result"] == "pass" for row in result["observations"])


def test_budget_is_shared_across_cases_and_stops_before_second_send(target_db):
    db, _ = target_db
    with upstream() as server:
        result = complete(db, max_probes=1, transport_factory=lambda: LoopbackTransport(server.url))
        assert server.count == result["attempt_count"] == 1
    assert result["verdict"]["decision"] == "inconclusive"
    assert result["reason_codes"] == ["probe_budget_exhausted"]


def test_generation_changes_while_response_waits_keep_history_but_reject_publish(simulated_live, target_db):
    db, offering_id = target_db
    waiting, release = threading.Event(), threading.Event()

    class DelayedTransport(LoopbackTransport):
        async def handle_async_request(self, request):
            assert db.pool.checkedout() == 0
            response = await super().handle_async_request(request)
            waiting.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            return response

    job = enqueue(db, "live")
    with upstream() as server, ThreadPoolExecutor(max_workers=1) as pool:
        claim = claim_job(db)
        future = pool.submit(service.process, db, claim, _transport_factory=lambda: DelayedTransport(server.url))
        try:
            assert waiting.wait(5) and server.count == 1
            assert configure(db, "test-deployment", offering_id, "second-account", _fixture=True)["generation"] == 2
        finally:
            release.set()
        future.result(timeout=10)
        assert server.count == 2
    result = service.detail(db, job["run_id"])
    assert result["generation"] == 1 and result["account_id"] == "test-account"
    assert result["verdict"]["decision"] == "eligible"
    assert {row["report"]["generation"] for row in result["observations"]} == {1}
    with pytest.raises(routes.RouteError, match="deployment_generation_changed"):
        routes.publish(db, MODEL, result["verdict"]["id"], 0, "operator:test")


def test_lease_takeover_preserves_unknown_and_fences_late_worker(simulated_live):
    db = simulated_live
    waiting, release = threading.Event(), threading.Event()

    class DelayedTransport(LoopbackTransport):
        async def handle_async_request(self, request):
            response = await super().handle_async_request(request)
            waiting.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            return response

    job = enqueue(db, "live")
    claim_a = claim_job(db)
    with upstream() as server, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.process, db, claim_a, _transport_factory=lambda: DelayedTransport(server.url))
        try:
            assert waiting.wait(5)
            with db.begin() as c:
                c.execute(update(jobs).where(jobs.c.id == job["job_id"]).values(lease_until=func.clock_timestamp()-timedelta(seconds=1)))
            claim_b = claim_job(db)
            assert claim_b.token == claim_a.token + 1
            service.process(db, claim_b)
            result_b = service.detail(db, job["run_id"])
            assert result_b["verdict"]["decision"] == "inconclusive" and result_b["attempt_count"] == 1
            assert result_b["client_send_count"] is None
        finally:
            release.set()
        future.result(timeout=10)
        assert server.count == 1
    assert service.detail(db, job["run_id"]) == result_b
    assert result_b["observations"][0]["report"]["provider_outcome"] == "unknown"
    with pytest.raises(LostLease):
        service.finish(db, claim_a, "ineligible", ["stale_failure"])
    with db.connect() as c:
        job_row = c.execute(select(jobs).where(jobs.c.id == job["job_id"])).mappings().one()
        assert job_row["status"] == "succeeded" and job_row["fencing_token"] == claim_b.token


@pytest.mark.parametrize("mutation,reason", [("expiry", "verdict_expired"), ("revoke", "deployment_or_account_revoked"),
    ("account", "deployment_or_account_revoked"), ("policy", "qualification_policy_changed")])
def test_expiry_revocation_policy_block_publish_read_and_rollback(simulated_live, mutation, reason):
    db = simulated_live
    run = live_verdict(db)
    published = routes.publish(db, MODEL, run["verdict"]["id"], 0, "operator:one")
    if mutation == "revoke":
        set_enabled(db, "test-deployment", False)
        set_enabled(db, "test-deployment", True)  # Re-enable must not revive old approval.
    else:
        with db.begin() as c:
            if mutation == "expiry":
                c.execute(update(verdicts).values(expires_at=func.clock_timestamp()-timedelta(seconds=1)))
            elif mutation == "account":
                c.execute(update(accounts).values(enabled=False))
            else:
                c.execute(update(policy).values(suite_revision="text-smoke-v2"))
    for operation in (lambda: routes.publish(db, MODEL, run["verdict"]["id"], 1, "operator:two"),
        lambda: routes.read_route(db, MODEL),
        lambda: routes.publish(db, MODEL, None, 1, "operator:two", rollback_of=published["id"])):
        with pytest.raises(routes.RouteError, match=reason):
            operation()
    assert routes.snapshot_detail(db, published["id"])["usable"] is False
    with db.connect() as c:
        assert c.scalar(select(func.count()).select_from(snapshots)) == 1
        assert c.scalar(select(pins.c.route_version)) == 1


@pytest.mark.parametrize("initial_version", [0, 1])
def test_two_real_transactions_cas_first_publish_and_update(simulated_live, initial_version):
    db = simulated_live
    result = live_verdict(db)
    if initial_version:
        routes.publish(db, MODEL, result["verdict"]["id"], 0, "operator:first")
    barrier = threading.Barrier(2)

    def publish(actor):
        barrier.wait(timeout=5)
        try:
            return routes.publish(db, MODEL, result["verdict"]["id"], initial_version, actor)
        except routes.RouteError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish, actor) for actor in ("operator:a", "operator:b")]
        outcomes = [future.result(timeout=10) for future in futures]
    assert outcomes.count("route_version_conflict") == 1
    winner, = [row for row in outcomes if isinstance(row, dict)]
    assert winner["route_version"] == initial_version + 1
    current = routes.read_route(db, MODEL)
    assert current["id"] == winner["id"] and current["published_by"] in {"operator:a", "operator:b"}
    with db.connect() as c:
        assert list(c.execute(select(snapshots.c.route_version).order_by(snapshots.c.route_version)).scalars()) == list(range(1, initial_version+2))


def test_http_publish_conflict_rollback_and_complete_trace(simulated_live):
    db = simulated_live
    result = live_verdict(db)
    with TestClient(create_app()) as client:
        body = {"model_id": MODEL, "verdict_id": result["verdict"]["id"], "expected_route_version": 0}
        published = client.post("/admin/pinned-models", json=body, headers=AUTH)
        assert published.status_code == 201
        assert client.post("/admin/pinned-models", json=body, headers=AUTH).status_code == 409
        route = client.get(f"/admin/pinned-models/{MODEL}", headers=AUTH).json()
        snapshot = client.get(f"/admin/route-snapshots/{route['id']}", headers=AUTH).json()
        run = client.get(f"/admin/qualifications/{snapshot['run_id']}", headers=AUTH).json()
        assert route["verdict_id"] == snapshot["verdict_id"] == run["verdict"]["id"]
        assert route["expires_at"] == run["verdict"]["expires_at"]
        assert route["published_by"].startswith("operator:sha256:")
        assert route["generation"] == run["generation"] == 1 and len(run["observations"]) == 2
        assert all(row["attempt_id"] == row["report"]["attempt_id"] for row in run["observations"])
        rollback = client.post(f"/admin/pinned-models/{MODEL}/rollback", headers=AUTH,
            json={"snapshot_id": route["id"], "expected_route_version": 1})
        assert rollback.status_code == 201 and rollback.json()["route_version"] == 2
        assert rollback.json()["rollback_of"] == route["id"]
        assert "fixture-secret-token" not in json.dumps(run)


def test_upgrade_existing_step4_database_preserves_deployment_and_updates_health(target_db):
    db, _ = target_db
    config = Config("/app/alembic.ini")
    command.downgrade(config, "0005_deployments")
    assert not is_ready(db, migration_heads())
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with db.connect() as c:
        assert c.scalar(select(deployments.c.generation).where(deployments.c.id == "test-deployment")) == 1
    with TestClient(create_app()) as client:
        assert client.get("/health/live").status_code == client.get("/health/ready").status_code == 200
