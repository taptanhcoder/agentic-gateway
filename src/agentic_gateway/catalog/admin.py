"""Operator-only fixture registry API."""

import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from datetime import timezone

from .registry import enqueue, get_job, register_source
from .schema import memberships, observations, offerings, runs, sources
from .reconciliation import offering_evidence
from . import views

bearer = HTTPBearer(auto_error=False)


def operator(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    expected = os.getenv("OPERATOR_API_KEY", "")
    if not expected:
        raise HTTPException(503, detail="operator_not_configured")
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode(), expected.encode()
    ):
        raise HTTPException(401, detail="invalid_operator_credential", headers={"WWW-Authenticate": "Bearer"})


router = APIRouter(prefix="/admin", dependencies=[Depends(operator)])


class SyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str | None = Field(default=None, min_length=1, max_length=80)


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_seconds: int | None = Field(ge=1, le=31536000)
    next_sync_at: AwareDatetime | None
    revision: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None

    @model_validator(mode="after")
    def paired_schedule(self):
        if (self.interval_seconds is None) != (self.next_sync_at is None):
            raise ValueError("interval_and_due_time_must_be_set_or_cleared_together")
        return self


class SourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_/-]+$")
    fixture_name: str


@router.post("/sources", status_code=201)
def create_source(body: SourceInput, request: Request):
    try:
        source = register_source(request.app.state.engine, body.id, body.fixture_name)
        with request.app.state.engine.connect() as connection:
            return views.source_details(connection, source)
    except ValueError as error:
        raise HTTPException(400, detail=str(error)) from None


@router.get("/sources")
def list_sources(request: Request):
    return views.list_sources(request.app.state.engine)


@router.put("/sources/{source_id:path}/schedule")
def configure_schedule(source_id: str, body: ScheduleInput, request: Request):
    with request.app.state.engine.begin() as connection:
        source = connection.execute(select(sources).where(sources.c.id == source_id).with_for_update()).mappings().one_or_none()
        if source is None:
            raise HTTPException(404, detail="source_not_found")
        values = {"sync_interval_seconds":body.interval_seconds, "next_sync_at":
            body.next_sync_at.astimezone(timezone.utc) if body.next_sync_at else None, "sync_revision":body.revision}
        if body.enabled is not None:
            values["enabled"] = body.enabled
        connection.execute(update(sources).where(sources.c.id == source_id).values(**values))
        updated = connection.execute(select(sources).where(sources.c.id == source_id)).mappings().one()
        return views.source_details(connection, updated)


@router.post("/sources/{source_id:path}/sync", status_code=202)
def sync(source_id: str, body: SyncInput, request: Request):
    try:
        job_id = enqueue(request.app.state.engine, source_id, body.revision)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from None
    except ValueError as error:
        raise HTTPException(400, detail=str(error)) from None
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job(job_id: str, request: Request):
    try:
        result = get_job(request.app.state.engine, job_id)
        result["error_code"] = views.safe_error(result["error_code"])
        result["trigger"] = result["trigger_kind"]
        return result
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from None


@router.get("/sync-runs/{run_id}")
def sync_run(run_id: str, request: Request):
    try:
        with request.app.state.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                return views.run_detail(connection, run_id)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from None


@router.get("/offerings")
def current_offerings(request: Request, provider: str | None = None, source: str | None = None,
    region: str | None = None, task_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return views.list_offerings(request.app.state.engine, provider=provider, source=source,
        region=region, task_type=task_type, limit=limit, offset=offset)


@router.get("/offerings/{offering_id}/evidence")
def evidence_for_offering(offering_id: str, request: Request):
    try:
        return offering_evidence(request.app.state.engine, offering_id)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from None
