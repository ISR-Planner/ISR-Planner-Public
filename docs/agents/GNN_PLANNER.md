# GNN Mission Planner

A graph-neural-network planner that can stand in for parts of the deterministic
solving pipeline, plus the harness used to compare it against that pipeline.

**Status**: research / evaluation. The deterministic solver remains the
production path. See [Feasibility findings](#feasibility-findings) before
planning further work.

## What it replaces

The shipped pipeline is:

```
Environment → SAM-aware distance matrix → target allocator → Held-Karp per drone → post-optimizers
```

The GNN can substitute at two different points, and the distinction matters for
interpreting any benchmark:

| Mode | Allocation | Routing | Entrant name |
|---|---|---|---|
| Allocation only | GNN assignment model | exact Held-Karp | `gnn` |
| End-to-end | GNN assignment model | learned edge decoder | `gnn_decoder` |

Only `gnn_decoder` removes the exponential solver from the loop. `gnn` still
calls `solve_mission`, so it cannot beat the control on routing cost — it can
only change *which* targets each drone gets.

## Components

```
server/gnn/
  graph_schema.py    Feature names for nodes, edges and drones. The single
                     source of truth for checkpoint compatibility.
  data_pipeline.py   Environment → GraphSample. Calls prepare_distance_matrix,
                     so the model sees SAM-aware geometry rather than having to
                     learn SAM avoidance from raw coordinates.
  torch_gnn.py       Both models, training loops, and inference.
  model.py           run_gnn_planner(): the production entry point.

server/api/gnn_routes.py    POST /api/gnn/build_graph, POST /api/gnn/predict
```

### Models

- `ISRGraphAssignmentNet` — message-passing net scoring (target, drone) pairs.
  Trained by `scripts/train_gnn.py`.
- `ISRRouteEdgeScoringNet` — scores the next waypoint given the current
  position, drone and remaining fuel. Rolled out greedily by
  `predict_routes()`. Trained by `scripts/train_gnn_route_edges.py`.

Feasibility is enforced by the decoder, not trusted to the model: a candidate is
only scored if the leg is unblocked, the drone can still reach its end airport,
and the target type is permitted. The model chooses among legal moves and cannot
invent illegal ones.

## Workflow

```bash
# 1. Generate solver-labelled training data.
#    The teacher defaults to the exact solver, which is what the GNN is
#    benchmarked against. Do not use --teacher heuristic for real training.
python scripts/generate_gnn_dataset.py --runs 40 --seed 201 \
    --targets 8 --drones 2 --output data/gnn/train_t8_d2.jsonl

# 2. Train both models.
python scripts/train_gnn.py data/gnn/*.jsonl --output models/gnn_assign.pt
python scripts/train_gnn_route_edges.py data/gnn/*.jsonl --output models/gnn_route.pt

# 3. Compare against the control.
python scripts/gnn_replay_reference.py \
    --runs-jsonl results/reference/llm_100runs_1drone_15targets_seed42.jsonl \
    --model-path models/gnn_assign.pt --route-model-path models/gnn_route.pt \
    --out results/gnn_vs_llm_paired.json

python scripts/gnn_scaling_study.py --out results/gnn_scaling.json \
    --model-path models/gnn_assign.pt --route-model-path models/gnn_route.pt

python scripts/gnn_report.py
```

Checkpoints are selected at runtime by `GNN_MODEL_PATH` and
`GNN_ROUTE_MODEL_PATH`, or per-request via `model_path` / `route_model_path`.
With neither set, `run_gnn_planner` falls back to a greedy heuristic
(`gnn_heuristic_v0`) so the endpoint always answers.

## Benchmarking

`scripts/benchmark_agents.py` gained entrant selection and a scaling sweep:

```bash
# GNN vs deterministic control across a drone ladder, no LLM cost
python scripts/benchmark_agents.py --runs 5 \
    --drone-sweep 1,2,5,10,25,50,100 --targets 12 \
    --no-llm-agents --gnn --gnn-decoder --solver-control
```

The LLM agents are opt-out because they are neither affordable nor reliable at
high drone counts. Runs that error or are skipped are excluded from winner
scoring — otherwise a *failed* planner records `latency_s ≈ 0` and wins the
"fastest" category, which would invert the headline result of a scaling sweep.

## Feasibility findings

Measured on this repo; see `results/GNN_COMPARISON_REPORT.md` for the numbers.

1. **The distance matrix dominates, not Held-Karp.** Routing is a fairly flat
   ~0.7 s at the sizes tested, while the O(n²) SAM-aware matrix build swings
   from 0.05 s to 30 s+ depending on SAM geometry. `build_graph_sample` calls
   the same matrix builder, so **the GNN cannot avoid this cost** — it caps the
   achievable speedup regardless of how good the model is.
2. **Held-Karp only dominates at high targets-per-drone.**
   `orienteering_with_matrix.py` enumerates every subset of candidate targets
   and runs Held-Karp on each (~O(3^N)). Because the allocator spreads targets
   across drones, adding drones *reduces* targets-per-drone and keeps routing
   cheap. The learned decoder therefore pays off on the targets-per-drone axis,
   not the drone-count axis.
3. **Validity, not points, is the discriminator against LLM agents.** Raw point
   totals flatter planners that overrun fuel budgets. Always compare on
   fuel-feasible solutions only.

## Gotchas

- **Checkpoints are tied to the feature schema.** Changing
  `NODE_FEATURE_NAMES` (etc.) invalidates every existing checkpoint.
  `assert_checkpoint_matches_schema` raises an actionable error rather than
  letting torch fail with a bare shape mismatch.
- **`sam_clearance_norm` replaced `sam_risk`.** The old feature was non-zero
  only *inside* a SAM ring, but the matrix builder excludes targets inside
  rings before the graph is built, so it was identically `0.0` for every
  surviving target — a dead input channel.
- **Exact-solver labels are only obtainable at small scale.** That is inherent:
  if the teacher were cheap at scale there would be no reason to learn it.
  Train on small instances and rely on the GNN's size-generalization; verify
  that generalization explicitly rather than assuming it.
