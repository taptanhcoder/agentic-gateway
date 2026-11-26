from concurrent.futures import ThreadPoolExecutor
import json
import os
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update

from agentic_gateway.api import create_app
from agentic_gateway.catalog.fixture_reader import load_fixture
from agentic_gateway.catalog.registry import enqueue, get_job
from agentic_gateway.catalog.schema import jobs, observations, offerings, runs, sources, staging
from agentic_gateway.catalog.sync import (LostLease, SyncError, claim_job, fail_job,
    process_job, publish_snapshot, release_job, renew_lease, stage_page)

AUTH = {"Authorization": "Bearer test-only-operator"}


def complete(db, revision):
    job_id = enqueue(db, "fixture/catalog-a", revision)
    process_job(db, claim_job(db))
    assert get_job(db, job_id)["status"] == "succeeded"
    return job_id


def stage_all(db, claim):
    pages = load_fixture(claim.fixture_name, claim.revision)["pages"]
    for number, page in enumerate(pages, 1):
        stage_page(db, claim, number, len(pages), page)


def current_state(db):
    with db.connect() as connection:
        pointer = connection.scalar(select(sources.c.current_snapshot_id).where(sources.c.id == "fixture/catalog-a"))
        counts = tuple(connection.scalar(select(func.count()).select_from(table)) for table in (offerings, observations))
    with TestClient(create_app()) as client:
        catalog = client.get("/admin/offerings", headers=AUTH).json()
    return pointer, counts, catalog


def expire(db, claim):
    with db.begin() as connection:
        connection.execute(update(jobs).where(jobs.c.id == claim.job_id)
            .values(lease_until=func.clock_timestamp() - text("interval '1 second'")))


def test_last_page_timeout_preserves_populated_catalog_after_changed_first_page(db):
    complete(db, "r2")
    before = current_state(db)
    assert len(before[2]["data"]) == 3 and before[0] is not None
    job_id = enqueue(db, "fixture/catalog-a", "last-page-timeout")
    process_job(db, claim_job(db))
    job = get_job(db, job_id)
    assert job["status"] == "failed" and job["error_code"] == "fixture_page_timeout"
    assert job["pages_read"] == 1 and job["total_pages"] == 2
    # Demonstrate page 1 really introduced different data, not an empty/no-op test.
    with db.connect() as connection:
        staged = connection.execute(select(staging.c.record).where(staging.c.run_id == job["sync_run_id"])).scalars().all()
        assert [record["upstream_model"] for record in staged] == ["delta-unpublished"]
    assert current_state(db) == before  # pointer, entity counts, full memberships/evidence


def test_two_claimers_publish_once(db):
    job_id = enqueue(db, "fixture/catalog-a", "r1")
    barrier = threading.Barrier(2)

    def compete():
        barrier.wait()
        return claim_job(db)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: compete(), range(2)))
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    process_job(db, winners[0])
    with db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(runs)) == 1
    assert get_job(db, job_id)["status"] == "succeeded"


@pytest.mark.parametrize("new_owner_result", ["succeeded", "failed"])
def test_reclaimed_job_fences_late_success_failure_and_heartbeat(db, new_owner_result):
    complete(db, "r1")
    job_id = enqueue(db, "fixture/catalog-a", "r2")
    worker_a = claim_job(db)
    stage_all(db, worker_a)
    expire(db, worker_a)
    # Expiry alone revokes A, even before another claim.
    with pytest.raises(LostLease):
        renew_lease(db, worker_a)
    worker_b = claim_job(db)
    assert worker_b.job_id == worker_a.job_id
    assert worker_b.token == worker_a.token + 1
    if new_owner_result == "succeeded":
        stage_all(db, worker_b)
        assert publish_snapshot(db, worker_b)
    else:
        assert fail_job(db, worker_b, "new_owner_failure")
    state, job = current_state(db), get_job(db, job_id)
    with pytest.raises(LostLease):
        publish_snapshot(db, worker_a)
    with pytest.raises(LostLease):
        stage_page(db, worker_a, 1, 2, [])
    assert fail_job(db, worker_a, "late_old_failure") is False
    release_job(db, worker_a)
    assert get_job(db, job_id) == job
    assert current_state(db) == state
    assert job["status"] == new_owner_result
    with db.connect() as connection:
        assert connection.scalar(select(runs.c.status).where(runs.c.id == worker_a.run_id)) == "abandoned"


def test_older_source_generation_cannot_replace_new_snapshot(db):
    old_job = enqueue(db, "fixture/catalog-a", "r1")
    worker_a = claim_job(db)
    stage_all(db, worker_a)
    new_job = enqueue(db, "fixture/catalog-a", "r2")
    worker_b = claim_job(db)
    stage_all(db, worker_b)
    assert publish_snapshot(db, worker_b)
    state = current_state(db)
    assert publish_snapshot(db, worker_a) is False
    assert get_job(db, old_job)["status"] == "superseded"
    assert get_job(db, new_job)["status"] == "succeeded"
    assert current_state(db) == state


def test_incomplete_or_conflicting_revision_rolls_back_publication(db):
    complete(db, "r1")
    state = current_state(db)
    enqueue(db, "fixture/catalog-a", "r1")
    claim = claim_job(db)
    altered = load_fixture("catalog-a", "r1")["pages"]
    altered[0][0]["upstream_model"] = "must-rollback"
    stage_page(db, claim, 1, 2, altered[0])
    with pytest.raises(SyncError, match="incomplete_snapshot"):
        publish_snapshot(db, claim)
    stage_page(db, claim, 2, 2, altered[1])
    with pytest.raises(SyncError, match="revision_content_conflict"):
        publish_snapshot(db, claim)
    assert fail_job(db, claim, "revision_content_conflict")
    assert current_state(db) == state


def test_interrupted_run_requeues_and_next_worker_starts_new_staging(db):
    job_id = enqueue(db, "fixture/catalog-a", "r1")
    first = claim_job(db)
    stage_page(db, first, 1, 2, load_fixture("catalog-a", "r1")["pages"][0])
    stopped = threading.Event()
    stopped.set()
    process_job(db, first, stopped)
    assert get_job(db, job_id)["status"] == "queued"
    second = claim_job(db)
    assert second.run_id != first.run_id
    process_job(db, second)
    assert get_job(db, job_id)["status"] == "succeeded"
    with db.connect() as connection:
        assert connection.scalar(select(runs.c.status).where(runs.c.id == first.run_id)) == "interrupted"


def test_heartbeat_keeps_lease_during_slow_page_reads(db, monkeypatch):
    import agentic_gateway.catalog.sync as sync_module
    original = sync_module.stage_page

    def delayed_page(*args, **kwargs):
        # Simulate local page parsing outside any transaction, longer than lease.
        time.sleep(1.4)
        return original(*args, **kwargs)

    monkeypatch.setattr(sync_module, "stage_page", delayed_page)
    job_id = enqueue(db, "fixture/catalog-a", "r1")
    claim = claim_job(db, lease_seconds=1)
    process_job(db, claim, lease_seconds=1)
    assert get_job(db, job_id)["status"] == "succeeded"


def stop_process(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def worker_process():
    process = subprocess.Popen([sys.executable, "-m", "agentic_gateway.worker"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert json.loads(process.stdout.readline())["event"] == "worker_started"
    return process


def wait_job(db, job_id):
    until = time.monotonic() + 15
    while time.monotonic() < until:
        job = get_job(db, job_id)
        if job["status"] in {"succeeded", "failed", "superseded"}:
            return job
        time.sleep(0.05)
    raise AssertionError("Worker did not finish job")


def test_two_real_worker_processes_publish_once_and_stop_cleanly(db):
    first = second = None
    try:
        first, second = worker_process(), worker_process()
        job_id = enqueue(db, "fixture/catalog-a", "r1")
        assert wait_job(db, job_id)["status"] == "succeeded"
    finally:
        stop_process(first)
        stop_process(second)
    logs = first.stdout.read() + second.stdout.read()
    assert logs.count('"event": "sync_succeeded"') == 1
    assert logs.count('"event": "worker_stopped"') == 2
    assert first.returncode == second.returncode == 0
    with db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(runs)) == 1


def test_http_enqueue_survives_api_restart_and_stopped_worker(db):
    api_process = worker = None
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"

    def start_api():
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "agentic_gateway.api:create_app",
            "--factory", "--host", "127.0.0.1", "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            until = time.monotonic() + 10
            while time.monotonic() < until:
                try:
                    if httpx.get(url + "/health/live", timeout=1).status_code == 200:
                        return process
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
            raise AssertionError("API startup timed out")
        except BaseException:
            stop_process(process)
            raise

    try:
        api_process = start_api()
        worker = worker_process()
        stop_process(worker)
        assert worker.returncode == 0
        response = httpx.post(url + "/admin/sources/fixture/catalog-a/sync", json={"revision":"r1"}, headers=AUTH)
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert httpx.get(url + f"/admin/jobs/{job_id}", headers=AUTH).json()["status"] == "queued"
        stop_process(api_process)
        api_process = start_api()
        assert httpx.get(url + f"/admin/jobs/{job_id}", headers=AUTH).json()["status"] == "queued"
        worker = worker_process()
        assert wait_job(db, job_id)["status"] == "succeeded"
        assert httpx.get(url + f"/admin/jobs/{job_id}", headers=AUTH).json()["pages_read"] == 2
    finally:
        stop_process(worker)
        stop_process(api_process)
