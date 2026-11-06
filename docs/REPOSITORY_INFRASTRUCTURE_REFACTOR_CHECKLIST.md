# Repository and Application Infrastructure Refactor Checklist

This phase covers repository hygiene, developer tooling, shared page structure,
configuration, storage, and general UI infrastructure. Preserve existing behavior
through small, independently reviewable changes. Military targeting,
synchronization, tactical routing, and their operational workflows are outside
this checklist's implementation scope.

The earlier [Refactor Completion Plan](REFACTOR_COMPLETION_PLAN.md) remains a
historical record of completed backend modularization. This checklist starts a
new phase; it does not reopen those completed tasks.

## Baseline and status

Baseline: `Refactor-2`, commit `52bc04d`, plus the local infrastructure changes
listed below. Checked boxes mean implemented and locally verified, not committed,
merged, or deployed. No fresh full-suite pass is claimed by this checklist.
The initial infrastructure baseline was committed as `c2e1e33`. Subsequent data
classification is recorded in [Repository Data Inventory](REPOSITORY_DATA_INVENTORY.md).

| Area | Verified baseline | Priority |
| --- | --- | --- |
| Backend | `server/main.py` is 117 lines; routes and services are already separated | Preserve boundaries |
| Frontend | `webapp/isr.js` is 18,255 lines and combines multiple UI responsibilities | Extract general infrastructure gradually |
| Weather integration | Preview scripts wrap existing functions at runtime | Inventory UI dependencies before changing interfaces |
| Pages | Standard and weather pages share mostly duplicated HTML | Consolidate common shell after browser checks exist |
| Tests | Python and JavaScript regressions exist; some JavaScript tests use simulated DOMs | Distinguish unit checks from browser verification |
| CI | Python jobs exist; a frontend syntax job has been added locally | Verify in CI, then add general UI behavior coverage |
| Documentation | `CLAUDE.md` contains stale file sizes and architecture descriptions | Reconcile with current code |

## Phase 1 Repository hygiene

- [x] Inspect branch, working tree, and stashes before making changes.
- [x] Add `make audit-repo` for a read-only JSON inventory.
- [x] Inventory 576 tracked files, approximately 8.3 MB at the baseline.
- [x] Identify non-empty exact duplicates. The only pair found is an active
  configuration and its template; retain both because they serve different roles.
- [x] Remove repeated `.gitignore` rules without changing matching behavior.
- [x] Classify audited candidates as keep, move, archive, or delete, with a
  reason and references for each decision.
- [x] Distinguish referenced fixtures and retained history from runtime-output
  candidates; retain the legacy result pending a separate decision. An ignore-rule match
  alone does not make a tracked file disposable.
- [x] Preserve documented historical comparison results unless a separate archive
  decision explicitly supersedes the existing retention policy.
- [x] Review migration `.bak` files and the historical restore-document copy.
  Different versions are not exact duplicates.
- [x] Check scripts, tests, documentation, constructed paths, and static URL
  mappings for the current move candidates. Evidence and limitations are in
  [Repository Data Inventory](REPOSITORY_DATA_INVENTORY.md#reference-checks-before-moves).
- [ ] For each actual move, repeat the reference check against the proposed
  destination and update affected references in the same change. No files have
  been moved by the inventory review.
- [ ] Before deletion, establish a recoverable copy and verify that no required
  references remain. Preserve unrelated stashes and private exports.
- [ ] Keep generated audit reports outside tracked source unless intentionally
  publishing a dated baseline.

Done when: every proposed removal has a recorded reason and recovery path, and
retained historical material is distinguishable from current source and fixtures.

## Phase 2 Verification and developer commands

- [x] Add `make check-frontend`, requiring Node 22 or later.
- [x] Verify syntax for all 18 tracked frontend JavaScript files locally.
- [x] Add a frontend syntax job to `.github/workflows/ci.yml`.
- [x] Validate the CI YAML and retain `make` as the help command by default.
- [ ] Run the syntax and infrastructure browser jobs in CI and record their results. Existing triggers run on pull
  requests and pushes to `main` or `master`, not every feature-branch push.
- [x] Record a fresh Python test and quality-check baseline, including existing
  failures and skips, before structural refactors.
  See [Baseline Blockers](Baseline-Blockers.md): pytest collection is blocked by
  missing `torch`; mypy reports four errors. This is not a passing baseline.
- [x] Inventory JavaScript checks by type: syntax, isolated unit, simulated DOM,
  real browser, and network-dependent integration.
- [x] Establish a repeatable command for general infrastructure tests and add it
  to CI with explicit dependencies.
- [x] Add browser smoke coverage for page loading, tab navigation, settings,
  and error presentation using neutral fixtures and mocked services.
- [x] Include both standard and preview pages in shared-shell smoke checks.
- [x] Document which checks need a browser, server, credentials, or internet.

See [JavaScript Checks and Infrastructure Testing](JAVASCRIPT_TEST_INVENTORY.md).
Nine preference unit checks and fourteen Chromium checks passed locally. The new CI job is configured but has not
yet been verified on a hosted runner; that remains an open CI verification task.

Done when: contributors can run the same general infrastructure checks locally
and in CI. Syntax checks are explicitly distinguished from behavioral tests;
they do not check inline HTML scripts or execute the application.

## Phase 3 Documentation accuracy

- [ ] Update `CLAUDE.md` to reflect app assembly in `server/main.py` and routes in
  `server/api/`; remove brittle line counts and stale size claims.
- [ ] Verify developer commands against the Makefile and CI workflow.
- [ ] Document the purpose of standard, preview, and diagnostic pages and their
  script entrypoints.
- [ ] Identify canonical documents and label historical proposals as historical
  without rewriting their original claims as current facts.
- [ ] Link this checklist from the appropriate existing documentation index.
- [ ] Keep file moves and documentation corrections separate from behavior changes.

Done when: a new contributor can locate current entrypoints, run checks, and tell
active guidance from historical session notes.

## Phase 4 General frontend modules

- [x] Map initial dependencies for settings, local storage, API transport, DOM utilities,
  and reusable controls before choosing the first extraction.
- [x] Select one small module with a narrow interface and minimal dependencies.
- [x] Add behavior checks for that module's public contract before moving it.
- [x] Extract settings persistence while preserving storage keys and defaults;
  handle malformed or unavailable browser storage explicitly.
  See [Frontend Infrastructure Boundaries](architecture/FRONTEND_INFRASTRUCTURE.md).
  The preference adapter is extracted; settings presentation and other storage
  consumers remain in `isr.js`. No listener lifecycle changes are needed for this
  adapter because it owns no events or timers.
- [x] Extract generic HTTP transport and response/error handling while preserving
  existing caller behavior. Do not introduce automatic retries for writes.
  Scoped to the three settings requests; other API callers remain unchanged.
  The adapter propagates request/JSON failures and preserves error-status bodies.
- [x] Extract reusable UI controls and DOM helpers without changing labels,
  keyboard behavior, or styling.
  Tab controls are extracted and verified, including native Enter/Space behavior.
  Modal visibility controls are also extracted with backdrop and keyboard checks.
  Shared ID lookup and text assignment are extracted after caller inventory;
  global `$()`/`setText()` wrappers remain for existing consumers. Settings form listeners
  now support repeatable setup/disposal and binding replacement elements.
  This does not cancel in-flight requests or provide asynchronous shutdown.
- [x] Give each extracted component explicit initialization and disposal where
  event listeners or timers are involved.
  Tabs and modal visibility expose idempotent `mount()`/`dispose()`; storage and
  HTTP own no listeners.
- [x] Retain compatibility entrypoints required by existing callers.
  The DOM helper wrappers (`$`, `setText`), tab startup (`attachTabSwitching`),
  and settings modal entrypoints (`openModal`, `closeModal`) intentionally remain.
  They delegate to the extracted modules and preserve existing caller contracts.
  Their removal is deferred follow-up work, not a Phase 4 completion requirement.
  Remove a wrapper only after its callers have migrated, a repository reference
  check confirms no required consumers remain, and the relevant tests pass.
- [x] Avoid mixing a framework migration or visual redesign into module moves.

Done when: extracted modules can be tested without loading the entire application,
and existing general UI behavior remains unchanged.

## Phase 5 Shared page shell and UI extension boundaries

- [x] Compare the standard, preview, and diagnostic HTML and list intentional differences.
- [x] Choose one source for shared navigation, settings, assets, and layout.
  Selected a build-time template with a weather fragment and page-variant manifest;
  generated HTML retains the existing static paths. `make build-pages` regenerates
  all three pages; `make check-pages` rejects stale output locally and in CI.
  See [Shared Page Shell](architecture/SHARED_PAGE_SHELL.md).
- [x] Preserve existing URLs, DOM identifiers, CSS, and script ordering during
  consolidation, with smoke coverage for both pages.
- [x] Make page capabilities explicit in initialization instead of relying on
  undocumented script presence.
  Manifest capability declarations now select registered preview/diagnostic
  initializers through a shared startup contract. Disabled extensions stay inert;
  missing selected dependencies fail explicitly. Locally verified with 34 unit
  checks and 22 Chromium checks. See [Shared Page Shell](architecture/SHARED_PAGE_SHELL.md).
- [x] Inventory runtime function wrappers and classify which are general UI hooks
  versus out-of-scope operational integration.
  Reviewed 11 replacement sites across preview and diagnostics; Cesium init/render
  are general UI candidates, map synchronization is mixed, and operational and
  diagnostic wrappers remain unchanged. See [Runtime Wrapper Inventory](architecture/RUNTIME_WRAPPER_INVENTORY.md).
- [x] For general UI hooks, introduce explicit interfaces one at a time and verify
  each caller before removing its wrapper.
  Cesium init/render now expose lifecycle subscriptions; preview uses one
  subscription instead of its two wrappers. Existing callers remain unchanged.
  Verified with 40 infrastructure unit and 24 Chromium checks plus relevant
  offline regressions. See [the lifecycle contract](architecture/SHARED_PAGE_SHELL.md#cesium-presentation-lifecycle-contract-2026-10-21).
- [x] Keep weather UI enabled on the existing preview page until a separate
  product decision authorizes changing where it appears.
- [x] Leave domain calculations and operational workflow hooks unchanged.
  These last two entries are standing constraints, verified for the Phase 5
  changes: page capabilities/placement and domain/operational implementations
  remain unchanged by the lifecycle migration.

Done when: common layout changes require one source edit, and general UI extension
points have documented inputs, outputs, and initialization order.

## Phase 6 General backend infrastructure

**Closed with explicit deferrals on 2026-10-24.** The scoped infrastructure changes
are locally verified; unchecked items below have deferred remainders rather than
unassigned work. This is not full completion of the original acceptance gate.
See [Phase 6 Closeout](architecture/PHASE_6_CLOSEOUT.md) for reasons, reference
evidence, completion criteria, and verification limits.

- [x] Preserve the existing router/service separation; avoid another broad rewrite.
  Standing constraint verified; existing assembly and service boundaries remain.
- [ ] Review configuration loading and precedence with credentials excluded from
  logs and test fixtures. **Scoped review complete; remainder deferred (P6-D1/D2).**
  Launch precedence and covered adapter diagnostics are documented in
  [Backend Configuration](architecture/BACKEND_CONFIGURATION.md). Supabase,
  provider, session, ledger, retrieval, and agent-memory diagnostics use fixed
  messages. Live v4 tests are explicitly opt-in with shared fixture isolation.
  Wider application logging and suite-wide isolation require separate work.
- [x] Document health/readiness semantics and optional-service dependencies.
  [Backend Health and Readiness](architecture/BACKEND_HEALTH_AND_READINESS.md)
  documents liveness-only `/health`, absent readiness semantics, optional features,
  probe differences, and the debug endpoint's no-op ledger dependency.
  Probe implementation changes are deferred separately (P6-D4).
- [ ] Review generic external-service timeout and error presentation behavior
  using mocks, without changing domain outcomes. **Scoped review complete;
  policy/coverage expansion deferred (P6-D3).**
  LLM connection checks, Ollama, ElevenLabs, and the existing weather transport
  behavior are reviewed. Request timeout arguments and response contracts are
  preserved; explicit SDK/aggregate timeout policy is not implemented.
  See [Provider Service Boundaries](architecture/PROVIDER_SERVICE_BOUNDARIES.md).
- [ ] Remove obsolete compatibility infrastructure only after reference checks
  and focused tests establish that it is unused. **Retain reviewed interfaces;
  caller migration/removal deferred (P6-D5).**
  All four legacy ledger stubs still have source callers/imports. No reviewed
  interface was proven disposable, so nothing was removed.

Closeout verification: 112 focused checks passed, 3 live scenarios skipped; two
existing mocked weather transport checks passed separately. No full-suite,
container/startup, live-service, or hosted CI pass is implied.

Done when: startup, configuration, and optional-service failures are predictable
and independently testable without operational scenarios. Deferred entries above
remain outside the achieved scope of this iteration.

## Completion gate for every change

- [ ] Scope is one cohesive hygiene or infrastructure change.
- [ ] References and compatibility effects are checked.
- [ ] Relevant checks pass; pre-existing failures are explicitly distinguished.
- [ ] UI changes receive real-browser verification appropriate to their scope.
- [ ] Documentation reflects new commands, paths, and interfaces.
- [ ] Diff contains no credentials, private exports, generated noise, or unrelated work.
- [ ] Review notes explain the change, evidence, limitations, and rollback path.
- [ ] Commit, merge, and deployment status are reported separately from local completion.

## Current milestone status

Phase 6 is closed for this iteration with its explicit deferrals. The
[Frontend Extraction Checklist](FRONTEND_EXTRACTION_CHECKLIST.md) is also closed
for its selected small-extraction pass: nine extractions and the Leaflet timer
follow-up are complete. The [frontend closeout](architecture/FRONTEND_EXTRACTION_CLOSEOUT.md)
records verification limits and retained/deferred integration.

The [Frontend Architecture Plan](architecture/FRONTEND_ARCHITECTURE_PLAN.md) is
closed for its selected general UI iteration: A/B introduced inert composition,
C verified listener ownership, D verified presentation adapters, and E evaluated
and recorded closure. The [architecture closeout](architecture/FRONTEND_ARCHITECTURE_CLOSEOUT.md)
records source scope, verification limits and explicit retained/deferred work.
No next extraction or full-application lifecycle/startup rewrite is selected.
Shared application/workflow state, mixed trace and memory actions, and domain
calculations/operational workflows retain their documented boundaries. Shared HTML
generation from Phase 5 remains the source of truth for common page edits.

Earlier hosted-CI verification, repository hygiene, and documentation tasks remain
in their respective phases; this closeout does not complete them or supersede
[the recorded baseline results](Baseline-Blockers.md). Commit, merge, and deployment
status are separate from local verification.
