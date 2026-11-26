import asyncio
from dataclasses import replace
import json
import time

import pytest

from agentic_gateway.providers.inference.adapter import ENDPOINT, Target, TextInput, invoke
from inference_upstream import LoopbackTransport, upstream

TARGET=Target("fixture-deployment",1,"nvidia",ENDPOINT,"meta/llama-3.3-70b-instruct","NVIDIA_API_KEY",mode="fixture")
INPUT=TextInput("synthetic-user-input","synthetic-instructions",16)


def call(server,target=TARGET,seconds=2,input=INPUT):
    return asyncio.run(invoke(target,input,time.monotonic()+seconds,_transport=LoopbackTransport(server.url)))


@pytest.fixture(autouse=True)
def credential(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY","fixture-secret-token")


def test_valid_response_preserves_mapping_usage_request_id():
    with upstream() as server:
        result=call(server)
        assert server.count==result["client_send_count"]==1
        assert server.bodies==[{"model":TARGET.model,"messages":[{"role":"system","content":INPUT.instructions},
            {"role":"user","content":INPUT.text}],"max_tokens":16,"stream":False}]
    assert result["result_code"]=="ok" and result["output_text"]=="READY"
    assert result["requested_model"]==result["returned_model"]==TARGET.model
    assert result["upstream_request_id"]=="fixture-request-123"
    assert result["usage"]=={"status":"upstream","prompt_tokens":12,"completion_tokens":1,"total_tokens":13}


@pytest.mark.parametrize("scenario",["no_usage","bad_usage"])
def test_missing_or_invalid_usage_is_unknown_not_zero(scenario):
    with upstream(scenario) as server:
        result=call(server)
        assert server.count==1 and result["result_code"]=="ok"
        assert result["usage"]=={"status":"unknown","prompt_tokens":None,"completion_tokens":None,"total_tokens":None}


@pytest.mark.parametrize("scenario,code",[("bad_body","invalid_response"),("invalid_json","invalid_response"),
    ("401","authentication_error"),("403","authentication_error"),("429","rate_limited"),
    ("500","upstream_error"),("pending","upstream_pending"),("large","response_too_large")])
def test_error_contract_is_one_send_without_raw_body(scenario,code):
    with upstream(scenario) as server:
        result=call(server)
        assert server.count==result["client_send_count"]==1
    assert result["result_code"]==code and result["credit_status"]=="unknown"
    assert "RAW-ERROR-SECRET-PROMPT" not in json.dumps(result)


@pytest.mark.parametrize("scenario",["timeout","slow_drip"])
def test_total_deadline_after_upstream_receives_has_unknown_outcome(scenario):
    with upstream(scenario) as server:
        started=time.monotonic()
        result=call(server,seconds=0.2)
        elapsed=time.monotonic()-started
        assert server.received.is_set() and server.count==1
        assert elapsed<0.65
        assert result["result_code"]=="timeout" and result["provider_outcome"]=="unknown"
        assert result["usage"]["status"]=="unknown" and result["client_send_count"]==1


@pytest.mark.parametrize("mutation,code",[
    ({"endpoint":"https://untrusted.example/v1/chat/completions"},"invalid_target"),
    ({"endpoint":ENDPOINT+"?key=bad"},"invalid_target"),
    ({"endpoint":"http://integrate.api.nvidia.com/v1/chat/completions"},"invalid_target"),
    ({"enabled":False},"deployment_disabled"),({"adapter_version":"different"},"invalid_target"),
    ({"secret_env":"OPERATOR_API_KEY"},"invalid_target")])
def test_invalid_or_disabled_target_never_sends(mutation,code):
    with upstream() as server:
        result=call(server,target=replace(TARGET,**mutation))
        assert server.count==result["client_send_count"]==0
        assert result["result_code"]==code and result["provider_outcome"]=="not_sent"


def test_missing_secret_expired_deadline_invalid_input_never_send(monkeypatch):
    with upstream() as server:
        monkeypatch.delenv("NVIDIA_API_KEY")
        assert call(server)["result_code"]=="credential_missing"
        monkeypatch.setenv("NVIDIA_API_KEY","fixture-secret-token")
        assert call(server,seconds=-1)["result_code"]=="deadline_expired"
        assert call(server,input=TextInput("test",max_output_tokens=True))["result_code"]=="invalid_input"
        assert call(server,input=TextInput("\ud800"))["result_code"]=="invalid_input"
        monkeypatch.setenv("NVIDIA_API_KEY","invalid-credential-\n")
        assert call(server)["result_code"]=="credential_invalid"
        assert server.count==0


def test_redirect_never_reaches_second_host():
    with upstream("redirect") as first,upstream() as second:
        first.redirect_url=second.url.replace("127.0.0.1","localhost")
        result=call(first)
        assert first.count==1 and second.count==0
        assert result["result_code"]=="redirect_rejected"


def test_returned_model_is_not_silently_replaced():
    with upstream("wrong_model") as server:
        result=call(server)
        assert result["requested_model"]==TARGET.model and result["returned_model"]=="different/model"
        assert result["result_code"]=="model_mismatch"
    with upstream("missing_model") as server:
        assert call(server)["returned_model"] is None


def test_credential_echo_is_rejected_and_not_returned():
    with upstream("secret_echo") as server:
        result=call(server)
        assert result["result_code"]=="sensitive_output_rejected"
        assert "fixture-secret-token" not in json.dumps(result)
