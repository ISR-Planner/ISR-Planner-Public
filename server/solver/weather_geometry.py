"""Clip geographic weather to a bounded, explicitly verified routing domain.

The gnomonic chart is a square cone on the sphere. Clip source great-circle
edges against its four great-circle sides BEFORE projecting; remote polygons
never get projected across the chart's horizon. A larger clipping cone reserves
more than 5 km around the flight domain for the weather safety margin.
"""

import math

from path_planning_core.latlng_utils import EARTH_RADIUS_KM, _unit, plane_circle
from path_planning_core.sam_wrapping import _convex_hull

FLIGHT_LIMIT = math.tan(math.radians(80.0)) / math.sqrt(2)
WEATHER_LIMIT = math.tan(math.radians(80.2)) / math.sqrt(2)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _frame(lat, lng):
    p, longitude = math.radians(lat), math.radians(lng)
    return (
        (-math.sin(longitude), math.cos(longitude), 0),
        (
            -math.sin(p) * math.cos(longitude),
            -math.sin(p) * math.sin(longitude),
            math.cos(p),
        ),
        _unit(lat, lng),
    )


def project_weather_ring(ring, ref_lat, ref_lng):
    east, north, centre = _frame(ref_lat, ref_lng)
    vertices = [_unit(float(lat), float(lng)) for lng, lat in ring]
    if len(vertices) > 1 and vertices[0] == vertices[-1]:
        vertices.pop()
    # Spherical Sutherland-Hodgman: normalized chord intersections are on
    # the source's minor great-circle arc and the clipping great circle.
    for axis in (east, north):
        for sign in (-1, 1):
            normal = tuple(WEATHER_LIMIT * c + sign * a for c, a in zip(centre, axis))
            output = []
            if not vertices:
                return []
            previous = vertices[-1]
            dp = _dot(previous, normal)
            for current in vertices:
                dc = _dot(current, normal)
                if (dp >= 0) != (dc >= 0):
                    t = dp / (dp - dc)
                    cross = tuple(
                        (1 - t) * p + t * c for p, c in zip(previous, current)
                    )
                    length = math.sqrt(_dot(cross, cross))
                    if length < 1e-12:
                        raise ValueError(
                            "Weather polygon contains an ambiguous antipodal edge."
                        )
                    output.append(tuple(v / length for v in cross))
                if dc >= 0:
                    output.append(current)
                previous, dp = current, dc
            vertices = output
    if len(vertices) < 3:
        return []
    points = []
    for vertex in vertices:
        denominator = _dot(vertex, centre)
        x = EARTH_RADIUS_KM * _dot(vertex, east) / denominator
        y = EARTH_RADIUS_KM * _dot(vertex, north) / denominator
        points.extend(plane_circle(x, y, 5.0, n=24))
    return _convex_hull(points)


def inside_flight_domain(point):
    limit = EARTH_RADIUS_KM * FLIGHT_LIMIT
    return abs(point[0]) <= limit and abs(point[1]) <= limit
