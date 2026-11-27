"""Durable, bounded qualification through the step-4 adapter.

Claim/reserve/complete are short transactions on the shared jobs queue. A probe
reservation consumes budget before I/O. Reclaim never replays an uncertain send.
"""
import asyncio
from dataclasses import dataclass
from datetime import timedelta
import json
import os
import re
import threading
import time
from uuid import uuid4

import httpx
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from agentic_gateway.catalog.schema import jobs
from agentic_gateway.catalog.sync import LostLease
from agentic_gateway.providers.inference import adapter
from agentic_gateway.providers.inference.deployments import (
    DeploymentError, accounts, attempts, deployments, persisted_report, secrets, verified_evidence, versions,
)
from .schema import observations, policy, runs, verdicts

SCENARIOS = {"ok", "empty", "invalid", "429", "timeout", "no_usage"}


class QualificationError(Exception):
    pass


@dataclass(frozen=True)
class QualificationClaim:
    job_id: str
    run_id: str
    token: int


def event(name, claim, **fields):
    print(json.dumps({"event": name, "job_id": claim.job_id,
        "qualification_run_id": claim.run_id, "fencing_token": claim.token, **fields}), flush=True)


def enqueue(engine, deployment_id, mode, max_probes, actor, fixture_scenario="ok"):
    if mode not in {"fixture", "live"} or type(max_probes) is not int or not 1 <= max_probes <= 2:
        raise QualificationError("invalid_qualification_input")
    if fixture_scenario not in SCENARIOS or mode == "live" and fixture_scenario != "ok":
        raise QualificationError("invalid_fixture_scenario")
    run_id, job_id = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        current = connection.execute(select(deployments).where(deployments.c.id == deployment_id)
            .with_for_update()).mappings().one_or_none()
        if current is None:
            raise LookupError("deployment_not_found")
        version = connection.execute(select(versions).where(versions.c.deployment_id == deployment_id,
            versions.c.generation == current["generation"])).mappings().one()
        active = connection.execute(select(policy).where(policy.c.id == "text").with_for_update(read=True)).mappings().one()
        if max_probes > active["spec"]["max_probes"]:
            raise QualificationError("probe_budget_exceeds_profile")
        connection.execute(insert(jobs).values(id=job_id, kind="qualification", qualification_run_id=run_id,
            revision=active["profile_revision"], generation=current["generation"]))
        connection.execute(insert(runs).values(id=run_id, job_id=job_id, deployment_id=deployment_id,
            generation=current["generation"], account_id=version["account_id"], adapter_version=version["adapter_version"],
            qualification_epoch=current["qualification_epoch"], profile_revision=active["profile_revision"],
            suite_revision=active["suite_revision"], spec=active["spec"], mode=mode,
            max_probes=max_probes, fixture_scenario=fixture_scenario if mode == "fixture" else None, requested_by=actor))
    return {"run_id": run_id, "job_id": job_id}


def claim_locked(connection, job, lease_seconds):
    token = job["fencing_token"] + 1
    run = connection.execute(select(runs).where(runs.c.id == job["qualification_run_id"])).mappings().one()
    now = connection.scalar(select(func.clock_timestamp()))
    connection.execute(update(jobs).where(jobs.c.id == job["id"]).values(status="running", fencing_token=token,
        lease_until=now + timedelta(seconds=lease_seconds), error_code=None))
    connection.execute(update(runs).where(runs.c.id == run["id"]).values(status="running",
        started_at=run["started_at"] or now,
        deadline_at=run["deadline_at"] or now + timedelta(seconds=run["spec"]["deadline_seconds"])))
    claim = QualificationClaim(job["id"], run["id"], token)
    event("qualification_claimed", claim)
    return claim


def owned(connection, claim):
    row = connection.execute(select(jobs).where(jobs.c.id == claim.job_id,
        jobs.c.kind == "qualification", jobs.c.qualification_run_id == claim.run_id,
        jobs.c.fencing_token == claim.token, jobs.c.status == "running").with_for_update()).mappings().one_or_none()
    if row is None or not connection.scalar(select(jobs.c.lease_until > func.clock_timestamp()).where(jobs.c.id == claim.job_id)):
        raise LostLease()


def renew(engine, claim, lease_seconds):
    with engine.begin() as connection:
        owned(connection, claim)
        connection.execute(update(jobs).where(jobs.c.id == claim.job_id).values(
            lease_until=func.clock_timestamp() + timedelta(seconds=lease_seconds)))


def finish(engine, claim, decision, reasons, *, blocked=False):
    with engine.begin() as connection:
        owned(connection, claim)
        run = connection.execute(select(runs).where(runs.c.id == claim.run_id)).mappings().one()
        now = connection.scalar(select(func.clock_timestamp()))
        connection.execute(insert(verdicts).values(id=str(uuid4()), run_id=claim.run_id, decision=decision,
            reason_codes=reasons, expires_at=now + timedelta(seconds=run["spec"]["ttl_seconds"])))
        connection.execute(update(runs).where(runs.c.id == claim.run_id).values(status="blocked" if blocked else "completed",
            reason_codes=reasons, finished_at=now))
        connection.execute(update(jobs).where(jobs.c.id == claim.job_id).values(
            status="blocked" if blocked else "succeeded", error_code=reasons[0] if reasons else None,
            finished_at=now, lease_until=None))
    event("qualification_finished", claim, decision=decision, reason_codes=reasons)


def frozen_target(engine, run):
    with engine.connect() as connection:
        row = connection.execute(select(versions, secrets.c.env_name, accounts.c.enabled.label("account_enabled"))
            .select_from(versions.join(secrets, versions.c.secret_ref_id == secrets.c.id)
                .join(accounts, versions.c.account_id == accounts.c.id))
            .where(versions.c.deployment_id == run["deployment_id"], versions.c.generation == run["generation"])).mappings().one()
        current = connection.execute(select(deployments).where(deployments.c.id == run["deployment_id"])).mappings().one()
    target = adapter.Target(run["deployment_id"], run["generation"], row["provider"], row["endpoint"],
        row["upstream_model"], row["env_name"], current["enabled"] and row["account_enabled"], row["adapter_version"], run["mode"])
    return target, row, current


def live_budget(run):
    try:
        count = int(os.getenv("QUALIFICATION_LIVE_MAX_PROBES", "0"))
    except ValueError:
        count = 0
    return (os.getenv("QUALIFICATION_LIVE_ACK_ACCOUNT_TERMS") == "1" and
        os.getenv("QUALIFICATION_LIVE_ACCOUNT_ID") == run["account_id"] and run["max_probes"] <= count <= 2)


def preflight(engine, run):
    target, version, current = frozen_target(engine, run)
    if not target.enabled:
        return target, "deployment_disabled"
    if current["qualification_epoch"] != run["qualification_epoch"]:
        return target, "deployment_revoked"
    if not adapter.validate_target(target):
        return target, "invalid_target"
    if run["mode"] == "live":
        if not os.getenv(target.secret_env):
            return target, "credential_missing"
        if not re.fullmatch(r"[!-~]+", os.getenv(target.secret_env, "")):
            return target, "credential_invalid"
        if not live_budget(run):
            return target, "live_budget_not_authorized"
    try:
        verified_evidence(engine, version["offering_id"], fixture=run["mode"] == "fixture")
    except (DeploymentError, LookupError):
        return target, "deployment_evidence_not_current"
    return target, None


def reserve(engine, claim, run, case_id):
    attempt_id = str(uuid4())
    initial = {"attempt_id": attempt_id, "deployment_id": run["deployment_id"], "generation": run["generation"],
        "mode": run["mode"], "result_code": "interrupted_unknown", "provider_outcome": "unknown",
        "client_send_count": None, "usage": {"status": "unknown"}}
    with engine.begin() as connection:
        owned(connection, claim)
        used = connection.scalar(select(func.count()).select_from(observations).where(observations.c.run_id == claim.run_id))
        if used >= run["max_probes"]:
            raise QualificationError("probe_budget_exhausted")
        if connection.scalar(select(func.clock_timestamp())) >= run["deadline_at"]:
            raise QualificationError("run_deadline_exhausted")
        connection.execute(insert(attempts).values(id=attempt_id, deployment_id=run["deployment_id"],
            generation=run["generation"], mode=run["mode"], report=initial))
        connection.execute(insert(observations).values(run_id=claim.run_id, case_id=case_id,
            attempt_id=attempt_id, fencing_token=claim.token))
    event("qualification_attempt_reserved", claim, attempt_id=attempt_id, deployment_id=run["deployment_id"], generation=run["generation"])
    return attempt_id


def record(engine, claim, attempt_id, report, spec):
    code = report["result_code"]
    assertions = {"envelope_valid": code == "ok", "text_nonempty": bool((report.get("output_text") or "").strip()),
        "within_deadline": code == "ok" and report["latency_ms"] <= spec["probe_deadline_seconds"] * 1000,
        "usage_recorded": report.get("usage", {}).get("status") in {"unknown", "upstream"}}
    if code == "ok":
        failed = [name for name in spec["required"] if not assertions[name]]
        result, reason = ("fail", failed[0]) if failed else ("pass", "passed")
    elif code in {"invalid_response", "model_mismatch", "response_too_large", "sensitive_output_rejected"}:
        result, reason = "fail", code
    else:
        result, reason = "unknown", code
    safe = persisted_report({**report, "attempt_id": attempt_id, "deployment_id": report["deployment_id"]})
    with engine.begin() as connection:
        owned(connection, claim)
        connection.execute(update(attempts).where(attempts.c.id == attempt_id).values(report=safe,
            result_code=code, provider_outcome=report["provider_outcome"], finished_at=func.clock_timestamp()))
        connection.execute(update(observations).where(observations.c.attempt_id == attempt_id,
            observations.c.fencing_token == claim.token).values(result=result, reason_code=reason, assertions=assertions,
                latency_ms=report["latency_ms"], usage=report["usage"], finished_at=func.clock_timestamp()))
    event("qualification_attempt_finished", claim, attempt_id=attempt_id, result_code=code, latency_ms=report["latency_ms"])
    return result, reason


def fixture_transport(scenario):
    async def handle(request):
        if scenario == "timeout":
            raise httpx.ReadTimeout("fixture", request=request)
        if scenario == "429":
            return httpx.Response(429, json={})
        if scenario == "invalid":
            return httpx.Response(200, json={})
        body = {"model": json.loads(request.content)["model"], "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": "" if scenario == "empty" else "A brief synthetic reply."}}]}
        if scenario != "no_usage":
            body["usage"] = {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}
        return httpx.Response(200, json=body, headers={"x-request-id": "qualification-fixture"})
    return httpx.MockTransport(handle)


async def execute(engine, claim, stopped, lease_lost, transport_factory=None):
    with engine.connect() as connection:
        run = dict(connection.execute(select(runs).where(runs.c.id == claim.run_id)).mappings().one())
        previous = connection.scalar(select(func.count()).select_from(observations).where(observations.c.run_id == claim.run_id))
        now = connection.scalar(select(func.clock_timestamp()))
    deadline = time.monotonic() + max(0, (run["deadline_at"] - now).total_seconds())
    # A reservation may already have crossed the network boundary. Never replay.
    if previous:
        finish(engine, claim, "inconclusive", ["interrupted_unknown"])
        return
    target, error = preflight(engine, run)
    if error:
        finish(engine, claim, "inconclusive", [error], blocked=True)
        return
    results, reasons = [], []
    for case in run["spec"]["cases"]:
        if stopped.is_set():
            reasons.append("worker_stopped")
            break
        if lease_lost.is_set():
            raise LostLease()
        # Revocation, credential and live authorization are checked before each probe.
        _, error = preflight(engine, run)
        if error:
            reasons.append(error)
            break
        try:
            attempt_id = reserve(engine, claim, run, case["id"])
        except QualificationError as error:
            reasons.append(str(error))
            break
        transport = transport_factory() if transport_factory else (
            fixture_transport(run["fixture_scenario"]) if run["mode"] == "fixture" else None)
        task = asyncio.create_task(adapter.invoke(target, adapter.TextInput(case["text"], case.get("instructions"),
            run["spec"]["max_output_tokens"]), min(deadline, time.monotonic() + run["spec"]["probe_deadline_seconds"]),
            _transport=transport, _fixture_credential="gateway-fixture-only" if run["mode"] == "fixture" else None))
        try:
            while not task.done():
                if lease_lost.is_set() or stopped.is_set():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    if lease_lost.is_set():
                        raise LostLease()
                    finish(engine, claim, "inconclusive", ["worker_stopped_unknown"])
                    return
                await asyncio.wait({task}, timeout=0.05)
            report = {**task.result(), "deployment_id": run["deployment_id"], "generation": run["generation"], "mode": run["mode"]}
        finally:
            if not task.done():
                task.cancel()
        result, reason = record(engine, claim, attempt_id, report, run["spec"])
        results.append(result)
        if result != "pass":
            reasons.append(case["id"] + ":" + reason)
            break
    if "fail" in results:
        decision = "ineligible"
    elif len(results) == len(run["spec"]["cases"]) and all(value == "pass" for value in results):
        decision = "eligible"
    else:
        decision = "inconclusive"
    finish(engine, claim, decision, reasons)


def process(engine, claim, stopped=None, lease_seconds=30, *, _transport_factory=None):
    stopped = stopped if stopped is not None else threading.Event()
    heartbeat_stop, lease_lost = threading.Event(), threading.Event()

    def heartbeat():
        while not heartbeat_stop.wait(lease_seconds / 3):
            try:
                renew(engine, claim, lease_seconds)
            except (LostLease, SQLAlchemyError):
                lease_lost.set()
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        asyncio.run(execute(engine, claim, stopped, lease_lost, _transport_factory))
    except LostLease:
        event("qualification_stale_worker_ignored", claim)
    finally:
        heartbeat_stop.set()
        thread.join(timeout=2)


def detail(engine, run_id):
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection, connection.begin():
        row = connection.execute(select(runs).where(runs.c.id == run_id)).mappings().one_or_none()
        if row is None:
            raise LookupError("qualification_not_found")
        result = dict(row)
        verdict = connection.execute(select(verdicts).where(verdicts.c.run_id == run_id)).mappings().one_or_none()
        result["verdict"] = dict(verdict) if verdict else None
        entries = connection.execute(select(observations, attempts.c.report).select_from(observations.join(attempts,
            observations.c.attempt_id == attempts.c.id)).where(observations.c.run_id == run_id)
            .order_by(observations.c.created_at, observations.c.case_id)).mappings().all()
        result["observations"] = [dict(entry) for entry in entries]
        result["attempt_count"] = len(entries)
        sends = [entry["report"].get("client_send_count") for entry in entries]
        result["client_send_count"] = None if None in sends else sum(sends)
        result["progress"] = {"completed": sum(entry["finished_at"] is not None for entry in entries), "total": len(row["spec"]["cases"])}
        return result
