"""
Evaluate one drone's explicit route — the single implementation behind every
route edit: the operator's sequence bar (/api/apply_sequence) and the agent's
route-edit tools (set_route / insert_in_route / remove_from_route).

Given an ordered list of waypoint ids (airports, targets, cut checkpoints
C{n}-{d}, synthetic starts D{d}_START) and the mission env, it returns the
SAM-avoiding trajectory, distance, points and route->trajectory index map. It
applies the same mission-wide cruise altitude to SAMs that the planner does, so
an edited route avoids exactly the SAM slice a planned route avoids.
"""

from typing import Any, Dict, List, Optional, Tuple, cast

from path_planning_core.latlng_utils import (
    altitude_distance_factor,
    local_xy_to_latlng,
    polyline_km,
)

from .altitude import drone_altitude_km, env_for_drone, matrix_for_drone
from .optimizers.common import compute_route_loiter_fuel
from .projection import _project_env_to_local_km
from .refuel_solver import _relax_positions_to_detection_radii
from .trajectory_planner import ISRTrajectoryPlanner


def route_animation_frame(
    result: Dict[str, Any],
    env: Optional[Dict[str, Any]] = None,
    drone_config: Optional[Dict[str, Any]] = None,
    drone_id: Any = "",
) -> Optional[Dict[str, Any]]:
    """The playback frame for an evaluated route, anchored on its per-hop
    distances — the same builder the solve response uses — with the altitude
    of every point when the env is given (server/solver/flight_profile.py)."""
    if not result.get("success"):
        return None
    from .animation_frame import build_animation_frame, loiter_holds
    from .flight_profile import attach_flight_profile

    # span_distances are flying only; loiter appears once, as a hold.
    frame = build_animation_frame(
        result["route"],
        result["trajectory"],
        result["waypoint_indices"],
        leg_distances=result.get("span_distances"),
        hold_distances=(
            loiter_holds(result["route"], env, drone_config)
            if env is not None
            else None
        ),
    )
    if env is not None:
        attach_flight_profile(frame, result["route"], env, drone_config, None, drone_id)
    return frame


def _failure(route_ids: List[str], error: str) -> Dict[str, Any]:
    return {
        "success": False,
        "route": route_ids,
        "distance": 0.0,
        "travel_distance": 0.0,
        "loiter_fuel": 0.0,
        "span_distances": [],
        "fuel_spans": [],
        "radius_applied": False,
        "points": 0,
        "trajectory": [],
        "waypoint_indices": {},
        "error": error,
    }


def _waypoint_positions(env: Dict[str, Any]) -> Dict[str, List[float]]:
    """Every node a route may name, in local km (env already projected)."""
    positions: Dict[str, List[float]] = {}
    for a in env.get("airports", []):
        positions[str(a.get("id", ""))] = [float(a["x"]), float(a["y"])]
    for t in env.get("targets", []):
        positions[str(t.get("id", ""))] = [float(t["x"]), float(t["y"])]
    for cp in env.get("checkpoints", []):
        if "x" in cp and "y" in cp:
            positions[str(cp.get("id", ""))] = [float(cp["x"]), float(cp["y"])]
    for node_id, node in (env.get("synthetic_starts") or {}).items():
        if "x" in node and "y" in node:
            positions[str(node_id)] = [float(node["x"]), float(node["y"])]
    return positions


def _trajectory_leg_km(
    trajectory: List[List[float]], start_idx: Optional[int], end_idx: Optional[int]
) -> Optional[float]:
    """Length of the SAM-avoiding trajectory between two waypoint indices."""
    if start_idx is None or end_idx is None or end_idx < start_idx:
        return None
    return polyline_km(trajectory[start_idx : end_idx + 1])


def evaluate_route(
    route_ids: List[str],
    env_in: Dict[str, Any],
    drone_id: str,
    distance_matrix: Optional[Dict[str, Any]] = None,
    drone_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Evaluate an explicit route for one drone.

    distance_matrix: an optional SAM-aware matrix ({"labels", "matrix"}). A leg
    whose two ends are both in it uses the matrix value, like the planner does;
    any other leg (e.g. from a cut node the matrix never saw) uses the length of
    the SAM-avoiding trajectory — never a straight line through a SAM.

    drone_config: the drone's settings. With a detection radius, target waypoints
    slide inside their circles exactly as the planner does (taut string), and
    every leg is measured on that shorter flown path instead of the
    centre-to-centre matrix.
    """
    route_ids = [str(wp).strip().upper() for wp in route_ids if str(wp).strip()]
    if len(route_ids) < 2:
        return _failure(route_ids, "A route needs at least a start and an end")

    try:
        # SAMs as this drone sees them at its cruise altitude, and its matrix.
        altitude = drone_altitude_km(drone_config, env_in)
        env_in = env_for_drone(env_in, drone_config)
        distance_matrix = matrix_for_drone(distance_matrix, drone_id, altitude)

        has_latlng = any("lat" in a for a in env_in.get("airports", [])) or any(
            "lat" in t for t in env_in.get("targets", [])
        )
        if has_latlng:
            env, ref_lat, ref_lng = _project_env_to_local_km(env_in)
        else:
            env, ref_lat, ref_lng = env_in, None, None

        positions = _waypoint_positions(env)
        for wp_id in route_ids:
            if wp_id not in positions:
                return _failure(route_ids, f"Unknown waypoint: {wp_id}")

        # Detection radius: same taut-string step as the planner (no-op without one).
        relaxed = _relax_positions_to_detection_radii(
            route_ids,
            {k: tuple(v) for k, v in positions.items()},
            env.get("targets", []),
            drone_config or {},
            env.get("sams", []),
        )
        radius_applied = any(
            tuple(relaxed[k]) != tuple(positions[k]) for k in route_ids if k in relaxed
        )
        positions = {k: list(v) for k, v in relaxed.items()}

        planner = ISRTrajectoryPlanner(env.get("sams", []))
        trajectory, waypoint_indices = cast(
            Tuple[List[List[float]], Dict[int, int]],
            planner.generate_trajectory(
                route_ids, positions, drone_id=drone_id, return_waypoint_indices=True
            ),
        )

        label_to_idx: Dict[str, int] = {}
        matrix: List[List[float]] = []
        if distance_matrix:
            label_to_idx = {
                str(lbl): i for i, lbl in enumerate(distance_matrix.get("labels", []))
            }
            matrix = distance_matrix.get("matrix", [])

        span_distances: List[float] = []  # km per hop route[i] → route[i+1]
        for i in range(len(route_ids) - 1):
            a, b = route_ids[i], route_ids[i + 1]
            if not radius_applied and a in label_to_idx and b in label_to_idx:
                span_distances.append(float(matrix[label_to_idx[a]][label_to_idx[b]]))
                continue
            leg = _trajectory_leg_km(
                trajectory, waypoint_indices.get(i), waypoint_indices.get(i + 1)
            )
            if leg is None:
                return _failure(route_ids, f"No SAM-safe path for leg {a}→{b}")
            # Flown at cruise height, like the matrix legs.
            span_distances.append(leg * altitude_distance_factor(altitude))
        travel_distance = sum(span_distances)

        # Fuel = travel + loiter. A loitering drone loiters where it flies (from
        # its detection radius, never the target's centre), so loiter is added
        # to the hop that reaches each target (fuel_spans); span_distances stay
        # travel only — the animation times the hops by them.
        targets_by_id = {str(t.get("id")): t for t in env.get("targets", [])}
        loiter = [
            compute_route_loiter_fuel([wp], drone_config or {}, targets_by_id)
            for wp in route_ids[1:]
        ]
        fuel_spans = [s + lf for s, lf in zip(span_distances, loiter)]
        total_distance = travel_distance + sum(loiter)

        priorities = {
            str(t["id"]): int(t.get("priority", 5)) for t in env.get("targets", [])
        }
        points = sum(priorities.get(wp, 0) for wp in route_ids if wp.startswith("T"))

        if ref_lat is not None and ref_lng is not None and trajectory:
            trajectory = [
                list(local_xy_to_latlng(float(p[0]), float(p[1]), ref_lat, ref_lng))
                for p in trajectory
            ]

        return {
            "success": True,
            "route": route_ids,
            "distance": total_distance,
            "travel_distance": travel_distance,
            "loiter_fuel": sum(loiter),
            "span_distances": span_distances,
            "fuel_spans": fuel_spans,
            "radius_applied": radius_applied,
            "points": points,
            "trajectory": trajectory,
            "waypoint_indices": waypoint_indices,
            "error": None,
        }
    except Exception as e:  # noqa: BLE001 — report, never crash the caller
        return _failure(route_ids, f"{type(e).__name__}: {e}")
