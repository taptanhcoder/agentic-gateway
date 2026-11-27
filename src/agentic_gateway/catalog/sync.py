"""Short claim/staging transactions and a single fenced publish transaction."""

import json
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from datetime import datetime
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from .fixture_reader import load_fixture
from .identity import PARSER_VERSION, digest
from .evidence_records import normalize_record, stable_record
from agentic_gateway.providers.catalog.connectors import read_catalog
from agentic_gateway.providers.catalog.transport import CatalogError
from .schema import documents, jobs, memberships, observations, offerings, runs, snapshots, sources, staging


class LostLease(Exception):
    pass


class SyncError(Exception):
    pass


@dataclass(frozen=True)
class Claim:
    job_id: str
    source_id: str
    run_id: str
    token: int
    revision: str
    fixture_name: str | None
    connector: str = "fixture"
    locator: str | None = None
    config: dict = field(default_factory=dict)


def log(event, claim=None, **fields):
    if claim:
        fields.update(job_id=claim.job_id, source_id=claim.source_id,
                      sync_run_id=claim.run_id, fencing_token=claim.token)
    print(json.dumps(dict(event=event, **fields)), flush=True)


def claim_job(engine, lease_seconds=30):
    # One short transaction. SKIP LOCKED prevents workers claiming the same row.
    with engine.begin() as connection:
        job = connection.execute(select(jobs).where(or_(jobs.c.status == "queued",
            and_(jobs.c.status == "running", jobs.c.lease_until <= func.clock_timestamp())))
            .order_by(jobs.c.created_at, jobs.c.id).limit(1)
            .with_for_update(skip_locked=True)).mappings().one_or_none()
        if job is None:
            return None
        if job["kind"] == "qualification":
            from agentic_gateway.qualification.service import claim_locked
            return claim_locked(connection, job, lease_seconds)
        if job["sync_run_id"]:
            connection.execute(update(runs).where(runs.c.id == job["sync_run_id"], runs.c.status == "running")
                .values(status="abandoned", error_code="lease_expired", finished_at=func.clock_timestamp()))
        token, run_id = job["fencing_token"] + 1, str(uuid4())
        connection.execute(insert(runs).values(id=run_id, job_id=job["id"], fencing_token=token, status="running"))
        connection.execute(update(jobs).where(jobs.c.id == job["id"]).values(
            status="running", fencing_token=token, sync_run_id=run_id,
            lease_until=func.clock_timestamp() + timedelta(seconds=lease_seconds),
            pages_read=0, error_code=None, finished_at=None))
        source = connection.execute(select(sources).where(sources.c.id == job["source_id"])).mappings().one()
        claim = Claim(job["id"], job["source_id"], run_id, token, job["revision"],
                      source["fixture_name"], source["connector"], source["locator"], source["config"])
    log("job_claimed", claim)
    return claim


def owned(connection, claim):
    # All writers lock job first. Check server wall clock AFTER acquiring locks.
    job = connection.execute(select(jobs).where(jobs.c.id == claim.job_id,
        jobs.c.fencing_token == claim.token, jobs.c.sync_run_id == claim.run_id,
        jobs.c.status == "running").with_for_update()).mappings().one_or_none()
    if job is None or not connection.scalar(select(jobs.c.lease_until > func.clock_timestamp())
        .where(jobs.c.id == claim.job_id)):
        raise LostLease()
    return job


def renew_lease(engine, claim, lease_seconds=30):
    with engine.begin() as connection:
        owned(connection, claim)
        connection.execute(update(jobs).where(jobs.c.id == claim.job_id)
            .values(lease_until=func.clock_timestamp() + timedelta(seconds=lease_seconds)))


def stage_page(engine, claim, page_number, total_pages, records):
    normalized = [normalize_record(record) for record in records]
    with engine.begin() as connection:
        job = owned(connection, claim)
        if page_number != job["pages_read"] + 1 or not 1 <= page_number <= total_pages:
            raise SyncError("invalid_page_order")
        for index, record in enumerate(normalized, 1):
            connection.execute(insert(staging).values(run_id=claim.run_id,
                position=f"{page_number}:{index}", record=record))
        connection.execute(update(jobs).where(jobs.c.id == claim.job_id).values(pages_read=page_number))
        connection.execute(update(runs).where(runs.c.id == claim.run_id)
            .values(pages_read=page_number, total_pages=total_pages))
    log("page_staged", claim, page=page_number, total_pages=total_pages)


def finish_in_transaction(connection, claim, status, error_code=None, diff=None):
    # Caller MUST own and lock the fenced job in this transaction.
    connection.execute(update(runs).where(runs.c.id == claim.run_id).values(
        status=status, error_code=error_code, diff=diff, finished_at=func.clock_timestamp()))
    connection.execute(update(jobs).where(jobs.c.id == claim.job_id).values(
        status=status, error_code=error_code, lease_until=None, finished_at=func.clock_timestamp()))


def snapshot_offerings(connection, snapshot_id):
    return set(connection.execute(select(observations.c.offering_id)
        .select_from(memberships.join(observations, memberships.c.observation_id == observations.c.id))
        .where(memberships.c.snapshot_id == snapshot_id, observations.c.offering_id.is_not(None))).scalars()) if snapshot_id else set()


def publish_snapshot(engine, claim):
    with engine.begin() as connection:
        job = owned(connection, claim)
        source = connection.execute(select(sources).where(sources.c.id == claim.source_id)
            .with_for_update()).mappings().one()
        if source["generation"] != job["generation"] or not source["enabled"]:
            finish_in_transaction(connection, claim, "superseded", "source_superseded")
            return False
        run = connection.execute(select(runs).where(runs.c.id == claim.run_id)).mappings().one()
        if run["total_pages"] is None or run["pages_read"] != run["total_pages"]:
            raise SyncError("incomplete_snapshot")
        old_ids = snapshot_offerings(connection, source["current_snapshot_id"])
        old_evidence = set(connection.execute(select(memberships.c.observation_id)
            .where(memberships.c.snapshot_id == source["current_snapshot_id"])).scalars())
        new_ids, evidence = set(), []
        records = connection.execute(select(staging).where(staging.c.run_id == claim.run_id)
            .order_by(staging.c.position)).mappings().all()
        for row in records:
            record = row["record"]
            enriched = bool(record.get("evidence_record"))
            canonical = record.get("offering") if enriched else record
            provenance = record["provenance"] if enriched else {}
            fields = record["claims"] if enriched else {}
            offering_id = digest(canonical) if canonical is not None else None
            if canonical is not None:
                connection.execute(insert(offerings).values(id=offering_id, **canonical)
                    .on_conflict_do_nothing(index_elements=[offerings.c.id]))
                new_ids.add(offering_id)
            revision = provenance.get("revision", claim.revision)
            position = provenance.get("position", row["position"])
            parser_version = provenance.get("parser_version", PARSER_VERSION)
            content_hash = digest(stable_record(record))
            observation_id = digest([claim.source_id, revision, position, parser_version])
            connection.execute(insert(observations).values(id=observation_id, source_id=claim.source_id,
                revision=revision, position=position, parser_version=parser_version,
                offering_id=offering_id, content_hash=content_hash, record=record,
                fields=fields, provenance=provenance)
                .on_conflict_do_nothing(index_elements=[observations.c.id]))
            previous_hash = connection.scalar(select(observations.c.content_hash).where(observations.c.id == observation_id))
            if previous_hash != content_hash:
                raise SyncError("revision_content_conflict")
            evidence.append(observation_id)
        snapshot_id = str(uuid4())
        connection.execute(insert(snapshots).values(id=snapshot_id, source_id=claim.source_id,
            run_id=claim.run_id, revision=(run["fetch_metadata"][-1].get("content_hash", claim.revision)
                if run["fetch_metadata"] else claim.revision)))
        for observation_id in evidence:
            connection.execute(insert(memberships).values(snapshot_id=snapshot_id, observation_id=observation_id))
        # Recheck after all locks/work, immediately before swapping the pointer.
        owned(connection, claim)
        connection.execute(update(sources).where(sources.c.id == claim.source_id)
            .values(current_snapshot_id=snapshot_id))
        diff = {"added": sorted(new_ids - old_ids), "removed": sorted(old_ids - new_ids),
                "unchanged": sorted(old_ids & new_ids),
                "observations_added": sorted(set(evidence) - old_evidence),
                "observations_removed": sorted(old_evidence - set(evidence))}
        finish_in_transaction(connection, claim, "succeeded", diff=diff)
    log("sync_succeeded", claim, snapshot_id=snapshot_id, diff=diff)
    return True


def fail_job(engine, claim, error_code, blocked=False):
    try:
        with engine.begin() as connection:
            owned(connection, claim)
            finish_in_transaction(connection, claim, "blocked" if blocked else "failed", error_code)
        log("sync_blocked" if blocked else "sync_failed", claim, error_code=error_code, simulated=error_code == "fixture_page_timeout")
        return True
    except LostLease:
        log("stale_worker_ignored", claim)
        return False


def release_job(engine, claim):
    try:
        with engine.begin() as connection:
            owned(connection, claim)
            connection.execute(update(runs).where(runs.c.id == claim.run_id)
                .values(status="interrupted", error_code="worker_stopped", finished_at=func.clock_timestamp()))
            connection.execute(update(jobs).where(jobs.c.id == claim.job_id)
                .values(status="queued", lease_until=None))
        log("job_requeued", claim)
    except LostLease:
        log("stale_worker_ignored", claim)


def process_job(engine, claim, stopped=None, lease_seconds=30):
    stopped = stopped if stopped is not None else threading.Event()
    heartbeat_stop = threading.Event()
    lease_lost = threading.Event()

    def heartbeat():
        while not heartbeat_stop.wait(lease_seconds / 3):
            try:
                renew_lease(engine, claim, lease_seconds)
            except (LostLease, SQLAlchemyError):
                lease_lost.set()
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        if claim.connector == "fixture":
            spec = load_fixture(claim.fixture_name, claim.revision)
        elif claim.connector == "github_fixture":
            from .github_fixture import read_fixture
            spec = {"pages": [read_fixture(claim)]}
        else:
            def report(metadata, body=None):
                with engine.begin() as connection:
                    owned(connection, claim)
                    existing = connection.scalar(select(runs.c.fetch_metadata).where(runs.c.id == claim.run_id))
                    connection.execute(update(runs).where(runs.c.id == claim.run_id).values(fetch_metadata=existing + [metadata]))
                    if body is not None:
                        connection.execute(insert(documents).values(run_id=claim.run_id, locator=metadata["locator"],
                            content_hash=metadata["content_hash"], body=body, mode=metadata["mode"],
                            read_at=datetime.fromisoformat(metadata["read_at"])))
            spec = {"pages": [read_catalog(claim, report)]}
        total = len(spec["pages"])
        for page_number, records in enumerate(spec["pages"], 1):
            if stopped.is_set():
                release_job(engine, claim)
                return
            if lease_lost.is_set():
                raise LostLease()
            # All reads are local. This simulates timeout AFTER a different page 1.
            if spec.get("fail_page") == page_number:
                if stopped.wait(0.05):
                    release_job(engine, claim)
                    return
                raise SyncError("fixture_page_timeout")
            stage_page(engine, claim, page_number, total, records)
        if stopped.is_set():
            release_job(engine, claim)
        elif not publish_snapshot(engine, claim):
            log("sync_superseded", claim)
    except LostLease:
        log("stale_worker_ignored", claim)
    except CatalogError as error:
        fail_job(engine, claim, str(error), blocked=error.blocked)
    except SyncError as error:
        fail_job(engine, claim, str(error))
    except ValueError:
        fail_job(engine, claim, "invalid_fixture")
    except SQLAlchemyError:
        fail_job(engine, claim, "storage_error")
    finally:
        heartbeat_stop.set()
        thread.join(timeout=6)
