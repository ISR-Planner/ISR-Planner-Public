"""Lat/lng to mission-plane projection helpers for solver boundaries.

The plane is gnomonic (see path_planning_core.latlng_utils): straight legs
are great circles, and lengths are measured with plane_dist / polyline_km.
"""

import copy
import math
from typing import Any, Dict, List, Tuple

from path_planning_core.latlng_utils import (
    latlng_to_local_xy,
    local_xy_to_latlng,
    projection_centre,
)


def _validated_latlng(lat: Any, lng: Any, label: str) -> Tuple[float, float]:
    """Reject malformed mission coordinates instead of wrapping them on the globe."""
    coordinates = []
    for name, value, limit in (("latitude", lat, 90), ("longitude", lng, 180)):
        message = (
            f"{label}: invalid {name} {value!r}; expected a finite number "
            f"between {-limit} and {limit} degrees."
        )
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(message) from exc
        if isinstance(value, bool) or not math.isfinite(number) or abs(number) > limit:
            raise ValueError(message)
        coordinates.append(number)
    return coordinates[0], coordinates[1]


def _validate_env_latlng(env: Dict[str, Any]) -> None:
    """Validate geographic mission objects while retaining legacy x/y schemas."""
    for collection, kind in (
        ("airports", "Airport"),
        ("targets", "Target"),
        ("checkpoints", "Checkpoint"),
    ):
        for index, point in enumerate(env.get(collection, [])):
            if "lat" in point or "lng" in point:
                _validated_latlng(
                    point.get("lat"),
                    point.get("lng"),
                    f"{kind} {point.get('id', f'[{index}]')}",
                )
    for index, sam in enumerate(env.get("sams", [])):
        if sam.get("weather_ring"):
            continue  # Weather polygons have their own antimeridian-aware handling.
        center = sam.get("center")
        if center and ("lat" in center or "lng" in center):
            _validated_latlng(
                center.get("lat"),
                center.get("lng"),
                f"SAM {sam.get('id', f'[{index}]')} centre",
            )
    for node_id, point in env.get("synthetic_starts", {}).items():
        if "lat" in point or "lng" in point:
            _validated_latlng(
                point.get("lat"), point.get("lng"), f"Synthetic start {node_id}"
            )


def _project_env_to_local_km(
    env: Dict[str, Any],
) -> Tuple[Dict[str, Any], float, float]:
    """
    Project lat/lng environment to local x/y km coordinates.

    This is a boundary shim that converts the new lat/lng schema to the old
    x/y schema expected by solver internals. Internal logic operates in km, and
    results are unprojected back to lat/lng before return.

    Returns:
        (projected_env, ref_lat, ref_lng) where projected_env has x/y in km.
    """
    _validate_env_latlng(env)
    env = copy.deepcopy(env)

    all_latlngs: List[Tuple[float, float]] = []

    for a in env.get("airports", []):
        if "lat" in a and "lng" in a:
            all_latlngs.append((float(a["lat"]), float(a["lng"])))

    for t in env.get("targets", []):
        if "lat" in t and "lng" in t:
            all_latlngs.append((float(t["lat"]), float(t["lng"])))

    for s in env.get("sams", []):
        center = s.get("center")
        if s.get("weather_ring"):
            continue  # Remote weather must not move the flight projection centre.
        if center and "lat" in center and "lng" in center:
            all_latlngs.append((float(center["lat"]), float(center["lng"])))

    # The plane is centred so the farthest mission point is as close as
    # possible (see latlng_utils: the plane is gnomonic, exact on the globe).
    ref_lat, ref_lng = projection_centre(all_latlngs)

    for a in env.get("airports", []):
        if "lat" in a and "lng" in a:
            x_km, y_km = latlng_to_local_xy(
                float(a["lat"]), float(a["lng"]), ref_lat, ref_lng
            )
            a["x"] = x_km
            a["y"] = y_km

    for t in env.get("targets", []):
        if "lat" in t and "lng" in t:
            x_km, y_km = latlng_to_local_xy(
                float(t["lat"]), float(t["lng"]), ref_lat, ref_lng
            )
            t["x"] = x_km
            t["y"] = y_km

    routable_sams = []
    for s in env.get("sams", []):
        if s.get("weather_ring"):
            from .weather_geometry import project_weather_ring

            polygon = project_weather_ring(s["weather_ring"], ref_lat, ref_lng)
            if not polygon:
                continue  # Entirely outside the verified flight domain and margin.
            s["avoidance_polygon"] = polygon
            s["pos"] = list(polygon[0])
            s["x"], s["y"] = polygon[0]
        else:
            center = s.get("center")
            if center and "lat" in center and "lng" in center:
                x_km, y_km = latlng_to_local_xy(
                    float(center["lat"]), float(center["lng"]), ref_lat, ref_lng
                )
                s["pos"] = [x_km, y_km]
                s["x"], s["y"] = x_km, y_km
        if "range_km" in s:
            s["range"] = s["range_km"]
        routable_sams.append(s)
    env["sams"] = routable_sams

    for c in env.get("checkpoints", []):
        if "lat" in c and "lng" in c:
            x_km, y_km = latlng_to_local_xy(
                float(c["lat"]), float(c["lng"]), ref_lat, ref_lng
            )
            c["x"] = x_km
            c["y"] = y_km

    for _node_id, node_data in env.get("synthetic_starts", {}).items():
        if "lat" in node_data and "lng" in node_data:
            x_km, y_km = latlng_to_local_xy(
                float(node_data["lat"]), float(node_data["lng"]), ref_lat, ref_lng
            )
            node_data["x"] = x_km
            node_data["y"] = y_km

    for _did, state in env.get("drone_states", {}).items():
        if "fuel_budget_km" in state and "fuel_budget" not in state:
            state["fuel_budget"] = state["fuel_budget_km"]

    return env, ref_lat, ref_lng


def _apply_cruise_altitude_to_sams(
    env: Dict[str, Any], cruise_altitude_km: float
) -> Dict[str, Any]:
    """Slice every SAM at one altitude (see server/solver/altitude.py).

    Each SAM keeps its full range in `ground_range_km`, so calling this on an
    already-sliced env re-slices from the full range instead of shrinking
    twice, and an env can be re-sliced at a drone's own altitude. No-op for an
    unsliced env at altitude <= 0.
    """
    from .altitude import slice_sams_at_altitude

    return slice_sams_at_altitude(env, cruise_altitude_km)


def _project_solution_trajectories(
    solution: Dict[str, Any], ref_lat: float, ref_lng: float
) -> None:
    """
    Convert [lat, lng] trajectory points into [x_km, y_km] in place.

    Used by optimizer endpoints that receive a returned solution in lat/lng and
    need trajectories in km to match the projected environment.
    """
    routes = solution.get("routes", {})
    for did, route_data in routes.items():
        trajectory = route_data.get("trajectory")
        if trajectory and isinstance(trajectory, list):
            new_trajectory = []
            for index, point in enumerate(trajectory):
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    lat, lng = _validated_latlng(
                        point[0], point[1], f"Drone {did} trajectory point {index}"
                    )
                    x_km, y_km = latlng_to_local_xy(lat, lng, ref_lat, ref_lng)
                    new_trajectory.append([x_km, y_km])
                else:
                    new_trajectory.append(point)
            route_data["trajectory"] = new_trajectory


def _unproject_solution_trajectories(
    result: Dict[str, Any], ref_lat: float, ref_lng: float
) -> None:
    """Convert solution trajectory and wrapped polygon points to lat/lng in place."""
    routes = result.get("routes", {})
    for _did, route_data in routes.items():
        trajectory = route_data.get("trajectory")
        if trajectory and isinstance(trajectory, list):
            new_trajectory = []
            for point in trajectory:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    x_km, y_km = float(point[0]), float(point[1])
                    lat, lng = local_xy_to_latlng(x_km, y_km, ref_lat, ref_lng)
                    new_trajectory.append([lat, lng])
                else:
                    new_trajectory.append(point)
            route_data["trajectory"] = new_trajectory

    wrapped_polygons = result.get("wrapped_polygons", [])
    if wrapped_polygons:
        new_polygons = []
        for poly in wrapped_polygons:
            if isinstance(poly, list):
                new_poly = []
                for point in poly:
                    if isinstance(point, (list, tuple)) and len(point) >= 2:
                        x_km, y_km = float(point[0]), float(point[1])
                        lat, lng = local_xy_to_latlng(x_km, y_km, ref_lat, ref_lng)
                        new_poly.append([lat, lng])
                    else:
                        new_poly.append(point)
                new_polygons.append(new_poly)
            else:
                new_polygons.append(poly)
        result["wrapped_polygons"] = new_polygons
