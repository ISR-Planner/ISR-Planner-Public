#!/usr/bin/env python3
"""Two-axis scaling study: GNN planner vs the exact-solver control.

Runs in-process (no server, no LLM agents) so the numbers isolate algorithmic
cost. Two axes are measured separately because they stress different parts of
the pipeline:

  Axis A -- drones (1 -> hundreds), targets fixed.
      The allocator spreads a fixed target set across more drones, so
      targets-per-drone *falls*. Held-Karp stays cheap and the shared
      SAM-aware distance matrix dominates. Both planners pay the matrix cost,
      so this axis mostly measures what the GNN *cannot* save.

  Axis B -- targets per drone (drones fixed at 1).
      orienteering_with_matrix.py enumerates every subset of candidate targets
      and runs Held-Karp on each (~O(3^N)). This is the axis where a learned
      decoder can win, because it is O(N^2) greedy rollout instead.

Each row records matrix time separately from routing time, so the report can
attribute cost rather than just showing a total.

Usage:
    python scripts/gnn_scaling_study.py --out results/gnn_scaling.json
"""
from __future__ import annotations

import argparse
import json
import signal
import statistics
import sys
import time
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import benchmark_agents as ba  # noqa: E402
from server.gnn.model import run_gnn_planner  # noqa: E402
from server.solver import sam_distance_matrix as sdm  # noqa: E402
from server.solver.solver_bridge import (  # noqa: E402
    clear_cached_matrix,
    prepare_distance_matrix,
    solve_mission_with_allocation,
)

# A run that exceeds this is recorded as a timeout rather than being waited on.
DEFAULT_TIMEOUT_S = 120.0


def env_params(targets: int, drones: int, sams: int, airports: int = 2) -> dict:
    return {
        "airports": airports,
        "targets": targets,
        "sams": sams,
        "drones": drones,
        "bbox": ba.DEFAULT_BBOX,
        "fuel_budget_km": ba.DEFAULT_FUEL_BUDGET_KM,
        "fuel_budget_km_range": None,
        "max_regen_attempts": ba.MAX_REGEN_ATTEMPTS_PER_SLOT,
        "min_reachable_targets": ba.MIN_REACHABLE_TARGETS,
    }


class PlannerTimeout(Exception):
    """Raised when a planner exceeds the per-run budget."""


@contextmanager
def time_limit(seconds: float):
    """Abort a planner that runs too long.

    The exact solver enumerates every subset of candidate targets, so on the
    targets-per-drone axis it does not merely get slow, it stops returning at
    all. Recording *where* that happens is a result, but the sweep still has to
    terminate, so the run is interrupted rather than waited on.
    """
    if seconds <= 0:
        yield
        return

    def _handler(signum, frame):
        raise PlannerTimeout(f"exceeded {seconds:.0f}s")

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _reset_matrix_cache() -> None:
    """Force a cold matrix so each measurement is independent."""
    clear_cached_matrix()
    sdm._calculator._cached_matrix = None


def _points(solution: dict) -> int:
    routes = (solution or {}).get("routes") or {}
    return sum(int(r.get("points", 0) or 0) for r in routes.values() if isinstance(r, dict))


def _distance(solution: dict) -> float:
    routes = (solution or {}).get("routes") or {}
    return sum(float(r.get("distance", 0.0) or 0.0) for r in routes.values() if isinstance(r, dict))


def _targets_visited(solution: dict) -> int:
    routes = (solution or {}).get("routes") or {}
    seen = set()
    for r in routes.values():
        if isinstance(r, dict):
            seen.update(w for w in (r.get("route") or []) if str(w).startswith("T"))
    return len(seen)


def measure_once(
    targets: int,
    drones: int,
    sams: int,
    seed: int,
    run_index: int,
    route_model_path: str | None,
    model_path: str | None,
    timeout_s: float,
) -> dict:
    """One environment, all planners. Solver noise is suppressed to keep output readable."""
    sink = StringIO()
    with redirect_stdout(sink):
        env, meta = ba.accept_environment(seed, run_index, env_params(targets, drones, sams))
        drone_configs = env["drone_configs"]

        # Shared cost: the SAM-aware distance matrix. Both planners pay this.
        _reset_matrix_cache()
        t0 = time.time()
        prepare_distance_matrix(env, 0.0)
        matrix_s = time.time() - t0

        row: dict = {
            "targets": targets,
            "drones": drones,
            "sams": sams,
            "run_index": run_index,
            "reachable_targets": meta.get("reachable_target_count"),
            "matrix_s": round(matrix_s, 3),
        }

        # --- Control: deterministic allocator + exact orienteering solver ---
        t0 = time.time()
        try:
            with time_limit(timeout_s):
                solution = solve_mission_with_allocation(
                    env, drone_configs, allocation_strategy="geographic"
                )
            elapsed = time.time() - t0
            row["solver_control"] = {
                "total_s": round(elapsed, 3),
                "routing_s": round(max(0.0, elapsed - matrix_s), 3),
                "points": _points(solution),
                "distance_km": round(_distance(solution), 1),
                "targets_visited": _targets_visited(solution),
                "ok": True,
                "error": None,
                "timed_out": False,
            }
        except PlannerTimeout as exc:
            row["solver_control"] = {
                "total_s": round(time.time() - t0, 3),
                "ok": False,
                "error": str(exc),
                "timed_out": True,
            }
        except Exception as exc:  # noqa: BLE001
            row["solver_control"] = {
                "total_s": round(time.time() - t0, 3),
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "timed_out": False,
            }

        # --- GNN, allocation only (exact solver still routes) ---
        for label, use_solver, rmp in (
            ("gnn", True, None),
            ("gnn_decoder", False, route_model_path),
        ):
            _reset_matrix_cache()
            t0 = time.time()
            try:
                with time_limit(timeout_s):
                    result = run_gnn_planner(
                        env,
                        drone_configs,
                        model_path=model_path,
                        route_model_path=rmp,
                        use_solver=use_solver,
                    )
                elapsed = time.time() - t0
                row[label] = {
                    "total_s": round(elapsed, 3),
                    "routing_s": round(max(0.0, elapsed - matrix_s), 3),
                    "points": _points(result.solution),
                    "distance_km": round(_distance(result.solution), 1),
                    "targets_visited": _targets_visited(result.solution),
                    "model": result.model_name,
                    "ok": result.error is None,
                    "error": result.error,
                    "timed_out": False,
                }
            except PlannerTimeout as exc:
                row[label] = {
                    "total_s": round(time.time() - t0, 3),
                    "ok": False,
                    "error": str(exc),
                    "timed_out": True,
                }
            except Exception as exc:  # noqa: BLE001
                row[label] = {
                    "total_s": round(time.time() - t0, 3),
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "timed_out": False,
                }
    return row


def summarize(rows: list[dict], planners: list[str]) -> list[dict]:
    """Collapse repeats into per-scale-point medians."""
    grouped: dict[tuple[int, int], list[dict]] = {}
    for r in rows:
        grouped.setdefault((r["targets"], r["drones"]), []).append(r)

    out = []
    for (targets, drones), group in sorted(grouped.items()):
        point = {
            "targets": targets,
            "drones": drones,
            "runs": len(group),
            "matrix_s_median": round(statistics.median(g["matrix_s"] for g in group), 3),
        }
        for name in planners:
            oks = [g[name] for g in group if g.get(name, {}).get("ok")]
            point[name] = {
                "success_rate": round(len(oks) / len(group), 3),
                "total_s_median": round(statistics.median([o["total_s"] for o in oks]), 3) if oks else None,
                "routing_s_median": round(statistics.median([o["routing_s"] for o in oks]), 3) if oks else None,
                "points_median": round(statistics.median([o["points"] for o in oks]), 1) if oks else None,
                "targets_visited_median": round(statistics.median([o["targets_visited"] for o in oks]), 1) if oks else None,
                "errors": [g[name].get("error") for g in group if not g.get(name, {}).get("ok")][:3],
            }
        out.append(point)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="results/gnn_scaling.json")
    p.add_argument("--seed", type=int, default=20260729)
    p.add_argument("--repeats", type=int, default=3, help="Environments per scale point.")
    p.add_argument("--sams", type=int, default=2)
    p.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    p.add_argument("--model-path", default=None, help="Assignment checkpoint.")
    p.add_argument("--route-model-path", default=None, help="Route-edge checkpoint.")
    p.add_argument("--drone-axis", default="1,2,5,10,25,50,100",
                   help="Drone counts for axis A (targets fixed by --drone-axis-targets).")
    p.add_argument("--drone-axis-targets", type=int, default=12)
    p.add_argument("--target-axis", default="4,6,8,10,12,14,16",
                   help="Target counts for axis B (drones fixed at 1).")
    p.add_argument("--skip-axis-a", action="store_true")
    p.add_argument("--skip-axis-b", action="store_true")
    args = p.parse_args()

    planners = ["solver_control", "gnn", "gnn_decoder"]
    rows: list[dict] = []
    run_index = 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    def persist() -> None:
        """Write results after every run.

        Scale points can each take minutes, so writing only at the end means a
        kill or an outer timeout discards the whole sweep.
        """
        out.write_text(
            json.dumps(
                {
                    "config": {
                        "seed": args.seed,
                        "repeats": args.repeats,
                        "sams": args.sams,
                        "timeout_s": args.timeout_s,
                        "model_path": args.model_path,
                        "route_model_path": args.route_model_path,
                        "complete": False,
                    },
                    "rows": rows,
                    "summary_by_axis": {
                        axis: summarize([r for r in rows if r["axis"] == axis], planners)
                        for axis in sorted({r["axis"] for r in rows})
                    },
                },
                indent=2,
            )
        )

    def sweep(points: list[tuple[int, int]], axis: str) -> None:
        nonlocal run_index
        for targets, drones in points:
            for rep in range(args.repeats):
                t0 = time.time()
                row = measure_once(
                    targets, drones, args.sams, args.seed, run_index,
                    args.route_model_path, args.model_path, args.timeout_s,
                )
                row["axis"] = axis
                rows.append(row)
                run_index += 1
                persist()
                sc = row.get("solver_control", {})
                gd = row.get("gnn_decoder", {})
                print(
                    f"[{axis}] t={targets:>3} d={drones:>3} rep={rep} "
                    f"matrix={row['matrix_s']:>6.2f}s "
                    f"control={sc.get('total_s', 'ERR'):>7} "
                    f"decoder={gd.get('total_s', 'ERR'):>7} "
                    f"({time.time()-t0:.1f}s wall)",
                    flush=True,
                )

    if not args.skip_axis_a:
        drone_counts = [int(x) for x in args.drone_axis.split(",") if x.strip()]
        print(f"\n=== Axis A: drones {drone_counts}, targets={args.drone_axis_targets} ===", flush=True)
        sweep([(args.drone_axis_targets, d) for d in drone_counts], "drones")

    if not args.skip_axis_b:
        target_counts = [int(x) for x in args.target_axis.split(",") if x.strip()]
        print(f"\n=== Axis B: targets {target_counts}, drones=1 ===", flush=True)
        sweep([(t, 1) for t in target_counts], "targets_per_drone")

    payload = {
        "config": {
            "seed": args.seed,
            "repeats": args.repeats,
            "sams": args.sams,
            "timeout_s": args.timeout_s,
            "model_path": args.model_path,
            "route_model_path": args.route_model_path,
            "complete": True,
        },
        "rows": rows,
        "summary_by_axis": {
            axis: summarize([r for r in rows if r["axis"] == axis], planners)
            for axis in sorted({r["axis"] for r in rows})
        },
    }
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {out.resolve()} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
