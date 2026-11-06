"""
ISRTrajectoryPlanner (polygon-based)

Generates drone trajectories that avoid SAM zones represented as polygons.

- Obstacles are polygons: each polygon is a list of [x, y] vertices.
- Uses boundary_navigation.plan_path() which guarantees:
    * Paths NEVER cross any polygon edges.
    * Will return [] and inf distance if no valid path exists.
"""

from __future__ import annotations

import math
import os
import sys
from typing import Dict, List, Optional, Tuple

# Ensure project root is on path so we can import path_planning_core
project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# Add /app for Docker deployment
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from path_planning_core.boundary_navigation import plan_path as boundary_plan_path
from path_planning_core.latlng_utils import plane_dist, polyline_km

Point = Tuple[float, float]
Polygon = List[Point]


class ISRTrajectoryPlanner:
    """
    Plan trajectories for a drone route, avoiding polygon obstacles.

    Initialize with a list of polygons (already wrapped SAMs), e.g.:

        polygons = [
            [[x1, y1], [x2, y2], ..., [xk, yk]],   # polygon 0
            ...
        ]

        planner = ISRTrajectoryPlanner(polygons)
    """

    def __init__(self, polygons: List[List[List[float]]]):
        # Normalize polygons to (x, y) tuples
        self.polygons: List[Polygon] = []
        for poly in polygons or []:
            if not poly:
                continue
            clean_poly: Polygon = []
            for v in poly:
                clean_poly.append((float(v[0]), float(v[1])))
            self.polygons.append(clean_poly)

        print(
            f"🎯 ISRTrajectoryPlanner (POLYGON) initialized with {len(self.polygons)} SAM polygon(s)",
            flush=True,
        )
        total_vertices = sum(len(p) for p in self.polygons)
        print(f"   Total vertices across polygons: {total_vertices}", flush=True)

    # ---------------------------------------------
    # Single segment planning
    # ---------------------------------------------
    def plan_segment(
        self, start: Point, end: Point, debug: bool = False
    ) -> Tuple[List[List[float]], float]:
        """
        Plan a single segment from start to end, avoiding polygons.

        Returns:
          - path_points: list of [x, y]
          - distance: total path length (inf if no valid path)
        """
        if not self.polygons:
            # No obstacles → straight line
            d = plane_dist(start, end)
            if debug:
                print(
                    f"🟢 [SEGMENT] No polygons, direct {start} → {end}, d={d:.2f}",
                    flush=True,
                )
            return [list(start), list(end)], d

        path, d, method = boundary_plan_path(
            list(start), list(end), polygons=self.polygons, debug=debug
        )

        if method.startswith("INVALID"):
            if debug:
                print(
                    f"❌ [SEGMENT] NO VALID PATH {start} → {end}: {method}", flush=True
                )
            return [], float("inf")

        if debug:
            print(
                f"✅ [SEGMENT] {start} → {end} via {method}, {len(path)} waypoints, d={d:.2f}",
                flush=True,
            )

        return path, polyline_km(path)

    # ---------------------------------------------
    # Full route trajectory
    # ---------------------------------------------
    def generate_trajectory(
        self,
        route: List[str],
        waypoint_positions: Dict[str, List[float]],
        drone_id: Optional[str] = None,
        debug: bool = True,
    ) -> List[List[float]]:
        """
        Generate a full SAM-avoiding trajectory for a route.

        Args:
          route: list of waypoint IDs, e.g. ["A1", "T3", "T7", "A1"]
          waypoint_positions:
              { "A1": [x, y], "T3": [x, y], ... }
          drone_id: used only for debug printing

        Returns:
          List of [x, y] forming the full spline-like path.
          Empty list if any segment is impossible.
        """
        if len(route) < 2:
            return []

        label = f"D{drone_id}" if drone_id is not None else "D?"
        if debug:
            print(
                f"\n📍 [{label}] Generating trajectory for route:",
                " → ".join(route),
                flush=True,
            )
            for wp_id in route:
                pos = waypoint_positions.get(wp_id)
                if pos:
                    print(f"   {wp_id}: ({pos[0]:.1f}, {pos[1]:.1f})", flush=True)
                else:
                    print(f"   {wp_id}: MISSING POSITION!", flush=True)

        full_path: List[List[float]] = []

        for i in range(len(route) - 1):
            from_id = route[i]
            to_id = route[i + 1]

            if from_id not in waypoint_positions or to_id not in waypoint_positions:
                if debug:
                    print(
                        f"⚠️ [{label}] Missing waypoint position for {from_id} or {to_id}, aborting trajectory.",
                        flush=True,
                    )
                return []

            start = (
                float(waypoint_positions[from_id][0]),
                float(waypoint_positions[from_id][1]),
            )
            end = (
                float(waypoint_positions[to_id][0]),
                float(waypoint_positions[to_id][1]),
            )

            if debug:
                print(
                    f"   📐 [{label}] Segment {from_id}→{to_id}: "
                    f"({start[0]:.1f},{start[1]:.1f})→({end[0]:.1f},{end[1]:.1f})",
                    flush=True,
                )

            segment_path, d = self.plan_segment(start, end, debug=debug)

            if not segment_path or not math.isfinite(d):
                if debug:
                    print(
                        f"❌ [{label}] Segment {from_id}→{to_id} has NO VALID PATH. "
                        f"Aborting trajectory for this route.",
                        flush=True,
                    )
                return []

            if debug:
                print(
                    f"   ✅ [{label}] Segment {from_id}→{to_id}: "
                    f"{len(segment_path)} waypoints, d={d:.2f}",
                    flush=True,
                )

            # Concatenate segment path into full trajectory.
            if i == 0:
                # First segment keeps its start
                full_path.extend(segment_path)
            else:
                # Subsequent segments: skip first point to avoid duplicates
                if len(segment_path) > 1:
                    full_path.extend(segment_path[1:])
                else:
                    full_path.extend(segment_path)

        if debug:
            print(
                f"🎯 [{label}] Final trajectory has {len(full_path)} waypoints.",
                flush=True,
            )

        return full_path
