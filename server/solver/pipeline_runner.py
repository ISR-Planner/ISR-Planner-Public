"""Shared post-solve optimizer pipeline runner."""

import copy
from typing import Any, Dict, List, Optional

from server.solver.altitude import sams_for_drone
from server.solver.optimizers.pipeline import apply_optimizer_pipeline
from server.solver.projection import (
    _project_env_to_local_km,
    _project_solution_trajectories,
    _unproject_solution_trajectories,
)
from server.solver.trajectory_planner import ISRTrajectoryPlanner, clip_route_to_radii


def run_pipeline_on_solution(
    solution: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Dict[str, Any]],
    distance_matrix: Optional[Dict[str, Any]] = None,
    constraints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Apply the canonical post-solve optimizer pipeline (Insert -> Swap -> No-Cross)
    to a solver result that is currently in lat/lng coordinates.

    This is the shared entry point for any caller (HTTP endpoint, agent tool,
    benchmark harness) that has a solver result and wants the same post-pass
    Run Planner applies. It handles the full lat/lng<->km boundary dance, the
    trajectory regeneration step, and merges pipeline route fields back onto
    the solver's per-route metadata (preserving fuel_budget, refuel splits,
    etc.).

    Args:
        solution: Solver output. Routes' trajectories are in lat/lng.
        env: Environment dict (lat/lng).
        drone_configs: Drone configurations.
        distance_matrix: Pre-calculated SAM-aware distance matrix from the solver.

    Returns:
        {
            "success": bool,
            "routes": {did: {route, sequence, distance, trajectory (lat/lng), ...}},
            "pipeline_summary": {...} | None,
            "error": str | None,
        }

        On pipeline failure or exception, "success" is False, the original
        solver routes are returned unchanged, and pipeline_summary records
        the failed step. Callers can treat this as "pipeline didn't help"
        rather than fatal - the solver output is still usable.
    """
    routes_in = solution.get("routes", {})
    if not routes_in:
        return {
            "success": False,
            "routes": routes_in,
            "pipeline_summary": None,
            "error": "no routes in solution",
        }

    try:
        # 1. Project env + solution trajectories from lat/lng into local km.
        projected_env, ref_lat, ref_lng = _project_env_to_local_km(env)
        projected_solution = {
            "routes": copy.deepcopy(routes_in),
            "distance_matrix": distance_matrix,
        }
        _project_solution_trajectories(projected_solution, ref_lat, ref_lng)

        # 2. Run the pipeline in km space.
        pipeline_result = apply_optimizer_pipeline(
            solution=projected_solution,
            env=projected_env,
            drone_configs=drone_configs,
            distance_matrix=distance_matrix,
            constraints=constraints,
        )

        if not pipeline_result.get("success"):
            summary = pipeline_result.get("pipeline_summary") or {
                "failed_step": "unknown"
            }
            return {
                "success": False,
                "routes": routes_in,
                "pipeline_summary": summary,
                "error": pipeline_result.get("error"),
            }

        # 3. Merge pipeline route fields onto solver's per-route metadata
        #    (so we keep fuel_budget, refuel_airport, leg distances, etc.).
        merged_routes: Dict[str, Any] = {}
        for did, opt_rd in pipeline_result.get("routes", {}).items():
            base_rd = routes_in.get(did, {})
            merged = dict(base_rd)
            merged.update(opt_rd)
            merged_routes[did] = merged

        # 4. Regenerate SAM-avoiding trajectories for pipeline-modified routes.
        #    The pipeline changes route order/contents but does not regenerate
        #    per-leg trajectories. Without this step the response would carry
        #    stale (pre-pipeline) flight paths.
        waypoint_positions: Dict[str, List[float]] = {}
        for a in projected_env.get("airports", []):
            waypoint_positions[str(a.get("id", ""))] = [float(a["x"]), float(a["y"])]
        for t in projected_env.get("targets", []):
            waypoint_positions[str(t.get("id", ""))] = [float(t["x"]), float(t["y"])]
        for cp in projected_env.get("checkpoints", []):
            cpid = str(cp.get("id", ""))
            if "x" in cp and "y" in cp:
                waypoint_positions[cpid] = [float(cp["x"]), float(cp["y"])]

        _proj_targets = projected_env.get("targets", [])
        _target_type = {
            str(t.get("id")): str(t.get("type", "a")).lower() for t in _proj_targets
        }
        for did, rd in merged_routes.items():
            route_ids = rd.get("route", [])
            if len(route_ids) >= 2:
                # Around the SAMs as this drone sees them at its altitude.
                _proj_sams = sams_for_drone(projected_env, drone_configs, did)
                traj_planner = ISRTrajectoryPlanner(_proj_sams)
                # Detection-radius taut-string (post-optimize regen): the pipeline
                # rebuilds the trajectory from waypoint CENTERS, which would discard
                # the radius shortening applied at first solve. Re-apply it here so
                # the FINAL trajectory (the one animated) follows the shorter path.
                _wp = waypoint_positions
                _cfg = (
                    (drone_configs or {}).get(str(did))
                    or (drone_configs or {}).get(did)
                    or {}
                )
                _rbt = (_cfg or {}).get("detection_radius") or {}
                if isinstance(_rbt, dict) and any(
                    float(v or 0.0) > 0.0 for v in _rbt.values()
                ):
                    _tr = {}
                    for wid in route_ids:
                        sid = str(wid)
                        if sid in _target_type:
                            _tr[sid] = float(_rbt.get(_target_type[sid], 0.0) or 0.0)
                    if any(v > 0.0 for v in _tr.values()):
                        _wp = clip_route_to_radii(
                            route_ids, waypoint_positions, _tr, sams=_proj_sams
                        )
                # Regenerate the trajectory AND its waypoint_indices together. The
                # pipeline (Insert/Swap/No-Cross) changes the route, so the OLD
                # waypoint_indices no longer maps onto the NEW trajectory. The map
                # MUST be regenerated in lockstep with the trajectory it describes.
                new_traj, new_wpi = traj_planner.generate_trajectory(
                    route_ids,
                    _wp,
                    drone_id=did,
                    return_waypoint_indices=True,
                )
                rd["trajectory"] = new_traj
                rd["waypoint_indices"] = new_wpi

        # 5. Unproject trajectories back to lat/lng for downstream consumers.
        out = {"routes": merged_routes}
        _unproject_solution_trajectories(out, ref_lat, ref_lng)

        summary = pipeline_result.get("pipeline_summary") or {}
        print(
            f"PIPELINE  PIPELINE: insertions={summary.get('insertions')}, "
            f"swaps={summary.get('swaps')} ({summary.get('swap_iterations')} iters), "
            f"or_opt={summary.get('or_opt_moves')}, "
            f"crossings_fixed={summary.get('crossings_fixed')}, "
            f"targets {summary.get('targets_before')}->{summary.get('targets_after')}, "
            f"fuel {summary.get('fuel_before')}->{summary.get('fuel_after')}",
            flush=True,
        )

        return {
            "success": True,
            "routes": out["routes"],
            "pipeline_summary": summary,
            "error": None,
        }
    except Exception as e:
        import traceback

        traceback.print_exc()
        return {
            "success": False,
            "routes": routes_in,
            "pipeline_summary": {"failed_step": "exception", "error": str(e)},
            "error": str(e),
        }
