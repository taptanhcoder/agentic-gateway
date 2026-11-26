import asyncio
from dataclasses import replace
import json
from pathlib import Path
import time

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from agentic_gateway.catalog.registry import enqueue
from agentic_gateway.catalog.schema import sources
from agentic_gateway.catalog.sync import claim_job,process_job
from agentic_gateway.providers.catalog import connectors
from agentic_gateway.providers.catalog.seeds import seed_providers
from agentic_gateway.providers.catalog.transport import Document,NVIDIA
from agentic_gateway.providers.inference import cli
from agentic_gateway.providers.inference.adapter import TextInput
from agentic_gateway.providers.inference.deployments import (DeploymentError,attempts,configure,
    deployments,invoke_deployment,resolve,secrets,versions,set_enabled)
from inference_upstream import upstream,LoopbackTransport


@pytest.fixture
def target_db(db,monkeypatch):
    seed_providers(db)
    with db.begin() as connection:
        connection.execute(insert(sources).values(id="fixture/github",connector="github_fixture"))
    enqueue(db,"fixture/github","r1");process_job(db,claim_job(db))
    body=(Path(__file__).parents[1]/"fixtures/nvidia-hosted.html").read_text()
    monkeypatch.setattr(connectors,"fetch_document",lambda locator,credential=None:
        Document(NVIDIA,body,mode="fixture",fetched_at="2026-11-26T08:00:00+00:00"))
    enqueue(db,"official/nvidia-hosted","latest");process_job(db,claim_job(db))
    from agentic_gateway.catalog.schema import offerings
    with db.connect() as connection:
        offering_id=connection.scalar(select(offerings.c.id).where(offerings.c.provider=="nvidia",
            offerings.c.upstream_model=="meta/llama-3.3-70b-instruct"))
    monkeypatch.setenv("NVIDIA_API_KEY","fixture-secret-token")
    configure(db,"test-deployment",offering_id,"test-account",_fixture=True)
    return db,offering_id


def test_generation_history_and_secret_references(target_db):
    db,offering_id=target_db
    again=configure(db,"test-deployment",offering_id,"test-account",_fixture=True)
    assert again["generation"]==1
    changed=configure(db,"test-deployment",offering_id,"second-account",_fixture=True)
    assert changed["generation"]==2
    target,row=resolve(db,"test-deployment",_fixture=True)
    assert target.generation==2 and row["account_id"]=="second-account"
    with db.connect() as connection:
        old=connection.execute(select(versions).where(versions.c.generation==1)).mappings().one()
        assert old["account_id"]=="test-account"
        refs=connection.execute(select(secrets)).mappings().all()
        assert [row["env_name"] for row in refs]==["NVIDIA_API_KEY"]
        assert "fixture-secret-token" not in json.dumps([dict(row) for row in refs])


def test_fixture_evidence_cannot_configure_or_invoke_live(target_db):
    db,offering_id=target_db
    with pytest.raises(DeploymentError,match="official_model_task_evidence_missing"):
        configure(db,"live-deployment",offering_id,"test-account")
    with upstream() as server:
        report=asyncio.run(invoke_deployment(db,"test-deployment",TextInput("synthetic"),time.monotonic()+1,
            _transport=LoopbackTransport(server.url)))
        assert server.count==0 and report["provider_outcome"]=="not_sent"
        assert report["result_code"]=="deployment_evidence_not_current"


@pytest.mark.parametrize("scenario,code,outcome",[("ok","ok","succeeded"),("timeout","timeout","unknown"),
    ("401","authentication_error","rejected"),("429","rate_limited","rejected")])
def test_actual_operator_cli_uses_same_adapter_and_persists_clean_attempt(target_db,capfd,caplog,scenario,code,outcome):
    db,_=target_db
    caplog.set_level("INFO",logger="agentic_gateway.providers.inference.deployments")
    capfd.readouterr()
    with upstream(scenario) as server:
        # Successful CLI probes include DB/evidence setup and are not latency tests.
        # Keep a separate tight deadline for the receive-then-delay timeout case.
        deadline_seconds="0.5" if scenario=="timeout" else "2"
        transport=LoopbackTransport(server.url)
        start=time.monotonic()
        exit_code=cli.main(["probe","test-deployment","--live","--ack-account-terms","--deadline-seconds",deadline_seconds],
            _fixture=True,_transport=transport)
        elapsed=time.monotonic()-start
        assert server.received.is_set() and server.count==1
    report=json.loads(capfd.readouterr().out.strip().splitlines()[-1])
    assert exit_code==(0 if code=="ok" else 2)
    assert report["result_code"]==code and report["provider_outcome"]==outcome
    assert report["generation"]==1 and report["mode"]=="fixture"
    if scenario=="timeout":
        assert elapsed<1.2  # upstream delays 2 seconds, so this still proves cancellation
    with db.connect() as connection:
        persisted=connection.scalar(select(attempts.c.report).where(attempts.c.id==report["attempt_id"]))
        assert persisted["provider_outcome"]==outcome
        assert "output_text" not in persisted
        assert "fixture-secret-token" not in json.dumps(persisted)
        assert "Reply with the word READY" not in json.dumps(persisted)
    assert "fixture-secret-token" not in caplog.text and "Reply with the word READY" not in caplog.text
    assert report["attempt_id"] in caplog.text and report["result_code"] in caplog.text


def test_no_live_flag_missing_secret_disabled_and_expired_are_zero_send(target_db,capfd,monkeypatch):
    db,_=target_db
    capfd.readouterr()
    with upstream() as server:
        assert cli.main(["probe","test-deployment"],_fixture=True,_transport=LoopbackTransport(server.url))==2
        assert json.loads(capfd.readouterr().out)["result_code"]=="live_probe_not_authorized"
        monkeypatch.delenv("NVIDIA_API_KEY")
        report=asyncio.run(invoke_deployment(db,"test-deployment",TextInput("synthetic"),time.monotonic()+1,
            _fixture=True,_transport=LoopbackTransport(server.url)))
        assert report["result_code"]=="credential_missing"
        monkeypatch.setenv("NVIDIA_API_KEY","fixture-secret-token")
        set_enabled(db,"test-deployment",False)
        report=asyncio.run(invoke_deployment(db,"test-deployment",TextInput("synthetic"),time.monotonic()+1,
            _fixture=True,_transport=LoopbackTransport(server.url)))
        assert report["result_code"]=="deployment_disabled"
        set_enabled(db,"test-deployment",True)
        report=asyncio.run(invoke_deployment(db,"test-deployment",TextInput("synthetic"),time.monotonic()-1,
            _fixture=True,_transport=LoopbackTransport(server.url)))
        assert report["result_code"]=="deadline_expired"
        assert server.count==0


def test_network_has_no_checked_out_db_transaction_and_attempt_precedes_send(target_db):
    db,_=target_db
    class CheckTransport(LoopbackTransport):
        async def handle_async_request(self,request):
            assert db.pool.checkedout()==0
            with db.connect() as connection:
                row=connection.execute(select(attempts)).mappings().one()
                assert row["provider_outcome"]=="unknown" and row["finished_at"] is None
            return await super().handle_async_request(request)
    with upstream() as server:
        result=asyncio.run(invoke_deployment(db,"test-deployment",TextInput("synthetic"),time.monotonic()+2,
            _fixture=True,_transport=CheckTransport(server.url)))
    assert result["result_code"]=="ok"


def test_target_change_retains_prior_generation_attempt(target_db):
    db,offering_id=target_db
    with upstream() as server:
        first=asyncio.run(invoke_deployment(db,"test-deployment",TextInput("synthetic"),time.monotonic()+1,
            _fixture=True,_transport=LoopbackTransport(server.url)))
    configure(db,"test-deployment",offering_id,"second-account",_fixture=True)
    with db.connect() as connection:
        assert connection.scalar(select(attempts.c.generation).where(attempts.c.id==first["attempt_id"]))==1
        assert connection.scalar(select(deployments.c.generation))==2
