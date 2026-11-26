# Step 3C: official provider evidence

## Input boundary

This checkout did not contain a step 3B GitHub connector, its tests, or an
observation with live owner/repo/path/commit provenance. The existing step 2
suite is the verified baseline. `github_fixture` is explicitly synthetic input
for 3C integration/demo; it is not completion of 3B. Its locator is `fixture://`,
its example commit is labeled synthetic, and its evidence mode stays `fixture`.

## Reviewed metadata locators

- NVIDIA hosted: https://docs.api.nvidia.com/nim/reference/llm-apis
- SiliconFlow China web catalog: https://cloud.siliconflow.cn/models
- CN provider configuration: https://docs.siliconflow.cn/docs/usercases/use-siliconcloud-in-cline
- Documented authenticated model-list contract: https://docs.siliconflow.cn/docs/api/models-get
- Opt-in catalog API: `GET https://api.siliconflow.cn/v1/models?sub_type=chat`

Seeds are idempotent and never overwrite configured locators. NVIDIA's parser
requires the LLM APIs heading, hosted service URL, chat endpoint text and
Model/Endpoint tables. It captures the exact namespace/model tokens from each
Model cell (formatting spaces around `/` are removed, prefixes/case retained).
Task is confirmed only when the row explicitly mentions chat/conversation.
Self-hosted NIM notes do not create hosted deployments or capability claims.

SiliconFlow's web catalog is attempted without credentials. Login redirects,
login HTML or an undocumented UI representation produce `blocked`, not an empty
catalog. If the operator configures `SILICONFLOW_CN_CATALOG_API_KEY`, the
connector uses the reviewed CN model-list API instead. That secret is sent only
to the exact `api.siliconflow.cn/v1/models?sub_type=chat` locator, never to docs,
login pages, redirects or a `.com` provider. Cline documentation is a separate
source so reading a base URL cannot replace a previous model-list snapshot.

The transport makes bounded GET requests only, does not follow redirects, and
does not retry. No inference endpoints, browser cookies or discovered URLs are
used. No key is needed for NVIDIA public documentation. Do not put keys in chat.

## Stored evidence and publication

The existing durable job/lease/staging/publish pipeline is reused. An official
observation may have no offering_id when identity is incomplete. Its fields
retain values, model/provider scope, source pointers and excerpts. Provenance
retains locator, read_at, content hash, revision, parser version, catalog scope
and fixture/live mode. Fetched UTF-8 document bodies and hashes are archived per
run in `catalog_documents`, including parser failures. The run stores bounded
fetch diagnostics without credentials. Repeated content reuses observations;
the new run records the latest read timestamp even when observation count stays
unchanged. Fixture and live revision namespaces are separate.

All catalog completeness guards run before staging/publish. A documented JSON
`{"object":"list","data":[]}` is a valid empty SiliconFlow model list. Login
HTML, error objects, pagination flags, malformed rows, changed NVIDIA tables or
missing content never publish an empty replacement. A failure/blocked result
preserves the source's last successful snapshot. Other source jobs are independent.

Official catalog records do not invent an endpoint-specific offering. Read-time
reconciliation compares provider, catalog scope, exact model ID and task when
known against current discoveries. Unknown tasks or endpoints that leave
multiple offerings possible stay pending. Display names are never identity.
No offering is renamed, merged or given inference rights by reconciliation.

## Trace an offering

Authenticated `GET /admin/offerings/{offering_id}/evidence` returns:

- Original discovery observations, including GitHub provenance when present.
- Current and historical official observations with match/pending/conflict reasons.
- Unresolved display-only catalog rows in a separate queue, without an offering association.
- Claims for each field with both values, source, locator, read time and mode.
- Per-source latest job/run result and last successful snapshot pointer.

Field status is `provider_confirmed` for matched live model evidence,
`fixture_confirmed` for simulated official evidence, `provider_scope_only` for
general documentation, `conflicting` for different claims, or `unverified`.
GitHub saying `free` remains a discovery claim; price value remains `unknown`
without official model-level price evidence. Quota and tool calling are likewise
unknown. A stale successful snapshot stays visible alongside its latest failed
attempt, so it cannot be mistaken for a fresh verification.

## Reproduce on Windows

```powershell
docker build --target test -t agentic-gateway:test .
docker run --rm agentic-gateway:test python -m pytest -p no:cacheprovider tests/test_provider_parsers.py
./scripts/test-registry.ps1
docker compose up --build -d

# One metadata read per official source. Optional flag adds synthetic GitHub
# provenance for the trace example, without making a GitHub network request.
docker compose exec -T agentic-gateway-api python -m agentic_gateway.providers.catalog.probe --with-github-fixture |
    Set-Content -Encoding utf8 reports/runtime/provider-live.json
```

Use the operator key already in local `.env` for the evidence endpoint:

```powershell
$operatorKey = $env:OPERATOR_API_KEY
if (-not $operatorKey) {
    $operatorKey = ((Get-Content .env | Where-Object { $_ -match '^OPERATOR_API_KEY=' }) -replace '^OPERATOR_API_KEY=', '').Trim()
}
$headers = @{ Authorization = "Bearer $operatorKey" }
$report = Get-Content reports/runtime/provider-live.json -Raw | ConvertFrom-Json
$offeringId = $report.evidence.offering.id
Invoke-RestMethod "http://127.0.0.1:8000/admin/offerings/$offeringId/evidence" -Headers $headers |
    ConvertTo-Json -Depth 16
```

Tests run in a random Compose project/volume and include prefix differences,
display-only names, price/quotas, idempotency, valid empty lists, failed parser,
login after success, independent provider jobs, ambiguous endpoint identity and
conflicting task claims. CI is a local file until the user pushes it. Scheduler,
aggregate administration and real GitHub ingestion are outside this change.
