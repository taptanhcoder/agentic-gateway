"""NVIDIA chat adapter: one HTTP send, no redirects, absolute total deadline."""
import asyncio
from dataclasses import dataclass, field
import json
import math
import os
import re
import time

import httpx

ADAPTER_VERSION = "nvidia-httpx-text-v1"
ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
CONTRACT_LOCATOR = "https://docs.api.nvidia.com/nim/reference/meta-llama-3_3-70b-instruct-infer"
SUPPORTED_MODELS = frozenset({"meta/llama-3.3-70b-instruct"})


@dataclass(frozen=True)
class Target:
    deployment_id: str
    generation: int
    provider: str
    endpoint: str
    model: str
    secret_env: str
    enabled: bool = True
    adapter_version: str = ADAPTER_VERSION
    mode: str = "live"


@dataclass(frozen=True)
class TextInput:
    text: str = field(repr=False)
    instructions: str | None = field(default=None, repr=False)
    max_output_tokens: int = 32


def validate_target(target):
    return (target.provider == "nvidia" and target.endpoint == ENDPOINT
        and target.secret_env == "NVIDIA_API_KEY" and target.adapter_version == ADAPTER_VERSION
        and isinstance(target.model,str) and target.model in SUPPORTED_MODELS
        and type(target.generation) is int and target.generation > 0
        and target.mode in {"fixture","live"})


def valid_input(value):
    valid = (isinstance(value,TextInput) and isinstance(value.text,str) and 0 < len(value.text) <= 100000
        and (value.instructions is None or isinstance(value.instructions,str) and len(value.instructions) <= 100000)
        and type(value.max_output_tokens) is int and 1 <= value.max_output_tokens <= 4096)
    if not valid:
        return False
    try:
        value.text.encode("utf-8")
        if value.instructions:
            value.instructions.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def usage_from(body):
    usage = body.get("usage")
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    if not isinstance(usage,dict) or not all(type(usage.get(key)) is int and usage[key] >= 0 for key in keys):
        return {"status":"unknown", **{key:None for key in keys}}
    if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
        return {"status":"unknown", **{key:None for key in keys}}
    return {"status":"upstream", **{key:usage[key] for key in keys}}


def safe_identifier(value, secret, inputs):
    if (not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", value)
        or secret in value or any(text and text in value for text in (inputs.text,inputs.instructions))):
        return None
    return value


async def invoke(target: Target, input: TextInput, deadline: float, *, _transport=None):
    """deadline is absolute time.monotonic(). _transport is test-only dependency injection.

    send_started is a conservative client-side boundary, not proof of receipt,
    processing or billing. Test servers establish their own received counter.
    """
    start = time.monotonic()
    sent = 0
    status = None
    request_id = None
    unknown_usage = {"status":"unknown", "prompt_tokens":None, "completion_tokens":None, "total_tokens":None}

    def result(code, outcome, **data):
        return {"result_code":code, "provider_outcome":outcome, "send_started":bool(sent),
            "client_send_count":sent, "http_status":status, "requested_model":target.model,
            "returned_model":None, "upstream_request_id":request_id,
            "latency_ms":round((time.monotonic()-start)*1000,2), "usage":unknown_usage,
            "credit_status":"unknown", "output_text":None, **data}

    if not validate_target(target):
        return result("invalid_target", "not_sent")
    if not target.enabled:
        return result("deployment_disabled", "not_sent")
    if not valid_input(input):
        return result("invalid_input", "not_sent")
    if not isinstance(deadline,(int,float)) or not math.isfinite(deadline) or deadline <= start:
        return result("deadline_expired", "not_sent")
    secret = os.getenv(target.secret_env, "")
    if not secret:
        return result("credential_missing", "not_sent")
    if not re.fullmatch(r"[!-~]+", secret):
        return result("credential_invalid", "not_sent")
    messages = []
    if input.instructions:
        messages.append({"role":"system", "content":input.instructions})
    messages.append({"role":"user", "content":input.text})
    payload = {"model":target.model, "messages":messages, "max_tokens":input.max_output_tokens, "stream":False}
    try:
        loop = asyncio.get_running_loop()
        async with asyncio.timeout_at(loop.time() + max(0, deadline-time.monotonic())):
            # Default transport retries=0, auth=None, redirects disabled, no env proxy.
            transport = _transport if _transport is not None else httpx.AsyncHTTPTransport(retries=0)
            async with httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False,
                timeout=httpx.Timeout(max(0.001,deadline-time.monotonic())), auth=None) as client:
                if time.monotonic() >= deadline:
                    return result("deadline_expired", "not_sent")
                sent = 1
                async with client.stream("POST", target.endpoint,
                    headers={"Authorization":"Bearer " + secret,"Accept":"application/json"}, json=payload) as response:
                    status = response.status_code
                    request_id = safe_identifier(response.headers.get("x-request-id") or
                        response.headers.get("x-nv-request-id"), secret, input)
                    if status in {401,403}:
                        return result("authentication_error", "rejected")
                    if status == 429:
                        return result("rate_limited", "rejected")
                    if 500 <= status <= 599:
                        return result("upstream_error", "unknown")
                    if 300 <= status <= 399:
                        return result("redirect_rejected", "unknown")
                    if status == 202:
                        return result("upstream_pending", "unknown")
                    if status != 200:
                        return result("upstream_rejected", "rejected")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > 1024*1024:
                            return result("response_too_large", "unknown")
                        data.extend(chunk)
                    try:
                        body = json.loads(data)
                        choices = body["choices"]
                        if (not isinstance(choices,list) or len(choices) != 1 or
                            choices[0].get("index") != 0 or choices[0].get("finish_reason") not in {"stop","length"}):
                            raise ValueError()
                        message = choices[0]["message"]
                        if message.get("role") != "assistant" or not isinstance(message.get("content"),str) or message.get("tool_calls"):
                            raise ValueError()
                        output = message["content"]
                        if secret in output:
                            return result("sensitive_output_rejected", "unknown")
                        returned = body.get("model")
                        if returned is not None and (not isinstance(returned,str) or not returned):
                            raise ValueError()
                    except (ValueError,KeyError,TypeError,AttributeError):
                        return result("invalid_response", "unknown")
                    if time.monotonic() >= deadline:
                        return result("timeout", "unknown")
                    if returned is not None and returned != target.model:
                        return result("model_mismatch", "unknown", returned_model=safe_identifier(returned,secret,input))
                    return result("ok", "succeeded", output_text=output, usage=usage_from(body),
                        returned_model=safe_identifier(returned,secret,input), finish_reason=choices[0]["finish_reason"])
    except (TimeoutError,httpx.TimeoutException):
        return result("timeout", "unknown" if sent else "not_sent")
    except httpx.RequestError:
        return result("transport_error", "unknown" if sent else "not_sent")
