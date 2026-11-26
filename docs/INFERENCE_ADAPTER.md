# Step 4: deployment and one-send NVIDIA adapter

## Scope and current prerequisites

The selected first provider is NVIDIA hosted. The current database has live
official catalog evidence for `meta/llama-3.3-70b-instruct`, chat and provider
base URL `https://integrate.api.nvidia.com/v1`. The model-specific official
contract is:
https://docs.api.nvidia.com/nim/reference/meta-llama-3_3-70b-instruct-infer

That contract documents POST `/v1/chat/completions`, optional first system
message, user text, `max_tokens` (1–4096) and non-streaming JSON. No inference
credential was present at the start of this step. Metadata evidence does not
establish account access, pricing, quota, backend model identity or live
deployment qualification. SiliconFlow CN remains blocked as recorded by 3C.

## Data and generation identity

Migration `0005_deployments` creates `upstream_accounts`, `secret_refs`,
`deployments`, `deployment_versions` and `inference_attempts`. PostgreSQL stores
only `env:NVIDIA_API_KEY` and the environment variable name, never its value.
Configure creates a new immutable deployment version when its account,
offering, endpoint, region, adapter or evidence changes; identical configuration
is idempotent. An attempt references the exact deployment ID/generation, so a
later change cannot rewrite its target. Disabling is a current deployment flag.

The CLI configure command requires current official **live** model/task and
endpoint evidence. A fixture cannot configure a live target. The same evidence
gate is rechecked before runtime invocation. The endpoint is pinned to
`https://integrate.api.nvidia.com/v1/chat/completions`; userinfo, query strings,
alternate paths/hosts, HTTP, redirect destinations and community URLs cannot
receive the credential. Secret resolution happens at invoke time.

## Adapter contract

`await invoke(target, TextInput(text, instructions, max_output_tokens), deadline)`
uses an absolute `time.monotonic()` deadline. It returns normalized output,
requested/returned model, request ID, latency, usage, result code and provider
outcome. Instructions become the first system message; text becomes user
content; max_output_tokens becomes max_tokens. A returned different model is
reported as `model_mismatch` with its value retained, never silently substituted.
Missing usage is unknown/null, including malformed or inconsistent token counts.

HTTPX is the only inference library. Its transport has retries=0; redirects,
auth flows, environment proxies and streaming output are disabled. The async
timeout wraps client creation, send and the complete response read, including
slow drip responses. The body is capped at 1 MiB. No database transaction is
held across network I/O. There is no fallback, replay or poll after 202.

Result examples:

- Pre-send invalid target/input, disabled deployment, expired deadline or missing
  credential: provider_outcome=not_sent, client_send_count=0.
- 401/403: authentication_error; 429: rate_limited. Credit remains unknown;
  generic status/error text never proves exhausted credit.
- 5xx, invalid body, pending 202, redirect, transport failure or timeout after
  send starts: provider_outcome=unknown. Provider may have processed the request.
- Valid 200: provider_outcome=succeeded and usage is from upstream when present.

`client_send_count` is the number of send calls initiated by the adapter, not a
claim about provider receipt, processing or billing. Only a controlled upstream
counter measures actual receipt in tests. Even a received request does not prove
billing. When a process dies after its attempt is committed, its unfinished
attempt remains `interrupted_unknown`/unknown; it is never automatically replayed.

## Local operator CLI

The operator CLI runs through Docker access, separate from agent/admin HTTP
credentials. It never takes a provider key as a command-line argument.

```powershell
docker compose up --build -d

# Use a live-confirmed offering from the existing catalog.
$offeringId = 'cca81531ea2fcd3f747924245a7dcfdce28a1bada57db9d4724fbd543d3317f5'
docker compose exec -T agentic-gateway-api python -m agentic_gateway.providers.inference.cli configure nvidia-text-local --offering-id $offeringId --account-id operator-local
docker compose exec -T agentic-gateway-api python -m agentic_gateway.providers.inference.cli show nvidia-text-local
```

`show` prints only credential presence, never its value. Supply `NVIDIA_API_KEY`
locally via `.env` or the Compose process environment, then recreate the API
service to receive the changed environment. Preserve existing .env values. Check
the specific account's price/quota before opting in to one live probe:

```powershell
# This command opts into ONE invocation, with synthetic text and 16 output tokens.
docker compose exec -T agentic-gateway-api python -m agentic_gateway.providers.inference.cli probe nvidia-text-local --live --ack-account-terms --deadline-seconds 10

# Inspect sanitized durable report; use attempt_id returned by probe.
docker compose exec -T agentic-gateway-api python -m agentic_gateway.providers.inference.cli attempt '<attempt-id>'
docker compose exec -T agentic-gateway-api python -m agentic_gateway.providers.inference.cli set-enabled nvidia-text-local false
```

Probe returns 0 for valid success and 2 for failure/preflight. Omitting either
--live or --ack-account-terms sends zero requests. The report's output_text is
returned to the caller; audit storage and logs deliberately omit it, along with
prompts, auth headers, raw error bodies and credentials. Logs contain only
attempt/deployment/generation/latency/result. Network request IDs are bounded
and sanitized; credential echoes are rejected.

## Reproduce offline acceptance

```powershell
docker build --target test -t agentic-gateway:test .
docker run --rm agentic-gateway:test python -m pytest -p no:cacheprovider tests/test_inference_adapter.py
./scripts/test-registry.ps1

# Same adapter, real HTTPS loopback server and independent received counter.
docker run --rm agentic-gateway:test python /app/tests/inference_probe.py --scenario timeout --deadline-seconds 1
docker run --rm agentic-gateway:test python /app/tests/inference_probe.py --scenario ok --deadline-seconds 2
```

The self-signed TLS key/certificate under tests/fixtures/tls is generated during
the Docker test-target build and excluded from Git and the build context. It is
disposable test material, not a provider credential; only the test image contains it. The
test-only transport remaps the already validated NVIDIA target to loopback and
trusts that certificate explicitly. Production CLI/config cannot select it.
No host Python, provider key or external inference endpoint is used in tests.

The tests cover contract/output/usage/IDs, 200 without usage, invalid 200, auth,
429/5xx, receive-before-timeout, slow drip, zero-send preflight and second-host
redirect count=0. PostgreSQL tests exercise actual operator CLI logic, generation
history, persisted sanitized attempts, fixture/live evidence isolation and no
checked-out database connection during the actual send. Qualification, agent
keys, `/v1/responses`, quota policy and fallback remain later steps.
