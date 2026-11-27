# Step 5: deployment qualification and pinned routes

This module belongs to Gateway and reuses its inference adapter and PostgreSQL
jobs queue. It does not import the SAE qualification lab or its scores.

## Text policy

Migration `0006_qualifications` installs profile `text-v1` and suite
`text-smoke-v1`. The policy contains two required synthetic cases: a short
greeting and a short response with instructions. It checks a valid adapter
envelope, nonempty text, completion within the probe deadline, and recorded
usage provenance. There is no exact-match criterion. Missing usage stays
unknown and does not fail basic text capability.

The profile allows at most 2 probes, 16 output tokens per probe, 5 seconds per
probe, 12 seconds for the run, and a 3600-second verdict TTL. It makes no
throughput, reliability, price or remaining-quota claim. A request can lower the
probe budget to 1; this leaves the two-case suite inconclusive. No layer retries.

The run snapshots deployment generation, revocation epoch, account, adapter,
profile/suite revisions, complete policy spec, mode and requestor at enqueue.
Changing the active deployment during I/O cannot change that run's target.
An old generation can finish eligible as historical evidence, but cannot publish
a route for the current generation. Changing the active policy invalidates old
approvals. Policy/revision changes currently require a reviewed migration;
there is no policy editing endpoint in this step.

## Execution and evidence

`POST /admin/qualifications` returns 202 with `run_id` and `job_id` in one
transaction. `GET /admin/qualifications/{id}` returns execution status,
case observations, attempt reports, verdict and expiration. All endpoints use
the existing operator Bearer credential. Job status denotes execution:
`succeeded` can contain an inconclusive or ineligible verdict. The run exposes
`queued`, `running`, `completed` or `blocked` separately from its verdict.

Fixture mode uses a local HTTPX mock with synthetic responses; it never sends
to NVIDIA and does not need a provider credential. Its `client_send_count`
counts calls to this mock adapter transport. Test-only loopback TLS transports
also count server receipts. Neither count proves provider processing or billing.

Live mode requires current official evidence, a valid secret, an enabled
deployment/account and explicit budget configuration. Missing prerequisites
produce blocked/inconclusive with zero reserved attempts. A timeout or 429
produces inconclusive; invalid envelopes or empty text produce ineligible.
Each result retains its case, attempt ID, latency, usage or unknown, sanitized
error class, and UTC timestamps. Prompts and output text are not persisted.

The worker claims the shared `jobs` table using `FOR UPDATE SKIP LOCKED`, bumps
its fencing token and leases for `JOB_LEASE_SECONDS` (default 30). The heartbeat
renews every third of that duration. A short transaction reserves each attempt
and consumes its budget before network I/O; no DB connection stays checked out
while awaiting a response. Every completion checks ownership and the server's
lease clock inside its write transaction.

If a worker is reclaimed after a reservation, the successor finalizes the run
as inconclusive/interrupted_unknown without replaying it. Pending attempt usage
and send count remain unknown: reservation does not prove transmission. The old
worker cannot update observations, attempts, verdict or job status. SIGTERM
cancels in-flight I/O, preserves uncertainty and stops the worker cleanly. A
new explicit qualification request is required to try again.

## Publish, read and rollback

`POST /admin/pinned-models` accepts `model_id`, `verdict_id`, and
`expected_route_version` (0 for first publish). Only a completed, eligible,
unexpired **live** verdict for the active generation, account, adapter and
policy can publish. Fixture-eligible verdicts are rejected with 409.

A PostgreSQL transaction takes a per-pin advisory lock (including when the pin
does not exist), checks the expected version, locks current deployment/account
and policy state, creates an immutable route snapshot and swaps the pointer.
Unique constraints and an explicit version predicate back up the lock. Two
operators using one version get one success and one 409, with no orphan
snapshot. Expiration is rechecked just before commit.

`GET /admin/pinned-models/{model_id}` validates current eligibility again and
returns 409 when unusable. `GET /admin/route-snapshots/{id}` preserves historical
audit access, including `usable=false` and its reason. Rollback is
`POST /admin/pinned-models/{model_id}/rollback` with `snapshot_id` and
`expected_route_version`; it creates a new version and applies the same current
eligibility checks. Disabling/re-enabling a deployment increments a revocation
epoch so old approvals cannot revive. Account revocation blocks routes as well.

`published_by` and `requested_by` record a SHA-256 fingerprint of the configured
operator key. In this single-operator MVP this identifies the operator credential,
not separate humans sharing that key. No key value is returned. Agent keys and
inference serving remain outside step 5.

## PowerShell: reproduce locally

```powershell
docker compose up --build -d
$operatorKey = $env:OPERATOR_API_KEY
if (-not $operatorKey) {
    $operatorKey = ((Get-Content .env | Where-Object { $_ -match '^OPERATOR_API_KEY=' }) -replace '^OPERATOR_API_KEY=', '').Trim()
}
$headers = @{ Authorization = "Bearer $operatorKey" }
$baseUrl = 'http://127.0.0.1:8000' # use the configured GATEWAY_PORT
$deploymentId = 'nvidia-text-local' # use an existing deployment with catalog evidence
$job = Invoke-RestMethod -Method Post "$baseUrl/admin/qualifications" -Headers $headers -ContentType 'application/json' -Body (@{
    deployment_id=$deploymentId; mode='fixture'; max_probes=2
} | ConvertTo-Json)
do {
    Start-Sleep -Milliseconds 200
    $run = Invoke-RestMethod "$baseUrl/admin/qualifications/$($job.run_id)" -Headers $headers
} while ($run.status -in @('queued','running'))
$run | ConvertTo-Json -Depth 16
```

Fixture success is reviewable evidence; publishing it must return 409:

```powershell
$pinBody = @{model_id='ag/pinned-text-01'; verdict_id=$run.verdict.id; expected_route_version=0} | ConvertTo-Json
Invoke-WebRequest -SkipHttpErrorCheck -Method Post "$baseUrl/admin/pinned-models" -Headers $headers -ContentType 'application/json' -Body $pinBody
```

For **one explicitly authorized live run**, first verify account price/quota,
configure `NVIDIA_API_KEY` in local `.env`, set
`QUALIFICATION_LIVE_ACCOUNT_ID` to the deployment's account,
`QUALIFICATION_LIVE_MAX_PROBES=2`, and
`QUALIFICATION_LIVE_ACK_ACCOUNT_TERMS=1`. Recreate the application containers
with `docker compose up -d` so they receive the environment. POST the same
qualification body with `mode='live'`. Each request authorizes a separate run
of at most two probes; there is no automatic qualification scheduler. Disable
the budget again after the intended run. The limits cap requests and output
tokens, not a guaranteed monetary amount. Do not enable before checking terms.

After configuring and authorizing that budget, explicitly enqueue and poll the
live run (do not reuse the fixture run variable):

```powershell
$job = Invoke-RestMethod -Method Post "$baseUrl/admin/qualifications" -Headers $headers -ContentType 'application/json' -Body (@{
    deployment_id=$deploymentId; mode='live'; max_probes=2
} | ConvertTo-Json)
do {
    Start-Sleep -Milliseconds 200
    $run = Invoke-RestMethod "$baseUrl/admin/qualifications/$($job.run_id)" -Headers $headers
} while ($run.status -in @('queued','running'))
if ($run.verdict.decision -ne 'eligible' -or $run.mode -ne 'live') { throw 'No eligible live verdict to publish' }
```

When this live run is eligible, publish using its verdict and trace the result:

```powershell
$pinBody = @{model_id='ag/pinned-text-01'; verdict_id=$run.verdict.id; expected_route_version=0} | ConvertTo-Json
$pin = Invoke-RestMethod -Method Post "$baseUrl/admin/pinned-models" -Headers $headers -ContentType 'application/json' -Body $pinBody
$route = Invoke-RestMethod "$baseUrl/admin/pinned-models/ag/pinned-text-01" -Headers $headers
$snapshot = Invoke-RestMethod "$baseUrl/admin/route-snapshots/$($route.id)" -Headers $headers
$evidence = Invoke-RestMethod "$baseUrl/admin/qualifications/$($snapshot.run_id)" -Headers $headers
$evidence | ConvertTo-Json -Depth 16
```

Trace: pin → route snapshot (`published_by`, generation, expiration) → verdict
→ qualification run → case observations → sanitized inference attempts.

## Isolated verification

```powershell
docker build --target test -t agentic-gateway:test .
./scripts/test-registry.ps1 -TestPath tests/integration/test_qualifications.py
./scripts/test-registry.ps1
docker build -t agentic-gateway:dev .
./scripts/smoke.ps1
./scripts/smoke-catalog.ps1
```

The qualification tests include real HTTP API calls and real worker processes,
counted HTTPS responses, generation changes during response waits, stale-worker
fencing, initial/update CAS races, TTL, account/deployment revocation, rollback,
and upgrade from `0005_deployments` with existing data. Publisher live branches
are exercised using **simulated live evidence in the isolated test database**;
this is not a live provider qualification or a publishable local deployment.
Reports stay in ignored `reports/runtime/`; only test projects/volumes are removed.

The existing CI runs this file through the full PostgreSQL suite. Local results
do not change GitHub CI until the operator commits and pushes these files.

## Observed local results — 2026-11-26

- Full PostgreSQL suite: 122 passed, 0 failures/errors/skips. JUnit:
  `reports/runtime/ag-registry-7b69406d21a7.xml`.
- After tightening the total deadline to include preflight time, qualification
  rerun: 21 passed, 0 failures/errors/skips. JUnit:
  `reports/runtime/ag-registry-4e03e879f8a7.xml`.
- Runtime smoke: 8/8 passed; catalog/scheduler smoke: 5/5 passed. Both random
  projects and their test volumes were cleaned. Reports:
  `reports/runtime/ag-smoke-1eb0eb415014.json` and
  `reports/runtime/ag-catalog-9f51f2914a14.json`.
- The development stack upgraded to `0006_qualifications`; db is healthy,
  migration exited 0, API/worker/scheduler are running. Both health endpoints
  returned 200 at `http://127.0.0.1:8000`.
- HTTP fixture run `04d45c35-14b9-40e9-bd1e-84c682adc5bc` completed eligible,
  generation 1, two simulated adapter sends. Publishing its verdict returned
  409 `live_verdict_required`.
- HTTP live run `bca02192-83c9-456a-bcfe-7b1671d2025e` was blocked with
  `credential_missing`, zero attempts and zero sends. The live probe budget is
  disabled. No NVIDIA inference was sent and no live pin was published.
- Full local HTTP observations are in ignored
  `reports/runtime/qualification-local-demo.json`. These step-5 changes remain
  uncommitted; GitHub CI has not run this version.
