"""Enqueue one metadata read per configured official source, without inference."""
import argparse
import json
import time
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select

from agentic_gateway.config import Settings
from agentic_gateway.database import make_engine
from agentic_gateway.catalog.schema import sources, offerings
from agentic_gateway.catalog.registry import enqueue, get_job
from agentic_gateway.catalog.reconciliation import offering_evidence
from .seeds import seed_providers, PROVIDERS


def wait(engine, job_id):
    until = time.monotonic() + 100
    while time.monotonic() < until:
        job = get_job(engine, job_id)
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.2)
    raise RuntimeError("metadata_probe_pending_check_worker")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-github-fixture", action="store_true",
        help="Explicit synthetic provenance demo; never claims a live GitHub fetch")
    args = parser.parse_args()
    engine = make_engine(Settings.from_env())
    try:
        seed_providers(engine)
        if args.with_github_fixture:
            with engine.begin() as connection:
                connection.execute(insert(sources).values(id="fixture/github-provenance", connector="github_fixture",
                    locator="fixture://github/example/metadata-catalog/catalog.json").on_conflict_do_nothing(index_elements=[sources.c.id]))
            wait(engine, enqueue(engine, "fixture/github-provenance", "r1"))
        ids = [(source["id"], enqueue(engine, source["id"], "latest")) for source in PROVIDERS]
        results = [{"source_id":source_id, "job":wait(engine, job_id)} for source_id,job_id in ids]
        with engine.connect() as connection:
            demo_id = connection.scalar(select(offerings.c.id).where(offerings.c.provider == "nvidia",
                offerings.c.upstream_model == "meta/llama-3.3-70b-instruct", offerings.c.task == "chat"))
        report = {"inference_requests":0, "github_live_available":False,
                  "sources":results, "evidence":offering_evidence(engine, demo_id) if demo_id else None}
        print(json.dumps(report, default=str), flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
