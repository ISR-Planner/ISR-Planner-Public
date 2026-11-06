---

description: "Dependency-ordered task list for the agent benchmark harness"
---

# Tasks: Agent Benchmark Harness

**Input**: Design documents from `/specs/004-agent-benchmark-harness/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/compare-endpoint.md](contracts/compare-endpoint.md),
[quickstart.md](quickstart.md)

**Tests**: Included. The spec's Principle II (Testing Standards) and plan §FR → Test mapping
call for five dedicated test files; all are generated below under their owning user story.

**Organization**: Tasks are grouped by user story so each story can be implemented, tested,
and deployed independently. The 13-stage Implementation Walkthrough in
[plan.md §Implementation Walkthrough](plan.md#implementation-walkthrough) is the dependency
backbone; each task below cites the plan stage and the detail-file section(s) the implementer
should read.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks).
- **[Story]**: Which user story this task belongs to (US1, US2, US3). Absent for Setup,
  Foundational, and Polish phases.
- Every implementation task cites the exact file path plus the plan / detail-file section
  the implementer must read.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: One-time scaffolding shared by every user story. No server calls, no tests yet.

- [X] T001 Create directory skeleton: ensure [scripts/](../../scripts/) exists; create [tests/benchmark/](../../tests/benchmark/) with an empty `__init__.py` so pytest discovers the package
- [X] T002 Add `/benchmarks/` to the repo's top-level [.gitignore](../../.gitignore) per [research.md §10](research.md#10-gitignore-entry)
- [X] T003 [P] Create the module skeleton at [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py): shebang, module docstring citing the plan, absolute imports (`import argparse`, `hashlib`, `json`, `math`, `os`, `random`, `signal`, `sys`, `time`, `requests`), and a bare `def main(): ...` + `if __name__ == "__main__": main()` entry point. Per [plan.md §Implementation Walkthrough stage 1](plan.md#implementation-walkthrough)
- [X] T004 [P] Declare all module-level constants from [plan.md §Constants & Defaults](plan.md#constants--defaults) at the top of [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) (`DEFAULT_PROMPT`, `DEFAULT_SERVER_URL`, `DEFAULT_OUTPUT_DIR`, `DEFAULT_BATCH_NAME`, `SLUG_PATTERN`, `DEFAULT_AIRPORTS`, `DEFAULT_TARGETS`, `DEFAULT_SAMS`, `DEFAULT_DRONES`, `DEFAULT_BBOX`, `DEFAULT_FUEL_BUDGET_KM`, `DEFAULT_SPEED_KMH`, `TARGET_PRIORITY_RANGE`, `SAM_RANGE_KM_RANGE`, `TARGET_TYPES`, `PER_AGENT_TIMEOUT_S`, `HTTP_TIMEOUT_S`, `MAX_RETRIES`, `INTER_RUN_DELAY_S`, `MIN_REACHABLE_TARGETS`, `MAX_REGEN_ATTEMPTS_PER_SLOT`, `REACHABILITY_SAFETY_MARGIN`, `HEARTBEAT_INTERVAL_S`, `FUEL_TIE_TOLERANCE_KM`, `SCHEMA_VERSION`). No magic numbers anywhere else in the file.

**Checkpoint**: Running `python3 scripts/benchmark_agents.py` exits cleanly with `main()` as a no-op. Imports resolve. `pytest tests/benchmark/` discovers zero tests but exits 0.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Pure functions with no external dependencies, shared by every user story. Stages 2, 6, 7 of the Implementation Walkthrough. These are the building blocks; user-story phases glue them together.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T005 Implement `parse_args() -> argparse.Namespace` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) wiring every constant from T004 to its CLI flag per [quickstart.md §CLI reference](quickstart.md#cli-reference). Include `--bbox` parser that splits on `,` into four floats and validates `lat_min < lat_max` and `lng_min < lng_max`. Include `--name` validator that checks `SLUG_PATTERN`. Include `--dry-run`. `--runs` is required
- [X] T006 Implement `derive_sub_seed(batch_seed: int, run_index: int, attempt: int = 0) -> int` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) using `hashlib.sha256(f"{batch_seed}:{run_index}:{attempt}".encode()).digest()[:8]` interpreted as little-endian unsigned int. Per [research.md §3](research.md#3-environment-generator-reproducibility-mechanism) and [plan stage 2](plan.md#implementation-walkthrough)
- [X] T007 Implement `make_run_rng(batch_seed: int, run_index: int, attempt: int = 0) -> random.Random` that returns a fresh `random.Random` seeded with `derive_sub_seed(...)`. Per [research.md §3](research.md#3-environment-generator-reproducibility-mechanism)
- [X] T008 [P] Implement `haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) (great-circle distance in km; Earth radius 6371.0). Per [research.md §4](research.md#4-feasibility-filter-reachability-check)
- [X] T009 [P] Implement `validate_slug(name: str) -> str` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) raising `argparse.ArgumentTypeError` on mismatch against `SLUG_PATTERN`. Per [research.md §9](research.md#9-output-directory-naming)
- [X] T010 [P] Implement `utc_now_iso() -> str` (returns `YYYY-MM-DDTHH:MM:SSZ`) and `utc_timestamp_slug() -> str` (returns `YYYYMMDDTHHMMSSZ`) in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py). Per [data-model.md §1](data-model.md#1-batch-directory-layout)
- [X] T011 Implement `classify_error(exc_or_response) -> str` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) returning one of the `error.kind` enum values from [data-model.md §4](data-model.md#4-runsjsonl--one-json-record-per-line) (`timeout`, `network`, `http_5xx`, `http_429`, `http_4xx`, `parse`, `validation`, `unknown`). Input is either a `requests` exception or a `requests.Response`. Logic per [contracts/compare-endpoint.md §Failure classification](contracts/compare-endpoint.md#failure-classification-harness-side)
- [X] T012 Implement `is_transient(error_kind: str) -> bool` returning True for `timeout`-exception-path, `network`, `http_5xx`, `http_429` — **but** per FR-009c, `timeout` is listed as **permanent** (retrying just burns the budget again). Re-read [spec.md FR-009b/FR-009c](spec.md) and [research.md §8](research.md#8-retry-and-timeout-implementation) carefully: transient = `{network, http_5xx, http_429}`. Do not include `timeout`

**Checkpoint**: All foundation helpers exist and are importable. No user-story code yet. User stories can now begin in parallel.

---

## Phase 3: User Story 1 — Headless benchmark batch (Priority: P1) 🎯 MVP

**Goal**: One command generates N random environments, runs both agents on each via `/api/agents/compare`, and writes `manifest.json` + `runs.jsonl` + `summary.md` to a dated batch directory.

**Independent Test**: `python3 scripts/benchmark_agents.py --runs 10 --seed 42 --name smoke` against a running planner server produces `benchmarks/<ts>-smoke/{manifest.json,runs.jsonl,summary.md}`. `runs.jsonl` has exactly 10 lines, each a valid JSON object. Re-running with the same seed produces byte-for-byte identical `env` blocks across the 10 corresponding lines.

### Tests for User Story 1

- [X] T013 [P] [US1] Create [tests/benchmark/test_environment_generator.py](../../tests/benchmark/test_environment_generator.py) with: `test_sub_seed_determinism` (same `(batch_seed, run_index, attempt)` ⇒ same sub_seed; different inputs ⇒ different sub_seeds), `test_respects_counts` (generator honors `--airports/--targets/--sams/--drones`), `test_field_constraints` (airport ids match `^A\d+$`, target priorities in range, SAM range_km in range, types in `{a,b,c}`, drone start_airport exists in airports), `test_seed_reproducibility` (same seed + params ⇒ identical `env` dicts — asserting with `json.dumps(sort_keys=True)` equality). Tests must NOT hit the network. Covers FR-001..FR-005, FR-005a. Per [plan §FR → Test mapping](plan.md#fr--test-mapping)
- [X] T014 [P] [US1] Create [tests/benchmark/test_feasibility_filter.py](../../tests/benchmark/test_feasibility_filter.py) with: `test_all_targets_reachable_passes`, `test_fuel_constrained_rejects` (one drone with tiny fuel budget, `reachable_target_count` < `MIN_REACHABLE_TARGETS`), `test_sam_inflation_applied` (reachability uses `REACHABILITY_SAFETY_MARGIN`), `test_accept_loop_exhausts_then_flags` (force `MAX_REGEN_ATTEMPTS_PER_SLOT` failures; confirm final env returned with `low_feasibility: true` and `reachable_target_count` set). Covers FR-005b. Per [plan stage 4 + 5](plan.md#implementation-walkthrough)

### Implementation for User Story 1

- [X] T015 [US1] Implement `generate_environment(rng: random.Random, params: dict) -> dict` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py). Output shape: exactly [data-model.md §2](data-model.md#2-environment-record-generator-output-embedded-in-each-run). Positions drawn uniformly inside `params["bbox"]`; target priorities uniformly in `TARGET_PRIORITY_RANGE`; types uniformly from `TARGET_TYPES`; SAM centers inside bbox; SAM `range_km` uniformly in `SAM_RANGE_KM_RANGE`; drones assigned round-robin to airports with `fuel_budget_km = params["fuel_budget_km"]`, `speed_kmh = DEFAULT_SPEED_KMH`, `start_airport` set, `end_airport = "-"`. **Draw order matters** — same rng ⇒ same output, so document the exact draw sequence at the top of the function. Per [plan stage 3](plan.md#implementation-walkthrough), depends on T006, T007. Passes T013.
- [X] T016 [US1] Implement `reachable_target_count(env: dict) -> int` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py). For each target, check if any drone has `2 * haversine_km(drone.start_airport.latlng, target.latlng) * REACHABILITY_SAFETY_MARGIN <= drone.fuel_budget_km`, and the straight line from airport to target does not pass within `sam.range_km` of a SAM center. Return the count. Per [research.md §4](research.md#4-feasibility-filter-reachability-check), depends on T008. Passes T014 reachability cases.
- [X] T017 [US1] Implement `accept_environment(batch_seed: int, run_index: int, params: dict) -> tuple[dict, dict]` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) returning `(env, env_meta)`. Loop up to `MAX_REGEN_ATTEMPTS_PER_SLOT` attempts: for each attempt call `make_run_rng(batch_seed, run_index, attempt)`, call `generate_environment`, call `reachable_target_count`. Accept if count ≥ `MIN_REACHABLE_TARGETS`; else retry. After exhausting the budget, accept the last candidate with `low_feasibility=True`. Return `env_meta = {"regen_attempts": attempt, "low_feasibility": bool, "reachable_target_count": int, "generated_at": utc_now_iso()}`. Per [plan stage 5](plan.md#implementation-walkthrough), depends on T015, T016. Passes T014 accept-loop case.
- [X] T018 [US1] Implement `build_compare_request(env: dict, prompt: str) -> dict` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) returning the request body per [contracts/compare-endpoint.md §Request body](contracts/compare-endpoint.md#request-body): `{"message": prompt, "env": env_without_drone_configs, "drone_configs": env["drone_configs"]}` — drone configs go at the **top level** to avoid ambiguity.
- [X] T019 [US1] Implement `_invoke_compare_with_retry(session: requests.Session, url: str, body: dict, timeouts: dict) -> tuple[dict | None, dict | None, int]` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) returning `(response_json, error_dict, retries_used)`. Call `session.post(url, json=body, timeout=HTTP_TIMEOUT_S)`. On exception or non-200, call `classify_error` + `is_transient`. If transient and `retries_used < MAX_RETRIES`: on `http_429`, read `Retry-After` header and sleep; retry once. Otherwise return error. On parse/schema-validation failure of the JSON body, classify as `parse`/`validation`, no retry. Per [spec.md FR-009/FR-009a/FR-009b/FR-009c](spec.md), [research.md §8](research.md#8-retry-and-timeout-implementation), depends on T011, T012.
- [X] T020 [US1] Implement `normalize_agent_result(agent_block: dict, server_agent_type: str, harness_latency_s: float, retries_used: int, error: dict | None) -> dict` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py). Emits the per-agent JSONL block per [data-model.md §4 run-record field rules](data-model.md#run-record-field-rules): on success include all metric fields + server's `latency_s`; on failure include only `agent_type`, `latency_s` (harness measurement), `retries_used`, `error`. Be explicit about `success` vs. `valid_solution` — read those rules carefully.
- [X] T021 [US1] Implement `run_single_batch_step(session, batch_seed, run_index, params, prompt, jsonl_writer) -> dict` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py). Orchestrates one row of the state diagram in [data-model.md §6](data-model.md#6-state-transitions): `accept_environment` → `build_compare_request` → `_invoke_compare_with_retry` → `normalize_agent_result` (once per agent) → assemble full run record (schema_version, run_index, sub_seed, env_meta, env, prompt, drone_configs, started_at, ended_at, reasoning, langgraph_v4) → call `jsonl_writer(record)`. Return the record. Per [plan stage 9](plan.md#implementation-walkthrough), depends on T017, T018, T019, T020.
- [X] T022 [US1] Implement `assert_server_reachable(url: str) -> None` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) doing a `GET {url}/docs` (FastAPI's auto-docs) or a lightweight HEAD; on failure print one human-readable line to stderr and `sys.exit(2)`. Per [spec.md FR-008](spec.md), [quickstart.md §Failure modes](quickstart.md#failure-modes-and-what-to-do)
- [X] T023 [US1] Implement `create_batch_dir(output_dir: str, name: str) -> Path` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) returning `Path(output_dir) / f"{utc_timestamp_slug()}-{name}"`, `mkdir(parents=True, exist_ok=False)`. Per [research.md §9](research.md#9-output-directory-naming)
- [X] T024 [US1] Implement `open_jsonl_writer(path: Path) -> callable` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py). Open `path / "runs.jsonl"` in `"a"` mode with `buffering=1` (line buffered). Return a closure `write(record: dict) -> None` that writes `json.dumps(record) + "\n"`, calls `file.flush()`, then `os.fsync(file.fileno())`. Also return a `close()` method. Per [research.md §5](research.md#5-overnight-operation-interrupt-safety-and-progress-visibility), [spec.md FR-014](spec.md)
- [X] T025 [US1] Implement `aggregate_runs(run_records: list[dict]) -> dict` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) returning the `aggregate` block of [data-model.md §3](data-model.md#3-manifestjson-schema). For each agent compute `runs_successful` (count where `success=True`), `runs_failed`, `success_rate`, `points/fuel_km/fuel_efficiency/coverage/latency_s/llm_calls` as `{mean, median, min, max}` over successful runs (add `p95` for latency), `valid_solution_rate` over successful runs. Compute `head_to_head` only over records where both agents have `success=True`: `both_succeeded`, `reasoning_only_success`, `v4_only_success`, `both_failed`, `points.{reasoning_wins,v4_wins,ties}` (strict integer equality), `fuel_km.{reasoning_wins,v4_wins,ties}` (lower wins, ties within `FUEL_TIE_TOLERANCE_KM`). Include `low_feasibility_runs` count. **Ignore** any `winners` field from the response. Per [data-model.md §3 aggregate rules](data-model.md#aggregate-computation-rules-fr-013-research-7), [research.md §7](research.md#7-metric-aggregation-how-to-handle-mixed-successfailure-runs)
- [X] T026 [US1] Implement `percentile(values: list[float], p: float) -> float` as a helper for T025 (linear interpolation on sorted values). Single function, ~8 lines.
- [X] T027 [US1] Implement `build_manifest(config: dict, batch_meta: dict, aggregate: dict) -> dict` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) returning the full manifest document per [data-model.md §3](data-model.md#3-manifestjson-schema). `schema_version=SCHEMA_VERSION`, `batch={name, started_at, ended_at, completed, output_dir}`, `config={seed, runs_requested, runs_completed, server_url, prompt, generator={...}, execution={...}}`, `aggregate` as passed in.
- [X] T028 [US1] Implement `write_manifest_atomic(batch_dir: Path, manifest: dict) -> None` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py). Write to `batch_dir / "manifest.json.tmp"`, `flush+fsync`, then `os.rename` to `batch_dir / "manifest.json"`. Per [plan stage 12](plan.md#implementation-walkthrough)
- [X] T029 [US1] Implement `render_summary_markdown(manifest: dict) -> str` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) producing the text shown in [data-model.md §5](data-model.md#5-summarymd--human-readable-head-to-head). Compute `Duration` from `started_at`/`ended_at`. Bold the winner in the head-to-head table.
- [X] T030 [US1] Wire the batch driver in `main()` in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py). Order: `parse_args` → `assert_server_reachable` → `create_batch_dir` → `open_jsonl_writer` → build `config` dict and `started_at` timestamp → `session = requests.Session()` → `for run_index in range(runs_requested): run_single_batch_step(...); if args.inter_run_delay > 0: time.sleep(...)` → collect records into memory (they're small) → `aggregate_runs` → `build_manifest` (with `completed=True`) → `write_manifest_atomic` → `summary = render_summary_markdown(manifest)` → `(batch_dir/"summary.md").write_text(summary)` → `print(summary)`. Progress line per run: `print(f"[{i+1}/{N}] seed={sub_seed} reasoning={r.latency_s:.1f}s lg_v4={v.latency_s:.1f}s points={r.points}/{v.points}", flush=True)`. Per [plan stage 10, 13](plan.md#implementation-walkthrough), depends on T005, T021, T022, T023, T024, T025, T027, T028, T029.
- [X] T031 [US1] Implement `--dry-run` branch in `main()` (per [quickstart.md CLI reference](quickstart.md#cli-reference)): when set, generate all N environments, emit one stdout line per env (`[i/N] sub_seed=... reachable=...`), skip `assert_server_reachable`, skip all HTTP calls, skip writing `runs.jsonl`, skip manifest. Useful for validating generator determinism without a running server.

**Checkpoint**: US1 complete. Operator can run `python3 scripts/benchmark_agents.py --runs 10 --seed 42 --name smoke` and get full outputs. Re-running with same seed produces identical `env` blocks.

---

## Phase 4: User Story 2 — Survive agent failures overnight (Priority: P2)

**Goal**: One agent failing (timeout, 429, server down) does not abort the batch. Ctrl-C during a long run writes a clean partial manifest. Heartbeat lines prove the process is alive when nothing has completed for a while.

**Independent Test**: Point `--server-url` at an invalid port for 2 of 5 runs (pause/resume server during run), and confirm (a) the batch completes all 5 envs, (b) `runs.jsonl` has 5 lines, (c) the 2 failure lines have `success: false` with appropriate `error.kind`, (d) other agent still has results where it succeeded, (e) `manifest.aggregate.*.runs_failed` > 0.

### Tests for User Story 2

- [X] T032 [P] [US2] Create [tests/benchmark/test_retry_policy.py](../../tests/benchmark/test_retry_policy.py) covering `classify_error` and `_invoke_compare_with_retry`. Monkey-patch `session.post` to raise/return each relevant case: `ConnectionError`, `ReadTimeout`, 200-success, 500, 502, 429-no-header, 429-with-Retry-After (assert the sleep happened), 404, 400, 200-with-unparseable-JSON, 200-with-missing-`reasoning`-key. Assert `retries_used == 1` on transient (network, 5xx, 429), `retries_used == 0` on permanent (timeout, 4xx non-429, parse, validation). Use a fake clock (`time.sleep = lambda s: slept_for.append(s)`) to verify `Retry-After` honored. Covers FR-009, FR-009a, FR-009b, FR-009c
- [X] T033 [P] [US2] Create [tests/benchmark/test_jsonl_output.py](../../tests/benchmark/test_jsonl_output.py) covering crash-safety. `test_flush_fsync_per_line`: monkey-patch `os.fsync` to count calls, write 3 records, assert fsync called 3 times. `test_interrupt_preserves_prior_lines`: write 2 full records via the writer, then write a 3rd line and truncate the file at byte offset `len(line1)+len(line2)+5` to simulate a crash mid-line-3; reopen the file, split by `\n`, parse each line, assert lines 1 and 2 parse as valid JSON and line 3 raises (or is empty and skipped). Covers FR-014 crash-safety clause, spec US2 acceptance #3
- [X] T034 [P] [US2] Create [tests/benchmark/test_metric_aggregator.py](../../tests/benchmark/test_metric_aggregator.py) with a fixture JSONL covering all four head-to-head buckets: (a) 5 runs both-succeeded (2 reasoning wins, 2 v4 wins, 1 tie), (b) 1 reasoning-only success, (c) 1 v4-only success, (d) 1 both-failed. Assert `runs_successful` counts, `success_rate`, `head_to_head.both_succeeded=5`, `reasoning_only_success=1`, `v4_only_success=1`, `both_failed=1`, `points.{wins,ties}` correct. Include one record with `valid_solution: false` and assert `valid_solution_rate` reflects it correctly (denominator = successful runs, not total). Include one record with `low_feasibility: true` and assert `low_feasibility_runs == 1`. Assert the server's `winners` field is ignored (fixture sets it contradicting the recomputed values). Covers FR-012, FR-013, research §7

### Implementation for User Story 2

- [X] T035 [US2] Implement SIGINT handling in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py). Add module-level `_STOP = False`. In `main()`, install `signal.signal(signal.SIGINT, lambda *_: _set_stop())` before the batch loop. Check `_STOP` **between** runs (after `inter_run_delay`), not during — in-flight agent calls have their own 120 s timeout and complete naturally. On stop, break the loop, record `runs_completed = i`, call the same aggregate → manifest → summary path but with `batch.completed = False`. **The interrupt path MUST also `print(summary)` to stdout before exiting** so FR-015 is honored on both success and interrupt paths. Per [research.md §5](research.md#5-overnight-operation-interrupt-safety-and-progress-visibility), [spec.md FR-015](spec.md), extends T030.
- [X] T036 [US2] Implement heartbeat in [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py). Option: lightweight — in the batch loop, track `last_progress_ts = time.monotonic()`; after each inter-run delay, if no progress line has been printed in `HEARTBEAT_INTERVAL_S`, print `[heartbeat] run {i+1}/{N} in progress for {elapsed:.0f}s`. Because runs are sequential and I/O-bound inside `session.post`, the simpler approach is a daemon thread started in `main()` that loops `time.sleep(60); check last_progress_ts`; implement that instead. Per [research.md §5](research.md#5-overnight-operation-interrupt-safety-and-progress-visibility)
- [X] T037 [US2] Confirm per-run exception handling in `run_single_batch_step` (T021) never propagates: wrap the full function body in `try/except Exception as exc: record_failure_and_continue(exc)` so a code bug in `accept_environment` or `normalize_agent_result` for one run does not kill the overnight batch. The handler writes a JSONL line with `success=False` for both agents and `error.kind="unknown"`, then returns. Per [spec.md FR-009](spec.md), extends T021.
- [X] T038 [US2] Verify human-readable error lines at exit. In `main()` wrap top-level calls so that uncaught exceptions print one-line reasons (never a raw traceback) to stderr. Per [plan §Constitution Check III. UX Consistency](plan.md#constitution-check): "No raw tracebacks at the exit boundary."

**Checkpoint**: US2 complete. Overnight runs survive transient failures, Ctrl-C cleanly terminates, heartbeat visible when reconnecting to a detached process.

---

## Phase 5: User Story 3 — Reproducible, analyzable output (Priority: P2)

**Goal**: A completed batch's output is sufficient to reconstruct any run's environment and answer head-to-head questions without re-running. Two batches with different seeds produce identical schemas.

**Independent Test**: Run two batches with seeds 1 and 2. Load both `manifest.json` files in Python, verify identical key sets recursively. From `runs.jsonl` alone pick any run_index, re-seed a `random.Random` with its `sub_seed`, call `generate_environment`, confirm the resulting dict equals the `env` field in the JSONL line.

### Implementation for User Story 3

Most of US3's requirements are already met by US1 implementations (FR-011, FR-016) because we designed for it from the start. This phase adds the explicit reproducibility guarantees and schema stability checks.

- [X] T039 [US3] Verify FR-016 by ensuring `config` block in manifest (built in T027) contains: `seed` (the batch seed actually used, not None even if user didn't pass `--seed` — generate a random one at startup and log it), `runs_requested`, `runs_completed`, `server_url`, `prompt`, `generator={airports,targets,sams,drones,bbox,box_size_km_approx,default_drone}`, `execution={per_agent_timeout_s,max_retries,inter_run_delay_s,max_regen_attempts_per_slot,min_reachable_targets}`. **Every value the operator could have overridden via CLI MUST appear here.** Per [spec.md FR-016](spec.md), [data-model.md §3](data-model.md#3-manifestjson-schema)
- [X] T040 [US3] Compute `box_size_km_approx` at startup from the final `--bbox` (roughly `max(haversine(NW,NE), haversine(NW,SW))`) and include in `config.generator`. Pure convenience field for the operator reading summary.md.
- [X] T041 [US3] In `run_single_batch_step` (T021), before calling `jsonl_writer`, verify that the record conforms to the schema by asserting presence of required top-level keys (`schema_version`, `run_index`, `sub_seed`, `env_meta`, `env`, `prompt`, `drone_configs`, `started_at`, `ended_at`, `reasoning`, `langgraph_v4`). On assertion failure, log and skip writing rather than writing a malformed line. Per [data-model.md §4](data-model.md#4-runsjsonl--one-json-record-per-line)
- [X] T042 [US3] Add [tests/benchmark/test_reproducibility.py](../../tests/benchmark/test_reproducibility.py) (new sixth test file, not in the original plan §FR-test mapping — justified because US3 is specifically about reproducibility guarantees) with `test_env_reconstructs_from_sub_seed`: take a run record from a fixture JSONL, call `make_run_rng(batch_seed, run_index, env_meta.regen_attempts)`, call `generate_environment`, assert the resulting dict equals `record["env"]`. Also `test_manifest_schema_stable`: compare two fixture manifests (different seeds, same params) and assert identical recursive key sets. Per [spec.md US3](spec.md) acceptance scenarios.

**Checkpoint**: US3 complete. Schema is machine-readable-stable; any run is fully reconstructable from its JSONL record.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T043 [P] Run the full pytest suite from repo root: `PYTHONPATH=$(pwd) pytest tests/benchmark/ -v`. All 6 test files must pass, zero skips. Verify per [.specify/memory/constitution.md](../../.specify/memory/constitution.md) Principle II (Testing Standards).
- [ ] T044 [P] Smoke end-to-end: start the planner server (`./run_planner.sh`), then `python3 scripts/benchmark_agents.py --runs 2 --seed 1 --name smoke`. Confirm: (a) `benchmarks/<ts>-smoke/` exists, (b) `runs.jsonl` has 2 lines parseable as JSON, (c) `manifest.json` has `aggregate.head_to_head` populated, (d) `summary.md` renders, (e) summary is also echoed to stdout. Stop the server midway through a longer test run (`--runs 5`) to confirm Ctrl-C path and `manifest.batch.completed = false`.
- [X] T045 [P] Run the dry-run repro check: `python3 scripts/benchmark_agents.py --runs 20 --seed 42 --name repro --dry-run` twice. Diff the two stdout outputs. They must be identical (`diff` returns 0). This validates FR-003 + SC-002 without needing the server.
- [X] T046 Re-read [scripts/benchmark_agents.py](../../scripts/benchmark_agents.py) top-to-bottom. Check Principle I compliance: no magic numbers (every literal tied to a constant from T004 or an argparse default), no functions > 80 lines (the 80-line rule allows only single-algorithm exceptions — none of our functions qualify), absolute imports only, no dead code. Per [.specify/memory/constitution.md](../../.specify/memory/constitution.md) Principle I.
- [X] T047 Update [CLAUDE.md](../../CLAUDE.md) under the existing `<!-- SPECKIT START --> / <!-- SPECKIT END -->` block to point future agent sessions at the completed feature once merged. Leave the plan pointer until merge is clean.
- [ ] T048 Run a ~30-run overnight-scale validation: `nohup python3 scripts/benchmark_agents.py --runs 30 --seed 4242 --name overnight-check > benchmarks/overnight-check.log 2>&1 &`. Disconnect the terminal, reconnect, confirm progress in log. On completion, verify `manifest.json` aggregates look reasonable (no zero-everything, no mean > max, no negative counts) and `summary.md` reads cleanly. This catches bugs that only appear in longer runs.

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup T001–T004)**: No dependencies — start immediately.
- **Phase 2 (Foundational T005–T012)**: Depends on Phase 1. **BLOCKS** all user stories.
- **Phase 3 (US1, T013–T031)**: Depends on Phase 2. MVP — deliverable on its own.
- **Phase 4 (US2, T032–T038)**: Depends on Phase 2. US2 *extends* US1's main-loop code but the tests T032/T033/T034 can be written against the foundational helpers alone — they do not require US1 to be fully wired.
- **Phase 5 (US3, T039–T042)**: Depends on Phase 2 + most of US1's manifest/writer wiring (T027, T024). Tests can be written in parallel.
- **Phase 6 (Polish T043–T048)**: Depends on US1 at minimum. T044/T045/T048 require server for smoke runs.

### Within each user story

- Tests are written first per [Constitution Principle II](../../.specify/memory/constitution.md). They will FAIL before implementation (expected), PASS after.
- Helpers before composition: T015 + T016 before T017 (accept loop); T018/T019/T020 before T021 (per-run driver); T025/T027/T028/T029 before T030 (wiring).
- `main()` wiring (T030) is the last US1 implementation task — it depends on almost everything prior.

### Parallel opportunities

**Within Phase 1**: T003 + T004 can run in parallel (skeleton vs. constants — same file but non-overlapping regions; ideally T003 lands first to give T004 a file to edit).

**Within Phase 2**: T008 + T009 + T010 are trivially parallel (pure functions, different sections of the file).

**Within Phase 3 (US1)**:
- Test files T013 + T014 are fully parallel (different files, depend only on foundation).
- Helpers: T018 + T022 + T023 + T024 + T026 can all be built in parallel (different functions, no shared state).
- Tighter chain: T015 → T016 → T017 is strictly sequential.
- Tighter chain: T019 → T020 → T021 is strictly sequential.
- T025 + T027 + T029 can be built in parallel.

**Across stories after US1**: Once T030 lands (US1 MVP complete), US2 tests (T032/T033/T034) and US3 tests (T042) can all be written in parallel by different developers.

### Parallel execution example — US1 test phase

```bash
# Both test files can be written simultaneously:
Task: "Write tests/benchmark/test_environment_generator.py per T013 spec"
Task: "Write tests/benchmark/test_feasibility_filter.py per T014 spec"
```

### Parallel execution example — US1 helper phase (after T017 done)

```bash
# These five implementation tasks are disjoint:
Task: "Implement build_compare_request per T018"
Task: "Implement assert_server_reachable per T022"
Task: "Implement create_batch_dir per T023"
Task: "Implement open_jsonl_writer per T024"
Task: "Implement percentile helper per T026"
```

---

## Implementation Strategy

### MVP first (US1 only)

1. Complete Phase 1 (T001–T004).
2. Complete Phase 2 (T005–T012) — **blocks every subsequent phase**.
3. Complete Phase 3 US1 tests (T013, T014). Expect them to fail.
4. Complete Phase 3 US1 implementation (T015–T031). Tests should now pass.
5. **STOP and VALIDATE**: Run T044 smoke — operator runs `--runs 10 --seed 42` against a live server and gets three valid output files.
6. This is the shippable MVP. The harness already delivers the user's stated value (run a batch, get metrics).

### Incremental delivery

1. **MVP (US1)**: fixed-seed batches, all-or-nothing runs. If any run crashes the script, you lose everything — but the core comparison works.
2. **+US2**: overnight-safe — add SIGINT, heartbeat, per-run exception containment. Required for meaningful-scale batches.
3. **+US3**: reproducibility guarantees + schema-stability test. Required for downstream analysis pipelines.
4. **Polish**: re-read + constitution check (T046) + overnight validation (T048).

### Parallel team strategy

With multiple developers after Phase 2 is complete:

- Developer A: US1 implementation (T015–T031).
- Developer B: US2 tests written against foundational helpers (T032, T033). When US1 wiring lands, adds T035–T038.
- Developer C: US3 tests (T042) + shared aggregator/manifest tests that overlap with US1's T025.

All three land onto the same single file (`scripts/benchmark_agents.py`), so merge coordination matters — favor frequent small PRs over long-lived branches.

---

## Notes

- `[P]` tasks operate on different files or non-overlapping sections of the same file.
- `[Story]` label traces tasks to user stories from [spec.md](spec.md).
- Every implementation task cites the exact detail-file section the implementer should read — no guessing required.
- Commit after each task or each logical group (e.g., after T017 the env-acceptance loop is a cohesive unit).
- Verify tests fail before implementing the code that satisfies them (Principle II, TDD discipline).
- Stop at any checkpoint to validate the story independently before moving on.
- The single-file structure means merge conflicts are likely when parallelizing — prefer serializing work on `scripts/benchmark_agents.py` at the function-implementation level, parallelizing only at the test-writing level.
