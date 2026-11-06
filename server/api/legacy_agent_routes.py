import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from .environment_service import EnvironmentService


def _coord(env: Dict[str, Any], label: str) -> Optional[Dict[str, float]]:
    """Return {'x','y'} for airport or target label."""
    if label.startswith("A"):
        for a in env.get("airports", []):
            if a.get("id") == label:
                return {"x": float(a["x"]), "y": float(a["y"])}
    if label.startswith("T"):
        for t in env.get("targets", []):
            if t.get("id") == label:
                return {"x": float(t["x"]), "y": float(t["y"])}
    return None


def _dist(p1: Dict[str, float], p2: Dict[str, float]) -> float:
    return math.hypot(p1["x"] - p2["x"], p1["y"] - p2["y"])


def _nearest_neighbor_route(
    env: Dict[str, Any],
    start_id: str,
    end_id: str,
    candidate_ids: List[str],
    fuel_budget: float,
) -> List[str]:
    """Very simple TSP-like: start -> nearest -> ... -> end, obey fuel."""
    if not candidate_ids:
        if start_id == end_id:
            return [start_id, start_id]
        return [start_id, end_id]

    route = [start_id]
    cur = start_id
    total = 0.0

    remaining = candidate_ids[:]
    while remaining:
        p_cur = _coord(env, cur)
        if not p_cur:
            break

        best = None
        best_extra = None

        for tid in remaining:
            p_t = _coord(env, tid)
            if not p_t:
                continue
            to_t = _dist(p_cur, p_t)
            p_end = _coord(env, end_id) or p_cur
            t_to_end = _dist(p_t, p_end)
            projected = total + to_t + t_to_end
            if projected <= fuel_budget + 1e-6:
                if best is None or best_extra is None or to_t < best_extra:
                    best = tid
                    best_extra = to_t

        if best is None or best_extra is None:
            break

        total += best_extra
        cur = best
        route.append(cur)
        remaining.remove(best)

    if cur != end_id:
        p_last = _coord(env, cur)
        p_goal = _coord(env, end_id)
        if p_last and p_goal:
            total += _dist(p_last, p_goal)
        route.append(end_id)

    return route


def create_legacy_agent_router(environment_service: EnvironmentService) -> APIRouter:
    router = APIRouter()

    @router.post("/api/agent/run")
    def run_agent(req: Dict[str, Any]):
        """
        Simple legacy heuristic planner.

        This endpoint is kept for backward compatibility; newer UI flows use
        `/api/solve`, `/api/solve_with_allocation`, or `/api/agents/chat-v4`.
        """
        env = environment_service.load_environment()
        if env is None:
            return {"success": False, "error": "No environment loaded"}

        drone_cfgs = req.get("drone_configs") or {}
        enabled = req.get("enabled_drones") or {}

        all_target_ids = [t["id"] for t in env.get("targets", [])]
        routes: Dict[str, Dict[str, Any]] = {}
        sequences: Dict[str, str] = {}

        for did_str, cfg in drone_cfgs.items():
            did = int(did_str)
            is_enabled = enabled.get(did_str, enabled.get(did, False))
            if not is_enabled:
                start_id = cfg.get("start_airport", "A1")
                end_id = cfg.get("end_airport", start_id)
                route = [start_id, end_id]
            else:
                start_id = cfg.get("start_airport", "A1")
                end_id = cfg.get("end_airport", start_id)
                fuel = float(cfg.get("fuel_budget", 999))

                if did == 1:
                    candidate_ids = all_target_ids[:]
                else:
                    candidate_ids = []

                route = _nearest_neighbor_route(
                    env, start_id, end_id, candidate_ids, fuel
                )

            seq = ",".join(route)
            sequences[did_str] = seq
            routes[did_str] = {"route": route, "sequence": seq}

        environment_service.write_heuristic_outputs(
            sequences=sequences,
            drone_configs=drone_cfgs,
        )

        return {
            "success": True,
            "routes": routes,
            "sequences": sequences,
            "message": "Simple heuristic planner executed.",
        }

    return router
