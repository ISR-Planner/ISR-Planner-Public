# Phase 1 Data Model: Agent Benchmark Harness

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-04-21

This document defines the on-disk schemas for `manifest.json` and `runs.jsonl`, and the
in-memory shape of the environment generator output. All JSON is UTF-8, keys are `snake_case`,
and timestamps are ISO 8601 with a trailing `Z`.

---

## 1. Batch directory layout

```
benchmarks/<UTC-timestamp>-<batch-name>/
├── manifest.json    # written atomically at batch completion (see §3)
├── runs.jsonl       # append-only during the batch (see §4)
└── summary.md       # human-readable head-to-head (see §5)
```

Example name: `benchmarks/20260421T213005Z-overnight-sweep/`

---

## 2. Environment record (generator output, embedded in each run)

Matches the shape expected by `/api/agents/compare` (see
[contracts/compare-endpoint.md](contracts/compare-endpoint.md)).

```jsonc
{
  "airports": [
    {"id": "A1", "lat": 32.31, "lng": -90.18},
    {"id": "A2", "lat": 32.79, "lng": -89.65}
  ],
  "targets": [
    {"id": "T1", "lat": 32.55, "lng": -90.01, "priority": 8, "type": "a"}
  ],
  "sams": [
    {"center": {"lat": 32.48, "lng": -89.93}, "range_km": 12.5}
  ],
  "drone_configs": {
    "1": {
      "enabled": true,
      "fuel_budget_km": 250,
      "speed_kmh": 120,
      "start_airport": "A1",
      "end_airport": "-"
    }
  }
}
```

### Field constraints (validation rules for FR-004)

| Field | Type | Constraint |
|---|---|---|
| `airports[].id` | string | Unique within env. Matches `^A\d+$`. |
| `airports[].lat` / `lng` | float | Within the configured bounding box. |
| `targets[].id` | string | Unique. Matches `^T\d+$`. |
| `targets[].priority` | int | 1 ≤ priority ≤ 10 (default generator range). |
| `targets[].type` | string | One of `"a"`, `"b"`, `"c"` (inherited from existing script). |
| `sams[].range_km` | float | 5 ≤ range_km ≤ 25 (default generator range). |
| `drone_configs[k].fuel_budget_km` | float | Must leave at least `MIN_REACHABLE_TARGETS` (= 2) targets reachable under the two-stage feasibility check (fuel budget + SAM-blocking). See [research.md §4](research.md#4-feasibility-filter-reachability-check) and the `MIN_REACHABLE_TARGETS` constant in [plan.md Constants & Defaults](plan.md#constants--defaults). |
| `drone_configs[k].start_airport` | string | Must exist in `airports`. |
| `drone_configs[k].end_airport` | string | `"-"` (no-return) OR must exist in `airports`. |

---

## 3. `manifest.json` schema

Written **once**, atomically, at batch completion (or during SIGINT cleanup). Captures the
full configuration needed to reproduce the batch plus the aggregate summary.

```jsonc
{
  "schema_version": 1,
  "batch": {
    "name": "overnight-sweep",
    "started_at": "2026-04-21T21:30:05Z",
    "ended_at":   "2026-04-22T05:12:48Z",
    "completed": true,               // false if SIGINT / crash
    "output_dir": "benchmarks/20260421T213005Z-overnight-sweep"
  },
  "config": {
    "seed": 4242,
    "runs_requested": 500,
    "runs_completed":  487,          // may be < runs_requested on interrupt
    "server_url": "http://localhost:8893",
    "prompt": "Plan a mission to maximize priority points. Visit as many targets as possible.",
    "generator": {
      "airports": 2,
      "targets":  8,
      "sams":     2,
      "drones":   3,
      "bbox": {"lat_min": 32.0, "lat_max": 33.0, "lng_min": -90.5, "lng_max": -89.5},
      "box_size_km_approx": 111,
      "default_drone": {
        "fuel_budget_km": 250,
        "speed_kmh": 120,
        "enabled": true
      }
    },
    "execution": {
      "per_agent_timeout_s": 120,
      "max_retries": 1,
      "inter_run_delay_s": 2.0,
      "max_regen_attempts_per_slot": 10,
      "min_reachable_targets": 2
    }
  },
  "aggregate": {
    "reasoning": {
      "runs_successful": 480,
      "runs_failed":       7,
      "success_rate":      0.986,
      "points":   {"mean": 23.4, "median": 24, "min": 0, "max": 48},
      "fuel_km":  {"mean": 142.8, "median": 139.2, "min": 0, "max": 310.5},
      "fuel_efficiency":  {"mean": 0.19, "median": 0.18},
      "coverage": {"mean": 0.71, "median": 0.75},
      "latency_s":{"mean": 44.8, "median": 42.1, "p95": 78.9},
      "llm_calls":{"mean": 6.2,  "median": 6},
      "valid_solution_rate": 0.994
    },
    "langgraph_v4": { /* same shape */ },
    "head_to_head": {
      "both_succeeded":       475,
      "reasoning_only_success":  5,
      "v4_only_success":         3,
      "both_failed":             4,
      "points": {
        "reasoning_wins": 198,
        "v4_wins":         220,
        "ties":             57
      },
      "fuel_km": {
        "reasoning_wins":  180,      // lower is better
        "v4_wins":         260,
        "ties":             35
      }
    },
    "low_feasibility_runs": 12       // count where low_feasibility flag was set
  }
}
```

### Aggregate computation rules (FR-013, research §7)

- `runs_successful` = count of runs where this agent's run record has `success: true` (see
  §4 "success vs. valid_solution" — this is the infrastructure-level flag, not a solution-quality flag).
- `runs_failed` = count of runs where `success: false` for this agent.
- `success_rate` = `runs_successful / (runs_successful + runs_failed)`.
- All statistics (`mean`, `median`, `p95`, `min`, `max`) are computed over successful runs
  only (`success: true`). This includes runs with `valid_solution: false` — their numeric
  metrics are present and meaningful (the agent returned a plan, even if infeasible).
- `valid_solution_rate` = count of runs with `valid_solution: true` divided by `runs_successful`
  (so it is conditional on the agent responding at all). A separate metric from `success_rate`.
- `head_to_head.points.{reasoning_wins,v4_wins,ties}` are computed only over runs where
  **both agents have `success: true`**. Ties are strict equality on integer points.
- `head_to_head.fuel_km` counts the **lower** fuel user as winner (fuel is minimization);
  ties within `FUEL_TIE_TOLERANCE_KM` (= 0.1 km) are counted as ties.
- `low_feasibility_runs` counts runs where the feasibility filter flagged the environment
  even after regeneration (see run record `env_meta.low_feasibility`).
- The server's own top-level `winners` object in the compare response (see
  [contracts/compare-endpoint.md](contracts/compare-endpoint.md)) is **ignored** by the
  harness aggregator. Head-to-head counts are always recomputed from per-run data so the
  aggregate rules above are the sole source of truth.

---

## 4. `runs.jsonl` — one JSON record per line

Each line is a standalone JSON object. Lines are written in `run_index` order and flushed +
fsync'd before the next run begins (research §5). A consumer can `tail -f` or read line-by-line
without ever seeing a partial record.

```jsonc
{
  "schema_version": 1,
  "run_index": 3,
  "sub_seed": 17203982473,
  "env_meta": {
    "regen_attempts": 0,
    "low_feasibility": false,
    "reachable_target_count": 6,
    "generated_at": "2026-04-21T21:33:12Z"
  },
  "env": { /* full environment record per §2 */ },
  "prompt": "Plan a mission to maximize priority points. Visit as many targets as possible.",
  "drone_configs": { "1": {...}, "2": {...} },
  "started_at": "2026-04-21T21:33:12Z",
  "ended_at":   "2026-04-21T21:34:38Z",
  "reasoning": {
    "agent_type": "reasoning",
    "success": true,
    "latency_s": 42.1,
    "llm_calls": 6,
    "total_points": 24,
    "total_fuel_km": 138.7,
    "fuel_efficiency": 0.18,
    "targets_visited": 5,
    "targets_total": 8,
    "target_coverage": 0.625,
    "valid_solution": true,
    "drone_metrics": { /* per server response */ },
    "response_text": "## Optimal Mission Plan ...",
    "retries_used": 0,
    "error": null
  },
  "langgraph_v4": {
    "agent_type": "langgraph_v4",
    "success": false,
    "latency_s": 120.4,
    "error": {
      "kind": "timeout",               // one of: timeout, network, http_5xx, http_429,
                                       //        http_4xx, parse, validation, unknown
      "message": "Request timed out after 120s",
      "retries_used": 1,
      "http_status": null
    }
  }
}
```

### Run-record field rules

- `run_index` is 0-based.
- `sub_seed` is deterministic from `(batch_seed, run_index, attempt=0)` per
  [research §3](research.md#3-environment-generator-reproducibility-mechanism). The
  `sub_seed` written to the JSONL record is the seed of the `random.Random` instance that
  produced the *accepted* environment — so if regeneration happened, the recorded sub_seed
  is the one for the final accepted attempt, not the first rejected one. The final-accepted
  attempt number is recoverable as `env_meta.regen_attempts`.
- `env_meta.regen_attempts` counts rejected candidates before the accepted one (0 on first accept).
- `env_meta.low_feasibility` is true only when `regen_attempts == max_regen_attempts_per_slot`
  AND the final accepted env still fails the `min_reachable_targets` check.
- **`success` vs. `valid_solution`** (these are NOT the same thing):
  - `success` is true iff the HTTP call returned 200 AND the response JSON parsed AND the
    per-agent `error` field in the response is null. It is **purely an infrastructure-level
    flag**: did we get a structurally usable response from the agent?
  - `valid_solution` is the server-reported boolean indicating whether the returned routes
    satisfy fuel / SAM / airport constraints. It is **a solution-quality flag**.
  - A run where the agent responded normally but produced an infeasible plan is
    `success: true, valid_solution: false`. All metric fields are present and contribute to
    aggregates; the run is counted separately under `valid_solution_rate` in the manifest.
  - A run where the HTTP call failed or the response was unparseable is
    `success: false` — `valid_solution` is omitted entirely.
- On `success: false`, all metric fields other than `agent_type`, `latency_s`, `retries_used`,
  and `error` are omitted from the record (keeps the file small).
- `latency_s` on a `success: true` record is the value returned by the server (see
  [contracts/compare-endpoint.md §Latency measurement](contracts/compare-endpoint.md#latency-measurement)).
  On a `success: false` record it is the harness's own clock from request-send to
  exception-raise / timeout, rounded to one decimal place.
- `error.kind` enumeration maps to the retry classifier in research §8.

---

## 5. `summary.md` — human-readable head-to-head

Rendered from `manifest.aggregate` at batch completion (and also printed to stdout per FR-015).

Template (no placeholders remain in the real file):

```markdown
# Benchmark Summary: overnight-sweep

**Started**: 2026-04-21 21:30:05 UTC
**Ended**:   2026-04-22 05:12:48 UTC
**Duration**: 7h 42m
**Runs**: 487 completed / 500 requested
**Seed**: 4242
**Output**: `benchmarks/20260421T213005Z-overnight-sweep/`

## Head-to-head (points, higher is better)

| Outcome | Count |
|---|---|
| LangGraph v4 wins | **220** |
| Reasoning wins    | 198 |
| Ties              | 57 |
| Only v4 succeeded        | 3 |
| Only reasoning succeeded | 5 |
| Both failed              | 4 |

## Aggregate metrics (successful runs only)

| Metric | Reasoning | LangGraph v4 |
|---|---|---|
| Success rate     | 98.6 % (480/487) | 98.0 % (477/487) |
| Mean points      | 23.4  | 24.1  |
| Median points    | 24    | 25    |
| Mean fuel (km)   | 142.8 | 138.1 |
| Mean coverage    | 71 %  | 73 %  |
| Mean latency (s) | 44.8  | 47.2  |
| p95 latency  (s) | 78.9  | 81.4  |

## Configuration

- 8 targets, 2 airports, 2 SAMs, 3 drones per environment
- Bounding box: lat [32.0, 33.0] × lng [−90.5, −89.5]
- Per-agent timeout: 120 s
- Retry on transient errors: 1 attempt

## Low-feasibility runs: 12 / 487 (segment out for clean analysis)
```

---

## 6. State transitions

The harness is stateless between invocations (no resumable checkpoints in v1). Within a
single invocation, per-run state transitions are:

```
init → generating_env → env_accepted → calling_compare → recording_result → inter_run_delay
                                                      ↘ recording_failure ↗
                            ↘ regenerating (up to N) ↗
```

- `generating_env` → `regenerating`: feasibility filter rejected candidate.
- `regenerating` → `env_accepted` (with `low_feasibility: true`): retry budget exhausted.
- `calling_compare` → `recording_failure`: HTTP error or response schema mismatch.
- `recording_result` and `recording_failure` both write exactly one JSONL line.
