"""Authenticated qualification and pinned-route operator endpoints."""
import hashlib
import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from agentic_gateway.catalog.admin import operator
from . import routes, service

router = APIRouter(prefix="/admin", dependencies=[Depends(operator)])


def actor():
    # Audit identity is tied to the configured operator credential, never its value.
    return "operator:sha256:" + hashlib.sha256(os.environ["OPERATOR_API_KEY"].encode()).hexdigest()[:24]


class QualificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deployment_id: str = Field(min_length=1, max_length=120)
    mode: Literal["fixture", "live"] = "fixture"
    max_probes: int = Field(default=2, ge=1, le=2, strict=True)
    fixture_scenario: Literal["ok", "empty", "invalid", "429", "timeout", "no_usage"] = "ok"


class PublishInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(pattern=r"^ag/[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
    verdict_id: str = Field(min_length=1, max_length=120)
    expected_route_version: int = Field(ge=0, strict=True)


class RollbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str = Field(min_length=1, max_length=120)
    expected_route_version: int = Field(ge=1, strict=True)


def call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from None
    except service.QualificationError as error:
        raise HTTPException(400, detail=str(error)) from None
    except routes.RouteError as error:
        raise HTTPException(409, detail=str(error)) from None


@router.post("/qualifications", status_code=202)
def qualify(body: QualificationInput, request: Request):
    return call(service.enqueue, request.app.state.engine, body.deployment_id, body.mode,
        body.max_probes, actor(), body.fixture_scenario)


@router.get("/qualifications/{run_id}")
def qualification(run_id: str, request: Request):
    return call(service.detail, request.app.state.engine, run_id)


@router.post("/pinned-models", status_code=201)
def publish(body: PublishInput, request: Request):
    return call(routes.publish, request.app.state.engine, body.model_id, body.verdict_id,
        body.expected_route_version, actor())


@router.post("/pinned-models/{model_id:path}/rollback", status_code=201)
def rollback(model_id: str, body: RollbackInput, request: Request):
    return call(routes.publish, request.app.state.engine, model_id, None, body.expected_route_version,
        actor(), rollback_of=body.snapshot_id)


@router.get("/pinned-models/{model_id:path}")
def route(model_id: str, request: Request):
    return call(routes.read_route, request.app.state.engine, model_id)


@router.get("/route-snapshots/{snapshot_id}")
def snapshot(snapshot_id: str, request: Request):
    return call(routes.snapshot_detail, request.app.state.engine, snapshot_id)
