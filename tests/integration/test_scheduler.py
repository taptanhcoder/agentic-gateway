from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from agentic_gateway import scheduler
from agentic_gateway.api import create_app
from agentic_gateway.catalog.registry import enqueue, get_job
from agentic_gateway.catalog.schema import jobs, snapshots, sources
from agentic_gateway.catalog.sync import claim_job, process_job
from agentic_gateway.providers.catalog.seeds import seed_providers

NOW = datetime(2026, 11, 26, 8, tzinfo=timezone.utc)
AUTH = {"Authorization":"Bearer test-only-operator"}


def schedule(db, source="fixture/catalog-a", *, due=NOW, interval=60, revision="r1", **values):
    with db.begin() as connection:
        connection.execute(update(sources).where(sources.c.id == source).values(
            sync_interval_seconds=interval, next_sync_at=due, sync_revision=revision, **values))


def job_count(db):
    with db.connect() as connection:
        return connection.scalar(select(func.count()).select_from(jobs))


def test_two_schedulers_claim_same_due_period_once(db):
    schedule(db, due=NOW - timedelta(days=10))
    barrier = threading.Barrier(2)

    def compete():
        barrier.wait()
        return scheduler.tick(db, NOW)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _:compete(), range(2)))
    assert sum(len(result) for result in results) == 1
    assert job_count(db) == 1
    with db.connect() as connection:
        job = connection.execute(select(jobs)).mappings().one()
        assert job["trigger_kind"] == "scheduled" and job["scheduled_for"] == NOW - timedelta(days=10)
        assert connection.scalar(select(sources.c.next_sync_at)) == NOW + timedelta(seconds=60)


def test_crash_between_enqueue_and_cursor_update_rolls_back_both(db, monkeypatch):
    schedule(db)
    original = scheduler.enqueue_locked

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected_process_failure_before_commit")

    monkeypatch.setattr(scheduler, "enqueue_locked", crash)
    with pytest.raises(RuntimeError):
        scheduler.tick(db, NOW)
    assert job_count(db) == 0
    with db.connect() as connection:
        source = connection.execute(select(sources)).mappings().one()
        assert source["generation"] == 0 and source["next_sync_at"] == NOW
    monkeypatch.setattr(scheduler, "enqueue_locked", original)
    assert len(scheduler.tick(db, NOW)) == 1
    assert job_count(db) == 1


def test_real_scheduler_processes_and_restart_do_not_repeat_period(db):
    schedule(db)
    command = [sys.executable, "-m", "agentic_gateway.scheduler", "--once", "--now", NOW.isoformat()]
    first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        logs_a, _ = first.communicate(timeout=15)
        logs_b, _ = second.communicate(timeout=15)
        assert first.returncode == second.returncode == 0, logs_a + logs_b
    finally:
        for process in (first, second):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    assert (logs_a + logs_b).count('"action": "enqueued"') == 1
    restarted = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert restarted.returncode == 0 and job_count(db) == 1


def test_manual_job_at_due_time_defers_scheduler_without_losing_next_period(db):
    schedule(db)
    manual = enqueue(db, "fixture/catalog-a", "r1")
    decision, = scheduler.tick(db, NOW)
    assert decision["action"] == "deferred_busy" and decision["job_id"] == manual
    assert job_count(db) == 1
    process_job(db, claim_job(db))
    assert get_job(db, manual)["status"] == "succeeded"
    assert scheduler.tick(db, NOW) == []
    assert len(scheduler.tick(db, NOW + timedelta(seconds=60))) == 1
    assert job_count(db) == 2
    with db.connect() as connection:
        assert connection.scalar(select(sources.c.next_sync_at)) == NOW + timedelta(seconds=120)


def test_manual_after_scheduled_enqueue_keeps_fencing_and_publishes_once(db):
    schedule(db)
    scheduled, = scheduler.tick(db, NOW)
    manual = enqueue(db, "fixture/catalog-a", "r1")
    process_job(db, claim_job(db))
    process_job(db, claim_job(db))
    assert get_job(db, scheduled["job_id"])["status"] == "superseded"
    assert get_job(db, manual)["status"] == "succeeded"
    with db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(snapshots)) == 1
        assert connection.scalar(select(sources.c.next_sync_at)) == NOW + timedelta(seconds=60)


def test_disabled_missing_and_blocked_sources_are_visible_but_not_scheduled(db, monkeypatch):
    schedule(db, enabled=False)
    with db.begin() as connection:
        connection.execute(insert(sources).values(id="missing", connector="not_implemented"))
        connection.execute(insert(sources).values(id="bad-config", connector="nvidia_hosted", locator=None))
    schedule(db, "missing")
    schedule(db, "bad-config")
    seed_providers(db)
    schedule(db, "official/siliconflow-cn", revision="latest")
    monkeypatch.delenv("SILICONFLOW_CN_CATALOG_API_KEY", raising=False)
    assert scheduler.tick(db, NOW) == [] and job_count(db) == 0
    with TestClient(create_app()) as client:
        rows = {row["id"]:row for row in client.get("/admin/sources", headers=AUTH).json()["data"]}
    assert rows["missing"]["connector"]["status"] == "missing"
    assert rows["bad-config"]["connector"]["reason_code"] == "connector_locator_mismatch"
    assert rows["official/siliconflow-cn"]["connector"]["reason_code"] == "catalog_credential_missing"
    assert rows["fixture/catalog-a"]["schedule"]["reason_code"] == "source_disabled"
    for key in ("missing", "bad-config", "official/siliconflow-cn", "fixture/catalog-a"):
        assert rows[key]["next_sync_at"] is None


def test_replayed_period_is_still_unique_after_completion(db):
    due = NOW - timedelta(days=100)
    schedule(db, due=due, interval=1)
    first, = scheduler.tick(db, NOW)
    process_job(db, claim_job(db))
    # Simulate restoring an old cursor/configuration. The durable job key wins.
    schedule(db, due=due, interval=1)
    replay, = scheduler.tick(db, NOW)
    assert replay["job_id"] == first["job_id"] and job_count(db) == 1
    with db.connect() as connection:
        assert connection.scalar(select(sources.c.generation)) == 1
        assert connection.scalar(select(sources.c.next_sync_at)) == NOW + timedelta(seconds=1)


def test_database_rejects_inconsistent_schedule_and_duplicate_period(db):
    with pytest.raises(IntegrityError):
        with db.begin() as connection:
            connection.execute(update(sources).values(sync_interval_seconds=None, next_sync_at=NOW))
    schedule(db)
    result, = scheduler.tick(db, NOW)
    with db.connect() as connection:
        original = dict(connection.execute(select(jobs).where(jobs.c.id == result["job_id"])).mappings().one())
    with pytest.raises(IntegrityError):
        with db.begin() as connection:
            connection.execute(insert(jobs).values(**dict(original, id="duplicate-period", generation=99)))


def test_scheduler_sigterm_exits_cleanly(db):
    process = subprocess.Popen([sys.executable, "-m", "agentic_gateway.scheduler"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert json.loads(process.stdout.readline())["event"] == "scheduler_started"
        process.terminate()
        logs, _ = process.communicate(timeout=10)
        assert process.returncode == 0 and '"event": "scheduler_stopped"' in logs
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
