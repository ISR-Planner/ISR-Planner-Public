"""Shared final-route inputs for playback and time-on-target scheduling."""

from typing import Any, Dict, Optional

from .altitude import matrix_for_drone
from .animation_frame import (
    build_animation_frame,
    loiter_holds,
    travel_leg_distances,
)


def _matrix_spans(route, matrix):
    labels = {str(label): i for i, label in enumerate((matrix or {}).get("labels", []))}
    distances = (matrix or {}).get("matrix", [])
    spans = []
    for a, b in zip(route, route[1:]):
        i, j = labels.get(str(a)), labels.get(str(b))
        if i is None or j is None:
            return None
        try:
            spans.append(float(distances[i][j]))
        except (IndexError, TypeError, ValueError):
            try:
                spans.append(float(distances[j][i]))
            except (IndexError, TypeError, ValueError):
                return None
    return spans or None


def route_frame_inputs(route_data, distance_matrix, drone_id, env, drone_config):
    """Select travel and loiter once, with the same precedence for all callers.

    Flown spans (including detection-radius shortcuts) are travel only. Solver
    fuel legs include loiter, which is removed before inserting waypoint holds.
    The selected drone's altitude matrix is the fallback for older route data.
    """
    route = route_data.get("route", [])
    holds = loiter_holds(route, env, drone_config) if env is not None else {}
    if route_data.get("span_distances"):
        travel = route_data["span_distances"]
    elif route_data.get("leg_distances"):
        travel = travel_leg_distances(
            route_data["leg_distances"], route, route_data.get("refuel_indices"), holds
        )
    else:
        travel = _matrix_spans(route, matrix_for_drone(distance_matrix, drone_id))
    return {
        "route": route,
        "trajectory": route_data.get("trajectory", []),
        "waypoint_indices": route_data.get("waypoint_indices", {}),
        "leg_distances": travel,
        "refuel_indices": route_data.get("refuel_indices"),
        "hold_distances": holds,
    }


def build_route_frame(
    route_data: Dict[str, Any],
    distance_matrix: Optional[Dict[str, Any]],
    drone_id: Optional[str],
    env: Optional[Dict[str, Any]],
    drone_config: Optional[Dict[str, Any]],
    *,
    include_sync_waits: bool = True,
) -> Dict[str, Any]:
    """Build the same authoritative frame for synchronization and playback."""
    inputs = route_frame_inputs(
        route_data, distance_matrix, drone_id, env, drone_config
    )
    waits = {}
    if include_sync_waits and route_data.get("sync_target"):
        wait = float(route_data.get("sync_wait_fuel") or 0.0)
        if wait > 0:
            waits[str(route_data["sync_target"])] = wait
    frame = build_animation_frame(**inputs, wait_distances=waits)
    if "waypoint_times" in route_data:
        # An explicitly scheduled route cannot acquire extra cosmetic airport
        # pauses in the player; those seconds would invalidate exact ToT.
        frame["airport_pause_seconds"] = 0.0
    return frame


def route_clock_inputs(route_data, distance_matrix, drone_id, env, drone_config):
    """Travel spans and holds in the final frame, before any sync wait.

    Waypoint anchors are ARRIVAL positions. Subtract the preceding hold from
    each anchor difference so the scheduler adds loiter exactly once. Deriving
    spans from the frame also preserves its refuel-leg subdivision and detours.
    """
    inputs = route_frame_inputs(
        route_data, distance_matrix, drone_id, env, drone_config
    )
    route, holds = inputs["route"], inputs["hold_distances"]
    indices = inputs["waypoint_indices"]
    if len(inputs["trajectory"]) >= 2 and all(
        i in indices or str(i) in indices for i in range(len(route))
    ):
        frame = build_animation_frame(**inputs)
        cumulative = frame["cumulative_distances"]
        anchors = [cumulative[frame["waypoint_indices"][i]] for i in range(len(route))]
        spans = [
            max(0.0, anchors[i] - anchors[i - 1] - holds.get(i - 1, 0.0))
            for i in range(1, len(route))
        ]
        return spans, holds
    # Route-only callers (including legacy agents) may not yet have geometry.
    # Prefer their actual travel spans if available, then the per-drone matrix.
    travel = inputs["leg_distances"]
    if travel is not None and len(travel) == len(route) - 1:
        return travel, holds
    return None, holds
