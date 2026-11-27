"""Immutable target generations; database stores secret references, never keys."""
import logging
from uuid import uuid4

from sqlalchemy import Boolean, Column, BigInteger, DateTime, MetaData, Table, Text, func, insert, select, update
from sqlalchemy.dialects.postgresql import JSONB

from agentic_gateway.catalog.reconciliation import offering_evidence
from .adapter import ADAPTER_VERSION, CONTRACT_LOCATOR, ENDPOINT, SUPPORTED_MODELS, Target, invoke

metadata = MetaData()
accounts = Table("upstream_accounts",metadata,Column("id",Text),Column("provider",Text),Column("label",Text),Column("enabled",Boolean))
secrets = Table("secret_refs",metadata,Column("id",Text),Column("env_name",Text))
deployments = Table("deployments",metadata,Column("id",Text),Column("generation",BigInteger),Column("enabled",Boolean),Column("qualification_epoch",BigInteger))
versions = Table("deployment_versions",metadata,*[Column(name,Text) for name in (
    "deployment_id","offering_id","account_id","secret_ref_id","provider","endpoint","upstream_model","region","adapter_version")],
    Column("generation",BigInteger),Column("evidence",JSONB),Column("created_at",DateTime(timezone=True)))
attempts = Table("inference_attempts",metadata,Column("id",Text),Column("deployment_id",Text),Column("generation",BigInteger),
    Column("mode",Text),Column("started_at",DateTime(timezone=True)),Column("finished_at",DateTime(timezone=True)),
    Column("result_code",Text),Column("provider_outcome",Text),Column("report",JSONB))


class DeploymentError(Exception):
    pass


def verified_evidence(engine, offering_id, *, fixture=False):
    proof = offering_evidence(engine, offering_id)
    offering = proof["offering"]
    if (offering["provider"] != "nvidia" or offering["task"] != "chat" or offering["upstream_model"] not in SUPPORTED_MODELS
        or offering["base_url"] != "https://integrate.api.nvidia.com/v1"):
        raise DeploymentError("offering_target_not_supported")
    allowed = {"provider_confirmed", "fixture_confirmed"} if fixture else {"provider_confirmed"}
    if any(proof["fields"][name]["status"] not in allowed for name in ("upstream_model","task")):
        raise DeploymentError("official_model_task_evidence_missing")
    base = proof["fields"]["base_url"]
    if base["status"] == "conflicting" or not any(c["origin"] == "official" and c["current"] and
        (fixture or c["mode"] == "live") and c["value"] == offering["base_url"] for c in base["claims"]):
        raise DeploymentError("official_endpoint_evidence_missing")
    return offering, {"model_observations":[row["id"] for row in proof["official_evidence"]
        if row["current"] and row["match_status"] == "matched"], "endpoint_contract_locator":CONTRACT_LOCATOR,
        "endpoint":ENDPOINT,"evidence_mode":"fixture" if fixture else "live"}


def configure(engine, deployment_id, offering_id, account_id, *, _fixture=False):
    if not deployment_id or not account_id or len(deployment_id)>120 or len(account_id)>120:
        raise DeploymentError("invalid_deployment_configuration")
    offering, evidence = verified_evidence(engine, offering_id, fixture=_fixture)
    secret_id = "env:NVIDIA_API_KEY"
    with engine.begin() as connection:
        # Serialize creation/update of the same logical deployment, including first insert.
        from sqlalchemy import text
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key":"deployment:"+deployment_id})
        account = connection.execute(select(accounts).where(accounts.c.id == account_id)).mappings().one_or_none()
        if account and account["provider"] != offering["provider"]:
            raise DeploymentError("account_provider_conflict")
        if account is None:
            connection.execute(insert(accounts).values(id=account_id,provider=offering["provider"],label=account_id))
        secret = connection.execute(select(secrets).where(secrets.c.id == secret_id)).mappings().one_or_none()
        if secret and secret["env_name"] != "NVIDIA_API_KEY":
            raise DeploymentError("secret_reference_conflict")
        if secret is None:
            connection.execute(insert(secrets).values(id=secret_id,env_name="NVIDIA_API_KEY"))
        current = connection.execute(select(deployments).where(deployments.c.id == deployment_id).with_for_update()).mappings().one_or_none()
        target = dict(deployment_id=deployment_id,offering_id=offering_id,account_id=account_id,secret_ref_id=secret_id,
            provider=offering["provider"],endpoint=ENDPOINT,upstream_model=offering["upstream_model"],
            region=offering["region"],adapter_version=ADAPTER_VERSION,evidence=evidence)
        if current:
            previous = connection.execute(select(versions).where(versions.c.deployment_id == deployment_id,
                versions.c.generation == current["generation"])).mappings().one()
            if all(previous[key] == value for key,value in target.items()):
                return {"deployment_id":deployment_id,"generation":current["generation"],"enabled":current["enabled"]}
            generation = current["generation"]+1
            connection.execute(update(deployments).where(deployments.c.id==deployment_id).values(generation=generation))
        else:
            generation=1
            connection.execute(insert(deployments).values(id=deployment_id,generation=generation,enabled=True))
        connection.execute(insert(versions).values(**target,generation=generation))
    return {"deployment_id":deployment_id,"generation":generation,"enabled":current["enabled"] if current else True}


def resolve(engine, deployment_id, *, _fixture=False):
    with engine.connect() as connection:
        row = connection.execute(select(versions,deployments.c.enabled,secrets.c.env_name)
            .select_from(deployments.join(versions,(deployments.c.id==versions.c.deployment_id)&
                (deployments.c.generation==versions.c.generation)).join(secrets,secrets.c.id==versions.c.secret_ref_id))
            .where(deployments.c.id==deployment_id)).mappings().one_or_none()
        if row is None:
            raise DeploymentError("deployment_not_found")
        row=dict(row)
    return Target(deployment_id,row["generation"],row["provider"],row["endpoint"],row["upstream_model"],
        row["env_name"],row["enabled"],row["adapter_version"],"fixture" if _fixture else "live"), row


def set_enabled(engine,deployment_id,enabled):
    with engine.begin() as connection:
        current = connection.execute(select(deployments).where(deployments.c.id==deployment_id).with_for_update()).mappings().one_or_none()
        if current is None:
            raise DeploymentError("deployment_not_found")
        if current["enabled"] != enabled:
            connection.execute(update(deployments).where(deployments.c.id==deployment_id).values(
                enabled=enabled, qualification_epoch=current["qualification_epoch"]+1))


def persisted_report(report):
    # Text is returned to the caller, but prompts, credentials and outputs never enter audit storage/logs.
    return {key:value for key,value in report.items() if key != "output_text"}


async def invoke_deployment(engine,deployment_id,input,deadline,*,_fixture=False,_transport=None):
    target, version = resolve(engine,deployment_id,_fixture=_fixture)
    attempt_id=str(uuid4())
    initial={"attempt_id":attempt_id,"deployment_id":deployment_id,"generation":target.generation,
        "mode":target.mode,"result_code":"interrupted_unknown","provider_outcome":"unknown"}
    with engine.begin() as connection:
        connection.execute(insert(attempts).values(id=attempt_id,deployment_id=deployment_id,generation=target.generation,
            mode=target.mode,report=initial))
    # No database transaction or connection is held while the adapter awaits network I/O.
    evidence_error=None
    if target.enabled:
        try:
            verified_evidence(engine,version["offering_id"],fixture=_fixture)
        except (DeploymentError,LookupError):
            evidence_error="deployment_evidence_not_current"
    if evidence_error:
        report={**initial,"result_code":evidence_error,"provider_outcome":"not_sent","client_send_count":0,
            "send_started":False,"latency_ms":0,"usage":{"status":"unknown"},"output_text":None}
    else:
        report={**initial,**await invoke(target,input,deadline,_transport=_transport)}
    safe=persisted_report(report)
    with engine.begin() as connection:
        connection.execute(update(attempts).where(attempts.c.id==attempt_id).values(
            finished_at=func.clock_timestamp(),result_code=report["result_code"],provider_outcome=report["provider_outcome"],report=safe))
    logging.getLogger(__name__).info("inference_attempt %s",json_log(safe))
    return report


def json_log(report):
    import json
    return json.dumps({key:report.get(key) for key in
        ("attempt_id","deployment_id","generation","latency_ms","result_code","provider_outcome")})
