"""Bounded, secret-safe operator read models; no provider calls from reads."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from agentic_gateway.catalog.admin import operator
from agentic_gateway.catalog.schema import sources, offerings, jobs
from agentic_gateway.catalog.views import safe_error
from agentic_gateway.providers.inference import deployments as targets
from agentic_gateway.qualification import routes, schema as qualification

router = APIRouter(prefix="/admin/portal", dependencies=[Depends(operator)])
Resource = Literal["deployments", "accounts", "qualifications", "routes", "attempts", "jobs"]


@router.get("/summary")
def summary(request: Request):
    tables = {"sources": sources, "offerings": offerings, "deployments": targets.deployments,
              "qualifications": qualification.runs, "routes": qualification.pins,
              "attempts": targets.attempts, "jobs": jobs}
    with request.app.state.engine.connect() as connection:
        counts = {name: connection.scalar(select(func.count()).select_from(table))
                  for name, table in tables.items()}
        policy = connection.execute(select(qualification.policy)).mappings().all()
    return {"counts": counts, "profiles": [dict(row) for row in policy], "capabilities": {
        "source_sync": True, "source_schedule": True, "evidence": True,
        "deployment_configuration": True, "qualification_fixture": True,
        "qualification_live_from_portal": False, "route_publication": True,
        "agents": False, "virtual_keys": False, "public_inference": False,
        "client_request_logs": False, "billing": False, "automatic_failover": False,
        "mcp": False, "guardrails": False, "rbac": False},
        "adapter": {"provider": "nvidia", "task": "chat",
                    "models": ["meta/llama-3.3-70b-instruct"]},
        "usage_note": "Attempts include qualification/CLI probes; not client request traffic."}


@router.get("/{resource}")
def resources(resource: Resource, request: Request,
              limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    engine = request.app.state.engine
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection, connection.begin():
        if resource == "deployments":
            table = targets.deployments
            statement = select(table.c.id, table.c.generation, table.c.enabled, table.c.qualification_epoch,
                targets.versions.c.offering_id, targets.versions.c.account_id, targets.versions.c.secret_ref_id,
                targets.versions.c.provider, targets.versions.c.endpoint, targets.versions.c.upstream_model,
                targets.versions.c.region, targets.versions.c.adapter_version, targets.versions.c.created_at).select_from(
                table.join(targets.versions, (table.c.id == targets.versions.c.deployment_id) &
                           (table.c.generation == targets.versions.c.generation))).order_by(table.c.id)
        elif resource == "accounts":
            table = targets.accounts
            statement = select(table.c.id, table.c.provider, table.c.label, table.c.enabled).order_by(table.c.id)
        elif resource == "qualifications":
            table = qualification.runs
            statement = select(table.c.id, table.c.job_id, table.c.deployment_id, table.c.generation,
                table.c.qualification_epoch, table.c.mode, table.c.status, table.c.max_probes,
                table.c.profile_revision, table.c.suite_revision, table.c.created_at, table.c.finished_at,
                qualification.verdicts.c.id.label("verdict_id"), qualification.verdicts.c.decision,
                qualification.verdicts.c.expires_at).select_from(table.outerjoin(qualification.verdicts,
                table.c.id == qualification.verdicts.c.run_id)).order_by(table.c.created_at.desc(), table.c.id)
        elif resource == "routes":
            table = qualification.pins
            statement = select(table.c.id, table.c.route_version, table.c.snapshot_id,
                qualification.snapshots.c.deployment_id, qualification.snapshots.c.generation,
                qualification.snapshots.c.verdict_id, qualification.snapshots.c.published_at).select_from(
                table.join(qualification.snapshots, table.c.snapshot_id == qualification.snapshots.c.id)).order_by(table.c.id)
        elif resource == "jobs":
            table = jobs
            statement = select(table.c.id, table.c.kind, table.c.source_id, table.c.qualification_run_id,
                table.c.sync_run_id, table.c.status, table.c.pages_read, table.c.error_code,
                table.c.created_at, table.c.finished_at, table.c.trigger_kind).order_by(table.c.created_at.desc(), table.c.id)
        else:
            table = targets.attempts
            statement = select(table.c.id, table.c.deployment_id, table.c.generation, table.c.mode,
                table.c.started_at, table.c.finished_at, table.c.result_code, table.c.provider_outcome,
                table.c.report).order_by(table.c.started_at.desc(), table.c.id)
        total = connection.scalar(select(func.count()).select_from(table))
        data = [dict(row) for row in connection.execute(statement.limit(limit).offset(offset)).mappings()]
        for item in data:
            if resource == "attempts":
                report = item.pop("report") or {}
                # Never return arbitrary report fields, output_text, prompt, credentials or headers.
                usage = report.get("usage") or {}
                item.update(latency_ms=report.get("latency_ms"), client_send_count=report.get("client_send_count"),
                    usage={key: usage.get(key) for key in ("status", "prompt_tokens", "completion_tokens", "total_tokens")},
                    agent_id=None, virtual_key_id=None, cost_usd=None)
            if resource == "jobs" and item["kind"] == "catalog":
                item["error_code"] = safe_error(item["error_code"])
            if resource == "routes":
                try:
                    _, verdict = routes.approved(connection, item["verdict_id"])
                    item.update(usable=True, invalid_reason=None, expires_at=verdict["expires_at"])
                except (routes.RouteError, LookupError) as error:
                    item.update(usable=False, invalid_reason=str(error))
    return {"data": data, "pagination": {"limit": limit, "offset": offset, "total": total,
        "has_more": offset + len(data) < total}}


class ConfigureDeployment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deployment_id: str = Field(min_length=1, max_length=120)
    offering_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1, max_length=120)


class EnabledInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)


@router.post("/deployments", status_code=201)
def configure(body: ConfigureDeployment, request: Request):
    try:
        # Existing service enforces live official evidence, supported model and pinned endpoint.
        return targets.configure(request.app.state.engine, body.deployment_id, body.offering_id, body.account_id)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from None
    except targets.DeploymentError as error:
        raise HTTPException(409, detail=str(error)) from None


@router.put("/deployment-state/{deployment_id}")
def enabled(deployment_id: str, body: EnabledInput, request: Request):
    try:
        targets.set_enabled(request.app.state.engine, deployment_id, body.enabled)
    except targets.DeploymentError as error:
        raise HTTPException(404, detail=str(error)) from None
    return {"id": deployment_id, "enabled": body.enabled}
