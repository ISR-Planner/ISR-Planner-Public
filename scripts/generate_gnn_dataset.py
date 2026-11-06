#!/usr/bin/env python3
"""Generate local supervised GNN JSONL data without running the planner server.

This uses the benchmark harness environment generator, then labels each graph
with a teacher planner.

The teacher defaults to the deterministic allocator + exact orienteering solver
-- the same pipeline the GNN is benchmarked against. That matters: this script
previously defaulted to labelling with `run_gnn_planner(use_solver=False)`,
i.e. the GNN's *own* greedy fallback heuristic. Training on those labels caps
the model at imitating a greedy heuristic, so comparing the result against the
exact solver could only ever show the GNN losing -- a property of the labels,
not of the approach. Imitating the exact solver is the only setup in which
"can the GNN approximate the solver more cheaply?" is a meaningful question.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import benchmark_agents as ba
from server.gnn import build_dataset_record
from server.gnn.model import run_gnn_planner
from server.solver.solver_bridge import solve_mission_with_allocation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate local GNN training JSONL.")
    parser.add_argument("--runs", type=int, default=250)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="data/gnn/generated_gnn_dataset.jsonl")
    parser.add_argument("--airports", type=int, default=ba.DEFAULT_AIRPORTS)
    parser.add_argument("--targets", type=int, default=ba.DEFAULT_TARGETS)
    parser.add_argument("--sams", type=int, default=ba.DEFAULT_SAMS)
    parser.add_argument("--drones", type=int, default=ba.DEFAULT_DRONES)
    parser.add_argument("--fuel-budget-km", type=float, default=ba.DEFAULT_FUEL_BUDGET_KM)
    parser.add_argument("--bbox", type=ba.parse_bbox, default=ba.DEFAULT_BBOX)
    parser.add_argument("--min-reachable-targets", type=int, default=ba.MIN_REACHABLE_TARGETS)
    parser.add_argument("--max-regen-attempts", type=int, default=ba.MAX_REGEN_ATTEMPTS_PER_SLOT)
    parser.add_argument(
        "--teacher",
        choices=("solver", "heuristic"),
        default="solver",
        help=(
            "Label source. 'solver' (default) uses the deterministic allocator + "
            "exact orienteering solver, which is what the GNN is benchmarked "
            "against. 'heuristic' uses the GNN's own greedy fallback and is only "
            "useful for smoke-testing the pipeline -- a model trained on it "
            "cannot exceed greedy quality."
        ),
    )
    parser.add_argument(
        "--allocation-strategy",
        default="geographic",
        help="Allocation strategy for the solver teacher.",
    )
    return parser.parse_args()


def _params(args: argparse.Namespace) -> dict:
    return {
        "airports": args.airports,
        "targets": args.targets,
        "sams": args.sams,
        "drones": args.drones,
        "bbox": args.bbox,
        "fuel_budget_km": args.fuel_budget_km,
        "fuel_budget_km_range": None,
        "max_regen_attempts": args.max_regen_attempts,
        "min_reachable_targets": args.min_reachable_targets,
    }


def _allocations_from_routes(routes: dict) -> dict:
    """Recover per-drone target lists from solver routes."""
    return {
        str(did): [wp for wp in (rd.get("route") or []) if str(wp).startswith("T")]
        for did, rd in routes.items()
        if isinstance(rd, dict)
    }


def label_with_teacher(env: dict, drone_configs: dict, args: argparse.Namespace) -> dict:
    """Produce supervision labels (routes + allocations) for one environment."""
    if args.teacher == "solver":
        solution = solve_mission_with_allocation(
            env,
            drone_configs,
            allocation_strategy=args.allocation_strategy,
        )
        routes = solution.get("routes", {}) or {}
        if not routes:
            raise ValueError("solver teacher produced no routes")
        return {
            "routes": routes,
            "sequences": solution.get("sequences", {}),
            "allocations": solution.get("allocations")
            or _allocations_from_routes(routes),
        }

    result = run_gnn_planner(env, drone_configs, use_solver=False)
    return {
        "routes": result.solution.get("routes", {}),
        "sequences": result.solution.get("sequences", {}),
        "allocations": {
            did: [
                tid for tid, assigned in result.assignments.items()
                if str(assigned) == str(did)
            ]
            for did in drone_configs.keys()
        },
    }


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    params = _params(args)
    written = 0
    skipped = 0

    with output.open("w") as f:
        for i in range(args.runs):
            env, meta = ba.accept_environment(args.seed, i, params)
            drone_configs = env["drone_configs"]
            try:
                teacher_result = label_with_teacher(env, drone_configs, args)
                record = build_dataset_record(
                    env,
                    drone_configs,
                    teacher_result,
                    sample_id=f"{args.seed}-{i}",
                )
                record["env_meta"] = meta
                f.write(json.dumps(record) + "\n")
                written += 1
            except Exception as exc:  # noqa: BLE001
                skipped += 1
                print(f"[skip] run={i} {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            if (i + 1) % 25 == 0 or i == args.runs - 1:
                print(f"[{i + 1}/{args.runs}] written={written} skipped={skipped}", flush=True)

    print(f"dataset: {output.resolve()}")
    print(f"written={written} skipped={skipped}")
    return 0 if written > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
