"""Registry and enqueue transactions. A source generation orders sync requests."""

from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from .fixture_reader import NAMES, load_fixture
from .schema import jobs, runs, sources


def register_source(engine, source_id, fixture_name):
    if fixture_name not in NAMES:
        raise ValueError("unknown_fixture")
    with engine.begin() as connection:
        connection.execute(insert(sources).values(id=source_id, fixture_name=fixture_name)
            .on_conflict_do_nothing(index_elements=[sources.c.id]))
        return dict(connection.execute(select(sources).where(sources.c.id == source_id)).mappings().one())


def enqueue_locked(connection, source, revision, *, trigger="manual", scheduled_for=None):
    """Shared enqueue path. Caller holds this source's row lock until commit."""
    if not source["enabled"]:
        raise ValueError("source_disabled")
    if trigger not in {"manual", "scheduled"} or (trigger == "scheduled") != (scheduled_for is not None):
        raise ValueError("invalid_trigger")
    if scheduled_for is not None:
        existing = connection.scalar(select(jobs.c.id).where(jobs.c.source_id == source["id"], jobs.c.scheduled_for == scheduled_for))
        if existing:
            return existing
    if source["connector"] == "fixture":
        load_fixture(source["fixture_name"], revision)
    elif source["connector"] == "github_fixture" and revision != "r1":
        raise ValueError("unknown_revision")
    generation = source["generation"] + 1
    connection.execute(update(sources).where(sources.c.id == source["id"]).values(generation=generation))
    job_id = str(uuid4())
    connection.execute(insert(jobs).values(id=job_id, source_id=source["id"], revision=revision,
        generation=generation, trigger_kind=trigger, scheduled_for=scheduled_for))
    return job_id


def enqueue(engine, source_id, revision):
    with engine.begin() as connection:
        source = connection.execute(select(sources).where(sources.c.id == source_id)
            .with_for_update()).mappings().one_or_none()
        if source is None:
            raise LookupError("source_not_found")
        from .availability import default_revision
        return enqueue_locked(connection, source, revision or default_revision(source))


def get_job(engine, job_id):
    with engine.connect() as connection:
        row = connection.execute(select(jobs).where(jobs.c.id == job_id)).mappings().one_or_none()
        if row is None:
            raise LookupError("job_not_found")
        result = dict(row)
        if row["sync_run_id"]:
            run = connection.execute(select(runs).where(runs.c.id == row["sync_run_id"])).mappings().one()
            result.update(total_pages=run["total_pages"], diff=run["diff"])
        else:
            result.update(total_pages=None, diff=None)
        return result
