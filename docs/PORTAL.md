# Operator Portal

The approved Golden Pixel gateway design is implemented under `frontend/` as React + TypeScript + Radix/shadcn-style components. It uses real admin records, not the synthetic traffic scenario under `demos/portal-v2/`.

## Run

From the repository root with the existing `.env` and Docker Desktop running:

```powershell
docker compose up --build -d
docker compose ps -a
```

Open `http://127.0.0.1:3000` (or `PORTAL_PORT`). Sign in with the existing `OPERATOR_API_KEY` from `.env`. The key is used only during login; it is not stored in browser storage, URLs, or frontend bundles. The server creates a random eight-hour HttpOnly, SameSite=Strict session. Restarting the Portal invalidates sessions. Missing operator configuration disables login. The independent design demo remains on its separately launched port 4173.

The BFF forwards only allowlisted methods/paths to FastAPI and adds the operator bearer key on the server. Writes require same-origin JSON requests. Request size, response size, upstream timeout and login retries are bounded. Provider keys are not passed to the frontend. Static assets have a CSP. Use HTTPS and `PORTAL_COOKIE_SECURE=1` behind an HTTPS reverse proxy; loopback HTTP uses `0`. This is the existing single-operator identity model, not per-user RBAC. Do not expose this local setup publicly without a deployment-specific identity and TLS configuration.

## Implemented backend integration

- Sources: register known packaged fixtures, sync existing sources, edit/pause schedules, inspect job/run details.
- Catalog: paginate current snapshot offerings and inspect field-level evidence, origin, conflicts and provenance.
- Deployments: list immutable current generations, configure supported targets through the existing evidence gates, enable/disable (qualification epoch changes).
- Qualification: enqueue **fixture-only** runs, list execution status separately from verdict, inspect per-case observations.
- Publication: list aliases and current usability; publish with an existing eligible live verdict or rollback via snapshot ID and expected version. The existing backend eligibility/CAS checks are unchanged. These operations do not initiate inference.
- Accounts/credential references: read records without displaying secret values.
- Adapter attempts: paginate persisted records with explicit fixture/live mode, unknown usage and no fabricated prices. Prompt/output/arbitrary report fields are not returned by the Portal read model.
- Summary/readiness: real DB counts and API readiness; no invented worker/scheduler health.

`/admin/portal/*` is operator-authenticated. Collection endpoints are bounded to 100 records/page (default 50) and include pagination. Sources use the existing list endpoint. The frontend refreshes the active view every 15 seconds while visible; errors are shown and stale data is labeled. Search filters the currently loaded page. Concurrent resource lists are independent observations, not a transactionally consistent global snapshot.

## Capability boundaries

The existing backend does not implement agent registration, agent API keys, public `/v1/responses` or embeddings, client usage attribution, budgets, teams/RBAC, automatic failover, guardrails, MCP or plugins. The corresponding menus retain the approved navigation but clearly show capability-unavailable states and do not create synthetic operational records. Expanding these requires dedicated backend work; moving the design into source does not silently invent those services.

The only inference-related action exposed by this Portal is fixture qualification. The BFF rejects live qualification regardless of browser payload; existing CLI/direct admin authorization paths remain as before. Real operator sync of an official source may read provider documentation/catalog metadata. Tests and smoke use packaged fixtures and disposable PostgreSQL projects, not live inference.

## Development and verification

```powershell
cd frontend
npm ci
npm test
npm run build
# BFF reads OPERATOR_API_KEY and GATEWAY_API_URL from the process environment.
npm start
# In another terminal, npm run dev starts Vite with /portal-api proxied to port 3000.
```

From the root:

```powershell
docker build --target test -t agentic-gateway:test .
./scripts/test-registry.ps1 -TestPath tests/integration/test_portal.py
docker build --target runtime -t agentic-gateway:dev .
docker compose build agentic-gateway-portal
./scripts/smoke-portal.ps1
```

The Portal smoke verifies HTTP static assets, login/cookie, unauthorized denial, BFF→API→worker→PostgreSQL fixture sync, offerings/evidence, cross-origin denial, live-qualification denial and logout. It only removes its own random Compose project/volume. Reports are written to ignored `reports/runtime/`. Browser visual verification is separate from HTTP/contract tests.

## Provider analytics

Overview, Usage & Costs and Provider Reports use the operator-only
GET /admin/portal/reports endpoint through the existing session BFF.
PostgreSQL aggregates all selected persisted attempts, independent of table pagination.
Filters: provider, all/fixture/live, 1-90 UTC calendar days including today.
Daily attempts/tokens support stacked bars and trends; latency is a per-provider trend.
An accessible data table and JSON export accompany the charts.

Provider detail separates catalog source state from observed inference outcome.
It includes current offering inventory, deployments/accounts by exact current generation,
recent sanitized attempts, errors, qualification decisions and current route usability.
Historical attempt attribution joins its exact deployment generation.
Current inventory is not filtered by the reporting time window or inference mode.
Detail lists are capped (100 models/targets/routes, 30 recent attempts); aggregate counts are complete.

These are qualification/CLI adapter attempts, not a public inference traffic ledger.
Latency means adapter elapsed time, not TTFT. Tokens are summed only from numeric
upstream-reported usage; missing/malformed usage stays unknown.
Spend, uptime and TPS stay unknown. Fixture observations never prove live readiness.
No report request probes a provider or reads credential values.

### Report navigation and layout

- Overview presents four KPIs, a compact activity chart and catalog issues with direct provider links.
  The collapsible resource diagram contains only current inventory counts. Qualification counts
  are shown separately for the selected window and must not be read as a conversion funnel.
- Usage has Activity, Providers, Models and Outcomes/Error tabs. Raw paginated attempts are
  available from Inference Attempts rather than repeated under every report.
- Provider Reports uses directly selectable provider tabs above a full-width report. At narrow widths,
  the provider tabs wrap instead of introducing horizontal scrolling. Details start with compact status/metrics and
  separate tabs for sources, models, attempts and publication.
- Report provider, mode and day filters persist as URL query parameters for reload/share.
  Provider links preserve the selected provider rather than reopening an unfiltered list.
- Chart colors are stable by provider. Legends toggle series; day samples support pointer
  and keyboard focus. Chart dimensions follow the actual card width via ResizeObserver.
- Main navigation switches to a drawer at 1024px so it does not consume the report workspace.


Responsive reports use named CSS containers, not only viewport breakpoints.
The provider list wraps below 960px of report space, while detail KPIs adapt
to the width of their own pane. Charts observe their card size; tables keep
horizontal scrolling inside the table wrapper. The resource map switches to
a vertical flow in narrow report containers. Crossing the 1024px navigation
breakpoint clears desktop collapse/mobile drawer state to avoid hidden labels
or a leftover overlay after resizing.

### Catalog cards

Offering Catalog, Discovery offering evidence and the provider Models tab share
the same card/table component. Cards follow the supplied Model Square layout:
model/provider identity, task/region, pricing availability, source/endpoint metadata
and observed metrics with an evidence detail action. Provider comparison also offers
cards by default, with a table toggle. Catalog provider/task filters apply to the
loaded page; the existing outer pagination continues to show the full record count.
Pricing is unknown until verified billing data exists. Uptime bars and TPS are not
fabricated. Model metrics, when available inside provider reports, use the report's
mode/time scope and exact provider/model match; chat observations are not attributed
to embedding or rerank offerings.

Model Square is now the default surface of Models & Providers and the initial
tab of Models & Endpoints. It displays model offerings across the full content
width; provider operations are a separate section selected with reportSection=providers.
Use /?reportSection=models#providers to open the reference-style model grid directly.
The grid uses 3 columns at 960px of catalog width, 2 columns at 640-959px, and
one column below 640px. The footer follows Status / P95 elapsed / TPS / Details;
status uses observed success ratio only when available, never synthetic uptime.

### Provider tabs and brand theme

Provider operations uses directly selectable Radix tabs above the report.
All providers remains available; selected tabs show a model count and a compact
catalog/target summary below. The tabs wrap on narrow screens. When more than
six providers exist an inline search appears; the selected provider remains visible.
Selection preserves mode/days and updates the report URL. Radix provides arrow-key
tab navigation.

The original octopus artwork is preserved. Theme surfaces are neutral: light
uses #F6F6F7 / white, and dark uses #121212 / #191919. Amber #FFBF00 is only
the accent for controls, selection, focus and pixel effects; there is no purple
or cream page background. Success/error colors remain semantic.

The header exposes Light / Dark / System. The saved gateway-theme preference
accepts light, dark or system; system listens for OS color-scheme changes.
No credential is stored in that preference. The refined pixel interactions are documented in [PIXEL_EFFECTS.md](PIXEL_EFFECTS.md): thin stepped button borders, card focus brackets and genuine loading dots. Global offset shadows and logo movement have been removed; reduced-motion support includes pseudo-elements.
