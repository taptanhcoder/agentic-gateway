"""Read-time, exact-ID reconciliation; never mutates offering identity."""
from sqlalchemy import select
from .schema import jobs, memberships, observations, offerings, runs, sources

OFFICIAL = {"nvidia_hosted", "siliconflow_cn", "siliconflow_cn_docs"}


def offering_evidence(engine, offering_id):
    with engine.connect() as connection:
        offering = connection.execute(select(offerings).where(offerings.c.id == offering_id)).mappings().one_or_none()
        if offering is None:
            raise LookupError("offering_not_found")
        offering = dict(offering)
        all_sources = {row["id"]:dict(row) for row in connection.execute(select(sources)).mappings()}
        all_obs = [dict(row) for row in connection.execute(select(observations).order_by(observations.c.source_id, observations.c.id)).mappings()]
        current = set(connection.execute(select(memberships.c.observation_id)
            .select_from(sources.join(memberships, sources.c.current_snapshot_id == memberships.c.snapshot_id))).scalars())
        discovery = [row for row in all_obs if row["offering_id"] == offering_id]
        scopes = {row["provenance"].get("catalog_scope") for row in discovery if row["provenance"].get("catalog_scope")}
        scope = next(iter(scopes)) if len(scopes) == 1 else None
        selected_sources = [source for source in all_sources.values() if source["connector"] in OFFICIAL
            and source["provider"] == offering["provider"] and source["catalog_scope"] == scope]
        attempts = []
        for source in selected_sources:
            latest = connection.execute(select(jobs.c.id, jobs.c.status, jobs.c.error_code,
                jobs.c.sync_run_id, jobs.c.created_at, jobs.c.finished_at, runs.c.fetch_metadata)
                .select_from(jobs.outerjoin(runs, jobs.c.sync_run_id == runs.c.id))
                .where(jobs.c.source_id == source["id"]).order_by(jobs.c.generation.desc()).limit(1)).mappings().one_or_none()
            attempts.append({"source_id":source["id"], "locator":source["locator"],
                "last_successful_snapshot_id":source["current_snapshot_id"], "latest_attempt":dict(latest) if latest else None})

    official_records, matched, unresolved = [], [], []
    source_ids = {source["id"] for source in selected_sources}
    current_discoveries = [row for row in all_obs if row["id"] in current and row["offering_id"] is not None]
    for row in all_obs:
        if row["source_id"] not in source_ids:
            continue
        if row["provenance"].get("provider") != offering["provider"] or row["provenance"].get("catalog_scope") != scope:
            continue
        claims = row["fields"]
        model = claims.get("upstream_model", {}).get("value")
        task = claims.get("task", {}).get("value")
        if model is None and any(claim["scope"] == "model" for claim in claims.values()):
            # Catalog-level queue, explicitly NOT evidence associated with this offering.
            unresolved.append(dict(row, current=row["id"] in current, match_status="pending",
                reason_code="exact_model_id_missing", associated_offering_id=None))
            continue
        if model is not None and model != offering["upstream_model"]:
            continue  # No prefix stripping, fuzzy names or display-name identity.
        state, reason = "pending", "exact_model_id_missing"
        if model is not None:
            candidates = {candidate["offering_id"] for candidate in current_discoveries
                if candidate["provenance"].get("provider") == offering["provider"]
                and candidate["provenance"].get("catalog_scope") == scope
                and candidate["record"].get("offering", {}).get("upstream_model") == model
                and (task is None or candidate["record"].get("offering", {}).get("task") == task)}
            model_endpoint = claims.get("base_url", {})
            if model_endpoint.get("scope") == "model":
                candidates = {candidate["offering_id"] for candidate in current_discoveries
                    if candidate["offering_id"] in candidates
                    and candidate["record"].get("offering", {}).get("base_url") == model_endpoint["value"]}
            if task is not None and task != offering["task"]:
                state, reason = "conflict", "task_conflict"
            elif candidates == {offering_id}:
                state, reason = "matched", "exact_model_and_scope"
            else:
                reason = "ambiguous_offering" if len(candidates) > 1 else "no_current_candidate"
        elif claims and all(claim["scope"] == "provider" for claim in claims.values()):
            state, reason = "provider_scope_only", "does_not_confirm_model"
        item = dict(row, current=row["id"] in current, match_status=state, reason_code=reason)
        official_records.append(item)
        if item["current"] and state == "matched":
            matched.append(row)

    result_fields = {}
    for name in (*[key for key in offering if key != "id"], "pricing", "quota", "tool_calling"):
        claims = []
        for row in discovery + official_records:
            if name not in row["fields"]:
                continue
            claims.append({**row["fields"][name], "observation_id":row["id"], "source_id":row["source_id"],
                "locator":row["provenance"].get("locator"), "read_at":row["provenance"].get("read_at"),
                "mode":row["provenance"].get("mode", "legacy_unknown"), "current":row["id"] in current,
                "origin":"official" if row["source_id"] in source_ids else "discovery"})
        confirmations = [claim for claim in claims if claim["origin"] == "official" and claim["current"]
            and claim["scope"] == "model" and claim["observation_id"] in {row["id"] for row in matched}]
        provider_claims = [claim for claim in claims if claim["origin"] == "official" and claim["current"] and claim["scope"] == "provider"]
        status = "unverified"
        value = offering.get(name, "unknown")
        official_values = {str(claim["value"]) for claim in confirmations}
        if confirmations:
            if len(official_values) > 1 or (name in offering and any(claim["value"] != value for claim in confirmations)):
                status = "conflicting"
            else:
                value = confirmations[0]["value"]
                status = "provider_confirmed" if any(claim["mode"] == "live" for claim in confirmations) else "fixture_confirmed"
        elif provider_claims:
            status = "provider_scope_only"
            if name in offering and any(claim["value"] != value for claim in provider_claims):
                status = "conflicting"
        # Preserve conflicting values even if the whole offering could not match.
        if any(row["match_status"] == "conflict" and row["current"] and name in row["fields"]
               and row["fields"][name]["value"] != offering.get(name) for row in official_records):
            status = "conflicting"
        result_fields[name] = {"value":value, "status":status, "claims":claims}
    return {"offering":offering, "inference_eligible":False,
        "match_status":"matched" if matched else "pending", "fields":result_fields,
        "discovery_evidence":[dict(row, current=row["id"] in current) for row in discovery],
        "official_evidence":official_records, "unresolved_catalog_records":unresolved, "reconciliation":attempts,
        "github_live_available":any(row["provenance"].get("mode") == "live" and row["provenance"].get("github")
            and not row["provenance"].get("synthetic") for row in discovery)}
