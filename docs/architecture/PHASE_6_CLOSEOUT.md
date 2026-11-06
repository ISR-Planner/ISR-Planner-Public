# Phase 6 closeout and explicit deferrals

Closed for this iteration on 2026-10-24, with the user's authorization to complete
or explicitly defer the remaining work before planning frontend extraction.
This is a **closeout with deferrals**, not a claim that every Phase 6 acceptance
condition or application-wide review is complete. No runtime changes are made by
this closeout. Unchecked Phase 6 entries refer to the follow-ups below.

## Disposition of the original items

| Checklist item | Disposition |
| --- | --- |
| Preserve router/service separation | Verified; existing app assembly and boundaries are preserved. |
| Review configuration loading/precedence and credential boundaries | Completed for the documented launch paths and infrastructure adapters. Broader logging and suite-wide test isolation are explicitly deferred as P6-D1/P6-D2. |
| Document health/readiness and optional dependencies | Complete. `/health` remains liveness only; readiness and deployment limitations are documented. |
| Review external-service timeout/error presentation | Completed for connection checks, Ollama, and ElevenLabs; weather transport/cache failure behavior was reviewed at closeout. Policy changes and broader coverage are deferred as P6-D3. |
| Remove proven-unused compatibility infrastructure | Reference review found active consumers of the ledger stubs. Retain them; no removal is justified by this review. Future migration/deletion is P6-D5. |

## Deferred work register

These are separate future changes, not prerequisites for creating the frontend
extraction checklist. No external owner, deadline, or deployment approval is
implied. Reopen an entry when scheduling its focused backend work.

| ID | Work retained for later | Reason for deferral and completion criterion |
| --- | --- | --- |
| P6-D1 | Review error logging beyond the covered infrastructure adapters, including `RunLedgerService` exception diagnostics and agent/workflow/SDK logging | These paths can still print exception representations. A wider rewrite is outside this closeout. Complete with a caller inventory and mocked private-detail failures, preserving existing propagation/fallback behavior and domain outcomes. Do not claim application-wide credential-safe logging until verified. |
| P6-D2 | Establish suite-wide configuration/network isolation beyond the classified v4 live tests | Other modules can import application configuration during collection; archived scripts and third-party plugins are outside the runner's guarantee. Complete with an inventory of import-time/live boundaries and isolated synthetic-environment checks. Keep intentional live tests explicit, retain their assertions, and report skips separately. |
| P6-D3 | Define explicit LLM connection-test timeout/retry policy; expand remaining external-service coverage where warranted | The factory supplies no explicit SDK timeout/retry settings. Ollama, ElevenLabs, and NOAA have request timeout arguments, not total-duration deadlines. Complete with agreed timeout semantics, mocked timeout/cleanup checks, and preserved request/response/domain contracts. Do not add automatic write retries incidentally. |
| P6-D4 | Align deployment probes and decide whether a readiness endpoint is needed | Docker/Compose target the root HTML response; the generic image does not explicitly install its required curl tool. Railway targets `/health`. Complete probe changes with image/runtime verification. A new readiness endpoint additionally requires a defined required-feature set and bounded read-only checks. Documentation is complete; implementation is deliberately deferred. |
| P6-D5 | Migrate legacy ledger callers before considering compatibility removal | All four stubs still have source references. Complete caller migration separately, repeat repository/public-entrypoint checks, and run relevant focused tests before deleting anything. Preserve operational workflow semantics; a no-op implementation alone is not evidence that its interface is unused. |

Hosted CI/full startup/container verification is not replaced by this closeout.
Existing CI and baseline tasks remain in their original checklist sections and
[Baseline Blockers](../Baseline-Blockers.md); no new passing full-suite claim is made.

## Compatibility reference evidence

A repository source search for `create_mission_run`, `log_event`,
`create_env_version`, and `create_plan` established the following:

| Interface in `server/database/mission_ledger.py` | Required source references |
| --- | --- |
| `create_mission_run` | `server/api/run_ledger_service.py`, `server/api/debug_routes.py`, `server/agents/mission_executive.py` |
| `log_event` | The same modules; solve routes also use `RunLedgerService.log_event` |
| `create_env_version` | `server/api/run_ledger_service.py` imports and calls it |
| `create_plan` | `server/api/run_ledger_service.py` imports and calls it |

Some call paths are conditional, but imports and callers still exist. The stubs
return `None`/`False`; replacing them with real writes or deleting their callers
would change behavior and is not part of this phase's infrastructure closeout.
The database package's `is_supabase_configured` export is also used by
`server/memory/session_store.py`. `require_supabase_client` has tests and is
documented; lack of a production caller does not by itself authorize removal of
that public helper. This is a bounded candidate review, not proof that every
compatibility surface throughout the repository is needed or unused.

A consequence worth making explicit: the shipped debug Supabase test uses those
no-op ledger stubs, so it returns its existing failure envelope and does not
verify real storage writes. Its successful route test uses mocked functions.
It must not be treated as a working database-readiness probe.

## Weather transport review

`server/api/weather_routes.py::fetch_feed` supplies `timeout=(5, 20)` for connect
and read waiting. The current-weather endpoint has a 60-second cache and requests
feeds concurrently. Retrieval/validation failures produce the existing fixed 502
message rather than returning expired data as refreshed weather. This preserves
current behavior; no weather calculations, policy, or operational hooks changed.

Two existing mocked checks passed at closeout: an upstream 204 is accepted as an
empty feed, and a retrieval timeout does not return old data. This is not a new
comprehensive weather transport/cache/concurrency test suite. An aggregate deadline,
SDK timeout policy, and expanded boundary checks belong to P6-D3.

## Verification at closeout

The combined focused Phase 6 command was rerun:

```sh
ISR_RUN_LIVE_AGENT_TESTS=0 PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest -q tests/test_live_agent_test_policy.py tests/test_multi_agent_v4.py tests/test_ledger_retrieval_memory_diagnostics.py tests/test_mission_ledger_embedding.py tests/test_mission_retrieval.py tests/test_session_storage_diagnostics.py tests/test_supabase_configuration.py tests/test_provider_error_presentation.py tests/test_local_launcher.py tests/test_api_route_hardening.py::test_debug_routes_health_and_supabase_probe tests/test_api_route_hardening.py::test_debug_supabase_probe_reports_run_creation_failure
```

Result: **112 passed, 3 live scenarios skipped**. The two weather transport checks
were run separately and passed:

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest -q tests/test_weather_feed.py::test_empty_noaa_response_is_valid tests/test_weather_feed.py::test_retrieval_failure_does_not_return_old_data
```

Provider/database/network boundaries in these checks were mocked or deliberately
blocked. No real provider calls, database writes, application deployment, or
operational scenarios were run. Documentation links and diff whitespace checks
passed. No new tests were added for this documentation-only closeout.

## Next boundary

The next authorized planning step is a focused frontend extraction checklist for
`webapp/isr.js`. It has not been created as part of this closeout. Shared-page
consolidation is already implemented through templates and generated outputs;
repeated HTML in generated pages is expected, not a reason to redo Phase 5.

Rollback of this closeout is limited to documentation. Earlier Phase 6 code/test
changes retain their own recorded validation and rollback scope. Current changes
remain local; no commit, merge, or deployment was performed by this closeout.
