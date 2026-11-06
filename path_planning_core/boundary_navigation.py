"""
Boundary Navigation using Tangent-Arc-Tangent Algorithm

Plans paths around convex polygon obstacles (SAM zones) using:
1. Radar sweep to find tangent vertices from external point
2. Walk vertex-to-vertex along polygon boundary (arc)
3. Try both directions, pick shorter total path
4. Handle multiple polygons sequentially

INVIOLABLE CONSTRAINT: Paths NEVER enter polygon interiors.
"""

from __future__ import annotations

import math
from concurrent.futures import Future
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from path_planning_core.latlng_utils import plane_dist

# Import wrap_sams to convert SAM circles to convex polygons
from .sam_wrapping import wrap_sams

Point = Tuple[float, float]
Polygon = List[Point]


# ---------- Basic Geometry Utilities ----------


def _distance(a: Point, b: Point) -> float:
    """Euclidean distance between two points."""
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _cross(o: Point, a: Point, b: Point) -> float:
    """
    2D cross product (OA x OB).
    >0: counter-clockwise, <0: clockwise, =0: collinear
    """
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _point_in_polygon(p: Point, polygon: Polygon) -> bool:
    """
    Check if point is inside polygon using ray casting.
    Returns True if strictly inside.
    """
    x, y = p
    n = len(polygon)
    inside = False

    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]

        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i

    return inside


def _segment_intersects_segment(a1: Point, a2: Point, b1: Point, b2: Point) -> bool:
    """Check if line segment a1-a2 intersects segment b1-b2."""
    d1 = _cross(b1, b2, a1)
    d2 = _cross(b1, b2, a2)
    d3 = _cross(a1, a2, b1)
    d4 = _cross(a1, a2, b2)

    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    ):
        return True

    # Check collinear cases
    eps = 1e-9
    if abs(d1) < eps and _on_segment(b1, a1, b2):
        return True
    if abs(d2) < eps and _on_segment(b1, a2, b2):
        return True
    if abs(d3) < eps and _on_segment(a1, b1, a2):
        return True
    if abs(d4) < eps and _on_segment(a1, b2, a2):
        return True

    return False


def _on_segment(p: Point, q: Point, r: Point) -> bool:
    """Check if q lies on segment p-r (assuming collinear)."""
    return min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and min(p[1], r[1]) <= q[
        1
    ] <= max(p[1], r[1])


def _is_polygon_vertex(p: Point, polygon: Polygon, eps: float = 1e-9) -> bool:
    """Check if point p is a vertex of the polygon."""
    for v in polygon:
        if abs(p[0] - v[0]) < eps and abs(p[1] - v[1]) < eps:
            return True
    return False


def _segment_crosses_polygon(a: Point, b: Point, polygon: Polygon) -> bool:
    """
    Check if segment a-b crosses through the interior of polygon.
    Returns True if it enters the polygon (not just touches boundary).

    A tangent line that touches the polygon at a vertex is NOT a crossing.
    """
    n = len(polygon)
    if n < 3:
        return False

    # Exact early exit: a segment whose bounding box misses the polygon's
    # bounding box cannot touch it (every test below would return False).
    # Most segment/polygon pairs in a mission are far apart.
    eps = 1e-9
    if (
        max(a[0], b[0]) < min(p[0] for p in polygon) - eps
        or min(a[0], b[0]) > max(p[0] for p in polygon) + eps
        or max(a[1], b[1]) < min(p[1] for p in polygon) - eps
        or min(a[1], b[1]) > max(p[1] for p in polygon) + eps
    ):
        return False

    # Check if either endpoint is inside (but not if it's a polygon vertex)
    a_is_vertex = _is_polygon_vertex(a, polygon)
    b_is_vertex = _is_polygon_vertex(b, polygon)

    if not a_is_vertex and _point_in_polygon(a, polygon):
        return True
    if not b_is_vertex and _point_in_polygon(b, polygon):
        return True

    # Check if segment intersects any polygon edge (proper crossing, not at endpoints)
    for i in range(n):
        p1 = polygon[i]
        p2 = polygon[(i + 1) % n]

        # Skip edges that share a vertex with our segment endpoints
        if b_is_vertex:
            # If b is a polygon vertex, skip edges that include b
            if _is_polygon_vertex(b, [p1]) or _is_polygon_vertex(b, [p2]):
                continue
        if a_is_vertex:
            # If a is a polygon vertex, skip edges that include a
            if _is_polygon_vertex(a, [p1]) or _is_polygon_vertex(a, [p2]):
                continue

        if _segment_intersects_segment(a, b, p1, p2):
            return True

    # Sample multiple points along the segment (not just midpoint)
    # This catches cases where the segment clips a corner of the polygon
    n_samples = 20  # More samples for longer segments
    for i in range(1, n_samples):  # Skip endpoints (already checked above)
        t = i / n_samples
        px = a[0] + t * (b[0] - a[0])
        py = a[1] + t * (b[1] - a[1])
        if _point_in_polygon((px, py), polygon):
            return True

    return False


def _path_length(path: List[Point]) -> float:
    """Calculate total length of a path."""
    if len(path) < 2:
        return 0.0
    total = 0.0
    for i in range(len(path) - 1):
        total += _distance(path[i], path[i + 1])
    return total


def _is_point_on_sam_circle_boundary(
    point: Point,
    sams: List[Dict[str, Any]],
    tolerance: float = 1e-4,
) -> bool:
    """Return True when point lies on a SAM's ring (within 10 cm, measured on
    the globe — the ring on the plane differs from a flat circle by ~1e-5 km)."""
    x, y = point
    for sam in sams:
        sx = float(sam.get("x", 0.0))
        sy = float(sam.get("y", 0.0))
        radius = float(sam.get("range", sam.get("radius", 0.0)))
        if radius <= 0:
            continue

        if abs(plane_dist((x, y), (sx, sy)) - radius) <= tolerance:
            return True

    return False


def _snap_boundary_point_to_polygon_vertex(
    point: Point,
    polygons: List[Polygon],
) -> Point:
    """
    Snap a legal SAM-boundary point to the existing polygon representation.

    The planner navigates by polygon vertices. If a checkpoint lands on a boundary
    segment, use the closer segment vertex; if equidistant, use the next vertex.
    """
    best_vertex: Optional[Point] = None
    best_dist = float("inf")

    for polygon in polygons:
        for vertex in polygon:
            dist = _distance(point, vertex)
            # <= intentionally picks the later/next vertex on exact ties.
            if dist <= best_dist:
                best_dist = dist
                best_vertex = vertex

    return best_vertex if best_vertex is not None else point


# A point this close to a polygon edge counts as ON the edge. Cut points lie on the
# drone's own SAM-hugging path, and lat/lng <-> km conversion puts them a hair
# inside or outside the edge; 50 m matches the "reached a target" tolerance.
NEAR_BOUNDARY_KM = 0.05


def _distance_to_polygon_boundary(p: Point, polygon: Polygon) -> float:
    best = float("inf")
    n = len(polygon)
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        seg_sq = dx * dx + dy * dy
        t = (
            0.0
            if seg_sq == 0
            else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / seg_sq))
        )
        best = min(best, _distance(p, (a[0] + t * dx, a[1] + t * dy)))
    return best


def _snap_near_boundary_point(point: Point, polygons: List[Polygon]) -> Point:
    """A point on, or a hair inside, a polygon edge is snapped to that polygon's
    nearest vertex — a known-good navigation node. Points clearly outside, or
    genuinely deep inside a SAM, are returned unchanged."""
    for polygon in polygons:
        d = _distance_to_polygon_boundary(point, polygon)
        if d > NEAR_BOUNDARY_KM:
            continue
        if _point_in_polygon(point, polygon) or d <= 1e-6:
            return min(polygon, key=lambda v: _distance(point, v))
    return point


def _is_point_on_any_polygon_boundary(point: Point, polygons: List[Polygon]) -> bool:
    """Return True if point lies on any wrapped SAM polygon boundary."""
    return any(_is_point_on_polygon_boundary(point, polygon) for polygon in polygons)


# ---------- Tangent Finding for Convex Polygons ----------


def _line_of_sight_clear(
    point: Point, target: Point, polygon: Polygon, target_idx: int
) -> bool:
    """
    Check if the line from point to target (a polygon vertex) is clear.

    The line is clear if it doesn't cross any polygon edge except for edges
    that share the target vertex.

    Args:
        point: External point
        target: Target vertex on polygon (polygon[target_idx])
        polygon: The convex polygon
        target_idx: Index of target in polygon

    Returns:
        True if line of sight is clear
    """
    n = len(polygon)

    for i in range(n):
        # Skip edges that include the target vertex
        if i == target_idx or (i + 1) % n == target_idx:
            continue

        p1 = polygon[i]
        p2 = polygon[(i + 1) % n]

        if _segment_intersects_segment(point, target, p1, p2):
            return False

    # Also check if the midpoint is inside the polygon
    mid = ((point[0] + target[0]) / 2, (point[1] + target[1]) / 2)
    if _point_in_polygon(mid, polygon):
        return False

    return True


def _find_tangent_vertices(point: Point, polygon: Polygon) -> Tuple[int, int]:
    """
    Find the two tangent vertices from an external point to a convex polygon.

    For a convex polygon, the tangent vertices are found by checking where the
    external point transitions from being on one side of adjacent edges to the other.
    These are the "silhouette" vertices - the extreme points visible from the external point.

    A vertex V is a tangent point if:
    - For LEFT tangent: the polygon "turns away" from the point at V
      (point is LEFT of incoming edge, RIGHT of outgoing edge)
    - For RIGHT tangent: the polygon "turns toward" the point at V
      (point is RIGHT of incoming edge, LEFT of outgoing edge)

    CRITICAL: After finding candidate tangents, we verify that the tangent LINE
    from the external point to the tangent vertex doesn't cross the polygon.
    If it does, we walk along adjacent vertices to find the actual visible tangent.

    Returns (left_tangent_idx, right_tangent_idx)
    """
    n = len(polygon)
    if n < 3:
        return (0, 0)

    left_tangent_idx = -1
    right_tangent_idx = -1

    # Track best candidates with their "strength" (how clearly they satisfy the condition)
    # This handles the case where a point is nearly collinear with an edge
    left_candidates = []
    right_candidates = []

    for i in range(n):
        prev_idx = (i - 1) % n
        next_idx = (i + 1) % n

        v_prev = polygon[prev_idx]
        v_curr = polygon[i]
        v_next = polygon[next_idx]

        # Cross product to determine which side of each edge the point is on
        # _cross(A, B, P) computes (B-A) x (P-A)
        # > 0 means P is to the LEFT of edge A->B
        # < 0 means P is to the RIGHT of edge A->B

        # For edge prev->curr: is point to the left or right?
        cross_prev = _cross(v_prev, v_curr, point)
        # For edge curr->next: is point to the left or right?
        cross_next = _cross(v_curr, v_next, point)

        # Left tangent: point is LEFT of prev edge (cross_prev > 0)
        #               AND RIGHT of next edge (cross_next < 0)
        # This is where the polygon boundary goes from facing toward the point
        # to facing away - the "left silhouette edge"
        # Use STRICT inequality for left tangent
        if cross_prev > 0 and cross_next < 0:
            # Clear left tangent - strong candidate
            left_candidates.append(
                (i, cross_prev - cross_next)
            )  # larger difference = stronger
        elif cross_prev >= 0 and cross_next <= 0:
            # Weak candidate (one or both conditions are exactly 0)
            # Only add if we don't have a strong candidate yet
            left_candidates.append((i, 0.1))  # low priority

        # Right tangent: point is RIGHT of prev edge (cross_prev < 0)
        #                AND LEFT of next edge (cross_next > 0)
        # This is where the polygon boundary goes from facing away from the point
        # to facing toward - the "right silhouette edge"
        # Use STRICT inequality for right tangent
        if cross_prev < 0 and cross_next > 0:
            # Clear right tangent - strong candidate
            right_candidates.append(
                (i, cross_next - cross_prev)
            )  # larger difference = stronger
        elif cross_prev <= 0 and cross_next >= 0:
            # Weak candidate
            right_candidates.append((i, 0.1))

    # Select best candidates (highest strength score)
    if left_candidates:
        left_tangent_idx = max(left_candidates, key=lambda x: x[1])[0]
    if right_candidates:
        right_tangent_idx = max(right_candidates, key=lambda x: x[1])[0]

    # Fallback if not found (shouldn't happen for proper convex polygon with external point)
    if left_tangent_idx == -1:
        left_tangent_idx = 0
    if right_tangent_idx == -1:
        right_tangent_idx = n // 2

    # If both tangents ended up at the same vertex, we need to find the other one
    # This can happen when the point is nearly collinear with a polygon edge
    if left_tangent_idx == right_tangent_idx:
        # Find the vertex that is geometrically opposite (farthest around the polygon)
        # Use angular sweep from the point to find the two extreme vertices

        # Calculate angle from point to each vertex
        angles = []
        for i in range(n):
            vx, vy = polygon[i]
            dx, dy = vx - point[0], vy - point[1]
            angle = math.atan2(dy, dx)
            angles.append((angle, i))

        angles.sort(key=lambda x: x[0])

        # Find the largest angular gap between consecutive vertices
        max_gap: float = -1
        gap_start_idx = 0
        for i in range(n):
            curr_angle = angles[i][0]
            next_angle = angles[(i + 1) % n][0]

            # Handle wrap-around
            gap = next_angle - curr_angle
            if gap < 0:
                gap += 2 * math.pi

            if gap > max_gap:
                max_gap = gap
                gap_start_idx = i

        # The two tangent vertices are at the edges of this gap
        right_tangent_idx = angles[gap_start_idx][1]
        left_tangent_idx = angles[(gap_start_idx + 1) % n][1]

    # CRITICAL: Verify line-of-sight visibility for both tangents
    # If a tangent line crosses the polygon, walk along vertices to find the actual visible tangent

    # Check left tangent - if blocked, walk CW (decrement) to find visible vertex
    if not _line_of_sight_clear(
        point, polygon[left_tangent_idx], polygon, left_tangent_idx
    ):
        # Walk CW from left tangent toward right tangent
        for step in range(1, n):
            candidate_idx = (left_tangent_idx - step) % n  # CW direction
            if _line_of_sight_clear(
                point, polygon[candidate_idx], polygon, candidate_idx
            ):
                left_tangent_idx = candidate_idx
                break

    # Check right tangent - if blocked, walk CCW (increment) to find visible vertex
    if not _line_of_sight_clear(
        point, polygon[right_tangent_idx], polygon, right_tangent_idx
    ):
        # Walk CCW from right tangent toward left tangent
        for step in range(1, n):
            candidate_idx = (right_tangent_idx + step) % n  # CCW direction
            if _line_of_sight_clear(
                point, polygon[candidate_idx], polygon, candidate_idx
            ):
                right_tangent_idx = candidate_idx
                break

    return (left_tangent_idx, right_tangent_idx)


# ---------- Arc Walking (Vertex to Vertex) ----------


def _dot_product(v1: Tuple[float, float], v2: Tuple[float, float]) -> float:
    """Compute dot product of two 2D vectors."""
    return v1[0] * v2[0] + v1[1] * v2[1]


def _walk_polygon_cw(polygon: Polygon, start_idx: int, end_idx: int) -> List[Point]:
    """
    Walk along polygon from start_idx to end_idx in clockwise direction.
    Returns list of vertices including start and end.
    """
    n = len(polygon)
    path = []
    i = start_idx
    max_steps = n + 1  # Safety limit
    steps = 0
    while steps < max_steps:
        path.append(polygon[i])
        if i == end_idx:
            break
        i = (i - 1) % n  # CW = decrement index (polygon is CCW)
        steps += 1
    return path


def _walk_polygon_ccw(polygon: Polygon, start_idx: int, end_idx: int) -> List[Point]:
    """
    Walk along polygon from start_idx to end_idx in counter-clockwise direction.
    Returns list of vertices including start and end.
    """
    n = len(polygon)
    path = []
    i = start_idx
    max_steps = n + 1  # Safety limit
    steps = 0
    while steps < max_steps:
        path.append(polygon[i])
        if i == end_idx:
            break
        i = (i + 1) % n  # CCW = increment index
        steps += 1
    return path


# ---------- Single Polygon Navigation ----------


def _tangent_continues_toward_goal(
    start: Point, tangent_vertex: Point, goal: Point, debug: bool = False
) -> bool:
    """
    Check if going from start to tangent_vertex continues toward the goal.

    A tangent is valid if the angle between:
    - Vector from start to tangent_vertex
    - Vector from start to goal
    is less than 90 degrees (dot product > 0).

    This rejects tangents that would cause the drone to fly AWAY from the goal.
    """
    # Vector from start to tangent
    to_tangent = (tangent_vertex[0] - start[0], tangent_vertex[1] - start[1])
    # Vector from start to goal
    to_goal = (goal[0] - start[0], goal[1] - start[1])

    dot = _dot_product(to_tangent, to_goal)

    if debug:
        print(
            f"    Tangent direction check: to_tangent={to_tangent}, to_goal={to_goal}, dot={dot:.2f}"
        )

    return dot > 0


def _exit_tangent_continues_toward_goal(
    tangent_vertex: Point, goal: Point, prev_vertex: Point, debug: bool = False
) -> bool:
    """
    Check if exiting from tangent_vertex toward goal continues forward.

    The drone arrives at tangent_vertex from the polygon boundary (prev_vertex).
    Check if going from tangent_vertex to goal continues in roughly the same
    direction (doesn't reverse).

    We use the sign of the dot product between:
      - arrival_vec: prev_vertex -> tangent_vertex
      - exit_vec:    tangent_vertex -> goal

    If dot < 0, angle > 90 degrees => reversal => REJECT.
    """
    # Arrival direction (from previous polygon vertex to tangent)
    arrival_vec = (
        tangent_vertex[0] - prev_vertex[0],
        tangent_vertex[1] - prev_vertex[1],
    )
    # Exit direction (from tangent to goal)
    exit_vec = (
        goal[0] - tangent_vertex[0],
        goal[1] - tangent_vertex[1],
    )

    dot = _dot_product(arrival_vec, exit_vec)

    if debug:
        print(
            f"    Exit direction check: arrival={arrival_vec}, "
            f"exit={exit_vec}, dot={dot:.2f}"
        )

    # Reject if angle > 90° (backward turn from entry to exit)
    return dot >= 0


def _entry_tangent_continues_forward(
    start: Point, first_vertex: Point, second_vertex: Point, debug: bool = False
) -> bool:
    """
    Ensure that when we ENTER the polygon boundary we do not reverse direction.

    We look at:
      - arrival_vec: from start to first boundary vertex (entry),
      - boundary_vec: from first vertex to second vertex (direction of travel on boundary).

    If the dot product is negative, we are turning back on ourselves
    (angle > 90°) -> reject.
    """
    arrival_vec = (first_vertex[0] - start[0], first_vertex[1] - start[1])
    boundary_vec = (
        second_vertex[0] - first_vertex[0],
        second_vertex[1] - first_vertex[1],
    )

    dot = _dot_product(arrival_vec, boundary_vec)

    if debug:
        print(
            f"    Entry direction check: arrival={arrival_vec}, "
            f"boundary={boundary_vec}, dot={dot:.2f}"
        )

    # dot < 0 -> angle > 90° -> reversal -> reject
    return dot >= 0.0


def _find_first_visible_exit(
    polygon: Polygon, start_idx: int, direction: str, goal: Point, debug: bool = False
) -> Tuple[int, List[Point]]:
    """
    Walk around polygon in given direction from start_idx until we find a vertex
    from which we can see the goal (direct line doesn't cross polygon).

    This ensures we exit at the FIRST opportunity in our chosen direction,
    preventing unnecessary "going the long way around" the polygon.

    Returns (exit_idx, arc_path) where arc_path includes all vertices from start to exit.
    Returns (-1, []) if no valid exit found (shouldn't happen for convex polygons).
    """
    n = len(polygon)
    arc = [polygon[start_idx]]
    current_idx = start_idx

    # Walk around the polygon (at most n steps to avoid infinite loop)
    for step in range(n):
        # Check if we can see the goal from current vertex
        current_vertex = polygon[current_idx]
        if not _segment_crosses_polygon(current_vertex, goal, polygon):
            if debug:
                print(
                    f"    Found visible exit at idx={current_idx} after {step} steps in {direction.upper()} direction"
                )
            return current_idx, arc

        # Move to next vertex in chosen direction
        if direction == "cw":
            current_idx = (current_idx - 1) % n
        else:  # ccw
            current_idx = (current_idx + 1) % n

        arc.append(polygon[current_idx])

    if debug:
        print("    No visible exit found after walking entire polygon")
    return -1, []


def _navigate_around_single_polygon(
    start: Point, goal: Point, polygon: Polygon, debug: bool = False
) -> Tuple[List[Point], float, str]:
    """
    Navigate from start to goal around a single convex polygon.

    CRITICAL BEHAVIOR (per user requirements):
    - Never enter the polygon interior (SAM zone).
    - When ENTERING the boundary: do not reverse direction.
    - When EXITING the boundary: do not reverse direction.
    - Try both tangents (left/right) and both directions (CW/CCW),
      then pick the SHORTEST valid path.

    Algorithm:
      1. Find the two tangent vertices from start (left and right).
      2. For each start tangent that does NOT fly away from the goal:
           - Try CW and CCW walks along the boundary to find the first
             vertex that can see the goal.
           - For each arc candidate:
               * Enforce entry-direction check (no reversal at entry).
               * Enforce exit-direction check (no reversal at exit).
               * Build full path start → arc → goal.
               * Keep only paths that pass polygon intersection validation.
      3. If no candidates, run a broader fallback search.
      4. Return the shortest valid path.
    """
    n = len(polygon)
    if n < 3:
        # Degenerate polygon – just go direct
        return [start, goal], _distance(start, goal), "direct (degenerate polygon)"

    # Find tangent vertices from start
    start_left_idx, start_right_idx = _find_tangent_vertices(start, polygon)

    if debug:
        print(f"  Start: {start}, Goal: {goal}")
        print(
            f"  Start tangents: left={start_left_idx} at {polygon[start_left_idx]}, "
            f"right={start_right_idx} at {polygon[start_right_idx]}"
        )

    candidate_paths: List[Tuple[List[Point], float, str]] = []

    # Try each start tangent: left and right
    for start_tangent_idx, tangent_name in [
        (start_left_idx, "left"),
        (start_right_idx, "right"),
    ]:
        tangent_vertex = polygon[start_tangent_idx]

        # Reject tangents that cause the drone to fly AWAY from the goal
        if not _tangent_continues_toward_goal(start, tangent_vertex, goal, debug=debug):
            if debug:
                print(
                    f"  REJECTED {tangent_name} tangent (idx={start_tangent_idx}): "
                    f"would fly away from goal"
                )
            continue

        if debug:
            print(f"  Trying {tangent_name} tangent (idx={start_tangent_idx})")

        # TRY BOTH DIRECTIONS - CW and CCW
        for direction in ["cw", "ccw"]:
            exit_idx, arc = _find_first_visible_exit(
                polygon, start_tangent_idx, direction, goal, debug=debug
            )

            if exit_idx == -1:
                if debug:
                    print(
                        f"    No visible exit found walking "
                        f"{direction.upper()} from {tangent_name} tangent"
                    )
                continue

            # --- ENTRY ANGLE CHECK ---
            # We require at least 2 vertices to define the boundary direction.
            if len(arc) >= 2:
                first_vertex = arc[0]
                second_vertex = arc[1]
                if not _entry_tangent_continues_forward(
                    start, first_vertex, second_vertex, debug=debug
                ):
                    if debug:
                        print(
                            f"    REJECTED {direction.upper()} via {tangent_name}: "
                            f"entry would reverse direction"
                        )
                    continue

            # --- EXIT ANGLE CHECK ---
            if len(arc) >= 2:
                prev_vertex = arc[-2]  # second-to-last boundary vertex
                exit_vertex = arc[-1]  # exit vertex
                if not _exit_tangent_continues_toward_goal(
                    exit_vertex, goal, prev_vertex, debug=debug
                ):
                    if debug:
                        print(
                            f"    REJECTED {direction.upper()} via {tangent_name}: "
                            f"exit would reverse direction"
                        )
                    continue

            # Build the full candidate path: start → arc → goal
            path = [start] + arc + [goal]
            length = _path_length(path)
            method = f"{direction.upper()} via {tangent_name} tangent, exit at idx {exit_idx}"

            # Validate the path doesn't cross / enter the polygon
            if _validate_path(path, [polygon]):
                candidate_paths.append((path, length, method))
                if debug:
                    print(
                        f"    Valid path: {method}, length={length:.2f}, "
                        f"waypoints={len(path)}"
                    )
            elif debug:
                print(f"    Invalid path: {method} - crosses polygon")

    # If nothing survived the strict checks, try a broader fallback
    if not candidate_paths:
        if debug:
            print(
                "  No valid paths found under strict rules. "
                "Trying all combinations as fallback..."
            )

        for start_tangent_idx, tangent_name in [
            (start_left_idx, "left"),
            (start_right_idx, "right"),
        ]:
            for direction in ["cw", "ccw"]:
                exit_idx, arc = _find_first_visible_exit(
                    polygon, start_tangent_idx, direction, goal, debug=debug
                )

                if exit_idx == -1 or not arc:
                    continue

                path = [start] + arc + [goal]
                length = _path_length(path)
                method = (
                    f"FALLBACK {direction.upper()} via {tangent_name}, "
                    f"exit at idx {exit_idx}"
                )

                if _validate_path(path, [polygon]):
                    candidate_paths.append((path, length, method))
                    if debug:
                        print(
                            f"    Fallback valid path: {method}, "
                            f"length={length:.2f}, waypoints={len(path)}"
                        )
                elif debug:
                    print(f"    Fallback invalid path: {method} - crosses polygon")

    # Still nothing? Report failure clearly.
    if not candidate_paths:
        if debug:
            print("  No valid paths found even with fallback!")
        return [], float("inf"), "INVALID: no valid path around polygon"

    # Pick the shortest valid path
    best_path, best_length, best_method = min(candidate_paths, key=lambda x: x[1])

    if debug:
        print(f"  Best path: {best_method}, length={best_length:.2f}")
        # Also show all candidates for debugging
        print("  All candidates:")
        for path, length, method in sorted(candidate_paths, key=lambda x: x[1]):
            print(f"    - {method}: {length:.2f} ({len(path)} waypoints)")

    return best_path, best_length, best_method


# ---------- Multi-Polygon Navigation using Visibility Graph ----------


def _find_blocking_polygon(
    a: Point, b: Point, polygons: List[Polygon]
) -> Optional[int]:
    """
    Find the first polygon that blocks the segment a->b.
    Returns the index of the blocking polygon, or None if path is clear.
    """
    for i, poly in enumerate(polygons):
        if _segment_crosses_polygon(a, b, poly):
            return i
    return None


def _segment_clear_of_all_polygons(a: Point, b: Point, polygons: List[Polygon]) -> bool:
    """Check if segment a->b doesn't cross any polygon."""
    for poly in polygons:
        if _segment_crosses_polygon(a, b, poly):
            return False
    return True


class _PreparedPolygon:
    """Immutable, query-local arrays for the existing visibility predicate."""

    def __init__(self, polygon: Polygon):
        self.vertices = np.array(polygon, dtype=float, copy=True).reshape(-1, 2)
        self.following = np.roll(self.vertices, -1, axis=0)
        previous = np.roll(self.vertices, 1, axis=0)
        nonhorizontal = self.vertices[:, 1] != previous[:, 1]
        self.ray_starts = self.vertices[nonhorizontal]
        self.ray_ends = previous[nonhorizontal]
        self.bounds = (
            (
                float(self.vertices[:, 0].min()),
                float(self.vertices[:, 1].min()),
                float(self.vertices[:, 0].max()),
                float(self.vertices[:, 1].max()),
            )
            if len(self.vertices)
            else (0.0, 0.0, 0.0, 0.0)
        )
        for array in (self.vertices, self.following, self.ray_starts, self.ray_ends):
            array.flags.writeable = False


# Bound temporary pair/edge arrays independently of polygon vertex count.
_VISIBILITY_BATCH_SIZE = 8192
_VISIBILITY_EDGE_ELEMENTS = 262144


def _points_in_polygon_batch(
    points: np.ndarray, polygon: _PreparedPolygon
) -> np.ndarray:
    """The same ray casts and inequalities as _point_in_polygon, in arrays."""
    result = np.zeros(len(points), dtype=bool)
    # A ray edge can satisfy (yi > y) != (yj > y) only in this y range.
    # Keep the exact half-open comparison used by the scalar predicate.
    indices = np.flatnonzero(
        (points[:, 1] >= polygon.bounds[1]) & (points[:, 1] < polygon.bounds[3])
    )
    if not len(indices):
        return result
    candidates = points[indices]
    y = candidates[:, 1, None]
    current, previous = polygon.ray_starts, polygon.ray_ends
    rows, columns = np.nonzero((current[:, 1] > y) != (previous[:, 1] > y))
    # Calculate x intersections only for the edges that actually straddle
    # each ray. Preserve the scalar operation order, including division.
    current, previous = current[columns], previous[columns]
    crossings = (
        candidates[rows, 0]
        < (previous[:, 0] - current[:, 0])
        * (candidates[rows, 1] - current[:, 1])
        / (previous[:, 1] - current[:, 1])
        + current[:, 0]
    )
    result[indices] = np.bincount(rows[crossings], minlength=len(candidates)) % 2 != 0
    return result


def _segments_cross_polygon_batch(
    starts: np.ndarray, ends: np.ndarray, polygon: _PreparedPolygon
) -> np.ndarray:
    """Batch _segment_crosses_polygon without changing its geometry decisions.

    Keep the scalar predicate as the reference. Endpoint/vertex tolerances,
    collinear intersections, skipped shared edges, and all 19 interior samples
    are identical, including their boundary behavior. This also supports
    concave polygons; no convex approximation or obstacle filtering is used.
    """
    result = np.zeros(len(starts), dtype=bool)
    if len(polygon.vertices) < 3:
        return result
    eps = 1e-9
    left, bottom, right, top = polygon.bounds
    candidates = np.flatnonzero(
        ~(
            (np.maximum(starts[:, 0], ends[:, 0]) < left - eps)
            | (np.minimum(starts[:, 0], ends[:, 0]) > right + eps)
            | (np.maximum(starts[:, 1], ends[:, 1]) < bottom - eps)
            | (np.minimum(starts[:, 1], ends[:, 1]) > top + eps)
        )
    )
    if not len(candidates):
        return result
    batch_size = max(1, _VISIBILITY_EDGE_ELEMENTS // len(polygon.vertices))
    if len(candidates) > batch_size:
        for offset in range(0, len(candidates), batch_size):
            indices = candidates[offset : offset + batch_size]
            result[indices] = _segments_cross_polygon_batch(
                starts[indices], ends[indices], polygon
            )
        return result

    a, b = starts[candidates], ends[candidates]
    vertices = polygon.vertices
    a_vertex = np.any(
        (np.abs(a[:, 0, None] - vertices[:, 0]) < eps)
        & (np.abs(a[:, 1, None] - vertices[:, 1]) < eps),
        axis=1,
    )
    b_vertex = np.any(
        (np.abs(b[:, 0, None] - vertices[:, 0]) < eps)
        & (np.abs(b[:, 1, None] - vertices[:, 1]) < eps),
        axis=1,
    )
    blocked = (~a_vertex & _points_in_polygon_batch(a, polygon)) | (
        ~b_vertex & _points_in_polygon_batch(b, polygon)
    )
    # The original tenth sample, evaluated early to reject interior chords.
    blocked |= _points_in_polygon_batch(a + 0.5 * (b - a), polygon)
    result[candidates[blocked]] = True
    keep = ~blocked
    candidates = candidates[keep]
    a, b, a_vertex, b_vertex = a[keep], b[keep], a_vertex[keep], b_vertex[keep]
    if not len(candidates):
        return result

    x1, y1 = vertices[:, 0], vertices[:, 1]
    x2, y2 = polygon.following[:, 0], polygon.following[:, 1]
    ax, ay = a[:, 0, None], a[:, 1, None]
    bx, by = b[:, 0, None], b[:, 1, None]
    dx, dy = bx - ax, by - ay
    skip = (
        a_vertex[:, None]
        & (
            ((np.abs(ax - x1) < eps) & (np.abs(ay - y1) < eps))
            | ((np.abs(ax - x2) < eps) & (np.abs(ay - y2) < eps))
        )
    ) | (
        b_vertex[:, None]
        & (
            ((np.abs(bx - x1) < eps) & (np.abs(by - y1) < eps))
            | ((np.abs(bx - x2) < eps) & (np.abs(by - y2) < eps))
        )
    )
    d1 = (x2 - x1) * (ay - y1) - (y2 - y1) * (ax - x1)
    d2 = (x2 - x1) * (by - y1) - (y2 - y1) * (bx - x1)
    d3 = dx * (y1 - ay) - dy * (x1 - ax)
    d4 = dx * (y2 - ay) - dy * (x2 - ax)
    hit = (((d1 > 0) & (d2 < 0)) | ((d1 < 0) & (d2 > 0))) & (
        ((d3 > 0) & (d4 < 0)) | ((d3 < 0) & (d4 > 0))
    )
    min_x, max_x = np.minimum(x1, x2), np.maximum(x1, x2)
    min_y, max_y = np.minimum(y1, y2), np.maximum(y1, y2)
    hit |= (
        (np.abs(d1) < eps)
        & (min_x <= ax)
        & (ax <= max_x)
        & (min_y <= ay)
        & (ay <= max_y)
    )
    hit |= (
        (np.abs(d2) < eps)
        & (min_x <= bx)
        & (bx <= max_x)
        & (min_y <= by)
        & (by <= max_y)
    )
    min_x, max_x = np.minimum(ax, bx), np.maximum(ax, bx)
    min_y, max_y = np.minimum(ay, by), np.maximum(ay, by)
    hit |= (
        (np.abs(d3) < eps)
        & (min_x <= x1)
        & (x1 <= max_x)
        & (min_y <= y1)
        & (y1 <= max_y)
    )
    hit |= (
        (np.abs(d4) < eps)
        & (min_x <= x2)
        & (x2 <= max_x)
        & (min_y <= y2)
        & (y2 <= max_y)
    )
    blocked = np.any(~skip & hit, axis=1)
    result[candidates[blocked]] = True
    keep = ~blocked
    candidates, a, b = candidates[keep], a[keep], b[keep]
    if not len(candidates):
        return result

    blocked = np.zeros(len(candidates), dtype=bool)
    for i in range(1, 20):
        if i == 10:  # Already checked; retain every other original sample.
            continue
        indices = np.flatnonzero(~blocked)
        if not len(indices):
            break
        samples = a[indices] + (i / 20) * (b[indices] - a[indices])
        blocked[indices] |= _points_in_polygon_batch(samples, polygon)
    result[candidates[blocked]] = True
    return result


def _segments_clear_batch(
    starts: np.ndarray, ends: np.ndarray, polygons: List[_PreparedPolygon]
) -> np.ndarray:
    clear = np.ones(len(starts), dtype=bool)
    for polygon in polygons:
        indices = np.flatnonzero(clear)
        if not len(indices):
            break
        clear[indices] = ~_segments_cross_polygon_batch(
            starts[indices], ends[indices], polygon
        )
    return clear


def _vertex_pair_batches(count: int):
    """Upper-triangle pairs in the original nested-loop order, bounded memory."""
    left = np.empty(_VISIBILITY_BATCH_SIZE, dtype=np.intp)
    right = np.empty(_VISIBILITY_BATCH_SIZE, dtype=np.intp)
    used = 0
    for i in range(count):
        j = i + 1
        while j < count:
            size = min(count - j, _VISIBILITY_BATCH_SIZE - used)
            left[used : used + size] = i
            right[used : used + size] = np.arange(j, j + size)
            used += size
            j += size
            if used == _VISIBILITY_BATCH_SIZE:
                yield left, right
                used = 0
    if used:
        yield left[:used], right[:used]


# The polygon-vertex part of the visibility graph depends only on the polygons,
# which are the same for every query of a solve (a distance matrix asks for
# every waypoint pair). Building it per query was O(V^2 * E) each time —
# minutes for three separate SAMs. It is built once per polygon set and reused;
# each query only adds its start and goal (O(V * E)). Same graph, same paths.
_BASE_GRAPH_CACHE: Dict[Tuple, Dict[Point, List[Tuple[Point, float]]]] = {}
_BASE_GRAPH_CACHE_SIZE = 8
_BASE_GRAPH_INFLIGHT: Dict[Tuple, Future] = {}
_BASE_GRAPH_LOCK = Lock()
_ENDPOINT_CONTEXT_CACHE: Dict[Tuple, "_EndpointVisibilityContext"] = {}
_ENDPOINT_CONNECTION_CACHE_SIZE = 512


class _EndpointVisibilityContext:
    """Bounded reuse of exact endpoint visibility for one cached base graph."""

    def __init__(self, base, polygons):
        # Retain the graph while this context is cached so its identity cannot
        # be reused for a different graph with the same polygon coordinates.
        self.base = base
        self.vertices = tuple(base)
        self.coordinates = np.asarray(self.vertices, dtype=float).reshape(-1, 2)
        self.coordinates.flags.writeable = False
        self.polygons = [_PreparedPolygon(poly) for poly in polygons]
        self.connections: Dict[Point, Tuple[Tuple[Point, float], ...]] = {}


def _endpoint_visibility_context(base, polygons) -> _EndpointVisibilityContext:
    # Unlike the pre-existing base-graph key, endpoint visibility must not
    # merge almost-equal polygons: its boundary comparisons use 1e-9 epsilon.
    exact_polygons = tuple(
        tuple((float(x), float(y)) for x, y in poly) for poly in polygons
    )
    key = (id(base), exact_polygons)
    with _BASE_GRAPH_LOCK:
        context = _ENDPOINT_CONTEXT_CACHE.get(key)
        if context is None:
            context = _EndpointVisibilityContext(base, polygons)
            if len(_ENDPOINT_CONTEXT_CACHE) >= _BASE_GRAPH_CACHE_SIZE:
                _ENDPOINT_CONTEXT_CACHE.pop(next(iter(_ENDPOINT_CONTEXT_CACHE)))
            _ENDPOINT_CONTEXT_CACHE[key] = context
        return context


def _endpoint_connections(
    point: Point, context: _EndpointVisibilityContext
) -> Tuple[Tuple[Point, float], ...]:
    with _BASE_GRAPH_LOCK:
        cached = context.connections.get(point)
    if cached is not None:
        return cached

    connections = []
    for offset in range(0, len(context.vertices), _VISIBILITY_BATCH_SIZE):
        destinations = context.coordinates[offset : offset + _VISIBILITY_BATCH_SIZE]
        origins = np.broadcast_to(point, destinations.shape)
        clear = _segments_clear_batch(origins, destinations, context.polygons)
        for index in np.flatnonzero(clear):
            vertex = context.vertices[offset + int(index)]
            if vertex != point:
                connections.append((vertex, _distance(point, vertex)))
    immutable = tuple(connections)
    with _BASE_GRAPH_LOCK:
        if point not in context.connections:
            if len(context.connections) >= _ENDPOINT_CONNECTION_CACHE_SIZE:
                context.connections.pop(next(iter(context.connections)))
            context.connections[point] = immutable
        return context.connections[point]


def _polygons_key(polygons: List[Polygon]) -> Tuple:
    return tuple(
        tuple((round(float(x), 9), round(float(y), 9)) for x, y in poly)
        for poly in polygons
    )


def _base_visibility_graph(
    polygons: List[Polygon],
) -> Dict[Point, List[Tuple[Point, float]]]:
    """Share one build for overlapping queries of the same polygon set."""
    key = _polygons_key(polygons)
    with _BASE_GRAPH_LOCK:
        cached = _BASE_GRAPH_CACHE.get(key)
        if cached is not None:
            return cached
        pending = _BASE_GRAPH_INFLIGHT.get(key)
        owner = pending is None
        if owner:
            pending = Future()
            _BASE_GRAPH_INFLIGHT[key] = pending
    if not owner:
        return pending.result()
    try:
        graph = _compute_base_visibility_graph(polygons)
        with _BASE_GRAPH_LOCK:
            if len(_BASE_GRAPH_CACHE) >= _BASE_GRAPH_CACHE_SIZE:
                evicted = _BASE_GRAPH_CACHE.pop(next(iter(_BASE_GRAPH_CACHE)))
                for context_key in list(_ENDPOINT_CONTEXT_CACHE):
                    if context_key[0] == id(evicted):
                        _ENDPOINT_CONTEXT_CACHE.pop(context_key)
            _BASE_GRAPH_CACHE[key] = graph
            _BASE_GRAPH_INFLIGHT.pop(key, None)
        pending.set_result(graph)
        return graph
    except BaseException as error:
        with _BASE_GRAPH_LOCK:
            _BASE_GRAPH_INFLIGHT.pop(key, None)
        pending.set_exception(error)
        raise


def _compute_base_visibility_graph(
    polygons: List[Polygon],
) -> Dict[Point, List[Tuple[Point, float]]]:
    """Original visibility graph, evaluating its predicates in bounded batches."""

    nodes: List[Point] = []
    seen = set()
    for poly in polygons:
        for node in poly:
            k = (round(node[0], 6), round(node[1], 6))
            if k not in seen:
                seen.add(k)
                nodes.append(node)

    graph: Dict[Point, List[Tuple[Point, float]]] = {node: [] for node in nodes}
    prepared = [_PreparedPolygon(poly) for poly in polygons]
    coordinates = np.asarray(nodes, dtype=float).reshape(-1, 2)
    for left, right in _vertex_pair_batches(len(nodes)):
        clear = _segments_clear_batch(coordinates[left], coordinates[right], prepared)
        for i, j in zip(left[clear], right[clear]):
            a, b = nodes[i], nodes[j]
            dist = _distance(a, b)
            graph[a].append((b, dist))
            graph[b].append((a, dist))

    # Also add edges along polygon boundaries (these are always valid)
    for poly in polygons:
        n = len(poly)
        for i in range(n):
            a = poly[i]
            b = poly[(i + 1) % n]
            dist = _distance(a, b)
            if a not in graph:
                graph[a] = []
            if b not in graph:
                graph[b] = []
            a_neighbors = [neighbor for neighbor, _ in graph.get(a, [])]
            if b not in a_neighbors:
                graph[a].append((b, dist))
                graph[b].append((a, dist))

    return graph


def _build_visibility_graph(
    start: Point, goal: Point, polygons: List[Polygon], debug: bool = False
) -> Dict[Point, List[Tuple[Point, float]]]:
    """
    Build a visibility graph for path planning among multiple polygons.

    Nodes: start, goal, and all polygon vertices
    Edges: Pairs of nodes that can see each other (no polygon crossing)

    The vertex-to-vertex part is shared across queries (_base_visibility_graph);
    only the start/goal edges are computed here.

    Returns adjacency list: {point: [(neighbor, distance), ...]}
    """
    base = _base_visibility_graph(polygons)
    graph: Dict[Point, List[Tuple[Point, float]]] = {
        n: list(adj) for n, adj in base.items()
    }
    vertices = list(base.keys())
    vertex_keys = {(round(v[0], 6), round(v[1], 6)) for v in vertices}

    ends = []
    for p in (start, goal):
        k = (round(p[0], 6), round(p[1], 6))
        if k in vertex_keys and p in graph:
            continue  # the end point IS a polygon vertex: already connected
        ends.append(p)
        graph.setdefault(p, [])

    if debug:
        print(f"  Visibility graph: {len(graph)} nodes ({len(polygons)} polygons)")

    context = _endpoint_visibility_context(base, polygons) if ends else None
    for p in ends:
        for v, dist in _endpoint_connections(p, context):
            graph[p].append((v, dist))
            graph[v].append((p, dist))
    if len(ends) == 2 and _segment_clear_of_all_polygons(ends[0], ends[1], polygons):
        dist = _distance(ends[0], ends[1])
        graph[ends[0]].append((ends[1], dist))
        graph[ends[1]].append((ends[0], dist))

    return graph


def _dijkstra(
    graph: Dict[Point, List[Tuple[Point, float]]],
    start: Point,
    goal: Point,
    debug: bool = False,
) -> Tuple[List[Point], float]:
    """
    Find shortest path from start to goal using Dijkstra's algorithm.

    Returns (path, distance) or ([], inf) if no path found.
    """
    import heapq

    # Preserve the existing rounded-node identity, but compute it once per
    # coordinate rather than on every edge of every queued path.
    node_keys = {point: (round(point[0], 6), round(point[1], 6)) for point in graph}

    def node_key(point: Point) -> Point:
        key = node_keys.get(point)
        if key is None:
            key = (round(point[0], 6), round(point[1], 6))
            node_keys[point] = key
        return key

    goal_key = node_key(goal)
    # Priority queue: (distance, node, path). Keep the best label for each exact
    # coordinate; equal-distance labels retain the original lexicographic path
    # tie-break. Worse arrivals cannot improve a nonnegative shortest path.
    pq = [(0.0, start, [start])]
    best = {start: (0.0, [start])}
    visited = set()

    while pq:
        dist, node, path = heapq.heappop(pq)

        key = node_key(node)

        if key in visited:
            continue
        visited.add(key)

        # Check if we reached the goal
        if key == goal_key:
            if debug:
                print(f"  Dijkstra found path: {len(path)} waypoints, dist={dist:.2f}")
            return path, dist

        # Explore neighbors
        for neighbor, edge_dist in graph.get(node, []):
            try:
                neighbor_key = node_keys[neighbor]
            except KeyError:
                # Also support terminal neighbors absent from the adjacency map.
                neighbor_key = node_key(neighbor)
            if neighbor_key not in visited:
                new_dist = dist + edge_dist
                previous = best.get(neighbor)
                if previous is not None and new_dist > previous[0]:
                    continue
                new_path = path + [neighbor]
                if (
                    previous is not None
                    and new_dist == previous[0]
                    and new_path >= previous[1]
                ):
                    continue
                best[neighbor] = (new_dist, new_path)
                heapq.heappush(pq, (new_dist, neighbor, new_path))

    if debug:
        print("  Dijkstra: no path found")
    return [], float("inf")


def _navigate_multi_polygon(
    start: Point, goal: Point, polygons: List[Polygon], debug: bool = False
) -> Tuple[List[Point], float, str]:
    """
    Navigate from start to goal avoiding multiple polygons using visibility graph.

    This properly handles complex multi-polygon scenarios where tangent lines
    may be blocked by other polygons.

    Algorithm:
    1. Build visibility graph with start, goal, and all polygon vertices
    2. Use Dijkstra to find shortest path through the graph
    3. Validate and return the path
    """
    # Check if direct path to goal is clear
    if _segment_clear_of_all_polygons(start, goal, polygons):
        dist = _distance(start, goal)
        return [start, goal], dist, "direct"

    if debug:
        print("  Direct path blocked, building visibility graph...")

    # Build visibility graph
    graph = _build_visibility_graph(start, goal, polygons, debug=debug)

    # Find shortest path using Dijkstra
    path, dist = _dijkstra(graph, start, goal, debug=debug)

    if not path:
        if debug:
            print("  Visibility graph: no path found")
        return [], float("inf"), "INVALID: no path in visibility graph"

    # Validate the path
    if not _validate_path(path, polygons, debug=debug):
        if debug:
            print("  Visibility graph path failed validation")
        return [], float("inf"), "INVALID: visibility graph path crosses polygon"

    return path, dist, f"visibility-graph ({len(path)} waypoints)"


# ---------- Path Validation ----------


def _is_point_on_polygon_boundary(
    p: Point, polygon: Polygon, eps: float = 1e-6
) -> bool:
    """Check if point is on the polygon boundary (on an edge or vertex)."""
    n = len(polygon)
    for i in range(n):
        v1 = polygon[i]
        v2 = polygon[(i + 1) % n]

        # Check if on vertex
        if abs(p[0] - v1[0]) < eps and abs(p[1] - v1[1]) < eps:
            return True

        # Check if on edge v1-v2
        # Point is on edge if it's collinear and between v1 and v2
        dx = v2[0] - v1[0]
        dy = v2[1] - v1[1]

        if abs(dx) < eps and abs(dy) < eps:
            continue  # degenerate edge

        # Parameterize: p = v1 + t * (v2 - v1)
        if abs(dx) > abs(dy):
            t = (p[0] - v1[0]) / dx
            expected_y = v1[1] + t * dy
            if 0 <= t <= 1 and abs(p[1] - expected_y) < eps:
                return True
        else:
            t = (p[1] - v1[1]) / dy
            expected_x = v1[0] + t * dx
            if 0 <= t <= 1 and abs(p[0] - expected_x) < eps:
                return True

    return False


def _segment_enters_polygon(
    a: Point, b: Point, polygon: Polygon, n_samples: int = 20
) -> bool:
    """
    Check if segment a-b enters the interior of the polygon.
    Samples points along the segment and checks if any are strictly inside.
    Points on the boundary are allowed.
    """
    for i in range(n_samples + 1):
        t = i / n_samples
        px = a[0] + t * (b[0] - a[0])
        py = a[1] + t * (b[1] - a[1])
        p = (px, py)

        # Check if this point is strictly inside (not on boundary)
        if _point_in_polygon(p, polygon):
            # But allow if it's on the boundary
            if not _is_point_on_polygon_boundary(p, polygon):
                return True

    return False


def _validate_path(
    path: List[Point], polygons: List[Polygon], debug: bool = False
) -> bool:
    """
    Validate that no segment of the path enters any polygon interior.
    Points on polygon boundaries are allowed (they're part of the navigation).
    """
    if len(path) < 2:
        return True

    for i in range(len(path) - 1):
        for poly_idx, poly in enumerate(polygons):
            if _segment_enters_polygon(path[i], path[i + 1], poly):
                if debug:
                    print(
                        f"    VALIDATION FAIL: segment {i} ({path[i]}) -> ({path[i+1]}) crosses polygon {poly_idx}"
                    )
                return False
    return True


# ---------- Main Entry Point ----------


def _choose_hull_direction(entry_point, prev_vertex, next_vertex, tangent_vec):
    """
    Decide whether to leave the entry vertex towards prev_vertex or next_vertex,
    based on the incoming tangent direction.

    entry_point: (x, y) where we hit the hull (can equal the vertex)
    prev_vertex, next_vertex: neighbor vertices on polygon
    tangent_vec: direction we were travelling before touching the hull (dx, dy)
    """

    tx, ty = tangent_vec

    # Edge from entry to prev / next
    v_prev = (prev_vertex[0] - entry_point[0], prev_vertex[1] - entry_point[1])
    v_next = (next_vertex[0] - entry_point[0], next_vertex[1] - entry_point[1])

    dot_prev = tx * v_prev[0] + ty * v_prev[1]
    dot_next = tx * v_next[0] + ty * v_next[1]

    # Prefer edges that keep us roughly in the same direction (dot >= 0)
    candidates = []
    if dot_prev >= 0:
        candidates.append(("prev", dot_prev))
    if dot_next >= 0:
        candidates.append(("next", dot_next))

    if candidates:
        # Pick the edge with the largest dot product (most aligned with tangent)
        return max(candidates, key=lambda x: x[1])[0]

    # Both are "backwards" (dot < 0); pick the less-bad one
    return "prev" if dot_prev > dot_next else "next"


# wrap_sams for the same SAMs is identical every time; a solve asks for it on
# every path query (matrix pairs, trajectories, detection-radius placement).
_WRAP_CACHE: Dict[Tuple, List[Polygon]] = {}
_WRAP_CACHE_SIZE = 16


def _wrapped_polygons(normalized_sams: List[Dict[str, Any]]) -> List[Polygon]:
    key = tuple(
        (
            round(s["x"], 9),
            round(s["y"], 9),
            round(s["range"], 9),
            tuple(tuple(p) for p in (s.get("avoidance_polygon") or [])),
        )
        for s in normalized_sams
    )
    polys = _WRAP_CACHE.get(key)
    if polys is None:
        polys, _ = wrap_sams(normalized_sams, min_seg=0.3)
        if len(_WRAP_CACHE) >= _WRAP_CACHE_SIZE:
            _WRAP_CACHE.pop(next(iter(_WRAP_CACHE)))
        _WRAP_CACHE[key] = polys
    return polys


def plan_path(
    start: Tuple[float, float],
    goal: Tuple[float, float],
    sams: Optional[List[Dict[str, Any]]] = None,
    debug: bool = False,
    polygons: Optional[List[Polygon]] = None,
) -> Tuple[List[Tuple[float, float]], float, str]:
    """
    Plan a path from start to goal avoiding SAM zones.

    Uses tangent-arc-tangent algorithm:
    1. Convert SAMs to convex polygons via wrap_sams (unless polygons provided)
    2. Check if direct path is clear
    3. If blocked, navigate around polygons using tangent-arc-tangent
    4. Try both directions, return shorter valid path

    Args:
        start: (x, y) starting position
        goal: (x, y) goal position
        sams: List of SAM dicts with 'pos'/'x','y' and 'range'/'radius'
        debug: Enable debug output
        polygons: Optional pre-wrapped polygons (list of vertex lists).
                  If provided, sams parameter is ignored and these polygons
                  are used directly for path planning.

    Returns:
        (path, distance, method) where:
        - path: List of (x, y) waypoints from start to goal
        - distance: Total path length
        - method: Description of planning method used
    """
    start_pt: Point = (float(start[0]), float(start[1]))
    goal_pt: Point = (float(goal[0]), float(goal[1]))
    normalized_sams: List[Dict[str, Any]] = []

    # If pre-wrapped polygons provided, use them directly
    if polygons is not None:
        if not polygons:
            dist = _distance(start_pt, goal_pt)
            return [start_pt, goal_pt], dist, "direct (no polygons)"
        if _is_point_on_any_polygon_boundary(start_pt, polygons):
            start_pt = _snap_boundary_point_to_polygon_vertex(start_pt, polygons)
        if _is_point_on_any_polygon_boundary(goal_pt, polygons):
            goal_pt = _snap_boundary_point_to_polygon_vertex(goal_pt, polygons)
        # Skip SAM processing, use polygons directly (handled below)
    else:
        # No SAMs = direct path
        if not sams:
            dist = _distance(start_pt, goal_pt)
            return [start_pt, goal_pt], dist, "direct (no SAMs)"

        # Normalize SAM format for wrap_sams (needs 'x', 'y', 'range')
        for sam in sams:
            pos = sam.get("pos") or sam.get("position")
            if pos is None and "x" in sam and "y" in sam:
                pos = [sam["x"], sam["y"]]
            if pos is None:
                continue

            normalized_sams.append(
                {
                    "x": (
                        float(pos[0]) if isinstance(pos, (list, tuple)) else float(pos)
                    ),
                    "y": (
                        float(pos[1]) if isinstance(pos, (list, tuple)) else float(pos)
                    ),
                    "range": float(sam.get("range", sam.get("radius", 15))),
                    "avoidance_polygon": sam.get("avoidance_polygon"),
                }
            )

        if not normalized_sams:
            dist = _distance(start_pt, goal_pt)
            return [start_pt, goal_pt], dist, "direct (no valid SAMs)"

        # Convert SAMs to convex polygons (min_seg=0.3 for smooth circles on small SAMs)
        polygons = _wrapped_polygons(normalized_sams)

        if _is_point_on_sam_circle_boundary(start_pt, normalized_sams):
            start_pt = _snap_boundary_point_to_polygon_vertex(start_pt, polygons)
        if _is_point_on_sam_circle_boundary(goal_pt, normalized_sams):
            goal_pt = _snap_boundary_point_to_polygon_vertex(goal_pt, polygons)

    if not polygons:
        dist = _distance(start_pt, goal_pt)
        return [start_pt, goal_pt], dist, "direct (no polygons)"

    if debug:
        print(f"Planning path: {start_pt} -> {goal_pt}")
        print(f"  {len(polygons)} polygon(s) from {len(normalized_sams)} SAM(s)")  # type: ignore[has-type]

    # A start/goal on (or a hair inside) a polygon edge — e.g. a cut on a path that
    # skims the SAM — is planned from the nearest vertex, then joined back to the
    # real point with a short hop along that edge.
    snapped_start = _snap_near_boundary_point(start_pt, polygons)
    snapped_goal = _snap_near_boundary_point(goal_pt, polygons)
    if snapped_start != start_pt or snapped_goal != goal_pt:
        path, dist, method = plan_path(
            snapped_start, snapped_goal, polygons=polygons, debug=debug
        )
        if not path:
            return path, dist, method
        if snapped_start != start_pt:
            path = [start_pt] + list(path)
            dist += _distance(start_pt, snapped_start)
        if snapped_goal != goal_pt:
            path = list(path) + [goal_pt]
            dist += _distance(snapped_goal, goal_pt)
        return path, dist, method + " (edge point snapped to vertex)"

    # Check if start or goal is inside any polygon
    for i, poly in enumerate(polygons):
        if _point_in_polygon(start_pt, poly) and not _is_point_on_polygon_boundary(
            start_pt, poly
        ):
            if debug:
                print(f"  START is inside polygon {i}")
            return [], float("inf"), f"INVALID: start inside polygon {i}"
        if _point_in_polygon(goal_pt, poly) and not _is_point_on_polygon_boundary(
            goal_pt, poly
        ):
            if debug:
                print(f"  GOAL is inside polygon {i}")
            return [], float("inf"), f"INVALID: goal inside polygon {i}"

    # Check if direct path is clear
    blocking_idx = _find_blocking_polygon(start_pt, goal_pt, polygons)

    if blocking_idx is None:
        # Direct path is clear
        dist = _distance(start_pt, goal_pt)
        if debug:
            print(f"  Direct path clear, distance={dist:.2f}")
        return [start_pt, goal_pt], dist, "direct"

    if debug:
        print(f"  Direct path blocked by polygon {blocking_idx}")

    # Single polygon case - simpler algorithm
    if len(polygons) == 1:
        path, dist, method = _navigate_around_single_polygon(
            start_pt, goal_pt, polygons[0], debug=debug
        )
    else:
        # Multiple polygons - use iterative navigation
        path, dist, method = _navigate_multi_polygon(
            start_pt, goal_pt, polygons, debug=debug
        )

    # Validate the path doesn't cross any polygon
    if path and not _validate_path(path, polygons, debug=debug):
        if debug:
            print("  Path validation FAILED - segments cross polygons")
        return [], float("inf"), "INVALID: path crosses polygon"

    if debug:
        print(f"  Final path: {len(path)} points, distance={dist:.2f}, method={method}")

    return path, dist, method
