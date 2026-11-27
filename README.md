# agentic-gateway
An LLM API gateway that discovers providers from GitHub and public catalogs, validates endpoints, and gives agents a single OpenAI Responses-compatible API with per-agent keys, quota tracking, and automatic failover.

## Operator Portal

The implemented Portal runs at `http://127.0.0.1:3000` via `docker compose up --build -d`.
Sign in with the existing `OPERATOR_API_KEY`. The frontend/BFF uses an HttpOnly session
and reads real source/catalog/deployment/qualification/route/attempt data. See
[docs/PORTAL.md](docs/PORTAL.md) for setup, security boundaries and verification.
The introductory gateway capabilities above describe the project target; public agent
inference, agent keys, quotas and failover are not implemented yet. Their Portal screens
show explicit unavailable states instead of synthetic operational data.

## Step 1.1: package and container

Run these commands in PowerShell from this project directory. Docker Desktop
must be running with Linux containers. Python and uv run inside containers;
neither needs to be installed on Windows.

Check the engine (the last command must print `linux`):

```powershell
docker version
docker compose version
docker info --format '{{.OSType}}'
```

Generate or update the lockfile using the same uv version pinned in the
Dockerfile and `pyproject.toml`. Only this lock-generation command mounts the
project directory:

```powershell
docker run --rm --mount "type=bind,source=$($PWD.Path),target=/workspace" --workdir /workspace ghcr.io/astral-sh/uv:0.13.10-python3.12-trixie-slim uv lock --python /usr/local/bin/python --no-python-downloads --no-cache --default-index https://pypi.org/simple
```

Build and verify the installed package from `/tmp`, without a source mount:

```powershell
docker build -t agentic-gateway:dev .
docker run --rm --workdir /tmp agentic-gateway:dev python -c "import sys, agentic_gateway, fastapi, uvicorn; assert sys.version_info[:2] == (3, 12); print(sys.version); print(agentic_gateway.__file__)"
```

The build installs the project as a non-editable package in `/app/.venv` using
`uv sync --locked`. The container runs as UID/GID 10001. A successful check
prints Python `3.12.x` and a package path under
`/app/.venv/lib/python3.12/site-packages/agentic_gateway/`.

## Runtime foundation on Windows

Use PowerShell 7 in this directory and Docker Desktop with Linux containers.
All Python, uv, pytest, PostgreSQL and Alembic commands run inside containers.
There is no host Python/PostgreSQL installation requirement.

Create `.env` only if it does not exist. This generates a random local password
without displaying it, preserving any existing configuration:

```powershell
if (-not (Test-Path .env)) {
    $dbPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
    $template = Get-Content .env.example -Raw
    [IO.File]::WriteAllText((Join-Path $PWD '.env'), $template.Replace('replace-with-a-local-random-password', $dbPassword))
}
docker compose up --build -d
docker compose ps -a
```

If port 8000 is occupied, edit `GATEWAY_PORT` in `.env` to a free port before
starting. Do not stop unrelated containers. Only the API publishes a port, bound
to `127.0.0.1`; PostgreSQL is reachable only inside the Compose network at `db`.
Changing `POSTGRES_PASSWORD` after initial DB creation does not rotate the
password stored in an existing volume.

The default URL is `http://127.0.0.1:8000`. Use your configured port if different:

```powershell
curl.exe --max-time 10 -i http://127.0.0.1:8000/health/live
curl.exe --max-time 10 -i http://127.0.0.1:8000/health/ready
docker compose logs --tail 30 migrate agentic-gateway-api agentic-gateway-worker
```

- `db`: PostgreSQL 16 with a healthcheck and project-scoped `postgres_data` volume.
- `migrate`: runs `alembic upgrade head` after DB is healthy, then exits **0**.
- API and worker: use the same runtime image and wait for migration success.
- `/health/live`: `200 {"status":"live"}`, independent of database connectivity.
- `/health/ready`: `200 {"status":"ready"}` only when DB revisions exactly match
  the migration heads installed in the image. Otherwise `503 {"status":"not_ready"}`.

The baseline revision is `0001_baseline`. It establishes Alembic tracking only;
there are no business tables. Migrations ship inside the installed Python
package. API and Alembic share environment configuration. Readiness uses a
2-second connection timeout, 2-second statement/lock timeouts, a 2-second pool
checkout timeout and connection pre-ping; the API disposes its engine at shutdown.
These are individual limits, not a single overall request deadline.

Inspect or repeat migrations:

```powershell
docker compose run --rm migrate alembic -c /app/alembic.ini current
docker compose run --rm migrate
```

Optional manual dependency outage test (only affects this development project):

```powershell
docker compose stop db
curl.exe --max-time 10 -i http://127.0.0.1:8000/health/live
curl.exe --max-time 10 -i http://127.0.0.1:8000/health/ready
docker compose start db
# Retry readiness after DB becomes healthy; the API needs no restart.
curl.exe --max-time 10 -i http://127.0.0.1:8000/health/ready
```

### Tests and isolated Compose smoke

```powershell
docker build --target test -t agentic-gateway:test .
docker run --rm agentic-gateway:test
docker build -t agentic-gateway:dev .
./scripts/smoke.ps1
```

Check each command's exit code before continuing if running them interactively.
Pytest exercises health response contracts, missing/empty/wrong schema versions,
shutdown disposal and secret-safe configuration. These tests use SQLite as a
local fixture inside the test container; they do not replace PostgreSQL tests.

The PowerShell smoke script runs against **real PostgreSQL 16**. It creates a
fresh `ag-smoke-<random>` Compose project with its own password, volume and free
loopback port, using the already built runtime image. It tests unmigrated DB,
normal startup, repeat migration, wrong revision, DB outage with observed health
timings, recovery without API restart, persistence after DB container replacement
(read before migration), and worker SIGTERM with exit 0 and shutdown event.
It removes only its own test project/volume, even on failure. Development data
is retained. Results are written to ignored `reports/runtime/ag-smoke-*.json`.

The workflow in `.github/workflows/runtime.yml` builds the images, runs pytest
and the same smoke script on a Linux GitHub runner. A local test pass does not
mean the remote workflow has run; this setup does not push or trigger CI.

### Stop and restart without losing data

```powershell
docker compose stop
docker compose up -d
# Remove development containers/network while keeping the named volume:
docker compose down
```

Do not add `--volumes` to development teardown if you want to retain data.
The worker is intentionally idle and waits for SIGTERM/SIGINT with low CPU use.
Provider connectors, agent keys and `/v1/responses` belong to later steps.

## Step 2: Source Registry and local fixture sync

Rules and transaction boundaries: [docs/SOURCE_REGISTRY.md](docs/SOURCE_REGISTRY.md).
The only connector in this step reads packaged fixture files. No GitHub,
provider metadata endpoint or inference API is called.

Create an operator credential only if `.env` does not already define one,
without displaying its value. Keep this credential separate from future agent
keys and provider secrets:

```powershell
if (-not (Select-String -Path .env -Pattern '^OPERATOR_API_KEY=' -Quiet)) {
    $operatorKey = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
    [IO.File]::AppendAllText((Join-Path $PWD '.env'), "`nOPERATOR_API_KEY=$operatorKey`n")
}
docker compose up --build -d
```

Migration advances existing baseline databases to `0002_source_registry` and
idempotently seeds `fixture/catalog-a`. It preserves the development volume and
never resets an existing source pointer or generation. If reusing the runtime
setup snippet above, also run the operator credential snippet before admin API
requests. Missing configuration disables admin access with 503; missing/wrong
Bearer credentials return 401 when configured. Health stays public.

Enqueue and poll from PowerShell (the generated `.env` uses unquoted hex keys):

```powershell
$operatorKey = $env:OPERATOR_API_KEY
if (-not $operatorKey) {
    $operatorKey = ((Get-Content .env | Where-Object { $_ -match '^OPERATOR_API_KEY=' }) -replace '^OPERATOR_API_KEY=', '').Trim()
}
$headers = @{ Authorization = "Bearer $operatorKey" }
$baseUrl = 'http://127.0.0.1:8000' # use GATEWAY_PORT if changed
$job = Invoke-RestMethod -Method Post -Uri "$baseUrl/admin/sources/fixture/catalog-a/sync" -Headers $headers -ContentType 'application/json' -Body '{"revision":"r1"}'
do {
    Start-Sleep -Milliseconds 300
    $state = Invoke-RestMethod "$baseUrl/admin/jobs/$($job.job_id)" -Headers $headers
} while ($state.status -in @('queued','running'))
$state | ConvertTo-Json -Depth 8
Invoke-RestMethod "$baseUrl/admin/offerings" -Headers $headers | ConvertTo-Json -Depth 8
```

Repeat with r1 for an empty added/removed diff, r2 for one added gamma/chat,
and `last-page-timeout` for `failed/fixture_page_timeout`. In the last case,
page 1 stages a different model, page 2 fails, and the published catalog stays
unchanged. The worker logs job/source/run IDs for every staged page and result:

```powershell
docker compose logs --tail 40 agentic-gateway-worker
```

Authenticated endpoints:

- `POST /admin/sources` with `{"id":"fixture/catalog-b","fixture_name":"catalog-b"}`
  registers the second local fixture; registration is idempotent.
- `GET /admin/sources` shows current snapshot pointers and source generations.
- `POST /admin/sources/{id}/sync` accepts a fixture revision, returns 202/job_id.
- `GET /admin/jobs/{id}` shows status, progress, run ID, error code and diff.
- `GET /admin/sync-runs/{id}` shows the historical run and diff.
- `GET /admin/offerings` lists current offerings and per-source evidence.
  `inference_eligible` is always false at this stage.

Run all tests against a fresh PostgreSQL 16 Compose project and volume:

```powershell
docker build --target test -t agentic-gateway:test .
./scripts/test-registry.ps1
```

To run a phase independently, pass `-TestPath tests/integration/test_registry_a.py`
(or `_b.py`, `_c.py`). PostgreSQL tests intentionally skip in a plain
`docker run agentic-gateway:test`; the isolated runner supplies the dedicated
database and guard flag. JUnit reports are written to ignored `reports/runtime/`.
The runner removes only its own random project/volume. The race suite also starts
two real worker processes and a real HTTP server inside the test container.
No host Python is used. Source Registry tests are included in the local CI file;
they have not been submitted to GitHub by this work.

## Step 3C: NVIDIA and SiliconFlow China evidence

See [docs/PROVIDER_EVIDENCE.md](docs/PROVIDER_EVIDENCE.md) for source locators,
field-level matching rules, metadata-only probes and PowerShell trace commands.
The new authenticated endpoint is `/admin/offerings/{offering_id}/evidence`.
Live GitHub provenance is still missing in this checkout; the optional demo
input is explicitly synthetic. Official provider observations never promote a
community `free` label into verified price or quota, or grant inference access.

## Step 3D: catalog administration and scheduled sync

See [docs/CATALOG_OPERATIONS.md](docs/CATALOG_OPERATIONS.md) for the three
operator read APIs, schedule configuration and PowerShell examples. The new
`agentic-gateway-scheduler` service uses the runtime image and durable enqueue
path; schedules remain opt-in per source. Latest attempts, last success,
active snapshot and next schedule are separate fields. Run
`./scripts/smoke-catalog.ps1` for an isolated HTTP/scheduler smoke with observed
response examples and a failed run that preserves the prior catalog.

## Step 4: deployment and single-send inference adapter

[docs/INFERENCE_ADAPTER.md](docs/INFERENCE_ADAPTER.md) documents immutable
deployment generations, the NVIDIA HTTPX adapter, explicit operator probe CLI,
error/usage semantics and counted HTTPS fixture tests. A live probe requires a
current officially confirmed offering and operator-supplied `NVIDIA_API_KEY`.
No live deployment success is implied by passing offline tests.

## Step 5: qualification and pinned model publication

[docs/QUALIFICATION.md](docs/QUALIFICATION.md) describes the versioned text
profile, bounded worker probes, per-case evidence, live-only publication and
compare-and-swap route snapshots. The same eligibility checks apply to route
reads and rollback. Fixture success cannot publish a live route. Live execution
requires both provider credentials and an explicit account budget.

## Git workflow for this personal project

- `main`: stable release branch.
- `develop`: ongoing development and verified milestone commits.

Work on `develop`, run the container test suite and relevant Compose smoke
checks, then commit and push. When ready to release, merge `develop` into `main`
and optionally tag the release. No additional permanent branches are needed.
Text files are stored as LF using `.gitattributes`. Local `.env`, generated
reports, caches and fixture TLS keys are ignored. CI generates its own disposable
TLS certificate in the Docker test target.
