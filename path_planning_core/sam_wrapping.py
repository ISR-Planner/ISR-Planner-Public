from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from path_planning_core.latlng_utils import plane_circle, plane_dist, plane_seg_dist

Point = Tuple[float, float]
Polygon = List[Point]


# ---------- basic utilities ----------


def _distance(a: Point, b: Point) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    return math.hypot(dx, dy)


def _sample_circle(cx: float, cy: float, r: float, min_seg: float = 0.3) -> List[Point]:
    """
    Sample points around a circle such that chord lengths are ~>= min_seg.
    Returns a list of (x, y) points.

    Default min_seg=0.3 km gives smooth circles even for small SAMs:
    - 1 km radius: ~21 points
    - 2 km radius: ~42 points
    """
    if r <= 0:
        return [(cx, cy)]

    # chord length s ≈ 2R sin(dθ/2) ~ min_seg
    # approximate dθ; clamp to reasonable range
    dtheta = min_seg / max(r, 1e-3)  # rough
    dtheta = max(dtheta, math.radians(5.0))  # min 5° (max 72 points)
    dtheta = min(dtheta, math.radians(15.0))  # max 15° (min 24 points)

    n_steps = max(16, int(math.ceil(2.0 * math.pi / dtheta)))  # at least 16 points

    # r is km on the globe: the ring's true shape on the mission plane (a
    # circle near the plane's centre, slightly oval far from it).
    return plane_circle(cx, cy, r, n_steps, pad=False)


def _cross(o: Point, a: Point, b: Point) -> float:
    """
    2D cross product (OA x OB).
    >0: counter-clockwise
    <0: clockwise
    """
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _convex_hull(points: List[Point]) -> Polygon:
    """
    Monotone chain convex hull.
    Returns vertices in CCW order, without repeating the first point.
    """
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts

    lower: List[Point] = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper: List[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    # last point of each list is the starting point of the other list
    hull = lower[:-1] + upper[:-1]
    return hull


def _enforce_min_edge_length(poly: Polygon, min_seg: float = 2.0) -> Polygon:
    """
    Ensure consecutive vertices are at least min_seg apart by merging
    close ones. Returns a new polygon.
    """
    if len(poly) <= 2:
        return poly[:]

    pts = poly[:]
    changed = True
    max_iterations = len(poly) + 5  # Safety limit
    iteration = 0
    while changed and len(pts) > 2 and iteration < max_iterations:
        iteration += 1
        changed = False
        new_pts: List[Point] = []
        n = len(pts)
        i = 0
        while i < n:
            a = pts[i]
            b = pts[(i + 1) % n]
            if _distance(a, b) < min_seg:
                # merge a and b into their midpoint
                mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
                new_pts.append(mid)
                # skip b
                i += 2
                changed = True
            else:
                new_pts.append(a)
                i += 1

            # avoid runaway shrinking
            if len(new_pts) < 3 and i >= n:
                # fall back to original polygon if too small
                return poly[:]

        pts = new_pts

    return pts


# ---------- clustering utilities ----------


def _sams_overlap(sam1: Dict[str, Any], sam2: Dict[str, Any]) -> bool:
    """
    Check if two SAMs overlap (their circles intersect or touch).
    Returns True if distance between centers <= sum of radii.
    """
    x1 = float(sam1.get("x", 0.0))
    y1 = float(sam1.get("y", 0.0))
    r1 = float(sam1.get("range", sam1.get("radius", 0.0)))

    x2 = float(sam2.get("x", 0.0))
    y2 = float(sam2.get("y", 0.0))
    r2 = float(sam2.get("range", sam2.get("radius", 0.0)))

    dist = plane_dist((x1, y1), (x2, y2))
    return dist <= (r1 + r2)


def _point_in_polygon(pt: Point, poly: Polygon) -> bool:
    """
    Check if a point is inside a polygon using ray casting algorithm.
    Returns True if point is inside or on the boundary.
    """
    if not poly or len(poly) < 3:
        return False

    x, y = pt
    n = len(poly)
    inside = False

    p1x, p1y = poly[0]
    for i in range(1, n + 1):
        p2x, p2y = poly[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y

    return inside


def _circle_intersects_polygon(cx: float, cy: float, r: float, poly: Polygon) -> bool:
    """
    Check if a circle intersects or overlaps with a polygon.
    Returns True if:
    - Circle center is inside the polygon
    - Any polygon vertex is inside the circle
    - Circle intersects any polygon edge
    """
    if not poly or len(poly) < 3:
        return False

    # Check if circle center is inside polygon
    if _point_in_polygon((cx, cy), poly):
        return True

    # Check if any polygon vertex is inside the circle
    for vx, vy in poly:
        if plane_dist((cx, cy), (vx, vy)) <= r:
            return True

    # Check if circle intersects any edge
    n = len(poly)
    for i in range(n):
        p1 = poly[i]
        p2 = poly[(i + 1) % n]

        # Distance (globe km) from circle center to the edge
        dist = plane_seg_dist((cx, cy), p1, p2)
        if dist <= r:
            return True

    return False


def _point_to_segment_distance(pt: Point, seg_a: Point, seg_b: Point) -> float:
    """
    Calculate the minimum distance from a point to a line segment.
    """
    px, py = pt
    ax, ay = seg_a
    bx, by = seg_b

    # Vector from A to B
    abx = bx - ax
    aby = by - ay

    # Vector from A to P
    apx = px - ax
    apy = py - ay

    # Squared length of AB
    ab_len_sq = abx * abx + aby * aby

    if ab_len_sq == 0:
        # A and B are the same point
        return _distance(pt, seg_a)

    # Project P onto AB, clamped to [0, 1]
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab_len_sq))

    # Closest point on segment
    closest_x = ax + t * abx
    closest_y = ay + t * aby

    return _distance(pt, (closest_x, closest_y))


def _cluster_overlapping_sams(sams: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """
    Cluster SAMs that overlap into groups using iterative hull-based clustering.

    This algorithm handles the case where a SAM circle intersects a convex hull
    without intersecting any individual SAM circles in that hull.

    Algorithm:
    1. Start with initial clusters based on circle-to-circle overlap
    2. For each cluster, compute its convex hull
    3. Check if any unclustered SAM intersects this hull
    4. If yes, merge that SAM into the cluster and recompute
    5. Repeat until no new SAMs are added to any cluster

    Returns a list of clusters, where each cluster is a list of SAMs.
    """
    if not sams:
        return []

    n = len(sams)

    # Step 1: Initial clustering based on circle-to-circle overlap
    parent = list(range(n))

    def find(x: int) -> int:
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # Union SAMs that overlap directly
    for i in range(n):
        for j in range(i + 1, n):
            if _sams_overlap(sams[i], sams[j]):
                union(i, j)

    # Group SAMs by their root to form initial clusters
    clusters_dict: Dict[int, List[int]] = {}  # root -> list of indices
    for i in range(n):
        root = find(i)
        if root not in clusters_dict:
            clusters_dict[root] = []
        clusters_dict[root].append(i)

    # Convert to list of index lists
    clusters: List[List[int]] = list(clusters_dict.values())

    # Step 2: Iteratively merge clusters where SAM circles intersect convex hulls
    max_iterations = n + 10  # Safety limit
    iteration = 0
    changed = True

    while changed and iteration < max_iterations:
        iteration += 1
        changed = False

        # Try to merge each pair of clusters
        merged = [False] * len(clusters)
        new_clusters: List[List[int]] = []

        for i in range(len(clusters)):
            if merged[i]:
                continue

            # Build hull for cluster i
            all_points: List[Point] = []
            for idx in clusters[i]:
                sam = sams[idx]
                cx = float(sam.get("x", 0.0))
                cy = float(sam.get("y", 0.0))
                r = float(sam.get("range", sam.get("radius", 0.0)))
                circle_pts = _sample_circle(cx, cy, r, min_seg=0.3)
                all_points.extend(circle_pts)

            if not all_points:
                new_clusters.append(clusters[i])
                continue

            hull = _convex_hull(all_points)
            if len(hull) < 3:
                new_clusters.append(clusters[i])
                continue

            # Start with this cluster
            merged_cluster = clusters[i][:]

            # Check if any SAM from any other cluster intersects this hull
            for j in range(len(clusters)):
                if i == j or merged[j]:
                    continue

                should_merge = False
                for idx in clusters[j]:
                    sam = sams[idx]
                    cx = float(sam.get("x", 0.0))
                    cy = float(sam.get("y", 0.0))
                    r = float(sam.get("range", sam.get("radius", 0.0)))

                    if _circle_intersects_polygon(cx, cy, r, hull):
                        should_merge = True
                        break

                if should_merge:
                    # Merge cluster j into this merged cluster
                    merged_cluster.extend(clusters[j])
                    merged[j] = True
                    changed = True

            new_clusters.append(merged_cluster)

        clusters = new_clusters

    # Convert indices back to SAM objects
    result: List[List[Dict[str, Any]]] = []
    for cluster_indices in clusters:
        cluster = [sams[idx] for idx in cluster_indices]
        result.append(cluster)

    return result


# ---------- main entry point ----------


def wrap_sams(
    sams: List[Dict[str, Any]],
    min_seg: float = 0.3,
    *args: Any,
    **kwargs: Any,
) -> Tuple[List[Polygon], List[Dict[str, Any]]]:
    """
    Build wrapper polygons around SAM clusters.

    - Each SAM is assumed to have at least: 'x', 'y', 'range'.
    - SAMs that overlap (circles intersect) are grouped into clusters
    - Each cluster gets its own convex hull polygon
    - Isolated SAMs each get their own polygon

    Returns
    -------
    wrapped_polygons : list of polygons
        One convex polygon per cluster of overlapping SAMs.
        If sams is empty, this is [].
    metadata : list of SAM dicts (passed through)
    """
    if not sams:
        return [], sams

    # Cluster SAMs that overlap
    clusters = _cluster_overlapping_sams(
        [s for s in sams if not s.get("avoidance_polygon")]
    )

    wrapped_polygons: List[Polygon] = [
        s["avoidance_polygon"] for s in sams if s.get("avoidance_polygon")
    ]

    for cluster in clusters:
        # Sample points from all SAMs in this cluster
        all_points: List[Point] = []
        for s in cluster:
            cx = float(s.get("x", 0.0))
            cy = float(s.get("y", 0.0))
            r = float(s.get("range", s.get("radius", 0.0)))
            circle_pts = _sample_circle(cx, cy, r, min_seg=min_seg)
            all_points.extend(circle_pts)

        if not all_points:
            continue

        hull = _convex_hull(all_points)
        hull = _enforce_min_edge_length(hull, min_seg=min_seg)

        # If hull collapsed too much, skip this cluster
        if len(hull) < 3:
            continue

        wrapped_polygons.append(hull)

    return wrapped_polygons, sams
