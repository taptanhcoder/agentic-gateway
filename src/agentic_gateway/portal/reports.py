"""Observed provider reports. All time-series aggregation happens in PostgreSQL.

No provider probes, credentials, prompt/output text, estimated prices or synthetic data.
"""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import Float, Numeric, and_, case, cast, func, literal, select

from agentic_gateway.catalog.admin import operator
from agentic_gateway.catalog.membership_query import current_links
from agentic_gateway.catalog.schema import sources, offerings, jobs
from agentic_gateway.catalog.views import source_details
from agentic_gateway.providers.inference import deployments as targets
from agentic_gateway.qualification import schema as qualification
from agentic_gateway.qualification.routes import approved, RouteError

router = APIRouter(prefix="/admin/portal", dependencies=[Depends(operator)])


def numeric_json(field):
    # JSON may be absent or legacy. Unknown stays NULL, never coerced into measured zero.
    value = field.astext
    return case((value.op("~")(r"^[0-9]{1,15}(\.[0-9]{1,6})?$"), cast(value, Numeric)), else_=None)


def summarize_attempts(connection, dataset, by):
    latency = dataset.c.latency_ms
    return connection.execute(select(*by, func.count().label("attempts"),
        func.count().filter(dataset.c.outcome == "succeeded").label("succeeded"),
        func.count().filter(dataset.c.outcome == "not_sent").label("not_sent"),
        func.count().filter(dataset.c.outcome == "unknown").label("unknown"),
        func.count().filter(dataset.c.outcome.not_in(["succeeded", "not_sent", "unknown"])).label("other"),
        func.count(dataset.c.tokens).label("known_usage_attempts"),
        func.sum(dataset.c.input_tokens).label("input_tokens"),
        func.sum(dataset.c.output_tokens).label("output_tokens"),
        func.sum(dataset.c.tokens).label("total_tokens"),
        func.count(latency).label("latency_samples"),
        func.avg(latency).label("latency_avg_ms"),
        func.percentile_cont(.95).within_group(cast(latency, Float)).label("latency_p95_ms"))
        .select_from(dataset).group_by(*by)).mappings().all()


def metrics(row=None):
    result = dict(row or {})
    for name in ("attempts", "succeeded", "not_sent", "unknown", "other", "known_usage_attempts", "latency_samples"):
        result[name] = int(result.get(name) or 0)
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        result[name] = int(result[name]) if result.get(name) is not None else None
    for name in ("latency_avg_ms", "latency_p95_ms"):
        result[name] = round(float(result[name]), 2) if result.get(name) is not None else None
    result["success_rate"] = round(result["succeeded"] / result["attempts"] * 100, 2) if result["attempts"] else None
    result["unknown_usage_attempts"] = result["attempts"] - result["known_usage_attempts"]
    result.update(cost_usd=None, uptime_percent=None, throughput_tps=None)
    return result


@router.get("/reports")
def report(request: Request, days: int = Query(7, ge=1, le=90),
           provider: str | None = Query(None, min_length=1, max_length=120),
           mode: Literal["all", "fixture", "live"] = "all"):
    now = datetime.now(timezone.utc)
    # Include the complete current UTC day plus days-1 previous UTC days; all charts label UTC.
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    end = now
    with request.app.state.engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
        # Prevent an unbounded reporting query from tying up a DB connection indefinitely.
        from sqlalchemy import text
        c.execute(text("SET LOCAL statement_timeout = '10000'"))
        links = current_links()
        current_ids = select(links.c.offering_id).distinct()
        inventory = c.execute(select(offerings.c.provider, func.count().label("count"))
            .where(offerings.c.id.in_(current_ids)).group_by(offerings.c.provider)).mappings().all()
        source_rows = c.execute(select(sources).order_by(sources.c.id)).mappings().all()
        version_join = targets.deployments.join(targets.versions,
            and_(targets.deployments.c.id == targets.versions.c.deployment_id,
                 targets.deployments.c.generation == targets.versions.c.generation))
        deployment_counts = c.execute(select(targets.versions.c.provider,
            func.count().label("deployments"),
            func.count().filter(targets.deployments.c.enabled.is_(True)).label("enabled"))
            .select_from(version_join).group_by(targets.versions.c.provider)).mappings().all()
        account_counts = c.execute(select(targets.accounts.c.provider, func.count().label("count"))
            .group_by(targets.accounts.c.provider)).mappings().all()
        a, v = targets.attempts, targets.versions
        # Join the exact attempt generation, not the deployment's current version/provider.
        attempt_join = a.join(v, and_(a.c.deployment_id == v.c.deployment_id, a.c.generation == v.c.generation))
        known = a.c.report["usage"]["status"].astext == "upstream"
        def token(name):
            return case((known, numeric_json(a.c.report["usage"][name])), else_=None)
        selected = select(a.c.id, a.c.deployment_id, a.c.generation, a.c.mode, a.c.started_at,
            a.c.provider_outcome.label("outcome"), a.c.result_code, v.c.provider, v.c.upstream_model,
            numeric_json(a.c.report["latency_ms"]).label("latency_ms"),
            token("prompt_tokens").label("input_tokens"), token("completion_tokens").label("output_tokens"),
            token("total_tokens").label("tokens")).select_from(attempt_join).where(a.c.started_at >= start, a.c.started_at <= end)
        if provider:
            selected = selected.where(v.c.provider == provider)
        if mode != "all":
            selected = selected.where(a.c.mode == mode)
        dataset = selected.cte("report_attempts")
        totals = metrics(summarize_attempts(c, dataset, [])[0])
        per_provider = [metrics(r) for r in summarize_attempts(c, dataset, [dataset.c.provider])]
        per_model = [metrics(r) for r in summarize_attempts(c, dataset, [dataset.c.provider, dataset.c.upstream_model])]
        mode_rows = [metrics(r) for r in summarize_attempts(c, dataset, [dataset.c.mode])]
        day = func.date_trunc("day", func.timezone("UTC", dataset.c.started_at)).label("day")
        series = []
        for row in summarize_attempts(c, dataset, [day, dataset.c.provider]):
            item = metrics(row)
            item["day"] = row["day"].date().isoformat()
            series.append(item)
        errors = [dict(r) for r in c.execute(select(dataset.c.provider, dataset.c.result_code,
            func.count().label("count")).where(dataset.c.result_code != "ok")
            .group_by(dataset.c.provider, dataset.c.result_code).order_by(func.count().desc()).limit(30)).mappings()]
        job_join = jobs.outerjoin(sources, jobs.c.source_id == sources.c.id).outerjoin(
            qualification.runs, jobs.c.qualification_run_id == qualification.runs.c.id).outerjoin(v,
            and_(qualification.runs.c.deployment_id == v.c.deployment_id, qualification.runs.c.generation == v.c.generation))
        job_provider = func.coalesce(v.c.provider, sources.c.provider, literal("unassigned"))
        job_query = select(job_provider.label("provider"), jobs.c.kind, jobs.c.status, func.count().label("count"))
        job_query = job_query.select_from(job_join).where(jobs.c.created_at >= start, jobs.c.created_at <= end)
        if provider:
            job_query = job_query.where(job_provider == provider)
        # Mode filter applies to inference/qualification jobs; catalog has no inference mode.
        if mode != "all":
            job_query = job_query.where((jobs.c.kind == "catalog") | (qualification.runs.c.mode == mode))
        job_stats = [dict(r) for r in c.execute(job_query.group_by(job_provider, jobs.c.kind, jobs.c.status)).mappings()]
        verdict_join = qualification.runs.join(v, and_(qualification.runs.c.deployment_id == v.c.deployment_id,
            qualification.runs.c.generation == v.c.generation)).outerjoin(qualification.verdicts,
            qualification.runs.c.id == qualification.verdicts.c.run_id)
        verdict_query = select(v.c.provider, qualification.runs.c.mode,
            func.coalesce(qualification.verdicts.c.decision, literal("pending")).label("decision"),
            func.count().label("count")).select_from(verdict_join).where(
                qualification.runs.c.created_at >= start, qualification.runs.c.created_at <= end)
        if provider:
            verdict_query = verdict_query.where(v.c.provider == provider)
        if mode != "all":
            verdict_query = verdict_query.where(qualification.runs.c.mode == mode)
        verdict_stats = [dict(r) for r in c.execute(verdict_query.group_by(v.c.provider,
            qualification.runs.c.mode, qualification.verdicts.c.decision)).mappings()]
        providers = {}
        def ensure(name):
            if name not in providers:
                providers[name] = {"id": name, "current_offerings": 0, "deployments": 0,
                    "enabled_deployments": 0, "accounts": 0, "sources": 0,
                    "catalog_status": "not_configured", "catalog_last_success_at": None,
                    "inference_status": "not_observed", "metrics": metrics()}
            return providers[name]
        for r in inventory:
            ensure(r["provider"])["current_offerings"] = r["count"]
        for r in deployment_counts:
            ensure(r["provider"]).update(deployments=r["deployments"], enabled_deployments=r["enabled"])
        for r in account_counts:
            ensure(r["provider"])["accounts"] = r["count"]
        for r in per_provider:
            ensure(r["provider"])["metrics"] = r
        detailed_sources = []
        # Provider catalog status is the latest attempt of every configured source, independent of report window.
        for source in source_rows:
            name = source["provider"] or "unassigned"
            item = ensure(name)
            item["sources"] += 1
            dto = source_details(c, source)
            latest = dto["latest_job"]
            status = "paused" if not source["enabled"] else latest["status"] if latest else "not_synced"
            item.setdefault("source_states", []).append(status)
            success_at = dto["last_success_at"]
            if success_at and (not item["catalog_last_success_at"] or success_at > item["catalog_last_success_at"]):
                item["catalog_last_success_at"] = success_at
            if provider == name:
                detailed_sources.append(dto)
        latest_stmt = select(dataset.c.provider, dataset.c.outcome, dataset.c.mode,
            dataset.c.result_code, dataset.c.started_at).distinct(dataset.c.provider)
        latest_stmt = latest_stmt.order_by(dataset.c.provider, dataset.c.started_at.desc(), dataset.c.id.desc())
        for row in c.execute(latest_stmt).mappings():
            item = ensure(row["provider"])
            item["latest_inference"] = dict(row)
            item["inference_status"] = "fixture_only" if row["mode"] == "fixture" else (
                "observed_success" if row["outcome"] == "succeeded" else "attention_required")
        for item in providers.values():
            states = item.pop("source_states", [])
            item["catalog_status"] = "attention_required" if any(x in {"failed", "blocked"} for x in states) else (
                "syncing" if any(x in {"running", "queued"} for x in states) else
                "snapshot_available" if "succeeded" in states else "paused" if states and all(x == "paused" for x in states)
                else "not_synced" if states else "not_configured")
        # Route usability uses the same publication gate as the operator route reader.
        route_query = select(qualification.pins.c.id, qualification.pins.c.route_version,
            qualification.snapshots.c.verdict_id, qualification.snapshots.c.deployment_id,
            qualification.snapshots.c.generation, v.c.provider).select_from(qualification.pins.join(
                qualification.snapshots, qualification.pins.c.snapshot_id == qualification.snapshots.c.id).join(v,
                and_(v.c.deployment_id == qualification.snapshots.c.deployment_id,
                     v.c.generation == qualification.snapshots.c.generation)))
        if provider:
            route_query = route_query.where(v.c.provider == provider)
        route_rows = []
        route_total = c.scalar(select(func.count()).select_from(route_query.subquery()))
        # Detail route list is bounded, explicit truncation; no fabricated global usability count.
        for row in c.execute(route_query.order_by(qualification.pins.c.id).limit(100)).mappings():
            item = dict(row)
            try:
                approved(c, row["verdict_id"])
                item.update(usable=True, invalid_reason=None)
            except (RouteError, LookupError) as error:
                item.update(usable=False, invalid_reason=str(error))
            route_rows.append(item)
        details = None
        if provider:
            if provider not in providers:
                raise HTTPException(404, detail="provider_not_found")
            deployments_stmt = select(targets.deployments.c.id, targets.deployments.c.enabled,
                targets.deployments.c.generation, targets.deployments.c.qualification_epoch,
                v.c.upstream_model, v.c.account_id, v.c.endpoint, v.c.secret_ref_id).select_from(version_join).where(v.c.provider == provider)
            model_stmt = select(offerings).where(offerings.c.provider == provider, offerings.c.id.in_(current_ids))
            details = {"sources": detailed_sources,
                "deployments": [dict(r) for r in c.execute(deployments_stmt.order_by(targets.deployments.c.id).limit(100)).mappings()],
                "offerings": [dict(r) for r in c.execute(model_stmt.order_by(offerings.c.id).limit(100)).mappings()],
                "recent_attempts": [dict(r) for r in c.execute(select(dataset).order_by(dataset.c.started_at.desc()).limit(30)).mappings()],
                "list_limits": {"deployments": 100, "offerings": 100, "recent_attempts": 30}}
        return {"generated_at": now, "window": {"start": start, "end": end, "days": days, "timezone": "UTC"},
            "filters": {"provider": provider, "mode": mode}, "totals": totals,
            "providers": sorted(providers.values(), key=lambda x: x["id"]),
            "daily": sorted(series, key=lambda x: (x["day"], x["provider"])),
            "models": sorted(per_model, key=lambda x: -x["attempts"]), "modes": mode_rows,
            "errors": errors, "jobs": job_stats, "verdicts": verdict_stats,
            "routes": route_rows, "route_total": route_total, "route_list_truncated": route_total > len(route_rows),
            "details": details, "definitions": {
                "source": "Persisted adapter attempts (qualification/CLI), not client API traffic.",
                "success_rate": "provider_outcome=succeeded / all selected adapter attempts, including not_sent and unknown.",
                "latency": "Adapter elapsed milliseconds across samples; not provider-only latency or first-token latency.",
                "tokens": "Sum of upstream-reported usage only. NULL/unknown usage is not measured zero.",
                "inventory": "Current snapshot inventory; not filtered by date or fixture/live mode.",
                "catalog": "Latest job per registered source; current metadata, not provider uptime.",
                "unknown_provider": "unassigned means source has no provider metadata; it is not inferred from source name.",
                "cost": "Unknown: no verified billing rates or spend ledger.",
                "network": "This endpoint is read-only and performs no provider requests."}}
