"""PostgreSQL-serialized, coalescing scheduler. This process only enqueues."""
import argparse
import json
import os
import signal
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError

from .config import Settings
from .database import make_engine
from .catalog.availability import connector_status, default_revision
from .catalog.registry import enqueue_locked
from .catalog.schema import jobs, sources


def tick(engine, now=None):
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("scheduler_clock_must_be_aware")
    decisions = []
    # Sources are locked without ever locking a job row: compatible with the
    # worker's job->source publish locking order. No network calls in this txn.
    with engine.begin() as connection:
        clock = now.astimezone(timezone.utc) if now else connection.scalar(select(func.clock_timestamp()))
        due = connection.execute(select(sources).where(sources.c.enabled.is_(True),
            sources.c.next_sync_at <= clock, sources.c.sync_interval_seconds.is_not(None))
            .order_by(sources.c.id).with_for_update(skip_locked=True)).mappings().all()
        for source in due:
            availability = connector_status(source)
            if not availability["usable_for_schedule"]:
                continue
            active = connection.scalar(select(jobs.c.id).where(jobs.c.source_id == source["id"],
                jobs.c.status.in_(["queued", "running"])).limit(1))
            scheduled_for = source["next_sync_at"]
            if active:
                decision = {"source_id":source["id"], "action":"deferred_busy", "job_id":active}
            else:
                job_id = enqueue_locked(connection, source, source["sync_revision"] or default_revision(source),
                    trigger="scheduled", scheduled_for=scheduled_for)
                decision = {"source_id":source["id"], "action":"enqueued", "job_id":job_id}
            # One catch-up max. Missed slots are never replayed into a backlog.
            next_sync = clock + timedelta(seconds=source["sync_interval_seconds"])
            connection.execute(update(sources).where(sources.c.id == source["id"]).values(next_sync_at=next_sync))
            decisions.append(dict(decision, scheduled_for=scheduled_for, next_sync_at=next_sync))
    return decisions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--now", type=datetime.fromisoformat, help="Aware fixed clock for local fixture tests")
    args = parser.parse_args()
    if args.now and not args.once:
        parser.error("--now requires --once")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda signum, frame: stop.set())
    engine = make_engine(Settings.from_env())
    interval = max(0.1, min(60, float(os.getenv("SCHEDULER_POLL_SECONDS", "2"))))
    print(json.dumps({"event":"scheduler_started"}), flush=True)
    try:
        while not stop.is_set():
            try:
                for decision in tick(engine, args.now):
                    print(json.dumps(dict(event="schedule_decision", **decision), default=str), flush=True)
            except SQLAlchemyError:
                if args.once:
                    raise
                print(json.dumps({"event":"scheduler_storage_unavailable"}), flush=True)
            if args.once:
                break
            stop.wait(interval)
    finally:
        engine.dispose()
        print(json.dumps({"event":"scheduler_stopped"}), flush=True)


if __name__ == "__main__":
    main()
