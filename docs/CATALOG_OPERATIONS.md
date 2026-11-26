# Step 3D: catalog administration and schedules

## Runtime and scope

The Compose services are `db`, `migrate`, `agentic-gateway-api`,
`agentic-gateway-worker`, and `agentic-gateway-scheduler`. All application
services use the same non-root runtime image. Migration head is
`0004_catalog_schedule`; existing jobs are backfilled as manual. Existing
catalog/evidence/snapshot data is retained.

The scheduler only enqueues; worker fetch/parse/staging/publish and fencing are
unchanged. Source schedules are **opt-in**. No seeded source starts fetching
automatically after an upgrade. Fixture provenance stays fixture. A previously
blocked provider stays blocked in its latest attempt; 3D does not infer success
or send inference calls.

## Operator reads

`GET /admin/sources` returns every source, including disabled, unimplemented or
misconfigured sources. The fields have separate meanings:

- `latest_job`: most recently requested job, possibly queued without a run.
- `latest_attempt`: latest actual run with outcome, UTC times, progress and error.
- `last_success_at` / `last_successful_run_id`: last successful publication.
- `active_snapshot_id` / `active_snapshot`: the published data currently served,
  its revision and the run that produced it. `current_snapshot_id` remains as a
  compatibility alias.
- `connector`: local configuration/implementation readiness and reason code.
- `next_sync_at`: next eligible automatic update. Null when paused, unconfigured
  or unavailable. `schedule.configured_next_sync_at` preserves a configured due
  time even while the source cannot be scheduled.

Never-run sources have explicit null attempt/success/snapshot fields. A new
queued job does not erase the last actual attempt. A failed 09:00 run does not
change the 08:00 successful snapshot or its success timestamp.

`GET /admin/offerings` accepts exact canonical `provider`, `source`, `region`,
`task_type`, `limit` (1–100, default 50), and `offset` (>=0). Active membership
qualifies offerings first; unique offering IDs are ordered and paginated before
evidence is loaded. A source filter selects offerings recorded by that source,
but the response still includes evidence from every current source for each
selected offering. Each item includes `source_ids`, snapshot/run references and
`evidence_url`. Historical-only memberships are excluded. Each response reads a
consistent PostgreSQL repeatable-read snapshot; separate page requests may see
later catalog publications.

Unambiguous model-level official observations from 3C also count as current
evidence for their exactly matched offering, even when the raw observation has
no direct offering_id. That relation is resolved in SQL before qualification
and pagination. Display-only, conflicting, ambiguous and provider-only records
do not create model memberships. No new offering or inference right is created.

`GET /admin/sync-runs/{id}` includes source/job/trigger, UTC timestamps, pages,
`records_staged`, `records_published`, published diff, snapshot ID and normalized
error. `diff`/`published_diff` and `snapshot_id` are null on unsuccessful runs.
Staged records from a failed parse/fetch are not reported as published changes.
Missing IDs return 404. These endpoints and schedule configuration use the same
operator Bearer authentication. Registry config, Authorization headers and raw
upstream errors are not returned.

## Scheduling policy and transaction

`PUT /admin/sources/{id}/schedule` sets/clears a schedule. Supply
`interval_seconds` (1–31536000) and timezone-aware `next_sync_at` together, or
both null to clear. Optional `revision` selects a fixture revision; defaults are
r1 for fixtures and latest for official readers. Optional `enabled` changes
the source enable flag without deleting its catalog.

The scheduler polls every 2 seconds by default (`SCHEDULER_POLL_SECONDS`). In a
short transaction it locks due source rows with `FOR UPDATE SKIP LOCKED`, uses
the same `enqueue_locked` path as manual requests, then advances the cursor.
There are no network reads in this transaction. A unique PostgreSQL constraint
on `(source_id, scheduled_for)` prevents a period from being enqueued twice,
including after a restart or restoring an old cursor. Job insertion, source
generation increment and cursor advancement commit or roll back together.

Missed periods coalesce into at most one catch-up job, identified by the stored
due time. The next time is `current scheduler clock + interval`, rather than
replaying all elapsed periods. If a job is queued/running, the period is skipped
and the next time still advances. A queued expired-lease job is left for worker
reclaim, rather than creating another logical job. Manual requests remain
explicit jobs; if manual enqueue follows an unfinished scheduled job, source
generation fencing prevents the old scheduled run from publishing over it.

Disabled, missing and misconfigured connectors never enqueue automatically.
SiliconFlow CN automatic reads require the reviewed API configuration and
`SILICONFLOW_CN_CATALOG_API_KEY`; otherwise the source shows
`catalog_credential_missing`. Manual 3C web probing remains available. Runtime
timeouts on otherwise configured sources do not disable their next scheduled
attempt. The service handles SIGTERM and sleeps between polls.

## PowerShell operation

Run from the repo directory with local `.env` already configured:

```powershell
docker compose up --build -d
docker compose ps -a

if (-not $env:OPERATOR_API_KEY) {
    $env:OPERATOR_API_KEY = ((Get-Content .env | Where-Object { $_ -match '^OPERATOR_API_KEY=' }) -replace '^OPERATOR_API_KEY=', '').Trim()
}
$headers = @{ Authorization = "Bearer $env:OPERATOR_API_KEY" }
$baseUrl = 'http://127.0.0.1:8000' # replace if GATEWAY_PORT differs
$sourceId = 'fixture/catalog-a'

# Explicitly schedule only the local fixture. Choose a future time for normal operation.
$schedule = @{
    interval_seconds = 3600
    next_sync_at = [DateTimeOffset]::UtcNow.AddHours(1).ToString('o')
    revision = 'r1'
} | ConvertTo-Json
Invoke-RestMethod -Method Put "$baseUrl/admin/sources/$sourceId/schedule" -Headers $headers -ContentType 'application/json' -Body $schedule

$source = (Invoke-RestMethod "$baseUrl/admin/sources" -Headers $headers).data | Where-Object source_id -eq $sourceId
$source | ConvertTo-Json -Depth 12
$encodedSource = [Uri]::EscapeDataString($sourceId)
Invoke-RestMethod "$baseUrl/admin/offerings?source=$encodedSource&task_type=chat&limit=10&offset=0" -Headers $headers | ConvertTo-Json -Depth 12
if ($source.latest_attempt) {
    Invoke-RestMethod "$baseUrl/admin/sync-runs/$($source.latest_attempt.sync_run_id)" -Headers $headers | ConvertTo-Json -Depth 12
}
docker compose logs --tail 30 agentic-gateway-scheduler agentic-gateway-worker

# Clear the schedule while preserving catalog and source enable state.
Invoke-RestMethod -Method Put "$baseUrl/admin/sources/$sourceId/schedule" -Headers $headers -ContentType 'application/json' -Body '{"interval_seconds":null,"next_sync_at":null}'
```

## Tests and examples

```powershell
docker build --target test -t agentic-gateway:test .
./scripts/test-registry.ps1
docker build -t agentic-gateway:dev .
./scripts/smoke-catalog.ps1
./scripts/smoke.ps1
```

All PostgreSQL resets and outage/replacement checks use dedicated random
projects/volumes. `smoke-catalog.ps1` starts the real API/worker, configures only
a fixture source, runs/restarts the actual scheduler entry point, checks a
scheduled final-page failure against an older successful snapshot, and starts
and SIGTERMs the scheduler service. Its ignored JSON report under
`reports/runtime/ag-catalog-*.json` contains an observed example for each of the
three endpoints, plus the successful run for comparison. JUnit reports include
controlled clocks, concurrent real scheduler processes, transactional rollback,
manual/scheduled races, pagination and auth/redaction. The local CI workflow
uses the same scripts; no GitHub CI run is implied by local success.

`python -m agentic_gateway.scheduler --once --now <aware-ISO-time>` is available
for isolated fixture tests. Long-running services use PostgreSQL's clock.
Source key issuance, inference calls and provider qualification remain outside 3D.
