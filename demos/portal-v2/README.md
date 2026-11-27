# Agentic LLM Gateway Portal preview

Isolated UI demo for design review. Does not connect to FastAPI or providers.

Stack: React + TypeScript + Vite. UI components in `src/components/ui.tsx` follow shadcn/ui composition with Radix Dialog, Tabs, Slot, class-variance-authority and CSS theme tokens. Visual styling is local CSS, not a Tailwind build. Lucide icons; pixel artwork is CSS-native.

```powershell
npm ci
npm run build
node serve.cjs
```

Preview: http://127.0.0.1:4173/

References: Bifrost for gateway navigation and provider management; LiteLLM Admin UI for virtual key/model administration; New API for catalog/access table patterns. Independently authored UI; no source copied from those repositories.

Golden accent #DCA900, zinc neutrals and a small pixel mark. The current revision replaces the presentation dashboard with an operations console: action queue, source schedules, offering evidence, accounts/secret references, deployments, qualification verdicts, pinned route gates, jobs/runs, attempts, runtime inventory, session audit and API coverage.

Resources share session state. Sync creates a job and updates a source snapshot; qualification creates a job/verdict; deployment enable/disable increments its qualification epoch and makes old verdicts stale. Registration, scheduling and deployment forms validate input. Publication remains blocked without a live verdict. The right inspector links related resources without losing the management screen. Tables support search/status filters, and offerings support selection/export. Ctrl+K searches modules, sources and deployments.

Services are explicitly `Not checked`; attempts, quotas and spend are not fabricated. Project settings distinguish existing HTTP contracts, CLI/service support and planned APIs. State is in memory and resets on reload.

All model/evidence/status/usage values are illustrative. Keys and quota are planned, not connected features. A fixture pass never grants live eligibility.

## Discovery and agent management revision

- `#discovery`: source acquisition inventory and per-offering verification workbench, linking evidence, deployment generation, current fixture verdict and next action. GitHub live discovery and account terms remain explicitly unverified.
- `#agents`: in-memory bot registration, owner, environment, alias bindings, pause/resume, credential reference revoke/rotate, RPM and token policies, request decision trace. The sandbox alias is fixture-only; other aliases remain unpublished drafts.
- Request simulation applies agent/key/scope/rate/token/target/route checks. A simulated sandbox success consumes 64 illustrative tokens and sends no network request. Real price, usage and spend remain unknown.
- Agent state survives module navigation, and changes are recorded in the shared session activity. Reload resets all demo state. No actual API key is created or stored.

Verification: TypeScript, production build, server-side rendering of 15 screens, and 10 policy decision/boundary assertions. Interactive visual QA was unavailable in the current chat.

## Gateway-first revision v0.4 (current entry point)

`src/main.tsx` now mounts `GatewayConsole.tsx`. Earlier operations/agent workspaces are retained as source history but are not the current application entry point.

The current Portal is organized around AI Gateway, Observability, Governance, and Model Lifecycle. It includes Models & Endpoints (deployed models, Model Square, API endpoints, health), Agents & Applications, Virtual Keys, Routing & Failover, Providers & Credentials, Playground, Usage & Costs, Request Logs, Audit, Teams & Budgets, Policies & Guardrails, Integrations/MCP, Discovery/Verification, and backend coverage.

Models, agents, keys, routes and request records share one in-memory scenario. Agent registration and key scopes affect the request simulator; revoke/pause/limits/route changes affect subsequent decisions. Successful simulation contributes mock token/cost values to logs and usage. Model consumers show both granted scopes and observed successful traffic. New models begin staged and unqualified. UI states do not imply that any provider beyond the narrow current NVIDIA adapter is implemented.

The 96 initial request records, serving/qualification flags, context sizes and prices are explicitly synthetic, not runtime telemetry, benchmark results, provider catalogs or actual charges. The scenario calendar-month budget calculation uses UTC month boundaries. The frontend never sends prompt data to providers. Retention, PII redaction and integration toggles only configure the demo; they do not implement exporters, scanners or MCP runtime. No usable API keys are generated. Browser reload resets all state.

Design references reviewed: [LiteLLM Admin UI](https://docs.litellm.ai/docs/proxy/ui), [UI logs](https://docs.litellm.ai/docs/proxy/ui_logs), [AI Hub](https://docs.litellm.ai/docs/proxy/ai_hub), [Bifrost](https://github.com/maximhq/bifrost), [New API](https://github.com/QuantumNous/new-api), and user-supplied screenshots. The UI is independently authored; source from these projects was not copied.

Latest verification: TypeScript; production build; 21 pure simulator scenarios (identity/key ownership, revoke/pause, scope intersection, routes, compatible scoped fallback, RPM, token limits, per-key/per-agent budgets and month boundaries, embeddings); seeded log/cost consistency; server-side rendering of all 15 pages. This does not replace interactive browser/viewport QA, which remains unavailable in this chat.
