#!/usr/bin/env python3
"""Replay a recorded LLM benchmark's environments through the GNN and the solver control.

The benchmark batch on `origin/benchmark/100runs-1drone-15targets-seed42` stored
the full environment for each of its 100 runs alongside the reasoning and
LangGraph-v4 results. Replaying those exact environments gives a *paired*
comparison -- every planner sees identical inputs -- without spending another
2.5 hours of live LLM calls.

Usage:
    python scripts/gnn_replay_reference.py \
        --runs-jsonl results/reference/llm_100runs_1drone_15targets_seed42.jsonl \
        --out results/gnn_vs_llm_paired.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from server.agents.agent_metrics import extract_solution_metrics  # noqa: E402
from server.gnn.model import run_gnn_planner  # noqa: E402
from server.solver import sam_distance_matrix as sdm  # noqa: E402
from server.solver.solver_bridge import (  # noqa: E402
    clear_cached_matrix,
    solve_mission_with_allocation,
)

RECORDED_AGENTS = ("reasoning", "langgraph_v4", "crewai")


def _reset_matrix_cache() -> None:
    clear_cached_matrix()
    sdm._calculator._cached_matrix = None


def _solution_metrics(solution: dict, env: dict, drone_configs: dict) -> dict:
    """Score a solution with the same rule the rest of the system uses.

    Reusing extract_solution_metrics matters for `valid_solution`: the recorded
    LLM runs were scored with it, and it encodes the per-leg refuel handling.
    Scoring the new planners any other way would not be a like-for-like
    comparison -- and validity is the crux here, because the recorded agents'
    apparent point lead comes from fuel-infeasible routes.
    """
    routes = (solution or {}).get("routes") or {}
    m = extract_solution_metrics(routes, drone_configs, env.get("targets", []))
    return {
        "total_points": m["total_points"],
        "total_fuel_km": round(m["total_fuel_km"], 2),
        "targets_visited": m["targets_visited"],
        "targets_total": m["targets_total"],
        "target_coverage": m["target_coverage"],
        "valid_solution": m["valid_solution"],
    }


def replay_one(record: dict, model_path: str | None, route_model_path: str | None) -> dict:
    env = record.get("env")
    drone_configs = record.get("drone_configs") or (env or {}).get("drone_configs") or {}
    out: dict = {"run_index": record.get("run_index"), "sub_seed": record.get("sub_seed")}

    # Carry the recorded LLM results through unchanged.
    for name in RECORDED_AGENTS:
        block = record.get(name)
        if not block:
            continue
        out[name] = {
            "success": bool(block.get("success")),
            "latency_s": block.get("latency_s"),
            "total_points": block.get("total_points"),
            "total_fuel_km": block.get("total_fuel_km"),
            "target_coverage": block.get("target_coverage"),
            "valid_solution": block.get("valid_solution"),
            "recorded": True,
        }

    if not env:
        out["skipped"] = "record has no env"
        return out

    sink = StringIO()
    with redirect_stdout(sink):
        _reset_matrix_cache()
        t0 = time.time()
        try:
            solution = solve_mission_with_allocation(
                env, drone_configs, allocation_strategy="geographic"
            )
            out["solver_control"] = {
                "success": True,
                "latency_s": round(time.time() - t0, 3),
                **_solution_metrics(solution, env, drone_configs),
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            out["solver_control"] = {
                "success": False,
                "latency_s": round(time.time() - t0, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }

        for label, use_solver, rmp in (
            ("gnn", True, None),
            ("gnn_decoder", False, route_model_path),
        ):
            _reset_matrix_cache()
            t0 = time.time()
            try:
                result = run_gnn_planner(
                    env,
                    drone_configs,
                    model_path=model_path,
                    route_model_path=rmp,
                    use_solver=use_solver,
                )
                out[label] = {
                    "success": result.error is None,
                    "latency_s": round(time.time() - t0, 3),
                    "model": result.model_name,
                    **_solution_metrics(result.solution, env, drone_configs),
                    "error": result.error,
                }
            except Exception as exc:  # noqa: BLE001
                out[label] = {
                    "success": False,
                    "latency_s": round(time.time() - t0, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                }
    return out


def _agg(rows: list[dict], name: str) -> dict | None:
    oks = [r[name] for r in rows if r.get(name, {}).get("success")]
    total = sum(1 for r in rows if name in r)
    if not total:
        return None
    if not oks:
        return {"runs": total, "success_rate": 0.0}

    def med(key):
        vals = [o.get(key) for o in oks if isinstance(o.get(key), (int, float))]
        return round(statistics.median(vals), 3) if vals else None

    def mean(key):
        vals = [o.get(key) for o in oks if isinstance(o.get(key), (int, float))]
        return round(statistics.fmean(vals), 3) if vals else None

    valid = [o for o in oks if o.get("valid_solution")]
    valid_points = [
        o["total_points"] for o in valid if isinstance(o.get("total_points"), (int, float))
    ]

    return {
        "runs": total,
        "success_rate": round(len(oks) / total, 4),
        "latency_s_mean": mean("latency_s"),
        "latency_s_median": med("latency_s"),
        "total_points_mean": mean("total_points"),
        "total_points_median": med("total_points"),
        "total_fuel_km_mean": mean("total_fuel_km"),
        "target_coverage_mean": mean("target_coverage"),
        # Points on *fuel-feasible* solutions only. Reporting raw points alone
        # flatters planners that overrun the fuel budget: in the recorded batch
        # the LLM agents' invalid routes averaged far more points than their
        # valid ones, which inverts the ranking if validity is ignored.
        "valid_solution_rate": round(len(valid) / len(oks), 4),
        "total_points_mean_valid_only": (
            round(statistics.fmean(valid_points), 3) if valid_points else None
        ),
    }


def _paired_points(
    rows: list[dict], a: str, b: str, valid_only: bool = False
) -> dict | None:
    """Win/loss/tie on total_points over runs where both planners succeeded.

    With valid_only, restrict to runs where *both* produced a fuel-feasible
    solution — the only comparison in which points are meaningful.
    """
    both = [r for r in rows if r.get(a, {}).get("success") and r.get(b, {}).get("success")]
    if valid_only:
        both = [
            r for r in both
            if r[a].get("valid_solution") and r[b].get("valid_solution")
        ]
    if not both:
        return None
    a_wins = b_wins = ties = 0
    deltas = []
    for r in both:
        pa = r[a].get("total_points") or 0
        pb = r[b].get("total_points") or 0
        deltas.append(pa - pb)
        if pa > pb:
            a_wins += 1
        elif pb > pa:
            b_wins += 1
        else:
            ties += 1
    return {
        "compared_runs": len(both),
        f"{a}_wins": a_wins,
        f"{b}_wins": b_wins,
        "ties": ties,
        "mean_point_delta": round(statistics.fmean(deltas), 2),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs-jsonl", required=True)
    p.add_argument("--out", default="results/gnn_vs_llm_paired.json")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--model-path", default=None)
    p.add_argument("--route-model-path", default=None)
    args = p.parse_args()

    records = [json.loads(line) for line in Path(args.runs_jsonl).open() if line.strip()]
    if args.limit:
        records = records[: args.limit]
    print(f"replaying {len(records)} recorded environments", flush=True)

    rows = []
    for i, rec in enumerate(records):
        t0 = time.time()
        row = replay_one(rec, args.model_path, args.route_model_path)
        rows.append(row)
        sc = row.get("solver_control", {})
        gd = row.get("gnn_decoder", {})
        print(
            f"[{i+1}/{len(records)}] control={sc.get('latency_s','ERR')}s/"
            f"{sc.get('total_points','-')}pts "
            f"decoder={gd.get('latency_s','ERR')}s/{gd.get('total_points','-')}pts "
            f"({time.time()-t0:.1f}s)",
            flush=True,
        )

    planners = list(RECORDED_AGENTS) + ["solver_control", "gnn", "gnn_decoder"]
    payload = {
        "source": args.runs_jsonl,
        "runs": len(rows),
        "aggregate": {n: _agg(rows, n) for n in planners if _agg(rows, n)},
        "paired_points": {
            "gnn_vs_solver_control": _paired_points(rows, "gnn", "solver_control"),
            "gnn_decoder_vs_solver_control": _paired_points(rows, "gnn_decoder", "solver_control"),
            "solver_control_vs_langgraph_v4": _paired_points(rows, "solver_control", "langgraph_v4"),
            "gnn_decoder_vs_langgraph_v4": _paired_points(rows, "gnn_decoder", "langgraph_v4"),
        },
        "paired_points_valid_only": {
            "gnn_vs_solver_control": _paired_points(rows, "gnn", "solver_control", True),
            "gnn_decoder_vs_solver_control": _paired_points(rows, "gnn_decoder", "solver_control", True),
            "solver_control_vs_langgraph_v4": _paired_points(rows, "solver_control", "langgraph_v4", True),
            "gnn_decoder_vs_langgraph_v4": _paired_points(rows, "gnn_decoder", "langgraph_v4", True),
        },
        "rows": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
