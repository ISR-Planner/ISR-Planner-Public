"""Lat/lng <-> local-km projection helpers and geodesic distance.

All distances are kilometers, measured on the globe (sphere of radius
EARTH_RADIUS_KM).

The solver works on a flat x/y plane. The plane is a GNOMONIC projection
centred on the mission area: on it every straight line is a great circle, so
a straight leg on the plane is the shortest path on the globe, and SAM
crossing tests along a straight leg are tests along the real flight path.
Lengths on the plane are NOT kilometres away from the centre (the plane
stretches outward), so every route length must be measured with plane_dist /
polyline_km, which give the exact great-circle distance. Near the centre (any
normal mission area of a few hundred km) the plane is km to a fraction of a
percent, and plane_dist agrees with the straight-line length.
"""

import math
from typing import Iterable, List, Sequence, Tuple

EARTH_RADIUS_KM = 6371.0088
KM_PER_DEG_LAT = EARTH_RADIUS_KM * math.pi / 180.0  # ~111.195, on the same sphere

# A point this far (degrees of arc) from the plane's centre or farther cannot
# be projected usefully (the gnomonic plane ends at 90 degrees).
MAX_CENTRE_ANGLE_DEG = 85.0


def km_per_deg_lng(ref_lat: float) -> float:
    return KM_PER_DEG_LAT * math.cos(math.radians(ref_lat))


def _unit(lat: float, lng: float) -> Tuple[float, float, float]:
    phi, lam = math.radians(lat), math.radians(lng)
    return (math.cos(phi) * math.cos(lam), math.cos(phi) * math.sin(lam), math.sin(phi))


def _angle(u: Sequence[float], v: Sequence[float]) -> float:
    """Angle (radians) between two 3-vectors, accurate at small angles."""
    cx = u[1] * v[2] - u[2] * v[1]
    cy = u[2] * v[0] - u[0] * v[2]
    cz = u[0] * v[1] - u[1] * v[0]
    dot = u[0] * v[0] + u[1] * v[1] + u[2] * v[2]
    return math.atan2(math.sqrt(cx * cx + cy * cy + cz * cz), dot)


def projection_centre(latlngs: Iterable[Tuple[float, float]]) -> Tuple[float, float]:
    """Centre for the mission plane: the point whose farthest mission point is
    as close as possible (the middle of the smallest cap holding them all).

    For a normal mission area this is about the middle of the map. For a
    wide one (e.g. Oklahoma to Dubai) it sits between the far ends, so both
    stay well inside the plane.
    """
    pts = [_unit(lat, lng) for lat, lng in latlngs]
    if not pts:
        return 37.775, -122.435
    # Start from the mean direction, then pull towards the farthest point with
    # shrinking steps (Badoiu-Clarkson); converges to the minimax centre.
    c = [sum(p[k] for p in pts) for k in range(3)]
    if math.sqrt(sum(x * x for x in c)) < 1e-9:
        c = list(pts[0])
    for it in range(1, 200):
        n = math.sqrt(sum(x * x for x in c))
        c = [x / n for x in c]
        far = max(pts, key=lambda p: _angle(c, p))
        step = 1.0 / (it + 1)
        c = [c[k] + (far[k] - c[k]) * step for k in range(3)]
    n = math.sqrt(sum(x * x for x in c))
    c = [x / n for x in c]
    lat = math.degrees(math.asin(max(-1.0, min(1.0, c[2]))))
    lng = math.degrees(math.atan2(c[1], c[0]))
    return lat, lng


def latlng_to_local_xy(
    lat: float, lng: float, ref_lat: float, ref_lng: float
) -> tuple[float, float]:
    """Project (lat, lng) onto the mission plane centred at (ref_lat, ref_lng):
    (x east, y north), gnomonic, in km at the centre."""
    p, p0 = math.radians(lat), math.radians(ref_lat)
    dl = math.radians(lng - ref_lng)
    cos_c = math.sin(p0) * math.sin(p) + math.cos(p0) * math.cos(p) * math.cos(dl)
    if cos_c <= math.cos(math.radians(MAX_CENTRE_ANGLE_DEG)):
        raise ValueError(
            f"Point ({lat:.3f}, {lng:.3f}) is more than {MAX_CENTRE_ANGLE_DEG:.0f} degrees "
            f"of arc from the mission centre ({ref_lat:.3f}, {ref_lng:.3f}): "
            "the mission area is too wide to plan on one map."
        )
    x = EARTH_RADIUS_KM * math.cos(p) * math.sin(dl) / cos_c
    y = (
        EARTH_RADIUS_KM
        * (math.cos(p0) * math.sin(p) - math.sin(p0) * math.cos(p) * math.cos(dl))
        / cos_c
    )
    return x, y


def local_xy_to_latlng(
    x_km: float, y_km: float, ref_lat: float, ref_lng: float
) -> tuple[float, float]:
    """Inverse of latlng_to_local_xy."""
    rho = math.hypot(x_km, y_km)
    if rho < 1e-12:
        return ref_lat, ref_lng
    p0 = math.radians(ref_lat)
    c = math.atan(rho / EARTH_RADIUS_KM)
    sin_c, cos_c = math.sin(c), math.cos(c)
    lat = math.asin(
        max(-1.0, min(1.0, cos_c * math.sin(p0) + y_km * sin_c * math.cos(p0) / rho))
    )
    lng = math.radians(ref_lng) + math.atan2(
        x_km * sin_c, rho * math.cos(p0) * cos_c - y_km * math.sin(p0) * sin_c
    )
    lng_deg = (math.degrees(lng) + 180.0) % 360.0 - 180.0
    return math.degrees(lat), lng_deg


def plane_dist(a: Sequence[float], b: Sequence[float]) -> float:
    """Great-circle km between two points of the mission plane.

    Exact for any centre: a plane point (x, y) is the direction (x, y, R) from
    the globe's centre, so the distance is R times the angle between them.
    """
    return EARTH_RADIUS_KM * _angle(
        (a[0], a[1], EARTH_RADIUS_KM), (b[0], b[1], EARTH_RADIUS_KM)
    )


def polyline_km(points: Sequence[Sequence[float]]) -> float:
    """Great-circle length of a path of plane points."""
    return sum(plane_dist(points[i - 1], points[i]) for i in range(1, len(points)))


def _dir(p: Sequence[float]) -> Tuple[float, float, float]:
    """Unit direction from the globe's centre to a plane point."""
    n = math.sqrt(p[0] * p[0] + p[1] * p[1] + EARTH_RADIUS_KM * EARTH_RADIUS_KM)
    return (p[0] / n, p[1] / n, EARTH_RADIUS_KM / n)


def _to_plane(v: Sequence[float]) -> Tuple[float, float]:
    """Plane point of a direction (central projection onto the plane)."""
    return (v[0] * EARTH_RADIUS_KM / v[2], v[1] * EARTH_RADIUS_KM / v[2])


def plane_seg_dist(p: Sequence[float], a: Sequence[float], b: Sequence[float]) -> float:
    """Great-circle km from plane point p to the straight leg a-b (a great-
    circle arc on the globe): exact, for SAM-crossing tests."""
    P, A, B = _dir(p), _dir(a), _dir(b)
    nx = A[1] * B[2] - A[2] * B[1]
    ny = A[2] * B[0] - A[0] * B[2]
    nz = A[0] * B[1] - A[1] * B[0]
    nn = math.sqrt(nx * nx + ny * ny + nz * nz)
    ends = min(plane_dist(p, a), plane_dist(p, b))
    if nn < 1e-15:
        return ends
    nx, ny, nz = nx / nn, ny / nn, nz / nn
    t = P[0] * nx + P[1] * ny + P[2] * nz
    C = (P[0] - t * nx, P[1] - t * ny, P[2] - t * nz)  # foot on the great circle

    def side(u, v):  # (u x v) . n
        return (
            (u[1] * v[2] - u[2] * v[1]) * nx
            + (u[2] * v[0] - u[0] * v[2]) * ny
            + (u[0] * v[1] - u[1] * v[0]) * nz
        )

    if side(A, C) >= 0 and side(C, B) >= 0:
        return min(ends, EARTH_RADIUS_KM * _angle(P, C))
    return ends


def plane_circle(
    cx: float, cy: float, r_km: float, n: int = 72, pad: bool = True
) -> List[Tuple[float, float]]:
    """The ring r_km (on the globe) around plane point (cx, cy), as n plane
    points (with pad, pushed out so the polygon through them still covers
    the ring; without, on the ring itself).
    Near the plane's centre it is the ordinary circle; farther out, the
    slightly oval shape the ring really has on the plane."""
    if r_km <= 0:
        return [(cx, cy)]
    C = _dir((cx, cy))
    # Two unit vectors perpendicular to C: e1 towards the plane's +x, e2
    # towards +y (so the ring starts at +x and runs anticlockwise, like a
    # plain circle).
    X = (1.0, 0.0, 0.0)
    d = C[0]
    e1 = (X[0] - d * C[0], X[1] - d * C[1], X[2] - d * C[2])
    m = math.sqrt(sum(x * x for x in e1))
    e1 = (e1[0] / m, e1[1] / m, e1[2] / m)
    e2 = (
        C[1] * e1[2] - C[2] * e1[1],
        C[2] * e1[0] - C[0] * e1[2],
        C[0] * e1[1] - C[1] * e1[0],
    )
    # A chord cuts in by r(1 - cos(pi/n)): sample a slightly larger ring.
    ang = (r_km / EARTH_RADIUS_KM) / (math.cos(math.pi / n) if pad else 1.0)
    ca, sa = math.cos(ang), math.sin(ang)
    pts: List[Tuple[float, float]] = []
    for i in range(n):
        th = 2 * math.pi * i / n
        u = (
            math.cos(th) * e1[0] + math.sin(th) * e2[0],
            math.cos(th) * e1[1] + math.sin(th) * e2[1],
            math.cos(th) * e1[2] + math.sin(th) * e2[2],
        )
        v = (ca * C[0] + sa * u[0], ca * C[1] + sa * u[1], ca * C[2] + sa * u[2])
        pts.append(_to_plane(v))
    return pts


def altitude_distance_factor(altitude_km: float) -> float:
    """How much longer a leg is flown at altitude_km (above sea level) than
    measured at sea level: the drone is on a circle of radius R + h."""
    return (EARTH_RADIUS_KM + max(float(altitude_km or 0.0), 0.0)) / EARTH_RADIUS_KM


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometers."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


__all__: List[str] = [
    "EARTH_RADIUS_KM",
    "KM_PER_DEG_LAT",
    "km_per_deg_lng",
    "projection_centre",
    "latlng_to_local_xy",
    "local_xy_to_latlng",
    "plane_dist",
    "polyline_km",
    "plane_seg_dist",
    "plane_circle",
    "altitude_distance_factor",
    "haversine_km",
]
