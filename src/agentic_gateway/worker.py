"""Durable catalog worker; SIGTERM interrupts safely and returns exit 0."""

import json
import signal
import threading
import os

from sqlalchemy.exc import SQLAlchemyError
from .config import Settings
from .database import make_engine
from .catalog.sync import claim_job, process_job, log
from .qualification.service import QualificationClaim, process as process_qualification


def main() -> None:
    stopped = threading.Event()

    def stop(signum, frame):
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({"event": "worker_started"}), flush=True)
    engine = make_engine(Settings.from_env())
    lease_seconds = max(5, min(3600, int(os.getenv("JOB_LEASE_SECONDS", "30"))))
    try:
        while not stopped.is_set():
            try:
                claim = claim_job(engine, lease_seconds)
                if claim:
                    if isinstance(claim, QualificationClaim):
                        process_qualification(engine, claim, stopped, lease_seconds)
                    else:
                        process_job(engine, claim, stopped, lease_seconds)
                else:
                    stopped.wait(0.5)
            except SQLAlchemyError:
                log("worker_storage_unavailable")
                stopped.wait(1)
    finally:
        engine.dispose()
        print(json.dumps({"event": "worker_stopped"}), flush=True)


if __name__ == "__main__":
    main()
