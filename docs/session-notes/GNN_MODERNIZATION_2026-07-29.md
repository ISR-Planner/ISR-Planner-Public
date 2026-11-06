# GNN planner modernization — 2026-07-29

Bringing the `feature/gnn-planner` work forward onto current `main`, making it
plan complete missions, and building the harness to judge whether the GNN
approach is actually feasible.

**Branch**: `feature/gnn-planner-v2` (cut fresh from `main`)

## Starting position

`feature/gnn-planner` was `main@3e697f5` plus 3 commits, and `main` had moved
**260 commits** ahead. The gap was structural, not cosmetic:

| | GNN branch | `main` today |
|---|---|---|
| `server/main.py` | 2,352 lines, all endpoints inline | 109 lines, routers under `server/api/` |
| `server/solver/solver_bridge.py` | 2,490-line monolith | 82-line facade over 12 modules |
| Compare endpoint | 2 agents | `/api/agents/compare`, 3 agents (+CrewAI) |
| `scripts/benchmark_agents.py` | ~1,110 lines | 1,230 lines (seeded, lat/lng, retries) |

A merge would have meant hand-resolving `isr.js` and `main.py` across 260
commits, so the ~2,300 lines of GNN work were ported forward instead. That was
viable because the two APIs the GNN depends on — `prepare_distance_matrix` and
`solve_mission` — kept identical signatures and are still re-exported by the
compatibility facade.

## What was built

- **Ported** `server/gnn/` (5 modules), the three training/generation scripts,
  and the test suite onto current `main`.
- **`server/api/gnn_routes.py`** — `/api/gnn/build_graph` and `/api/gnn/predict`
  as a proper router (they were inline in the old 2,352-line `main.py`).
- **Three new comparison entrants** in `agent_metrics.py` and the compare
  endpoint: `gnn` (allocation only), `gnn_decoder` (end-to-end, no Held-Karp),
  and `solver_control` (deterministic allocator + exact solver, no LLM).
- **Route decoder** (`predict_routes`) — the branch had trained a route-edge
  model but never called it at inference. Now a greedy rollout with feasibility
  enforced outside the model: a candidate is only scored if the leg is
  unblocked, the drone can still reach its end airport, and the target type is
  permitted.
- **Harness upgrades** (`benchmark_agents.py`) — entrant selection
  (`--gnn`, `--gnn-decoder`, `--solver-control`, `--no-llm-agents`), a
  `--drone-sweep` scaling ladder, per-scale-point aggregation, schema v2.
- **GNN tab** in the web UI, plus a compare table that now builds its columns
  from whichever entrants the response contains.
- **Three study scripts**: `gnn_scaling_study.py` (two-axis timing),
  `gnn_replay_reference.py` (paired replay), `gnn_report.py` (report + charts).
- **Docs**: `docs/agents/GNN_PLANNER.md`.

Test suite: **243 passing**, up from 227, including new coverage for the
decoder, feasibility enforcement, entrant selection and the sweep.

## Bugs found and fixed

1. **The exact solver was never loading on this machine.**
   `webapp/editor/__init__.py` eagerly imported the Tk GUI, so on any host
   without `tkinter` the solver loader swallowed the `ImportError`, printed
   "orienteering features disabled", and every mission silently degraded to a
   fallback decoder. This would have invalidated the entire benchmark — there
   would have been no real control. The GUI import is now lazy.

2. **The GNN was being trained on its own output.**
   `generate_gnn_dataset.py` used `run_gnn_planner` as its teacher with
   `--use-solver` defaulting to **off** — i.e. labels came from the GNN's own
   greedy fallback heuristic. A model trained that way is capped at greedy
   quality and can never match the exact solver it is benchmarked against. The
   teacher now defaults to the exact solver.

3. **`sam_risk` was a dead input channel.**
   It was non-zero only *strictly inside* a SAM ring, but
   `sam_distance_matrix.py:150` excludes any target inside a ring before the
   graph is built — so it was identically `0.0` for every surviving target.
   Replaced with `sam_clearance_norm` (continuous distance to the nearest ring
   edge) and `sam_detour_ratio` (how much SAM geometry distorts routing through
   that node). Both now carry real signal.
   *En route*, a second defect: risk was read from `matrix_data["sams"]`, which
   the Supabase cache-hit path returns as `[]`, so the feature also flipped with
   cache state.

4. **The assignment heuristic billed a full round trip per target.**
   A drone taking N targets was charged for N separate out-and-back trips
   instead of one tour, exhausting its budget after two or three targets. Fixed
   to marginal insertion cost — coverage went from 18 % to 27 % immediately.

5. **Failed planners could win the benchmark.**
   A run that errored recorded `latency_s ≈ 0` and `total_points = 0`, so a
   *failed* planner won the "fastest" category outright. In a scaling sweep,
   where the exact solver is expected to fall over at high drone counts, this
   inverts the headline result. Failed and skipped runs are now excluded from
   scoring.

6. **Silent fallback masked solver failures.** `run_gnn_planner` caught every
   exception and quietly substituted the naive decoder, making a failed
   exact-solver run indistinguishable from a successful GNN run. It now records
   why.

Also: the six ported `.pt` checkpoints were trained against the 15-feature
schema and cannot load under the current 16-feature one. They were removed, and
`assert_checkpoint_matches_schema` now raises an actionable error instead of a
bare torch shape mismatch.

## Findings

Full numbers in `results/GNN_COMPARISON_REPORT.md`.

### The LLM agents' quality lead was an artifact of invalid solutions

Replaying 100 recorded environments (1 drone, 15 targets, seed 42) found on
`origin/benchmark/100runs-1drone-15targets-seed42` — which meant the LLM tier
cost nothing to re-run:

| Planner | Latency | Valid | Points (valid only) | Coverage |
|---|---|---|---|---|
| LangGraph v4 (LLM) | 37.4 s | 36 % | 32.4 | 50 % |
| Solver control | 0.24 s | 97 % | 35.9 | 40 % |
| GNN (allocation only) | 0.66 s | 97 % | 35.9 | 40 % |
| GNN (learned decoder) | 0.15 s | 97 % | 32.5 | 38 % |

LangGraph v4 appeared to beat the solver on raw points (41.5 vs 34.8), but only
36 % of its solutions are fuel-feasible and its *invalid* routes average 46.6
points against 32.4 for its valid ones. On the 36 runs where both produced a
valid solution they **tie 36/36 with zero point difference** — the agent calls
the same solver as a tool, so it reproduces the solver's answer 150× slower.

### The learned decoder reaches ~91 % of solver quality

Trained on 6–20 target instances and evaluated on 15-target problems, the
decoder scores 32.5 vs the solver's 35.9 (91 %) at 38 % vs 40 % coverage, while
running faster. That is genuine size-generalization: exact-solver labels are
only obtainable at small scale, which is precisely why learning the teacher is
interesting.

### The assignment model does not generalize to high drone counts

Measured on the drone axis (10 targets, 2 SAMs, 2 repeats per point):

| Drones | Control targets visited | GNN decoder targets visited |
|---|---|---|
| 1–10 | 9–10 | 8–10 |
| 25 | 9–10 | 7 |
| **50** | **9–10** | **0** |

At 50 drones the assignment model assigns *all* targets to a single drone — in
the reproduced case drone `10`, which starts at `A2` and cannot reach them
within budget. The decoder then correctly refuses every infeasible leg and
returns an empty route, so the mission scores zero.

This is a training-distribution failure, not a code defect: the models were
trained on 2–6 drone graphs and the assignment head collapses to one drone when
asked to discriminate across 50. The decoder's feasibility enforcement behaved
exactly as designed — it declined to emit a route the drone could not fly.

**Scaling to hundreds of drones therefore needs training data at that scale**,
which is the awkward part: exact-solver labels are cheapest when targets are
spread thin across many drones, so multi-drone label generation is actually the
*tractable* direction. It simply was not run here.

### The bottleneck is not the exponential solver

The headline caveat for the scalability hypothesis. Profiling shows routing at a
fairly flat ~0.7 s across sizes, while the **O(n²) SAM-aware distance matrix**
swings from 0.05 s to 30 s+ depending on SAM geometry — one 14-target case took
0.08 s and a 4-target case took 8.00 s, because cost tracks how many
waypoint pairs need visibility-graph detours, not target count.

`build_graph_sample` calls that same matrix builder, so **the GNN cannot avoid
this cost.** It caps the achievable speedup no matter how good the model gets.

Consequently the two scaling axes behave differently:

- **More drones** (the original hypothesis): the allocator spreads targets
  thinner, so targets-per-drone *falls* and Held-Karp stays cheap. Both planners
  pay the same matrix cost. Little GNN advantage.
- **More targets per drone**: `orienteering_with_matrix.py:781` enumerates every
  subset and runs Held-Karp on each (~O(3^N)). This is where the learned
  decoder's O(N²) rollout wins.

### Measured scaling (10 targets fixed on the drone axis, 1 drone on the target axis)

`routing` is total time minus the shared matrix build — the only part the GNN
can displace.

| Targets (1 drone) | Matrix | Control routing | Decoder routing |
|---|---|---|---|
| 8 | 8.14 s | 0.88 s | 0.04 s |
| 10 | 21.60 s | 1.26 s | 0.00 s |
| 12 | 7.50 s | 0.98 s | 0.01 s |
| 14 | 37.44 s | 5.27 s | 0.10 s |
| 16 | 13.82 s | 4.34 s | 0.00 s |

Control routing grows ~2.7× per +2 targets (0.99 → 1.93 → 5.36 s at 10 → 12 →
14) — the expected exponential — while decoder routing stays flat near zero. But
the matrix is 3–10× larger than either, so total wall-clock barely moves.

## Recommended next steps

1. **Cache the distance matrix in memory.** `calculate_sam_aware_matrix` writes
   `_cached_matrix` but never *reads* it as a hit check — only the (unavailable)
   Supabase cache is consulted, so every call recomputes. Since the matrix
   dominates every measurement, an in-process hash-keyed cache is the single
   highest-value change available, and it benefits the existing planner too.
   (A related double-build inside `run_gnn_planner` was already fixed here and
   halved GNN latency; the `gnn` entrant still pays twice because
   `solve_mission` rebuilds internally.)
2. **Generate multi-drone training data.** The assignment model fails at 25–50
   drones. This is the direct blocker for the "scale to hundreds" goal, and
   multi-drone labels are cheaper to produce than single-drone ones.
3. **Attack the matrix algorithmically** — spatial pruning or an approximate
   matrix would move the ceiling further than any model improvement, because
   both approaches share that cost.
4. **Retire the LLM planners for throughput work.** They reproduce the solver's
   answer when they succeed, 150× slower, and are fuel-feasible only ~36 % of
   the time. Their value is conversational intent handling, not planning.

## Verdict on feasibility

**Partially feasible, with a clear and narrower use case than hoped.**

- The learned decoder *does* eliminate the exponential routing cost and holds
  ~91 % of solver quality on 15-target problems while never emitting an
  infeasible route. That part works.
- It does **not** currently deliver a scalability win, for two reasons: the
  shared distance matrix dominates total runtime, and the assignment model does
  not generalize past ~10 drones.
- Both are addressable — the first by caching, the second by more training
  data — and neither is a flaw in the GNN approach itself. But on today's
  measurements the deterministic solver remains the better production choice.
