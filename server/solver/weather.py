"""Conservative horizontal weather constraints for the design preview."""

import copy

from path_planning_core.boundary_navigation import (
    _is_point_on_polygon_boundary,
    _point_in_polygon,
)
from path_planning_core.latlng_utils import latlng_to_local_xy

from .projection import _project_env_to_local_km


def segment_enters(a, b, polygon):
    # Split at every edge intersection and inspect each open interval. Unlike
    # uniform sampling, this catches even a very short crossing of a thin area.
    dx, dy = b[0] - a[0], b[1] - a[1]
    cuts = [0.0, 1.0]
    for p, q in zip(polygon, polygon[1:] + polygon[:1]):
        ex, ey = q[0] - p[0], q[1] - p[1]
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        px, py = p[0] - a[0], p[1] - a[1]
        t, u = (px * ey - py * ex) / den, (px * dy - py * dx) / den
        if 0 <= t <= 1 and 0 <= u <= 1:
            cuts.append(t)
    cuts = sorted(set(cuts))
    for t, u in zip(cuts, cuts[1:]):
        mid = (t + u) / 2
        p = (a[0] + mid * dx, a[1] + mid * dy)
        if _point_in_polygon(p, polygon) and not _is_point_on_polygon_boundary(
            p, polygon
        ):
            return True
    return False


def verify_weather_routes(env, routes, configs):
    if not any(s.get("weather_ring") for s in env.get("sams", [])):
        return []
    projected, lat, lng = _project_env_to_local_km(copy.deepcopy(env))
    polygons = [
        s["avoidance_polygon"] for s in projected["sams"] if s.get("weather_ring")
    ]
    from .weather_geometry import inside_flight_domain

    errors = []
    for did, cfg in configs.items():
        if not cfg.get("enabled", True):
            continue
        rd = routes.get(str(did), {})
        path = rd.get("trajectory", [])
        if len(path) < 2:
            errors.append(f"D{did}: no valid trajectory avoiding No fly weather.")
            continue
        try:
            points = [latlng_to_local_xy(p[0], p[1], lat, lng) for p in path]
        except ValueError:
            errors.append(f"D{did}: weather route leaves the supported routing domain.")
            continue
        if not all(inside_flight_domain(p) for p in points):
            errors.append(f"D{did}: weather route leaves the supported routing domain.")
            continue
        if any(
            (_point_in_polygon(p, poly) and not _is_point_on_polygon_boundary(p, poly))
            for p in points
            for poly in polygons
        ) or any(
            segment_enters(a, b, poly)
            for a, b in zip(points, points[1:])
            for poly in polygons
        ):
            errors.append(
                f"D{did}: trajectory intersects No fly weather or its 5 km safety margin."
            )
    return errors
