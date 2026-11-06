"""GNN inference adapter for mission comparison.

This module provides the production contract for a GNN planner.  The current
implementation supports a trained PyTorch message-passing checkpoint
(`torch_gnn_v1`) and falls back to a deterministic graph-guided baseline
(`gnn_heuristic_v0`) when no checkpoint is configured.
"""

from __future__ import annotations

import copy
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from server.solver.solver_bridge import prepare_distance_matrix, solve_mission

from .data_pipeline import BLOCKED_SENTINEL, build_graph_sample
from .torch_gnn import (
    load_checkpoint,
    predict_routes,
    predict_target_assignments,
)


@dataclass
class GnnPlannerResult:
    model_name: str
    graph: Dict[str, Any]
    assignments: Dict[str, str]
    solution: Dict[str, Any]
    latency_s: float
    error: Optional[str] = None

    def to_agent_payload(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "graph": self.graph,
            "assignments": self.assignments,
            "solution": self.solution,
            "latency_s": self.latency_s,
            "error": self.error,
        }


def load_model_checkpoint(path: str | Path | None) -> Dict[str, Any] | None:
    checkpoint_path = path or os.getenv("GNN_MODEL_PATH")
    if not checkpoint_path:
        return None
    p = Path(checkpoint_path)
    if not p.exists():
        raise FileNotFoundError(f"GNN checkpoint not found: {p}")
    checkpoint = load_checkpoint(p)
    checkpoint["path"] = str(p)
    return checkpoint


def load_route_model_checkpoint(path: str | Path | None) -> Dict[str, Any] | None:
    """Load the route-edge scoring checkpoint, if one is configured."""
    checkpoint_path = path or os.getenv("GNN_ROUTE_MODEL_PATH")
    if not checkpoint_path:
        return None
    p = Path(checkpoint_path)
    if not p.exists():
        raise FileNotFoundError(f"GNN route checkpoint not found: {p}")
    checkpoint = load_checkpoint(p)
    checkpoint["path"] = str(p)
    return checkpoint


def _drone_budget(cfg: Dict[str, Any]) -> float:
    return float(
        cfg.get("fuel_budget_km", cfg.get("fuel_budget", cfg.get("fuelBudget", 0.0)))
        or 0.0
    )


def _allowed(cfg: Dict[str, Any], target: Dict[str, Any]) -> bool:
    access = cfg.get("target_access") or {}
    if not access:
        return True
    ttype = str(target.get("type", "a")).lower()
    return bool(access.get(ttype, access.get(ttype.upper(), False)))


def _end_airport(cfg: Dict[str, Any]) -> str:
    end = str(cfg.get("end_airport") or cfg.get("start_airport") or "")
    return str(cfg.get("start_airport") or "") if end == "-" else end


def _distance(labels: List[str], matrix: List[List[float]], a: str, b: str) -> float:
    try:
        return float(matrix[labels.index(a)][labels.index(b)])
    except (ValueError, IndexError):
        return BLOCKED_SENTINEL


def _score_assignments(
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    matrix_data: Dict[str, Any],
) -> Dict[str, str]:
    labels = matrix_data.get("labels", [])
    matrix = matrix_data.get("matrix", [])
    excluded = {str(t) for t in matrix_data.get("excluded_targets", [])}
    enabled = {
        str(did): cfg
        for did, cfg in drone_configs.items()
        if isinstance(cfg, dict) and cfg.get("enabled", True)
    }
    assignments: Dict[str, str] = {}
    # Cost is tracked as the drone's running tour length (start -> ... -> end)
    # and each candidate is charged only its *marginal* insertion cost at the
    # end of that tour. Charging a full start->target->end round trip per target
    # (as this did originally) bills a drone for N separate out-and-back trips
    # instead of one loop, so budget is exhausted after a couple of targets and
    # most of the map is left unassigned.
    tour_cost = {did: 0.0 for did in enabled}
    tail = {did: str(cfg.get("start_airport") or "") for did, cfg in enabled.items()}
    targets = sorted(
        [t for t in env.get("targets", []) if str(t.get("id")) not in excluded],
        key=lambda t: (int(t.get("priority", 0) or 0), str(t.get("id", ""))),
        reverse=True,
    )

    for target in targets:
        tid = str(target.get("id", ""))
        best: tuple[float, str, float] | None = None
        for did, cfg in enabled.items():
            if not _allowed(cfg, target):
                continue
            end = _end_airport(cfg)
            budget = _drone_budget(cfg)
            leg = _distance(labels, matrix, tail[did], tid)
            inbound = _distance(labels, matrix, tid, end)
            if leg >= BLOCKED_SENTINEL or inbound >= BLOCKED_SENTINEL:
                continue
            # Cost of the tour if this target is appended before returning home.
            projected = tour_cost[did] + leg + inbound
            if budget > 0 and projected > budget:
                continue
            priority = float(target.get("priority", 0) or 0)
            slack = (budget - projected) if budget > 0 else 0.0
            score = (priority * 100.0) - leg + (0.05 * slack)
            if best is None or score > best[0]:
                best = (score, did, tour_cost[did] + leg)
        if best is None:
            continue
        _, best_did, new_tour_cost = best
        assignments[tid] = best_did
        tour_cost[best_did] = new_tour_cost
        tail[best_did] = tid
    return assignments


def _decode_assigned_routes(
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    matrix_data: Dict[str, Any],
    assignments: Dict[str, str],
) -> Dict[str, Any]:
    """Fallback decoder used when the heavier solver interface is unavailable."""
    labels = matrix_data.get("labels", [])
    matrix = matrix_data.get("matrix", [])
    waypoint_by_id = {str(wp["id"]): wp for wp in matrix_data.get("waypoints", [])}
    target_priority = {
        str(t.get("id")): int(t.get("priority", 0) or 0) for t in env.get("targets", [])
    }
    routes: Dict[str, Any] = {}
    sequences: Dict[str, str] = {}
    for did, cfg in drone_configs.items():
        if not isinstance(cfg, dict) or not cfg.get("enabled", True):
            continue
        did_str = str(did)
        start = str(cfg.get("start_airport") or "")
        end = _end_airport(cfg)
        assigned = [
            tid
            for tid, assigned_did in assignments.items()
            if str(assigned_did) == did_str
        ]
        assigned.sort(key=lambda tid: (-target_priority.get(tid, 0), tid))
        route = [start, *assigned, end] if start and end else []
        distance = 0.0
        trajectory: List[List[float]] = []
        for idx, wp_id in enumerate(route):
            wp = waypoint_by_id.get(wp_id)
            if wp:
                trajectory.append([float(wp["x"]), float(wp["y"])])
            if idx > 0:
                leg = _distance(labels, matrix, route[idx - 1], wp_id)
                if leg < BLOCKED_SENTINEL:
                    distance += leg
        points = sum(target_priority.get(tid, 0) for tid in assigned)
        sequences[did_str] = ",".join(route)
        routes[did_str] = {
            "route": route,
            "sequence": sequences[did_str],
            "points": points,
            "distance": round(distance, 2),
            "fuel_budget": _drone_budget(cfg),
            "trajectory": trajectory,
        }
    return {"routes": routes, "sequences": sequences}


def _routes_to_solution(
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    matrix_data: Dict[str, Any],
    sequences_by_drone: Dict[str, List[str]],
) -> Dict[str, Any]:
    """Convert decoded waypoint sequences into the standard solution shape."""
    labels = matrix_data.get("labels", [])
    matrix = matrix_data.get("matrix", [])
    waypoint_by_id = {str(wp["id"]): wp for wp in matrix_data.get("waypoints", [])}
    target_priority = {
        str(t.get("id")): int(t.get("priority", 0) or 0) for t in env.get("targets", [])
    }

    routes: Dict[str, Any] = {}
    sequences: Dict[str, str] = {}
    for did, route in sequences_by_drone.items():
        distance = 0.0
        trajectory: List[List[float]] = []
        for idx, wp_id in enumerate(route):
            wp = waypoint_by_id.get(wp_id)
            if wp:
                trajectory.append([float(wp["x"]), float(wp["y"])])
            if idx > 0:
                leg = _distance(labels, matrix, route[idx - 1], wp_id)
                if leg < BLOCKED_SENTINEL:
                    distance += leg
        points = sum(
            target_priority.get(wp, 0) for wp in route if str(wp).startswith("T")
        )
        cfg = drone_configs.get(did) or drone_configs.get(str(did)) or {}
        sequences[str(did)] = ",".join(route)
        routes[str(did)] = {
            "route": route,
            "sequence": sequences[str(did)],
            "points": points,
            "distance": round(distance, 2),
            "fuel_budget": _drone_budget(cfg) if isinstance(cfg, dict) else 0.0,
            "trajectory": trajectory,
        }
    return {"routes": routes, "sequences": sequences}


def _decode_with_route_model(
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    matrix_data: Dict[str, Any],
    graph: Dict[str, Any],
    assignments: Dict[str, str],
    route_checkpoint: Dict[str, Any],
) -> Dict[str, Any]:
    """Route each drone with the learned edge scorer instead of Held-Karp."""
    enabled = {
        str(did): cfg
        for did, cfg in drone_configs.items()
        if isinstance(cfg, dict) and cfg.get("enabled", True)
    }
    start_airports = {
        did: str(cfg.get("start_airport") or "") for did, cfg in enabled.items()
    }
    end_airports = {did: _end_airport(cfg) for did, cfg in enabled.items()}

    sequences_by_drone = predict_routes(
        graph,
        route_checkpoint,
        assignments,
        end_airports,
        start_airports,
    )
    return _routes_to_solution(env, drone_configs, matrix_data, sequences_by_drone)


def run_gnn_planner(
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    *,
    model_path: str | Path | None = None,
    route_model_path: str | Path | None = None,
    buffer: float = 0.0,
    use_solver: bool = True,
) -> GnnPlannerResult:
    t0 = time.time()
    model_info = load_model_checkpoint(model_path)
    model_name = (
        model_info.get("version", "gnn_checkpoint")
        if model_info
        else "gnn_heuristic_v0"
    )
    # Build the SAM-aware matrix once and share it. It used to be built twice
    # (once inside build_graph_sample, once here), which roughly doubled the
    # planner's latency since the matrix dominates the pipeline.
    matrix_data = prepare_distance_matrix(env, buffer)
    graph = build_graph_sample(
        env, buffer=buffer, drone_configs=drone_configs, matrix_data=matrix_data
    ).to_dict()
    if model_info:
        assignments = predict_target_assignments(graph, model_info)
    else:
        assignments = _score_assignments(env, drone_configs, matrix_data)

    assigned_env = copy.deepcopy(env)
    for target in assigned_env.get("targets", []):
        tid = str(target.get("id", ""))
        if tid in assignments:
            target["assigned_drone"] = assignments[tid]

    error: Optional[str] = None
    if use_solver:
        try:
            solution = solve_mission(assigned_env, drone_configs)
        except Exception as exc:
            # Fall back so a single bad environment doesn't abort a sweep, but
            # record *why*. Silently substituting the naive decoder would make a
            # failed exact-solver run indistinguishable from a successful GNN
            # run in the benchmark output.
            error = f"solver failed, fell back to greedy decode: {exc}"
            solution = _decode_assigned_routes(
                assigned_env,
                drone_configs,
                matrix_data,
                assignments,
            )
    else:
        route_info = load_route_model_checkpoint(route_model_path)
        if route_info:
            model_name = f"{model_name}+{route_info.get('version', 'route_edge')}"
            solution = _decode_with_route_model(
                assigned_env,
                drone_configs,
                matrix_data,
                graph,
                assignments,
                route_info,
            )
        else:
            solution = _decode_assigned_routes(
                assigned_env,
                drone_configs,
                matrix_data,
                assignments,
            )
    return GnnPlannerResult(
        model_name=model_name,
        graph=graph,
        assignments=assignments,
        solution=solution,
        latency_s=round(time.time() - t0, 2),
        error=error,
    )
