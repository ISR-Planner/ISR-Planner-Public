# Backend health, readiness, and optional dependencies

Phase 6 source review, 2026-10-21. This document describes current behavior. It
introduces no new endpoint, dependency probe, deployment setting, or runtime
change. A successful health response must not be interpreted as proof that every
feature is available.

## Existing probe contracts

| Surface | What a successful result establishes | What it does not establish |
| --- | --- | --- |
| `GET /health` | The application has imported, is serving requests, and the debug router's handler returned HTTP 200 with `{"status":"ok"}` | Provider authentication, database access, model availability, current weather, static asset completeness, browser execution, or feature readiness |
| `GET /` | The index handler responded with HTML | Even presence of `index.html`: the handler deliberately returns an explanatory fallback with HTTP 200 when the file is absent. It does not load JavaScript or call services. |
| `POST /api/debug/supabase-test` | Currently uses legacy no-op ledger stubs, so it returns HTTP 200 with `ok: false`; successful-path tests mock those functions | Real database connectivity/writes or readiness. The endpoint is designed around write-shaped operations, not a bounded read-only probe. |

The debug route currently cannot validate Supabase writes: `create_mission_run`
returns `None` in the shipped compatibility stub, so the event call is not reached.
Its successful-path test verifies routing with mocks, not actual storage.

The health handler in `server/api/debug_routes.py` returns a constant response.
It does not call the database functions imported alongside it. It remains
available after an optional-service failure provided that the process and
request handling remain functional.

There is **no dedicated readiness endpoint** or aggregated service-status
contract in the current application. No startup/lifespan hook validates external
services before accepting requests. Do not advertise `/health` as a dependency
readiness check, or infer a deployment's feature availability from its 200 alone.

## Startup boundary

`server/main.py` loads configuration, imports routers and their dependencies,
constructs lightweight service facades, registers middleware, and mounts static
files. It does not eagerly authenticate providers or validate database schemas.
`EnvironmentService` construction records paths and callbacks; `RunLedgerService`
starts with empty identifiers. This assembly remains unchanged.

External credentials being optional is different from Python packages being
optional. Unhandled import failures in eagerly imported routers/dependencies
prevent the app from serving any endpoint, including `/health`. Some feature
SDK imports are deferred until use, but that does not make every transitive
library optional at startup.

The static directory itself is another startup requirement: FastAPI's
`StaticFiles` is constructed with its default `check_dir=True`. A missing selected
`WEBAPP` directory can therefore prevent assembly. A directory that exists but
lacks the index file instead reaches the index handler's HTTP-200 fallback.
Neither case proves that all assets referenced by a page are present.

## Checked-in deployment probes

| Source | Configured probe | Timing values in the file |
| --- | --- | --- |
| `railway.toml` | `/health`; builds with `Dockerfile.railway` | `healthcheckTimeout = 300` |
| `Dockerfile` | `curl -f http://localhost:8893/` | Interval 30s, timeout 10s, start period 5s, retries 3 |
| `docker-compose.yml` | `curl -f http://localhost:8893/` | Interval 30s, timeout 10s, start period 10s, retries 3 |

These are repository configuration values, not verified hosted/runtime results.
The Railway image installs curl and starts `server.main:app` with its `PORT`
setting. The separate generic Dockerfile starts `isr_web.server.main:app` on
8893. Both launch contracts retain their current configuration behavior.

Two deployment gaps are recorded without changing them here:

- The Docker/Compose probe checks `/`, so a missing index file's fallback still
  satisfies its HTTP success condition.
- The generic Dockerfile explicitly installs gcc but not curl, although its
  health command and Compose's probe require curl. Curl availability in a built
  image has not been verified; it should not depend on an incidental base-image
  package. `Dockerfile.railway` explicitly installs curl.

A later focused deployment change can align probes and guarantee the probe tool
exists. A readiness implementation would additionally need a defined set of
required features/services and bounded, side-effect-free checks. Neither is
silently introduced by this documentation step.

## Optional-service and feature boundaries

The table concerns availability of a feature after app startup, not permission
to change its operational behavior or substitute domain results.

| Dependency / feature | Current timing and degraded behavior | Why health/configured status is insufficient |
| --- | --- | --- |
| Supabase storage | Client creation is lazy. Missing settings/import/creation failure returns `None`; many ledger/storage helpers return empty/false/none results. Agent memory has a local JSON path when no client is available. | A cached/constructed client does not prove connectivity, permissions, schema, or successful writes. There is no universal fallback guarantee for every storage consumer. `require_supabase_client` raises on an unavailable client. |
| Hosted LLM providers | Factory creates provider clients on demand. Missing credentials or packages prevent the dependent request; unrelated health handling does not probe them. | Provider-list `configured` means an environment value is present, not authenticated or reachable. Connection testing is a separate explicit action. |
| Ollama | Host resolution is local; model discovery and connection testing contact the selected server on demand. Discovery failure returns the existing failure envelope. | Keyless means configured, not installed, running, reachable, or model-ready. |
| ElevenLabs | Engine listing checks key presence; synthesis makes the actual upstream request. Missing key yields 503 and transport failure yields 502. | Listing an engine is not an authentication or streaming test. See the provider contract for status and timeout details. |
| NOAA weather | Current-weather requests fetch/cache feeds on demand. The endpoint uses a one-minute cache; retrieval/validation failure returns its fixed 502 response. | Health does not refresh forecasts or establish coverage. No weather/domain fallback is added by this review. |
| GNN features | GNN route handlers import their feature stack when used; model listing/loading requires its packages/files. | Health does not validate PyTorch, checkpoint formats, or the selected model. Model selection and existing fallback behavior remain unchanged. |
| Cesium and browser voice | Client configuration exposes the Cesium token; without a token the viewer uses its existing offline imagery/flat-terrain path. Voice includes browser/local options. | Backend health does not load CDN scripts, render WebGL, download Kokoro assets, or establish browser speech support. A configured token is not a successful terrain request. |
| Local assets/files | Static directory mount occurs at assembly; individual pages, catalog content, environment files, and output paths are consumed separately. | Health does not establish file completeness, parseability, or write permissions. A successful root request is not a browser smoke test. |

The debug write probe, model discovery, and provider connection test are explicit
diagnostic actions, not background readiness checks. Do not trigger them merely
to answer whether the HTTP process is alive.

## Verification and remaining work

The contracts were checked against `server/main.py`, `server/api/debug_routes.py`,
the deployment files above, and the relevant service/feature entrypoints. Existing
isolated debug-route checks verify the health response, mocked successful debug
writes, and the debug endpoint's HTTP-200 failure envelope. No new tests are
needed for this documentation-only change.

The existing focused offline checks can be repeated without importing the full
app or loading private dotenv files:

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest -q tests/test_api_route_hardening.py::test_debug_routes_health_and_supabase_probe tests/test_api_route_hardening.py::test_debug_supabase_probe_reports_run_creation_failure tests/test_provider_error_presentation.py tests/test_supabase_configuration.py tests/test_local_launcher.py
```

All 37 checks passed locally. Debug database writes and provider calls were mocked;
no real provider, database, or running deployment was contacted. This is not a
full startup, Docker build, hosted health-check, browser, or full-suite result.
The historical [baseline results](../Baseline-Blockers.md) remain separate.

Health/readiness documentation is complete. Configuration/logging review, other
external-service boundaries, explicit LLM timeout policy, deployment probe gaps,
and compatibility migration are explicitly deferred in [Phase 6 Closeout](PHASE_6_CLOSEOUT.md). This step
changes documentation only; rollback is a documentation revert. No commit,
merge, or deployment was performed.

See [Backend Configuration](BACKEND_CONFIGURATION.md),
[Provider Service Boundaries](PROVIDER_SERVICE_BOUNDARIES.md), and the
[refactor checklist](../REPOSITORY_INFRASTRUCTURE_REFACTOR_CHECKLIST.md).
