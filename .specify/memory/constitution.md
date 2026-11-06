<!--
SYNC IMPACT REPORT
==================
Version change: 1.0.0 → 1.1.0
Bump rationale: MINOR — new "Technical Decision Guidance" subsection added to Governance,
  expanding how Principles I–IV guide day-to-day implementation choices. No principles
  removed or redefined; no backward-incompatible governance changes.
Modified principles: none (Principles I–IV unchanged)
Added sections:
  - Governance → Technical Decision Guidance (new subsection)
Removed sections: none
Templates requiring updates:
  ✅ .specify/templates/plan-template.md — Constitution Check version reference updated to v1.1.0
  ✅ .specify/templates/spec-template.md — No changes needed; no mandatory section impact
  ✅ .specify/templates/tasks-template.md — No changes needed; workflow unaffected
Deferred TODOs: none
-->

# ISR Mission Planner Constitution

## Core Principles

### I. Code Quality (NON-NEGOTIABLE)

Every code change MUST leave the codebase in a cleaner or equivalent state.
Specific rules:

- Functions MUST have a single, clearly named responsibility; functions exceeding 80 lines
  MUST be decomposed unless the full body is a single algorithm (e.g., the Held-Karp solver).
- Magic numbers MUST be named constants; raw numeric bounds (fuel capacity, grid size,
  target scores) MUST be defined as module-level constants or config values.
- Dead code and commented-out blocks MUST be removed before merging; use version control
  for history.
- The monolithic `webapp/isr.js` MUST not grow in surface area without a corresponding
  refactoring task logged. New UI capabilities MUST be added as clearly delineated functions,
  not inlined into existing large functions.
- All backend modules (`server/`, `server/solver/`, `server/agents/`) MUST use absolute
  imports relative to the project root; relative imports are prohibited.

### II. Testing Standards

All solver, allocator, and trajectory-planning logic MUST be covered by automated tests.

- Every bug fix MUST be accompanied by a regression test that reproduces the failure
  before the fix and passes after.
- Unit tests MUST be provided for: orienteering solver, target allocator strategies,
  post-optimizer (insert/swap/no-cross), SAM distance matrix, and trajectory planner.
- Integration tests MUST cover the full `/solve-with-allocation` → trajectory pipeline
  for at least one scenario per allocator strategy (efficient, greedy, balanced,
  geographic, exclusive).
- Agent tests (`tests/test_multi_agent_v4.py`) MUST cover coordinator intent
  classification and each LangGraph node's output contract.
- Tests MUST be runnable with `pytest` from the project root with `PYTHONPATH` set;
  no tests may require a live `ANTHROPIC_API_KEY` unless explicitly tagged `@pytest.mark.live`.
- Test coverage for `server/solver/` MUST remain ≥ 80 % line coverage.

### III. User Experience Consistency

The frontend MUST present a predictable, coherent interface across all mission states.

- All interactive controls (buttons, canvas clicks, drag) MUST provide immediate visual
  feedback within one animation frame (≤ 16 ms on a standard desktop).
- Mission state transitions (edit → solved → accepted → segmented) MUST be communicated
  via a clearly visible status indicator; silent state changes are prohibited.
- Canvas rendering MUST be idempotent: calling `renderCanvas()` twice with the same
  state MUST produce the same visual output with no flickering or partial redraws.
- Error messages surfaced to the user MUST be human-readable and actionable; raw
  Python tracebacks or JSON error blobs MUST NOT be shown in the UI.
- Animated mission playback MUST run at a consistent frame rate; frame skipping is
  acceptable under load but the animation timeline MUST remain synchronized with
  mission time.
- UI controls that become invalid for the current mission state MUST be visually
  disabled, not silently no-op.

### IV. Performance Requirements

The system MUST remain responsive under the expected operational load.

- The orienteering solver MUST return a solution for ≤ 5 drones × 15 targets within
  **10 seconds** of wall-clock time on a standard development machine.
- FastAPI endpoints MUST respond within **30 seconds** for any solve request; requests
  exceeding this MUST return a structured timeout error, not hang.
- Canvas rendering MUST target **60 fps** during animation; any single `renderCanvas()`
  call MUST complete within **16 ms** for scenes with ≤ 5 drones and ≤ 20 waypoints.
- Agent planning (`/agent/plan`) MUST surface streaming partial responses to the client;
  the UI MUST NOT appear frozen during LLM inference.
- SAM distance matrix pre-computation MUST be cached per environment configuration;
  recomputation MUST only be triggered when the environment changes.

## Performance Standards

Quantitative thresholds that MUST be met before any feature is considered complete:

| Metric | Threshold | Measurement Method |
|---|---|---|
| Solver wall-clock time (5 drones, 15 targets) | ≤ 10 s | `pytest` benchmark in `tests/` |
| API p95 response time (`/solve-with-allocation`) | ≤ 30 s | Manual timing or load test |
| Canvas frame time | ≤ 16 ms | Browser DevTools Performance panel |
| Backend startup time | ≤ 5 s | `time ./run_planner.sh` until first request accepted |
| Pytest suite (unit + integration, no live calls) | ≤ 60 s total | `pytest --timeout=60` |

Regressions against any threshold MUST be flagged and addressed before merge.

## Development Workflow & Quality Gates

**Branch policy**: All changes target feature branches; direct commits to `main` or
`release/*` branches are prohibited except for hotfixes with a documented justification.

**Quality gate sequence** (all gates MUST pass before merge):

1. **Constitution Check** — Reviewer confirms the change complies with Principles I–IV.
2. **Test Gate** — `pytest` passes with zero failures; coverage for `server/solver/`
   ≥ 80 %; no new `@pytest.mark.skip` added without a linked TODO.
3. **Performance Gate** — Solver and canvas benchmarks remain within thresholds defined
   in the Performance Standards section.
4. **UX Review** — Any frontend change is validated in browser against at least one
   happy-path mission scenario (place targets → solve → accept → animate).
5. **Import Sanity** — `PYTHONPATH=$(pwd) python -c "from server.main import app"` MUST
   succeed in a clean environment.

**Hotfix process**: Constitution compliance review may be condensed to async Slack/PR
comment for P0 incidents, but ALL skipped gates MUST be addressed in a follow-up PR
within 48 hours.

## Governance

- This constitution supersedes all other informal practices and conventions documented
  in the repository; in case of conflict, this document takes precedence.
- Amendments MUST be proposed via PR, incrementing the version according to semantic
  versioning rules (MAJOR: principle removal/redefinition; MINOR: new principle or
  section; PATCH: clarification or wording).
- All PRs and code reviews MUST explicitly check compliance against the Core Principles.
- The `CLAUDE.md` file at the repository root serves as the runtime development guidance
  document for AI coding agents; it MUST be kept consistent with this constitution.
- Complexity MUST be justified: any new abstraction, helper, or module MUST reference
  a specific principle or measurable requirement it satisfies.

### Technical Decision Guidance

The Core Principles are not advisory — they MUST actively shape technical decisions
at design, implementation, and review time. The following rules operationalize that
expectation.

**Principle-to-decision mapping.** Every technical decision SHOULD be traceable to at
least one principle. Use this table when justifying choices in PR descriptions, design
docs, and `/speckit.plan` Constitution Check sections:

| Decision type | Governing principle(s) |
|---|---|
| Adding a new module, helper, or abstraction | I (Code Quality) — must cite the concrete duplication or complexity it removes |
| Modifying solver, allocator, post-optimizer, or trajectory planner | II (Testing) + IV (Performance) — must show test coverage and benchmark impact |
| Adding or changing a frontend control, state, or canvas interaction | III (UX Consistency) — must document feedback latency and state-transition visibility |
| Introducing an LLM call, agent tool, or new agent node | III + IV — must specify streaming behavior and 30 s timeout handling |
| Adding a new API endpoint or changing an existing contract | II + IV — must include integration test and p95 timing budget |
| Introducing a dependency (pip package, JS library) | I — must cite the specific capability the dependency provides that cannot be reasonably built in-tree |
| Relaxing or skipping a test | II — requires a linked TODO and an approver who is not the author |
| Changing a Performance Standards threshold | IV — requires a MINOR or MAJOR constitution amendment, not an in-PR exception |

**Trade-off resolution order.** When principles conflict, resolve in this priority:

1. **Correctness over speed.** A slower correct solver beats a faster wrong one. Test
   failures are never resolved by loosening the test.
2. **Performance Standards over code-aesthetic preferences.** If a cleaner abstraction
   cannot meet Principle IV thresholds, the threshold wins — optimize or redesign.
3. **UX Consistency over feature breadth.** A new feature that introduces inconsistent
   state feedback or silent failure modes MUST be held until the UX issue is resolved,
   even if this delays delivery.
4. **Code Quality over convenience.** Short-term shortcuts (duplicated logic, magic
   numbers, skipped tests, inlining into `webapp/isr.js`) MUST be refactored or
   explicitly tracked with a dated TODO before merge.

**Escalation path.** When a change cannot comply with a principle and the author
believes an exception is warranted:

1. The author MUST document the violation in the PR description under an explicit
   "Constitution Exception" heading, naming the principle and the reason.
2. The author MUST propose a remediation plan with a target date, or propose a
   constitution amendment if the violation reflects a lasting change in project direction.
3. A reviewer other than the author MUST explicitly approve the exception. Exceptions
   carried without remediation for more than 30 days MUST be escalated to a constitution
   amendment PR.

**Design-time enforcement.** `/speckit.plan` and `/speckit.tasks` outputs MUST satisfy
the Constitution Check before implementation begins. Principle violations identified at
plan time MUST be resolved by redesign or formally tracked in the plan's Complexity
Tracking table — they MUST NOT be deferred to implementation with the expectation of
discovering a workaround later.

**Review-time enforcement.** Code reviewers MUST reject PRs that:

- Add magic numbers, dead code, or relative imports in `server/`.
- Introduce new solver or agent behavior without tests.
- Introduce silent UI state changes or raw error blobs in the frontend.
- Regress a Performance Standards threshold without an accompanying amendment PR.

Reviewers are empowered — and expected — to block on these grounds without escalation.

**Version**: 1.1.0 | **Ratified**: 2026-04-10 | **Last Amended**: 2026-04-21
