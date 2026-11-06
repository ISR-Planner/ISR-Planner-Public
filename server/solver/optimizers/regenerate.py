from typing import Any, Dict, List, Optional

from server.solver.altitude import matrix_for_drone, sams_for_drone
from server.solver.optimizers.common import compute_route_loiter_fuel


def regenerate_solution_trajectories(
    solution: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Dict[str, Any]],
    distance_matrix: Optional[Dict[str, Any]] = None,
) -> None:
    """Regenerate SAM-aware trajectories in place for every route."""
    try:
        from server.solver.trajectory_planner import (
            ISRTrajectoryPlanner as ServerISRTrajectoryPlanner,
        )

        planner_cls = ServerISRTrajectoryPlanner
    except Exception:
        from trajectory_planner import ISRTrajectoryPlanner as LocalISRTrajectoryPlanner

        planner_cls = LocalISRTrajectoryPlanner

    # Waypoint positions (x/y km): airports + targets + checkpoints + synthetic starts.
    pos: Dict[str, List[float]] = {}
    for a in env.get("airports", []):
        if "x" in a and "y" in a:
            pos[str(a["id"])] = [float(a["x"]), float(a["y"])]
    for t in env.get("targets", []):
        if "x" in t and "y" in t:
            pos[str(t["id"])] = [float(t["x"]), float(t["y"])]
    for cp in env.get("checkpoints", []):
        if "x" in cp and "y" in cp:
            pos[str(cp["id"])] = [float(cp["x"]), float(cp["y"])]
    for sid, s in (env.get("synthetic_starts", {}) or {}).items():
        if isinstance(s, dict) and "x" in s and "y" in s:
            pos[str(s.get("id", sid))] = [float(s["x"]), float(s["y"])]

    def _leg_km(a: str, b: str, did: str) -> float:
        # This drone's matrix: distances at its cruise altitude.
        dm = matrix_for_drone(distance_matrix, did) or {}
        labels, matrix = dm.get("labels", []), dm.get("matrix", [])
        if str(a) in labels and str(b) in labels:
            return float(matrix[labels.index(str(a))][labels.index(str(b))])
        pa, pb = pos.get(str(a)), pos.get(str(b))
        if pa and pb:
            return ((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2) ** 0.5
        return 0.0

    target_by_id = {str(t["id"]): t for t in env.get("targets", [])}

    for did, rd in (solution.get("routes", {}) or {}).items():
        route = rd.get("route", []) or []
        if len(route) < 2:
            continue
        # A post-cut route can start at a checkpoint absent from env. Use the
        # old trajectory's first point so the planner does not fall back to [0, 0].
        old_traj = rd.get("trajectory") or []
        if route and str(route[0]) not in pos and old_traj:
            p0 = old_traj[0]
            if isinstance(p0, (list, tuple)) and len(p0) >= 2:
                pos[str(route[0])] = [float(p0[0]), float(p0[1])]
        try:
            # Around the SAMs as this drone sees them at its altitude.
            planner = planner_cls(sams_for_drone(env, drone_configs, did))
            traj = planner.generate_trajectory(route, pos, did)
        except Exception as e:
            print(f"   [regen] planner failed D{did}: {e}", flush=True)
            traj = [list(pos.get(str(wp), [0, 0])) for wp in route]
        rd["trajectory"] = traj
        travel = sum(
            _leg_km(route[i], route[i + 1], did) for i in range(len(route) - 1)
        )
        loiter = compute_route_loiter_fuel(
            route, (drone_configs or {}).get(str(did), {}), target_by_id
        )
        rd["distance"] = travel + loiter
