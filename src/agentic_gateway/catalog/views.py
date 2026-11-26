"""Operator DTOs: separate attempts, successful publication and scheduling."""
from datetime import datetime, timezone
import re

from sqlalchemy import func, select

from .availability import connector_status, default_revision
from .membership_query import current_links
from .schema import documents, jobs, memberships, observations, offerings, runs, snapshots, sources, staging
from agentic_gateway.providers.catalog.transport import REVIEWED

ERROR_CODES = frozenset({
    "lease_expired", "worker_stopped", "source_superseded", "fixture_page_timeout",
    "invalid_fixture", "invalid_page_order", "incomplete_snapshot", "revision_content_conflict", "storage_error",
    "unreviewed_catalog_locator", "credential_host_not_allowed", "catalog_login_required",
    "catalog_redirect_unreviewed", "catalog_auth_required", "catalog_document_too_large", "catalog_transport_error",
    "nvidia_catalog_structure_changed", "nvidia_catalog_missing_tables", "nvidia_catalog_invalid_row", "nvidia_catalog_missing_rows",
    "siliconflow_catalog_not_json", "siliconflow_catalog_incomplete_or_invalid", "siliconflow_catalog_invalid_row",
    "siliconflow_docs_structure_changed", "connector_locator_mismatch", "unreviewed_credential_reference",
    "catalog_api_not_documented", "catalog_interface_unavailable", "connector_not_supported",
})


def safe_error(code):
    if code is None:
        return None
    return code if code in ERROR_CODES or re.fullmatch(r"catalog_http_[345][0-9]{2}", code) else "sync_error"


def utc(value):
    return value.astimezone(timezone.utc) if value is not None else None


def locator(value):
    # Registry config and arbitrary upstream URLs/headers are never serialized.
    if value in REVIEWED:
        return value
    if value in {"fixture://github/example/metadata-catalog/catalog.json"}:
        return value
    return None


def fetch_summary(item):
    try:
        read_at = datetime.fromisoformat(item.get("read_at", ""))
        read_at = utc(read_at) if read_at.tzinfo else None
    except (ValueError, TypeError):
        read_at = None
    digest = item.get("content_hash")
    return {"locator":locator(item.get("locator")), "read_at":read_at,
        "http_status":item.get("http_status") if isinstance(item.get("http_status"), int) else None,
        "content_hash":digest if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest) else None,
        "mode":item.get("mode") if item.get("mode") in {"fixture", "live"} else "unknown"}


def run_detail(connection, run_id):
    row = connection.execute(select(runs, jobs.c.source_id, jobs.c.trigger_kind, jobs.c.scheduled_for,
        jobs.c.created_at.label("queued_at")).select_from(runs.join(jobs, runs.c.job_id == jobs.c.id))
        .where(runs.c.id == run_id)).mappings().one_or_none()
    if row is None:
        raise LookupError("run_not_found")
    snapshot = connection.execute(select(snapshots).where(snapshots.c.run_id == run_id)).mappings().one_or_none()
    published = snapshot is not None and row["status"] == "succeeded"
    staged = connection.scalar(select(func.count()).select_from(staging).where(staging.c.run_id == run_id))
    published_count = connection.scalar(select(func.count()).select_from(memberships)
        .where(memberships.c.snapshot_id == snapshot["id"])) if published else 0
    error = safe_error(row["error_code"])
    diff = row["diff"] if published else None
    return {"id":row["id"], "sync_run_id":row["id"], "job_id":row["job_id"], "source_id":row["source_id"],
        "trigger":row["trigger_kind"], "scheduled_for":utc(row["scheduled_for"]), "status":row["status"],
        "queued_at":utc(row["queued_at"]), "started_at":utc(row["started_at"]), "finished_at":utc(row["finished_at"]),
        "pages_read":row["pages_read"], "total_pages":row["total_pages"], "records_staged":staged,
        "records_published":published_count, "published":published, "snapshot_id":snapshot["id"] if published else None,
        "diff":diff, "published_diff":diff, "offerings_added":len(diff.get("added", [])) if diff else 0,
        "offerings_removed":len(diff.get("removed", [])) if diff else 0,
        "error_code":error, "error":{"code":error} if error else None,
        "fetches":[fetch_summary(item) for item in row["fetch_metadata"]]}


def source_details(connection, source):
    source_id = source["id"]
    latest_job = connection.execute(select(jobs).where(jobs.c.source_id == source_id)
        .order_by(jobs.c.generation.desc()).limit(1)).mappings().one_or_none()
    latest_run_id = connection.scalar(select(runs.c.id).select_from(runs.join(jobs, runs.c.job_id == jobs.c.id))
        .where(jobs.c.source_id == source_id).order_by(runs.c.started_at.desc(), runs.c.id.desc()).limit(1))
    success = connection.execute(select(runs.c.id, runs.c.finished_at)
        .select_from(runs.join(jobs, runs.c.job_id == jobs.c.id))
        .where(jobs.c.source_id == source_id, runs.c.status == "succeeded")
        .order_by(runs.c.finished_at.desc(), runs.c.id.desc()).limit(1)).mappings().one_or_none()
    snapshot = connection.execute(select(snapshots).where(snapshots.c.id == source["current_snapshot_id"])).mappings().one_or_none()
    availability = connector_status(source)
    if not source["enabled"]:
        state, reason = "paused", "source_disabled"
    elif source["sync_interval_seconds"] is None:
        state, reason = "not_configured", "schedule_not_configured"
    elif not availability["usable_for_schedule"]:
        state, reason = "blocked", availability["reason_code"]
    else:
        state, reason = "scheduled", None
    next_sync = utc(source["next_sync_at"]) if state == "scheduled" else None
    return {"id":source_id, "source_id":source_id, "source_type":source["connector"], "enabled":source["enabled"],
        "fixture_name":source["fixture_name"], "provider":source["provider"], "catalog_scope":source["catalog_scope"],
        "locator":locator(source["locator"]), "connector":availability,
        "latest_job":({"job_id":latest_job["id"], "status":latest_job["status"], "trigger":latest_job["trigger_kind"],
            "sync_run_id":latest_job["sync_run_id"], "error_code":safe_error(latest_job["error_code"]),
            "queued_at":utc(latest_job["created_at"])} if latest_job else None),
        "latest_attempt":run_detail(connection, latest_run_id) if latest_run_id else None,
        "last_success_at":utc(success["finished_at"]) if success else None,
        "last_successful_run_id":success["id"] if success else None,
        "active_snapshot_id":source["current_snapshot_id"],
        # Compatibility for step 2 clients; both names identify the published pointer.
        "current_snapshot_id":source["current_snapshot_id"],
        "active_snapshot":({"snapshot_id":snapshot["id"], "sync_run_id":snapshot["run_id"],
            "revision":snapshot["revision"], "published_at":utc(snapshot["created_at"])} if snapshot else None),
        "next_sync_at":next_sync,
        "schedule":{"interval_seconds":source["sync_interval_seconds"], "revision":source["sync_revision"] or default_revision(source),
            "configured_next_sync_at":utc(source["next_sync_at"]), "next_sync_at":next_sync, "state":state, "reason_code":reason}}


def list_sources(engine):
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            return {"data":[source_details(connection, row) for row in
                connection.execute(select(sources).order_by(sources.c.id)).mappings().all()]}


def list_offerings(engine, *, provider=None, source=None, region=None, task_type=None, limit=50, offset=0):
    links = current_links()
    active = links.join(offerings, offerings.c.id == links.c.offering_id)
    matching = select(offerings.c.id).select_from(active)
    for column, value in ((offerings.c.provider,provider), (links.c.source_id,source),
                          (offerings.c.region,region), (offerings.c.task,task_type)):
        if value is not None:
            matching = matching.where(column == value)
    matching = matching.distinct().subquery()
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            total = connection.scalar(select(func.count()).select_from(matching))
            page = connection.execute(select(offerings).where(offerings.c.id.in_(select(matching.c.id)))
                .order_by(offerings.c.id).limit(limit).offset(offset)).mappings().all()
            data = {row["id"]:dict(row, task_type=row["task"], evidence=[], source_ids=[],
                inference_eligible=False, evidence_url=f"/admin/offerings/{row['id']}/evidence") for row in page}
            if data:
                evidence = connection.execute(select(observations, sources.c.connector,
                    links.c.offering_id.label("linked_offering_id"), links.c.snapshot_id,
                    links.c.published_run_id, links.c.association)
                    .select_from(links.join(observations, links.c.observation_id == observations.c.id)
                        .join(sources, sources.c.id == links.c.source_id))
                    .where(links.c.offering_id.in_(list(data))).order_by(observations.c.source_id, observations.c.id)).mappings()
                for row in evidence:
                    item = data[row["linked_offering_id"]]
                    item["evidence"].append({"observation_id":row["id"], "source_id":row["source_id"],
                        "snapshot_id":row["snapshot_id"], "sync_run_id":row["published_run_id"],
                        "revision":row["revision"], "position":row["position"], "parser_version":row["parser_version"],
                        "association":row["association"],
                        "mode":row["provenance"].get("mode", "fixture" if row["connector"] == "fixture" else "legacy_unknown")})
                    if row["source_id"] not in item["source_ids"]:
                        item["source_ids"].append(row["source_id"])
    next_offset = offset + len(data) if offset + len(data) < total else None
    return {"data":list(data.values()), "pagination":{"limit":limit, "offset":offset,
        "total":total, "next_offset":next_offset, "has_more":next_offset is not None}}
