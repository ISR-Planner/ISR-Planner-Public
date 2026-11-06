# Implementation Plan: Agent Benchmark Harness

**Branch**: `feat/agent-comparison` | **Date**: 2026-04-21 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from [specs/004-agent-benchmark-harness/spec.md](spec.md)

## Summary

Build a standalone Python script, `scripts/benchmark_agents.py`, that generates randomized ISR
environments and invokes both the single-agent (v1) and LangGraph v4 planners on each via the
running planner server's existing `/api/agents/compare` endpoint. The script accumulates per-run
and aggregate metrics into a timestamped batch directory under `benchmarks/`. Per the user's
direction, the harness is a long-running foreground script designed to run unattended overnight:
it streams per-run results to `runs.jsonl` as each run completes (so Ctrl-C or a crash never
loses previously finished work), handles transient agent failures with one retry, enforces a
120 s per-agent timeout, and writes a human-readable `summary.md` at the end.

## Technical Context

**Language/Version**: Python 3.11 (matches existing `scripts/test_agent_compare.py` and server runtime).

**Primary Dependencies**:
- `requests` — already used by `scripts/test_agent_compare.py`; sole HTTP client dependency.
- `random` + `hashlib` (stdlib) — seeded environment generation.
- `json` (stdlib) — manifest and JSONL serialization.
- `argparse` (stdlib) — CLI flags.
- `math` (stdlib) — haversine distance for feasibility filter.
- **Not used**: pandas, numpy, pydantic, httpx. Stdlib only besides `requests` to keep the script trivially runnable.

**Storage**: Local filesystem only. Output tree:
```
benchmarks/<UTC-timestamp>-<batch-name>/
├── manifest.json          # batch config + aggregate summary (written at end, atomically)
├── runs.jsonl             # one JSON record per completed run, append-only as runs finish
└── summary.md             # human-readable head-to-head (written at end)
```

**Testing**: `pytest` under `tests/benchmark/`. Tests MUST NOT require a live planner server
or LLM credentials — they exercise the generator, the feasibility filter, the metric aggregator,
and the CLI argument parser against fixtures and mocked HTTP responses.

**Target Platform**: macOS / Linux dev machines. The script assumes a planner server running on
the same host reachable at `http://localhost:8893` by default (overridable via `--server-url`).

**Project Type**: Standalone CLI utility script. No new server endpoints; purely a client of the
existing `/api/agents/compare` endpoint.

**Performance Goals**:
- Harness overhead per run (generation + HTTP I/O + metric extraction, excluding agent latency):
  ≤ 500 ms wall-clock.
- Script idles near zero CPU between runs (pure request-wait behavior).
- Designed for overnight unattended runs (target capacity: 8-hour runs = ~300 environments at
  a ~90 s average per-run agent latency).

**Constraints**:
- 120 s per-agent wall-clock timeout (FR-009a) — script MUST NOT block longer than
  2 × 120 s + retry_budget per run.
- JSONL append model MUST be crash-safe: every line is a complete JSON document committed
  with `file.flush()` + `os.fsync()` before the next run begins.
- Output directory naming MUST avoid collisions across concurrently-started runs
  (timestamp is second-resolution with a `-<short-name>` suffix; operator supplies `--name`
  to disambiguate parallel invocations).
- No background threads or async. Single-threaded, sequential per-run execution — keeps
  failure modes analyzable in overnight runs.

**Scale/Scope**: Single script, ~400–600 LOC Python. 1 batch directory per invocation.
Per-run record ≈ 20–50 KB of JSON (mostly agent response text); a 500-run overnight batch
produces ~10–25 MB of `runs.jsonl`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Verify against [.specify/memory/constitution.md](../../.specify/memory/constitution.md) (v1.1.0):

- [x] **I. Code Quality** — Script will be one file under 600 LOC, decomposed into
  named top-level functions (`generate_environment`, `feasibility_filter`, `run_single_batch_step`,
  `write_manifest`, `main`), no magic numbers (retry count, timeout, default bbox size,
  min feasibility threshold all exposed as module-level constants or CLI flags), no dead code,
  absolute imports only.
- [x] **II. Testing Standards** — Unit tests planned for (a) seeded generator determinism,
  (b) feasibility filter correctness, (c) metric aggregator over mocked run records,
  (d) JSONL crash-safety (open/write/kill/reopen readable), (e) retry policy classification.
  No test requires a live API key; network is mocked via `responses` or `requests_mock`
  (or a simple monkey-patched `requests.post` if we want zero extra deps).
- [x] **III. UX Consistency** — CLI-only tool; UX consistency means consistent flag naming
  (`--runs`, `--seed`, `--bbox`, `--prompt`, `--output-dir`, `--name`, `--inter-run-delay`,
  `--server-url`, `--max-retries`, `--timeout-s`), a progress line per run
  (e.g., `[3/20] env seed=42/2 reasoning=45.1s lg_v4=52.3s`), and explicit terminal summary
  on completion or Ctrl-C. No raw tracebacks at the exit boundary — failures print a
  one-line human-readable reason.
- [x] **IV. Performance** — Harness itself is I/O-bound; it does not touch solver hot paths.
  The existing `/api/agents/compare` endpoint remains the performance-sensitive component
  and already has its own constitution-mandated budget. The harness's own perf target
  (≤ 500 ms overhead per run) is well within IV thresholds.

**Design-time enforcement** (Governance → Technical Decision Guidance): Every decision in
[research.md](research.md) below cites either a principle or a spec requirement. Per the
*Principle-to-decision mapping*, because this introduces **no new dependencies** beyond
the already-present `requests` library and **no changes to solver, agent, or UI code**,
no constitution exception is required.

## Project Structure

### Documentation (this feature)

```text
specs/004-agent-benchmark-harness/
├── plan.md              # This file
├── research.md          # Phase 0 — design decisions + alternatives
├── data-model.md        # Phase 1 — JSON schemas for manifest and run record
├── contracts/
│   └── compare-endpoint.md   # Phase 1 — captured contract of /api/agents/compare as consumed
├── quickstart.md        # Phase 1 — operator runbook
├── spec.md              # Already exists
└── checklists/
    └── requirements.md  # Already exists
```

### Source Code (repository root)

```text
isr-planner/
├── scripts/
│   └── benchmark_agents.py          # NEW — the harness script (this feature)
├── tests/
│   └── benchmark/                   # NEW — unit tests for the harness
│       ├── test_environment_generator.py
│       ├── test_feasibility_filter.py
│       ├── test_metric_aggregator.py
│       ├── test_jsonl_output.py
│       └── test_retry_policy.py
├── benchmarks/                      # NEW — gitignored output root (created on first run)
│   └── <UTC-timestamp>-<name>/
│       ├── manifest.json
│       ├── runs.jsonl
│       └── summary.md
├── server/                          # Unchanged
├── webapp/                          # Unchanged
└── orienteering_with_matrix.py      # Unchanged
```

**Structure Decision**: Single-file CLI under [scripts/](../../scripts/) alongside the
existing [test_agent_compare.py](../../scripts/test_agent_compare.py). Tests live under
[tests/benchmark/](../../tests/benchmark/) so they are picked up automatically by the
root `pytest` configuration. The `benchmarks/` directory is new and MUST be added to
`.gitignore` (batches are local artifacts, not source). Zero changes to `server/` or
`webapp/`.

## Implementation Walkthrough

Build in this order. Each stage is independently testable and builds on the previous one's
surface. The "Read" column points an implementer at the single place where the rules for that
stage actually live.

| # | Stage | Key symbols | Satisfies | Read | Validated by |
|---|---|---|---|---|---|
| 1 | **Module skeleton + constants** — create `scripts/benchmark_agents.py`, declare all module-level constants (see Constants & Defaults table below), `argparse` setup with the flags from [quickstart.md §CLI reference](quickstart.md#cli-reference), and `main()` stub. Add `/benchmarks/` to repo `.gitignore`. | `DEFAULT_PROMPT`, `PER_AGENT_TIMEOUT_S`, `HTTP_TIMEOUT_S`, `MAX_RETRIES`, `MIN_REACHABLE_TARGETS`, `MAX_REGEN_ATTEMPTS_PER_SLOT`, `REACHABILITY_SAFETY_MARGIN`, `INTER_RUN_DELAY_S`, `HEARTBEAT_INTERVAL_S`, `SLUG_PATTERN`, `parse_args()`, `main()` | FR-002, FR-005a, FR-006a, FR-007, FR-010, research §10 | [research §1](research.md#1-script-structure-single-file-vs-module-package), [research §9](research.md#9-output-directory-naming), [quickstart.md §CLI reference](quickstart.md#cli-reference), Constants & Defaults table | smoke import test |
| 2 | **Seeding helpers** — derive deterministic per-run and per-attempt sub-seeds from the batch seed. | `derive_sub_seed(batch_seed, run_index, attempt=0) -> int`, `make_run_rng(batch_seed, run_index, attempt) -> random.Random` | FR-003 | [research §3](research.md#3-environment-generator-reproducibility-mechanism) | `tests/benchmark/test_environment_generator.py::test_sub_seed_determinism` |
| 3 | **Environment generator** — given an rng and generator parameters, produce one candidate environment. Must produce byte-for-byte identical envs given the same rng seed. | `generate_environment(rng, params) -> dict` (shape matches [data-model §2](data-model.md#2-environment-record-generator-output-embedded-in-each-run)) | FR-001, FR-002, FR-003, FR-004, FR-005 | [data-model §2](data-model.md#2-environment-record-generator-output-embedded-in-each-run) (schema + field constraints) | `test_environment_generator.py::{test_respects_counts, test_field_constraints, test_seed_reproducibility}` |
| 4 | **Feasibility filter** — haversine-based reachability check; returns `(reachable_count, is_low_feasibility)`. Never calls the server. | `haversine_km(a, b) -> float`, `reachable_target_count(env) -> int`, `is_feasible(env) -> bool` | FR-005b | [research §4](research.md#4-feasibility-filter-reachability-check) (safety margin + thresholds) | `tests/benchmark/test_feasibility_filter.py` — unreachable, SAM-blocked, and fuel-constrained fixtures |
| 5 | **Env acceptance loop** — wraps stages 3 + 4: regenerate up to `MAX_REGEN_ATTEMPTS_PER_SLOT` times, then accept with `low_feasibility: true`. | `accept_environment(batch_seed, run_index, params) -> (env, env_meta)` | FR-005b, spec Edge Case "Infeasible environments" | [research §4](research.md#4-feasibility-filter-reachability-check), [data-model §4 `env_meta`](data-model.md#4-runsjsonl--one-json-record-per-line) | `test_feasibility_filter.py::test_accept_loop_exhausts_then_flags` |
| 6 | **HTTP error classifier + retry wrapper** — single `_invoke_compare_with_retry()` helper that classifies every exception / response into the `error.kind` enum and retries exactly once on transient. | `classify_error(resp_or_exc) -> ErrorKind`, `_invoke_compare_with_retry(session, url, body) -> (response_json \| error_dict, retries_used)` | FR-009, FR-009a, FR-009b, FR-009c | [research §8](research.md#8-retry-and-timeout-implementation), [contracts/compare-endpoint.md §Failure classification](contracts/compare-endpoint.md#failure-classification-harness-side) | `tests/benchmark/test_retry_policy.py` — monkey-patch `session.post`, assert retry exactly for each transient kind, never for each permanent kind |
| 7 | **Startup checks** — server reachability probe + slug validation + output-directory creation. Must fail fast with a one-line human-readable error before touching disk. | `assert_server_reachable(url)`, `validate_slug(name)`, `create_batch_dir(...)` | FR-008, research §9, spec Edge Case "Planner server not running" | [research §9](research.md#9-output-directory-naming), [quickstart.md §Failure modes](quickstart.md#failure-modes-and-what-to-do) | manual; integration test optional |
| 8 | **JSONL writer + SIGINT handler** — open `runs.jsonl` in append + line-buffered mode, write-flush-fsync per line, install `SIGINT` handler that sets a module-level `STOP` flag. | `open_jsonl_writer(path) -> file`, `write_run_record(f, record)`, `install_sigint_handler() -> callable returning bool`, `HEARTBEAT_INTERVAL_S = 300` | FR-014 (crash-safety), spec US2 acceptance #3 | [research §5](research.md#5-overnight-operation-interrupt-safety-and-progress-visibility), [data-model §4](data-model.md#4-runsjsonl--one-json-record-per-line) | `tests/benchmark/test_jsonl_output.py` — write N, kill mid-line, reopen, assert first N−1 readable |
| 9 | **Per-run driver** — given an accepted env, POST to `/api/agents/compare`, normalize the response into a run record, call the JSONL writer. This is the state-machine loop from [data-model §6](data-model.md#6-state-transitions). | `run_single_batch_step(session, batch_seed, run_index, params, jsonl_writer) -> run_record` | FR-006, FR-011, FR-012, data-model §6 | [contracts/compare-endpoint.md](contracts/compare-endpoint.md), [data-model §4](data-model.md#4-runsjsonl--one-json-record-per-line), [data-model §6](data-model.md#6-state-transitions) | exercised by stage-10 integration harness with a mocked session |
| 10 | **Batch loop** — the outer `for run_index in range(runs_requested): ...` with inter-run delay, heartbeat thread (or inline timer), and STOP-flag check between runs. Emits one progress line per run. | extend `main()` | FR-007, FR-010, FR-015 (progress visibility), US1 | [research §5](research.md#5-overnight-operation-interrupt-safety-and-progress-visibility), [research §6](research.md#6-parallelism-concurrent-agent-calls-per-run) | manual overnight dry-run with `--dry-run` |
| 11 | **Metric aggregator** — consume `runs.jsonl` in memory, compute the `aggregate` block of `manifest.json` per rules in [data-model §3](data-model.md#3-manifestjson-schema). | `aggregate_runs(run_records) -> aggregate_dict`, `percentile(values, p) -> float` | FR-013, research §7 | [data-model §3 aggregate rules](data-model.md#aggregate-computation-rules-fr-013-research-7), [research §7](research.md#7-metric-aggregation-how-to-handle-mixed-successfailure-runs) | `tests/benchmark/test_metric_aggregator.py` with fixture JSONL covering all 4 both/one-sided/neither buckets |
| 12 | **Manifest + summary writer** — compose `manifest.json` (atomic write via temp file + rename), render `summary.md` from the aggregate, print summary to stdout. | `write_manifest(path, config, aggregate)`, `render_summary_markdown(manifest) -> str` | FR-014, FR-015, FR-016 | [data-model §3](data-model.md#3-manifestjson-schema), [data-model §5](data-model.md#5-summarymd--human-readable-head-to-head) | snapshot test: run aggregator+renderer on fixture JSONL, diff against checked-in `expected_summary.md` |
| 13 | **End-to-end wiring in `main()`** — put stages 1–12 together in the documented order (startup checks → batch loop → aggregator → manifest/summary → stdout summary). Handle SIGINT by jumping straight to stage 12 with `completed: false`. | complete `main()` | all user stories | all detail files | `python3 scripts/benchmark_agents.py --runs 2 --seed 1 --name smoke --dry-run` exits clean |

**Stages 1–8 are independent of the server** and can all be built and tested offline. Stage 9
is the first stage that requires either a running server or a mocked `session.post`. Stages
11–12 consume JSONL that can be fixtures, so they are also offline-testable.

## Constants & Defaults

Every tunable value with its source of truth. All of these MUST be declared as module-level
constants in `scripts/benchmark_agents.py` (Principle I — no magic numbers). Values marked
"CLI override" are also exposed as argparse flags; the constant is the default.

| Constant | Value | CLI override | Source |
|---|---|---|---|
| `DEFAULT_PROMPT` | `"Plan a mission to maximize priority points. Visit as many targets as possible."` | `--prompt` | spec FR-006a, clarification Q3 |
| `DEFAULT_SERVER_URL` | `"http://localhost:8893"` | `--server-url` | plan Technical Context |
| `DEFAULT_OUTPUT_DIR` | `"benchmarks"` | `--output-dir` | spec FR-014 |
| `DEFAULT_BATCH_NAME` | `"default"` | `--name` | research §9 |
| `SLUG_PATTERN` | `r"^[a-z0-9-]+$"` | — | research §9 |
| `DEFAULT_AIRPORTS` | `2` | `--airports` | data-model §2 |
| `DEFAULT_TARGETS` | `8` | `--targets` | data-model §2 |
| `DEFAULT_SAMS` | `2` | `--sams` | data-model §2 |
| `DEFAULT_DRONES` | `3` | `--drones` | data-model §2 |
| `DEFAULT_BBOX` | `(32.0, 33.0, -90.5, -89.5)` | `--bbox` | quickstart §CLI reference, spec FR-005a |
| `DEFAULT_FUEL_BUDGET_KM` | `250.0` | `--fuel-budget-km` | quickstart §CLI reference |
| `DEFAULT_SPEED_KMH` | `120.0` | — | data-model §2 (matches `test_agent_compare.py`) |
| `TARGET_PRIORITY_RANGE` | `(1, 10)` inclusive | — | data-model §2 |
| `SAM_RANGE_KM_RANGE` | `(5.0, 25.0)` | — | data-model §2 |
| `TARGET_TYPES` | `("a", "b", "c")` | — | data-model §2 |
| `PER_AGENT_TIMEOUT_S` | `120` | `--per-agent-timeout-s` | spec FR-009a |
| `HTTP_TIMEOUT_S` | `130` | — (derived) | research §8 (server headroom) |
| `MAX_RETRIES` | `1` | `--max-retries` | spec FR-009b |
| `INTER_RUN_DELAY_S` | `2.0` | `--inter-run-delay` | spec FR-010, quickstart |
| `MIN_REACHABLE_TARGETS` | `2` | `--min-reachable-targets` | spec FR-005b, clarification Q5 |
| `MAX_REGEN_ATTEMPTS_PER_SLOT` | `10` | `--max-regen-attempts` | spec FR-005b, research §4 |
| `REACHABILITY_SAFETY_MARGIN` | `1.10` | — | research §4 (SAM-detour inflation) |
| `HEARTBEAT_INTERVAL_S` | `300` | — | research §5 (overnight visibility) |
| `FUEL_TIE_TOLERANCE_KM` | `0.1` | — | data-model §3 aggregate rules |
| `SCHEMA_VERSION` | `1` | — | data-model §3 and §4 |

## FR → Test mapping

Each test file covers a specific FR cluster. An FR is considered "verified" when at least one
test from this mapping passes.

| Test file | Covers |
|---|---|
| `test_environment_generator.py` | FR-001, FR-002, FR-003, FR-004, FR-005, FR-005a |
| `test_feasibility_filter.py` | FR-005b (including the low-feasibility flag path) |
| `test_retry_policy.py` | FR-009, FR-009a, FR-009b, FR-009c |
| `test_jsonl_output.py` | FR-014 (crash-safety clause), US2 acceptance #3 |
| `test_metric_aggregator.py` | FR-012, FR-013, research §7 (both / one-sided / neither buckets) |

FRs covered by the harness main loop rather than a dedicated test (because they are
integration-level behavior): FR-006, FR-006a, FR-007, FR-008, FR-010, FR-011, FR-015, FR-016.
A quickstart smoke run (`--runs 2 --dry-run`) exercises them end-to-end.

## Complexity Tracking

No constitution violations — table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
