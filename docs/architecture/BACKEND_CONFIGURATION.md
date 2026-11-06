# Backend configuration loading and review

Current status: Phase 6 is [closed with explicit deferrals](PHASE_6_CLOSEOUT.md)
as of 2026-10-24. Dated sections below preserve each change's verified scope;
remaining broader logging/test isolation is tracked as P6-D1/D2.

Phase 6 review, 2026-10-21. This records current source behavior rather than
introducing a shared settings framework. No private `.env` files or credential
values were read for this review. The environment template was inspected for
variable names only. Examples and tests use synthetic values.

## Assembly boundary

`server/main.py` loads dotenv before importing application routers and services.
It assembles FastAPI, includes the existing routers, constructs
`EnvironmentService` and `RunLedgerService`, and serves static assets. Routes
remain under `server/api/`; infrastructure services and database adapters retain
their existing responsibilities. This change does not move routes, add another
app factory, or modify domain workflows.

## Launch-time precedence

There are two intentionally different launch contracts:

| Launch path | Configuration precedence | File selection |
| --- | --- | --- |
| `./run_planner.sh` | Project `.env` values override inherited environment values; unspecified values remain inherited | Explicit project-root `.env`, loaded with `override=True` before executing Uvicorn |
| Direct Uvicorn import of `server.main:app` | Existing environment values win; discovered `.env` fills missing values | `server/main.py` calls `load_dotenv()` with default discovery and `override=False` |
| Docker Uvicorn command | Same default app-import behavior; deployed environment wins | Dockerfile does not copy a project `.env` |

The local launcher changes to the project directory, activates an available
virtual environment, sets `PYTHONPATH`, and execs the normal Uvicorn CLI with
`--reload --port 8893`. Reload workers inherit the selected environment. Its
port is fixed in the script; a template `PORT` value does not override that CLI
argument. Do not execute the launcher merely to inspect configuration: its
existing first step stops listeners on that port. The launcher tests replace
that process lookup and Uvicorn in a temporary project.

Normal python-dotenv discovery starts at the calling source file's directory
and searches ancestors. In interactive/debugger contexts it can start at the
working directory instead. This app does not explicitly load `.env.local` or
merge an environment-specific file stack. File interpolation and disabled-loading
behavior are python-dotenv's: the installed 1.2.2 supports
`PYTHON_DOTENV_DISABLED`, which can suppress both explicit and implicit loads.
The repository requirement is a lower bound, not an exact version pin.

`server/agents/isr_agent_multi_v4.py` also calls `load_dotenv()` at import with
default precedence. It can fill missing values but does not override already-set
values. No removal is made here: direct-import consumers and search locations
must be checked before declaring it redundant.

`docker-compose.yml` explicitly forwards only `OPENAI_API_KEY` through its
`environment` list. Compose's own `.env` interpolation is not application dotenv
loading; it does not automatically pass every project variable into the
container. Other settings require explicit deployment configuration. This review
does not change or validate Docker packaging or the hosted deployment command.

## Runtime consumers

These are configuration rules, not service reachability guarantees:

| Setting / consumer | Current resolution |
| --- | --- |
| LLM factory provider | Explicit nonempty provider argument, then `LLM_PROVIDER`, defaulting to `openai` when unset |
| LLM factory model | Explicit nonempty model argument, then nonempty `LLM_MODEL`, then the provider's registered default; validated by the factory |
| Hosted LLM keys | Factory argument, otherwise the provider's environment variable: `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`, or `ZAI_API_KEY` |
| Ollama host | Explicit nonempty host, then nonempty `OLLAMA_BASE_URL`, then localhost; trailing slashes removed by `get_ollama_base_url` |
| Supabase | `SUPABASE_URL` and `SUPABASE_KEY` read on client creation; absent settings yield `None`; a successful client is cached for the process, failed attempts are not |
| Cesium | `/api/client_config` returns `CESIUM_ION_TOKEN` or an empty string; this token intentionally reaches the browser |
| ElevenLabs | Server-only `ELEVENLABS_API_KEY`; request voice, then `ELEVENLABS_VOICE_ID`, then built-in voice; `ELEVENLABS_MODEL_ID`, then built-in model |
| GNN checkpoints | Explicit path, then `GNN_MODEL_PATH` or `GNN_ROUTE_MODEL_PATH`; this review does not change model loading or fallback behavior |
| Trajectory diagnostics | `ISR_DEBUG` is read at module import; preserved without changing domain behavior |

Consumers do not all normalize empty strings identically. For example, an
explicitly empty `LLM_PROVIDER` environment value is not equivalent to an unset
value in the factory. There is no shared eager startup validation layer.

Do not assume all agent entrypoints use identical model precedence. The active
reasoning-agent selector avoids inheriting `LLM_MODEL` when a request explicitly
selects a provider; it supplies a provider default instead. Its hosted credentials
remain server-side and its host override is limited to Ollama. The v4 and CrewAI
paths retain their own existing selection rules. This review records these
boundaries without unifying operational callers.

The template is not a definitive runtime contract: `DEFAULT_MODEL`, `DEBUG`,
and `PORT` are not consumed by the reviewed application settings paths;
`ANTHROPIC_API_KEY` is not represented in the current factory provider registry.
Conversely, checkpoint paths and `ISR_DEBUG` have source consumers even though
they are absent from that template. Template cleanup is deferred, not silently
mixed into this logging change.

## Credential and diagnostic boundary

The provider list reports environment-variable names and configured booleans,
not key values. A configured boolean is not evidence of valid authentication.
Ollama is considered configured without checking connectivity. Environment-route
Supabase diagnostics print presence booleans only. The Cesium token endpoint is
an intentional client-visible exception; it must not be described as a private
server-only credential store.

Supabase initialization previously printed raw SDK exceptions on import and
client-construction failures. Those messages could include supplied settings.
It now prints fixed diagnostics with an install hint or configuration/service
hint. Return values, caching, next-call retry behavior, and required-client
exceptions remain unchanged. No exception body, URL, or key is included by this
adapter's own diagnostic messages. Logs generated internally by dependencies
are outside that guarantee.

This does **not** establish application-wide credential-safe error handling.
The provider error-presentation change replaces raw connection-check, Ollama discovery, and
ElevenLabs failure details with fixed hints; see [Provider Service Boundaries](PROVIDER_SERVICE_BOUNDARIES.md).
Follow-up review is still needed for:

- Application-wide error/SDK logging beyond the adapters covered below. Ledger,
  retrieval, agent-memory, and session/conversation diagnostics now use fixed
  failure messages; that is not a guarantee about every application log.
- Opt-in live-agent execution deliberately uses exported provider credentials.
  The runner policy below now prevents accidental execution/default dotenv loading;
  it does not make live-provider scenarios offline coverage.

These findings keep the checklist's broader configuration review open. Address
error presentation with mocked failures in the later Phase 6 service review;
do not change provider selection or domain outcomes as part of diagnostic fixes.

## Validation and remaining work

Run the focused offline checks from the repository root:

```sh
.venv/bin/python -m pytest -q tests/test_supabase_configuration.py tests/test_local_launcher.py
```

Nine tests passed locally: seven cover missing settings, successful client reuse,
SDK/import failures, safe diagnostics, retry after failure, and the required-client
contract; two existing launcher cases check explicit project-file precedence,
missing-file inheritance, command arguments, and value-free output. Tests inject
synthetic values and replace the SDK; they do not load private `.env` files,
start/stop a real server, contact a provider, or create a database client.

Focused Black (Python 3.11 target), isort, and flake8 checks passed. No full-suite,
app startup, Docker build, hosted CI, or deployment verification is claimed. The
historical [baseline results](../Baseline-Blockers.md) remain separate; these nine
checks do not supersede them. Health/readiness semantics and optional-service
availability are now documented in [Backend Health and Readiness](BACKEND_HEALTH_AND_READINESS.md).
Remaining external-service timeout behavior and compatibility cleanup are still
Phase 6 work.

Rollback of the runtime change is limited to restoring the two Supabase diagnostic
handlers and their tests. It does not require data migration or configuration
changes. No commit, merge, or deployment was performed.

## Session/conversation diagnostics and fixture audit (2026-10-24)

All 11 exception handlers in `server/database/session_storage.py` now print fixed,
operation-specific diagnostics with a configuration/service hint. They omit SDK
exception text, URLs, credentials, and conversation data. The conversation-summary
failure response keeps its `available`/`error` shape but replaces `str(exception)`
with `Conversation summary is unavailable.` Missing-client and other failure
sentinels remain unchanged (`None`, `False`, empty lists, zero, and the summary's
unavailable result). This guarantee concerns the adapter's diagnostics, not logs
emitted internally by its dependencies or arbitrary caller-supplied data.

The module's re-exports in `server/database/__init__.py` remain intact. Repository
reference checks found no other production consumer of `get_conversation_summary`
that relies on its former exception text. No timeout, retry, query, write payload,
return type, caller selection, or domain calculation was changed. An AST comparison
confirmed that code outside exception handlers is unchanged.

The source review of test-side dotenv calls found:

| File | Execution boundary and remaining concern |
| --- | --- |
| `tests/test_multi_agent.py` | Module-level pytest skip precedes dotenv loading and legacy imports. It is retained history; ordinary pytest import does not reach its dotenv call. |
| `tests/archive/test_multi_agent_legacy.py` | Excluded by pytest's `norecursedirs`; it still loads dotenv if explicitly imported/executed. No archive rewrite is made. |
| `tests/test_multi_agent_v4.py` | The initial audit found that standalone execution loaded dotenv and bypassed the fixture. The live-test policy below now requires opt-in and uses the same pytest fixture in both modes, with no automatic dotenv loading. |

The v4 operational scenarios were not executed or altered during the initial
audit. Their live execution policy is now explicit as documented below; they
remain live integration scenarios rather than mocked/offline tests. `PYTHON_DOTENV_DISABLED=1` alone
is not a substitute for blocking network calls or isolating inherited credentials.
Other tests that import application modules can inherit import-time configuration;
this three-file source audit is not proof of suite-wide credential isolation.

Run the focused storage and earlier Phase 6 checks from the repository root:

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest -q tests/test_session_storage_diagnostics.py tests/test_supabase_configuration.py tests/test_provider_error_presentation.py tests/test_local_launcher.py
```

All 60 tests passed locally: 25 new storage checks plus the previous 35
configuration/provider/launcher checks. Storage tests replace the client lookup
with a scripted in-memory client, use synthetic exceptions, and never import the
SDK, load private dotenv files, write records to a real database, or contact a
provider. They cover all 11 failure paths and missing-client results, plus
successful session operations, metadata merging, conversation pagination/order,
summary counts, and record pass-through. They verify that synthetic credential,
URL, and conversation markers are absent from diagnostics and failure results.

Focused Black (Python 3.11 target), isort, flake8, documentation-link and diff
whitespace checks passed. No full app startup, full Python suite, browser,
container, live-service, or hosted CI pass is claimed. At this step configuration review remained open for ledger/retrieval/agent-memory
error paths and test isolation. The next diagnostic review below addresses those
adapters; the test-isolation limitation remains. Weather/external-service review and compatibility cleanup remain separate.
Rollback restores this module's exception handlers, including its former summary
error text, and the associated tests/docs; no data or configuration migration is
required. Nothing was committed, merged, or deployed.

## Ledger, retrieval, and agent-memory diagnostics (2026-10-24)

The remaining diagnostic prints in `mission_ledger.py`, `mission_retrieval.py`,
and `agent_memory.py` now use fixed operation-specific messages. Twenty-five
exception handlers were reviewed (19 ledger including its nested retry handler,
two retrieval, and four memory); the ledger retry notice also omits exception
text. SDK messages, response bodies, service URLs, and credential/record details
are not interpolated into these failure diagnostics. Existing success/presence
logs are unchanged, and this does not govern logs produced by SDKs themselves.

Only print arguments and now-unused exception bindings changed. An AST comparison
confirmed that all other code in the three modules is identical. In particular:

- The existing ledger insert retry when an embedding field was present still
  removes only that field and retries once. No new retry or write was introduced.
- Ledger failures keep their existing `None`, `False`, empty-list, or zero-stat
  results. Request payloads, calculations, hashes, cache formats, and legacy
  compatibility entrypoints remain unchanged.
- Embedding failures remain `None`; retrieval RPC failures remain an empty list.
  The migration/configuration hints are retained without SDK text.
- Agent-memory write failure retains its existing JSON fallback. A failed
  Supabase read still returns an empty list; no new read fallback was added.
  File-backed tests use a temporary path and no real memory file is touched.

Run the focused checks with:

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest -q tests/test_ledger_retrieval_memory_diagnostics.py tests/test_mission_ledger_embedding.py tests/test_mission_retrieval.py tests/test_session_storage_diagnostics.py tests/test_supabase_configuration.py tests/test_provider_error_presentation.py tests/test_local_launcher.py
```

All 106 checks passed locally: 29 new diagnostic checks, 17 existing mocked
embedding/retrieval regressions, and the prior 60 focused infrastructure checks.
The new cases exercise every ledger/memory failure handler, embedding constructor
and API failures, retrieval RPC failure, both outcomes of the existing embedding
retry, and memory write/read fallback policy. Synthetic private markers are
excluded from captured output and failure results. Clients and embedding/SDK
entrypoints are replaced; no provider, database, or operational solve is invoked.

Focused Black (Python 3.11 target), isort, flake8, documentation links, and diff
whitespace checks passed. Full application startup, browser, full-suite, container,
and hosted CI checks were not run. The configuration checklist remains open for
active v4 test isolation and broader logging boundaries at that point. The runner
policy below addresses the former; suite-wide credential isolation is not claimed. External-service timeout review and
compatibility cleanup remain separate work. Rollback restores only the three
modules' diagnostic messages/bindings and associated tests/docs; no data or
configuration migration is required. Nothing was committed, merged, or deployed.

## Explicit live-agent test policy (2026-10-24)

`tests/test_multi_agent_v4.py` contains three live-provider smoke scenarios, not
offline infrastructure tests. Their bodies, assertions, and input data are
unchanged (verified by AST comparison). Production code is unchanged.

Default pytest execution now reports these three cases as **skipped**, before
agent/database imports in the fixture. Running the file directly without `--live`
exits with an argument error before any agent import or configuration loading.
The registered `live_agent` marker describes their category; the environment
opt-in controls execution. Selecting the marker alone does not enable live calls.

For an intentional live run with provider settings already exported:

```sh
ISR_RUN_LIVE_AGENT_TESTS=1 .venv/bin/python -m pytest -m live_agent tests/test_multi_agent_v4.py
# Or use the standalone entrypoint, which delegates to the same pytest fixture:
.venv/bin/python tests/test_multi_agent_v4.py --live
```

These commands can contact providers; neither was run against real providers
for this change. Standalone execution no longer loads the project/sibling dotenv
files, checks a hardcoded OpenAI variable, or calls scenario functions outside
pytest. It uses exported settings and returns pytest's exit status. The fixture
disables dotenv loading, removes Supabase settings, clears the cached database
client, and preserves the operator's provider settings for the intentional live
calls. The previous test-directory `sys.path`/`PYTHONPATH` mutations were removed;
the repository pytest configuration supplies its existing project import path.

The change intentionally reduces default live-provider execution; it does not
convert the scenarios to offline tests or establish that they pass. Skips are
reported separately and do not remediate historical full-suite failures.
Archived legacy dotenv examples remain retained history. Third-party pytest
plugins and other test modules are outside this runner policy's guarantees.

Four new subprocess checks copy the runner into temporary projects with stub
agent/database/dotenv modules. They verify default denial/skips, explicit opt-in
in both modes, suppressed import-time dotenv calls, cleared database settings,
and retention of synthetic exported provider settings. The subprocess environments
are constructed from synthetic values rather than copied from the developer's
shell, and no real agent, provider SDK, or database is invoked. The stub response
exists only to exercise runner policy, not to validate agent behavior.

Validation command:

```sh
ISR_RUN_LIVE_AGENT_TESTS=0 PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest -q tests/test_live_agent_test_policy.py tests/test_multi_agent_v4.py tests/test_ledger_retrieval_memory_diagnostics.py tests/test_mission_ledger_embedding.py tests/test_mission_retrieval.py tests/test_session_storage_diagnostics.py tests/test_supabase_configuration.py tests/test_provider_error_presentation.py tests/test_local_launcher.py
```

Local result: **110 passed, 3 live cases skipped**. Focused Black (Python 3.11
target), isort, flake8, documentation links, and diff checks passed. No full suite,
live-provider, browser, hosted CI, or deployment result is claimed. Broader
application logging, external-service review, and compatibility cleanup remain
open. Rollback restores the test runner/fixture and marker declaration plus the
associated policy checks/docs; no production or data migration is involved.
Nothing was committed, merged, or deployed.
