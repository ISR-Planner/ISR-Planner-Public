"""Legacy post-insert optimizer and coverage helpers."""

import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from path_planning_core.latlng_utils import plane_dist
from server.solver.altitude import matrix_for_drone
from server.solver.optimizers.common import (
    _constraints_for_route,
    _ordering_legal,
    compute_route_loiter_fuel,
    parse_priority_constraint,
    target_allowed_for_drone,
)

# Add paths for SAM navigation
root_path = Path(__file__).resolve().parents[3]
if str(root_path) not in sys.path:
    sys.path.insert(0, str(root_path))
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

boundary_plan_path: Any = None
wrap_sams: Any = None

try:
    from path_planning_core.boundary_navigation import plan_path as _boundary_plan_path
    from path_planning_core.sam_wrapping import wrap_sams as _wrap_sams

    boundary_plan_path = _boundary_plan_path
    wrap_sams = _wrap_sams

    HAS_SAM_NAVIGATION = True
except ImportError:
    HAS_SAM_NAVIGATION = False


class PostOptimizer:
    """
    Post-optimization for mission solutions.

    Handles unvisited targets by finding drones with spare fuel capacity
    and inserting targets into their routes optimally.
    """

    def __init__(self):
        self._distance_matrix: Optional[Dict[str, Any]] = None
        self._env: Optional[Dict[str, Any]] = None
        self._waypoint_positions: Optional[Dict[str, Tuple[float, float]]] = None
        self._sams: Optional[List[Dict[str, Any]]] = None
        self._wrapped_polygons: Optional[List[Any]] = None
        self._drone_configs: Dict[str, Dict[str, Any]] = {}
        self._target_by_id: Dict[str, Dict[str, Any]] = {}

    def set_distance_matrix(self, matrix_data: Dict[str, Any]):
        """Set the pre-calculated distance matrix for optimization decisions."""
        self._distance_matrix = matrix_data

    def set_environment(self, env: Dict[str, Any]):
        """
        Set environment data for SAM-aware distance fallback calculations.

        Args:
            env: Environment dict with airports, targets, and sams
        """
        self._env = env

        # Build waypoint position lookup
        waypoint_positions: Dict[str, Tuple[float, float]] = {}
        airports = env.get("airports", [])
        targets = env.get("targets", [])

        print(
            f"   [set_environment] Building waypoint positions from {len(airports)} airports and {len(targets)} targets",  # noqa: E501
            flush=True,
        )

        for a in airports:
            waypoint_positions[str(a["id"])] = (float(a["x"]), float(a["y"]))
        for t in targets:
            waypoint_positions[str(t["id"])] = (float(t["x"]), float(t["y"]))
        self._waypoint_positions = waypoint_positions

        print(
            f"   [set_environment] Waypoint positions: {list(waypoint_positions.keys())}",
            flush=True,
        )

        # Store SAM data
        self._sams = env.get("sams", [])

        # Generate wrapped polygons for SAM-aware distance calculations
        # boundary_plan_path expects polygon format: List[List[List[float]]]
        if wrap_sams and self._sams:
            sams_for_wrapping = []
            for sam in self._sams:
                pos = sam.get("pos") or sam.get("position")
                if pos:
                    sams_for_wrapping.append(
                        {
                            "x": pos[0] if isinstance(pos, (list, tuple)) else pos,
                            "y": pos[1] if isinstance(pos, (list, tuple)) else pos,
                            "radius": float(sam.get("range", sam.get("radius", 15))),
                        }
                    )
            if sams_for_wrapping:
                wrapped_polygon_arrays, _ = wrap_sams(sams_for_wrapping)
                # Convert numpy arrays to lists of [x, y] points
                self._wrapped_polygons = []
                for poly in wrapped_polygon_arrays:
                    if hasattr(poly, "tolist"):
                        self._wrapped_polygons.append(poly.tolist())
                    else:
                        self._wrapped_polygons.append(list(poly))
        else:
            self._wrapped_polygons = None

    def _get_refuel_leg_info(
        self,
        route_data: Dict[str, Any],
        drone_cfg: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Calculate per-leg fuel info for a refueling drone.

        For refueling drones, the route has two legs:
        - Leg 1: start -> refuel_airport (with fresh fuel budget)
        - Leg 2: refuel_airport -> end (with fresh fuel budget after refuel)

        Returns:
            For refueling drones: {
                "is_refuel": True,
                "refuel_airport": str,
                "refuel_idx": int,  # index of refuel airport in route
                "leg1_remaining": float,
                "leg2_remaining": float,
                "budget_per_leg": float
            }
            For non-refueling drones: {
                "is_refuel": False,
                "remaining": float,
                "budget": float
            }
        """
        refuel_ap = route_data.get("refuel_airport", "")
        fuel_budget = route_data.get("fuel_budget", 0) or drone_cfg.get(
            "fuel_budget", 0
        )

        if not refuel_ap or refuel_ap == "":
            # Non-refueling drone: simple remaining calculation
            distance_used = route_data.get("distance", 0)
            return {
                "is_refuel": False,
                "remaining": fuel_budget - distance_used,
                "budget": fuel_budget,
            }

        # Refueling drone: per-leg remaining calculation
        leg1_dist = route_data.get("leg1_distance", 0)
        leg2_dist = route_data.get("leg2_distance", 0)

        # Find refuel airport position in route
        route = route_data.get("route", [])
        refuel_idx = -1
        for i, wp in enumerate(route):
            # Refuel airport appears in the middle (not at start or end)
            if wp == refuel_ap and i > 0 and i < len(route) - 1:
                refuel_idx = i
                break

        return {
            "is_refuel": True,
            "refuel_airport": refuel_ap,
            "refuel_idx": refuel_idx,
            "leg1_remaining": fuel_budget - leg1_dist,
            "leg2_remaining": fuel_budget - leg2_dist,
            "budget_per_leg": fuel_budget,
        }

    def _is_inside_polygon(self, waypoint_id: str) -> bool:
        """
        Check if a waypoint is inside any SAM polygon (no-fly zone).

        Args:
            waypoint_id: Waypoint identifier (e.g., "T15", "A1")

        Returns:
            True if waypoint is inside any SAM polygon, False otherwise
        """
        if not self._waypoint_positions or not self._sams:
            return False

        if waypoint_id not in self._waypoint_positions:
            return False

        wx, wy = self._waypoint_positions[waypoint_id]

        # Check if waypoint is inside any SAM's circular range (polygon)
        for sam in self._sams:
            pos = sam.get("pos") or sam.get("position")
            if not pos:
                continue

            sam_x = pos[0] if isinstance(pos, (list, tuple)) else pos
            sam_y = pos[1] if isinstance(pos, (list, tuple)) else pos
            sam_range = float(sam.get("range", sam.get("radius", 15)))

            # Calculate distance from waypoint to SAM center
            distance = plane_dist((wx, wy), (sam_x, sam_y))  # km on the globe

            # If waypoint is inside this SAM's range polygon, it's invalid
            if distance < sam_range:
                return True

        return False

    def _unreachable_for_drone(self, tid: str, drone_id: str) -> bool:
        """tid lies inside a SAM at this drone's cruise altitude."""
        dist_data = matrix_for_drone(self._distance_matrix, drone_id)
        if dist_data is self._distance_matrix:
            return self._is_inside_polygon(tid)
        return tid in {str(t) for t in (dist_data or {}).get("excluded_targets", [])}

    def _find_closest_trajectory_segment(
        self,
        target_pos: Tuple[float, float],
        trajectory: List[Tuple[float, float]],
        route: List[str],
        waypoint_positions: Dict[str, Tuple[float, float]],
    ) -> Tuple[int, bool, int]:
        """
        Find the closest segment in a trajectory to a target point.

        Args:
            target_pos: (x, y) position of the target to insert
            trajectory: List of (x, y) vertices in the SAM-avoiding trajectory
            route: Route sequence (e.g., ['A1', 'T9', 'T17', 'A2'])
            waypoint_positions: Mapping of waypoint IDs to (x, y) positions

        Returns:
            Tuple of (route_index, has_perpendicular, closest_vertex_offset) where:
            - route_index: Index in route where target should be inserted
            - has_perpendicular: True if perpendicular exists to closest segment
            - closest_vertex_offset: If no perpendicular, which vertex is closer (0=first, 1=second)
        """
        if not trajectory or len(trajectory) < 2:
            return (1, False, 0)

        tx, ty = target_pos
        min_dist = float("inf")
        best_route_idx = None
        best_has_perp = False
        best_closest_vertex = 0

        # Build mapping from trajectory indices to route waypoint positions
        traj_to_route_wp = {}  # Maps trajectory index to route waypoint index
        traj_idx = 0
        for route_wp_idx, wp_id in enumerate(route):
            wp_pos = waypoint_positions.get(str(wp_id))
            if wp_pos:
                # Find matching trajectory vertex
                for i in range(traj_idx, len(trajectory)):
                    if (
                        abs(trajectory[i][0] - wp_pos[0]) < 0.001
                        and abs(trajectory[i][1] - wp_pos[1]) < 0.001
                    ):
                        traj_to_route_wp[i] = route_wp_idx
                        traj_idx = i + 1
                        break

        # Iterate through trajectory segments
        for i in range(len(trajectory) - 1):
            p1 = trajectory[i]
            p2 = trajectory[i + 1]

            # Calculate point-to-segment distance
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            segment_length_sq = dx * dx + dy * dy

            if segment_length_sq == 0:
                # Degenerate segment
                dist = math.sqrt((tx - p1[0]) ** 2 + (ty - p1[1]) ** 2)
                has_perp = False
                t = 0.0
            else:
                # Calculate projection parameter t
                t = ((tx - p1[0]) * dx + (ty - p1[1]) * dy) / segment_length_sq
                t_clamped = max(0.0, min(1.0, t))

                # Find closest point on segment
                closest_x = p1[0] + t_clamped * dx
                closest_y = p1[1] + t_clamped * dy

                # Distance to closest point
                dist = math.sqrt((tx - closest_x) ** 2 + (ty - closest_y) ** 2)

                # Check if perpendicular exists (t strictly between 0 and 1)
                has_perp = 0 < t < 1

            # Update if this is the closest segment
            if dist < min_dist:
                min_dist = dist

                # Find which route waypoints this trajectory segment is between
                # Look for the route waypoint at or after trajectory vertex i
                route_wp_start = None
                for traj_v_idx in range(i, -1, -1):
                    if traj_v_idx in traj_to_route_wp:
                        route_wp_start = traj_to_route_wp[traj_v_idx]
                        break

                if route_wp_start is None:
                    route_wp_start = 0

                # Insertion position is after route_wp_start
                best_route_idx = route_wp_start + 1
                best_has_perp = has_perp

                # If no perpendicular, determine which vertex is closer
                if not has_perp:
                    dist_to_p1 = math.sqrt((tx - p1[0]) ** 2 + (ty - p1[1]) ** 2)
                    dist_to_p2 = math.sqrt((tx - p2[0]) ** 2 + (ty - p2[1]) ** 2)
                    best_closest_vertex = 0 if dist_to_p1 < dist_to_p2 else 1

        if best_route_idx is None:
            best_route_idx = 1

        return (best_route_idx, best_has_perp, best_closest_vertex)

    def optimize(
        self,
        solution: Dict[str, Any],
        env: Dict[str, Any],
        drone_configs: Dict[str, Dict[str, Any]],
        priority_constraints: Optional[str] = None,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Post-optimize a mission solution to include unvisited targets.

        Args:
            solution: Current solution with routes for each drone
            env: Environment dict with targets, airports, sams
            drone_configs: Drone configurations with fuel budgets
            priority_constraints: Optional priority constraints string
                                  e.g., "D1,D2: priority>=6; D3,D4: priority<6"

        Returns:
            Optimized solution with updated routes
        """
        print("\n [OPTIMIZER.optimize] Starting optimization", flush=True)
        print(
            f"   _waypoint_positions has {len(self._waypoint_positions or {})} entries",
            flush=True,
        )
        print(
            f"   _distance_matrix is {'SET' if self._distance_matrix else 'NOT SET'}",
            flush=True,
        )

        # Parse priority constraints into per-drone filter functions
        priority_filters = (
            parse_priority_constraint(priority_constraints)
            if priority_constraints
            else {}
        )
        targets = env.get("targets", [])
        airports = env.get("airports", [])
        if self._waypoint_positions is None:
            self.set_environment(env)
        waypoint_positions = self._waypoint_positions or {}

        print(
            f"   env has {len(targets)} targets, {len(airports)} airports", flush=True
        )

        # Get all target IDs
        all_target_ids = {str(t["id"]) for t in targets}

        # Find visited targets from all routes
        visited_targets: Set[str] = set()
        for did, route_data in solution.get("routes", {}).items():
            route = route_data.get("route", [])
            for wp_id in route:
                if str(wp_id).startswith("T") and not str(wp_id).startswith("TK"):
                    visited_targets.add(str(wp_id))

        # Identify unvisited targets
        unvisited = all_target_ids - visited_targets

        print(f"   all_target_ids = {sorted(all_target_ids)}", flush=True)
        print(f"   visited_targets = {sorted(visited_targets)}", flush=True)
        print(f"   unvisited = {sorted(unvisited)}", flush=True)

        if not unvisited:
            # All targets visited, no optimization needed
            print("   EARLY EARLY RETURN: All targets already visited!", flush=True)
            return solution

        # Filter out targets inside SAM polygons (no-fly zones)
        # These targets cannot be visited safely and will cause trajectory failures
        targets_inside_polygons = set()
        by_altitude = (self._distance_matrix or {}).get("by_altitude")
        for tid in unvisited:
            # With drones at several altitudes, skip only targets no drone can reach.
            if self._is_inside_polygon(tid) and (
                not by_altitude
                or all(self._unreachable_for_drone(tid, d) for d in drone_configs)
            ):
                targets_inside_polygons.add(tid)
                print(
                    f"WARN Insert Missed: Skipping {tid} - inside SAM polygon (no-fly zone)",
                    flush=True,
                )

        unvisited = unvisited - targets_inside_polygons

        if not unvisited:
            # No valid unvisited targets to insert
            return solution

        # Build target lookup
        target_by_id = {str(t["id"]): t for t in targets}
        self._drone_configs = drone_configs
        self._target_by_id = target_by_id

        # Sort unvisited targets by priority (highest first)
        unvisited_sorted = sorted(
            unvisited,
            key=lambda tid: int(target_by_id[tid].get("priority", 5)),
            reverse=True,
        )

        # Calculate remaining fuel for each drone (per-leg for refueling drones)
        # Include ALL drones from drone_configs, not just those with existing routes
        drone_fuel_info: Dict[str, Dict[str, Any]] = {}
        for did, cfg in drone_configs.items():
            route_data = solution.get("routes", {}).get(did, {})
            if route_data:
                # Drone has an existing route - calculate fuel info (per-leg if refueling)
                drone_fuel_info[did] = self._get_refuel_leg_info(route_data, cfg)
            else:
                # Drone has no route yet - full fuel budget available
                fuel_budget = cfg.get("fuel_budget", 0)
                drone_fuel_info[did] = {
                    "is_refuel": False,
                    "remaining": fuel_budget,
                    "budget": fuel_budget,
                }

        # DEBUG: Log state before insertion attempts
        print("\n INSERT MISSED OPTIMIZER DEBUG:", flush=True)
        print(f"   All targets: {sorted(all_target_ids)}", flush=True)
        print(f"   Visited: {sorted(visited_targets)}", flush=True)
        print(f"   Unvisited (sorted by priority): {unvisited_sorted}", flush=True)
        print(f"   Drone configs received: {list(drone_configs.keys())}", flush=True)
        for did in sorted(drone_configs.keys()):
            cfg = drone_configs[did]
            fuel_info = drone_fuel_info.get(did, {})
            if fuel_info.get("is_refuel"):
                fuel_str = f"L1={fuel_info.get('leg1_remaining', 0):.1f}, L2={fuel_info.get('leg2_remaining', 0):.1f}"
            else:
                fuel_str = f"{fuel_info.get('remaining', 0):.1f}"
            print(
                f"   D{did}: enabled={cfg.get('enabled')}, fuel=[{fuel_str}], refuel={fuel_info.get('is_refuel', False)}",
                flush=True,
            )

        # Try to assign each unvisited target
        # GOAL: Maximize total points by inserting ALL targets that fit within fuel budget
        # STRATEGY:
        #   1. Process targets by priority (highest first) - maximizes points
        #   2. For each target, find ALL drones that can take it (capability + fuel check)
        #   3. Among viable drones, pick the one with LOWEST insertion cost - minimizes fuel
        optimized_routes = dict(solution.get("routes", {}))

        for tid in unvisited_sorted:
            target = target_by_id[tid]
            priority = int(target.get("priority", 5))
            target_type = target.get("type", "a")

            # Find ALL viable (drone, position, cost) options for this target
            viable_options = []  # List of (drone_id, insertion_index, insertion_cost)
            skip_reasons = {}  # Track why each drone can't take this target

            for did, cfg in drone_configs.items():
                # Check ALL constraints: enabled, target type, AND priority
                if not target_allowed_for_drone(target, did, cfg, priority_filters):
                    skip_reasons[did] = "not allowed (type/priority/disabled)"
                    continue
                if by_altitude and self._unreachable_for_drone(tid, did):
                    skip_reasons[did] = "inside a SAM at this drone's altitude"
                    continue

                # Get fuel info for this drone
                fuel_info = drone_fuel_info.get(did, {})

                # Quick check: if no fuel at all, skip
                if fuel_info.get("is_refuel"):
                    max_remaining = max(
                        fuel_info.get("leg1_remaining", 0),
                        fuel_info.get("leg2_remaining", 0),
                    )
                    if max_remaining <= 0:
                        skip_reasons[did] = (
                            f"no fuel in either leg (L1={fuel_info.get('leg1_remaining', 0):.1f}, L2={fuel_info.get('leg2_remaining', 0):.1f})"
                        )
                        continue
                else:
                    if fuel_info.get("remaining", 0) <= 0:
                        skip_reasons[did] = (
                            f"no fuel remaining ({fuel_info.get('remaining', 0):.1f})"
                        )
                        continue

                # Find best insertion point in this drone's route
                route_data = optimized_routes.get(did, {})
                route = route_data.get("route", [])

                # Handle drones with no route (still at home airport)
                if not route:
                    # Get start and end airports from drone config
                    start_airport = cfg.get("start_airport", f"A{did}")
                    end_airport = cfg.get("end_airport", start_airport)

                    # Calculate insertion cost: start -> target -> end
                    d_start_to_target = self._get_distance(start_airport, tid, did)
                    d_target_to_end = self._get_distance(tid, end_airport, did)

                    if math.isinf(d_start_to_target) or math.isinf(d_target_to_end):
                        skip_reasons[did] = (
                            f"no path from {start_airport} to {tid} or {tid} to {end_airport}"
                        )
                        continue

                    # Total route distance for new route
                    insertion_cost = d_start_to_target + d_target_to_end

                    # Check if drone can afford this route (per-leg for refuel drones)
                    if fuel_info.get("is_refuel"):
                        # For new route with refuel drone, check leg1 budget
                        # (new route would start in leg1)
                        available_fuel = fuel_info.get("leg1_remaining", 0)
                        if insertion_cost > available_fuel:
                            skip_reasons[did] = (
                                f"new route cost {insertion_cost:.1f} > L1 budget {available_fuel:.1f}"
                            )
                            continue
                    else:
                        available_fuel = fuel_info.get("remaining", 0)
                        if insertion_cost > available_fuel:
                            skip_reasons[did] = (
                                f"new route cost {insertion_cost:.1f} > fuel budget {available_fuel:.1f}"
                            )
                            continue

                    # Mark with special insertion index -1 to indicate "create new route"
                    # Store (start_airport, end_airport, insertion_leg) for later route creation
                    # For refuel drones creating new route, target goes in L1
                    new_route_leg = "L1" if fuel_info.get("is_refuel") else None
                    viable_options.append(
                        (
                            did,
                            -1,
                            insertion_cost,
                            start_airport,
                            end_airport,
                            new_route_leg,
                        )
                    )
                    print(
                        f"      NEW D{did}: New route {start_airport}->{tid}->{end_airport}, cost={insertion_cost:.1f}",
                        flush=True,
                    )
                    continue

                # Get frozen segments for this drone (indices where route[i]->route[i+1] is frozen)
                frozen_segments = set(route_data.get("frozen_segments", []))

                # Use trajectory-based insertion logic
                # Find closest segment in the drone's trajectory
                trajectory = route_data.get("trajectory", [])
                target_pos = waypoint_positions.get(tid)

                if not trajectory or not target_pos:
                    skip_reasons[did] = (
                        f"no trajectory ({len(trajectory) if trajectory else 0} pts) or target_pos ({target_pos})"
                    )
                    continue

                # Convert trajectory to list of tuples if needed
                if trajectory and not isinstance(trajectory[0], tuple):
                    trajectory = [(float(p[0]), float(p[1])) for p in trajectory]

                # Find closest trajectory segment and insertion position
                route_idx, has_perp, closest_vertex = (
                    self._find_closest_trajectory_segment(
                        target_pos, trajectory, route, waypoint_positions
                    )
                )

                # Adjust insertion index if no perpendicular exists
                if not has_perp and closest_vertex == 1:
                    # Closer to second vertex, insert after it
                    route_idx = min(route_idx + 1, len(route))

                # Check if this position is frozen
                if route_idx > 0 and (route_idx - 1) in frozen_segments:
                    skip_reasons[did] = f"frozen segment at position {route_idx}"
                    continue

                # Check if we're trying to insert after the end airport
                if route_idx >= len(route):
                    skip_reasons[did] = (
                        f"route_idx {route_idx} >= route length {len(route)}"
                    )
                    continue

                # Calculate insertion cost using proper SAM-avoiding distances
                if route_idx == 0:
                    prev_wp = None
                    next_wp = route[0] if route else None
                elif route_idx >= len(route):
                    prev_wp = route[-1] if route else None
                    next_wp = None
                else:
                    prev_wp = route[route_idx - 1]
                    next_wp = route[route_idx]

                # Calculate cost as difference in distances
                if prev_wp and next_wp:
                    orig_dist = self._get_distance(prev_wp, next_wp, did)
                    new_dist = self._get_distance(
                        prev_wp, tid, did
                    ) + self._get_distance(tid, next_wp, did)
                    insertion_cost = new_dist - orig_dist
                elif prev_wp:
                    insertion_cost = self._get_distance(prev_wp, tid, did)
                elif next_wp:
                    insertion_cost = self._get_distance(tid, next_wp, did)
                else:
                    continue

                # Check if we have enough fuel (per-leg for refuel drones)
                if fuel_info.get("is_refuel"):
                    # Determine which leg this insertion falls into
                    refuel_idx = fuel_info.get("refuel_idx", -1)
                    if refuel_idx < 0:
                        skip_reasons[did] = (
                            "malformed refuel route (no refuel airport found)"
                        )
                        continue

                    if route_idx <= refuel_idx:
                        # Inserting into Leg 1
                        leg_remaining = fuel_info.get("leg1_remaining", 0)
                        leg_name = "L1"
                    else:
                        # Inserting into Leg 2
                        leg_remaining = fuel_info.get("leg2_remaining", 0)
                        leg_name = "L2"

                    if insertion_cost > leg_remaining:
                        skip_reasons[did] = (
                            f"{leg_name} insertion_cost {insertion_cost:.1f} > remaining {leg_remaining:.1f}"
                        )
                        continue

                    # Store which leg this insertion is in for fuel tracking
                    insertion_leg = leg_name
                else:
                    remaining_fuel = fuel_info.get("remaining", 0)
                    if insertion_cost > remaining_fuel:
                        skip_reasons[did] = (
                            f"insertion_cost {insertion_cost:.1f} > remaining_fuel {remaining_fuel:.1f}"
                        )
                        continue
                    insertion_leg = None  # Not a refuel drone

                # Add this as a viable option (route_idx >= 0 means insert into existing route)
                # Include insertion_leg for fuel tracking after insertion
                # Format: (did, route_idx, cost, start_airport, end_airport, insertion_leg)
                viable_options.append(
                    (did, route_idx, insertion_cost, None, None, insertion_leg)
                )

            # If no drone can take this target, log why
            if not viable_options:
                print(
                    f"   NO {tid} (type={target_type}, priority={priority}): NO VIABLE DRONES",
                    flush=True,
                )
                for did, reason in sorted(skip_reasons.items()):
                    print(f"      D{did}: {reason}", flush=True)
                continue
            else:
                print(
                    f"   OK {tid} (type={target_type}, priority={priority}): {len(viable_options)} viable options",
                    flush=True,
                )

            # CONSTRAINT GATE: drop viable options whose resulting route would
            # violate ordering for that drone (e.g. a free target inserted after
            # the pinned `last`, or splitting an adjacent pair). New-route options
            # (route_idx == -1) are [start, tid, end] and always legal for the
            # single inserted target.
            if constraints:
                gated = []
                for opt in viable_options:
                    _did, _idx = opt[0], opt[1]
                    _c = _constraints_for_route(constraints, _did)
                    if _c is None or _idx < 0:
                        gated.append(opt)
                        continue
                    _route = list(optimized_routes.get(_did, {}).get("route", []))
                    _trial = _route[:_idx] + [tid] + _route[_idx:]
                    if _ordering_legal(_trial, _c):
                        gated.append(opt)
                if not gated:
                    print(
                        f"   BLOCKED {tid}: all {len(viable_options)} options rejected by ordering constraints",
                        flush=True,
                    )
                    continue
                viable_options = gated

            # Among viable options, pick the drone with LOWEST insertion cost
            # This minimizes fuel usage when multiple drones can service the target
            # Tuple format: (did, route_idx, cost, start_airport, end_airport, insertion_leg)
            best_option = min(viable_options, key=lambda x: x[2])
            best_drone = best_option[0]
            best_insertion = best_option[1]
            best_cost = best_option[2]
            best_leg = best_option[5]  # Which leg this insertion is in (L1/L2/None)

            # Apply the insertion
            if best_insertion == -1:
                # New route case: create route [start_airport, target, end_airport]
                start_airport = best_option[3]
                end_airport = best_option[4]
                route = [start_airport, tid, end_airport]
                print(
                    f"      NEW Creating new route for D{best_drone}: {start_airport}->{tid}->{end_airport}",
                    flush=True,
                )
            else:
                # Existing route case: insert target at specified position
                route = list(optimized_routes[best_drone].get("route", []))
                route.insert(best_insertion, tid)

            # Recalculate route metrics
            new_distance = self._calculate_route_distance(route, best_drone)
            new_points = self._calculate_route_points(route, target_by_id)

            # Get fuel budget - from existing route data or from drone_configs
            existing_route_data = optimized_routes.get(best_drone, {})
            fuel_budget = existing_route_data.get("fuel_budget", 0)
            if not fuel_budget:
                fuel_budget = drone_configs.get(best_drone, {}).get("fuel_budget", 0)

            optimized_routes[best_drone] = {
                "route": route,
                "sequence": ",".join(route),
                "points": new_points,
                "distance": new_distance,
                "fuel_budget": fuel_budget,
            }

            # Update remaining fuel for this drone (per-leg for refuel drones)
            fuel_info = drone_fuel_info[best_drone]
            if best_leg == "L1":
                fuel_info["leg1_remaining"] -= best_cost
                print(
                    f"      FUEL D{best_drone} L1 fuel: {fuel_info['leg1_remaining']:.1f} remaining after insertion",
                    flush=True,
                )
            elif best_leg == "L2":
                fuel_info["leg2_remaining"] -= best_cost
                print(
                    f"      FUEL D{best_drone} L2 fuel: {fuel_info['leg2_remaining']:.1f} remaining after insertion",
                    flush=True,
                )
            else:
                # Non-refuel drone
                fuel_info["remaining"] -= best_cost

        # Rebuild solution
        optimized_solution = {
            "sequences": {
                did: data.get("sequence", "") for did, data in optimized_routes.items()
            },
            "routes": optimized_routes,
        }

        return optimized_solution

    def find_unvisited_targets(
        self, solution: Dict[str, Any], env: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Find all targets that are not in any drone's route.

        Returns:
            List of unvisited target dicts with full details
        """
        targets = env.get("targets", [])
        all_target_ids = {str(t["id"]) for t in targets}

        visited: Set[str] = set()
        for did, route_data in solution.get("routes", {}).items():
            for wp_id in route_data.get("route", []):
                if str(wp_id).startswith("T") and not str(wp_id).startswith("TK"):
                    visited.add(str(wp_id))

        unvisited_ids = all_target_ids - visited
        target_by_id = {str(t["id"]): t for t in targets}

        return [target_by_id[tid] for tid in unvisited_ids if tid in target_by_id]

    def calculate_coverage_stats(
        self, solution: Dict[str, Any], env: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calculate coverage statistics for a solution.

        Returns:
            Dict with coverage metrics
        """
        targets = env.get("targets", [])
        target_by_id = {str(t["id"]): t for t in targets}

        visited: Set[str] = set()
        total_points = 0
        total_distance = 0

        for did, route_data in solution.get("routes", {}).items():
            for wp_id in route_data.get("route", []):
                if str(wp_id).startswith("T") and not str(wp_id).startswith("TK"):
                    visited.add(str(wp_id))
                    target = target_by_id.get(str(wp_id))
                    if target:
                        total_points += int(target.get("priority", 5))
            total_distance += route_data.get("distance", 0)

        max_possible_points = sum(int(t.get("priority", 5)) for t in targets)

        return {
            "targets_visited": len(visited),
            "targets_total": len(targets),
            "coverage_percent": (len(visited) / len(targets) * 100) if targets else 0,
            "points_collected": total_points,
            "points_possible": max_possible_points,
            "points_percent": (
                (total_points / max_possible_points * 100) if max_possible_points else 0
            ),
            "total_distance": total_distance,
            "unvisited_targets": list({str(t["id"]) for t in targets} - visited),
        }

    def _get_distance(
        self, from_id: str, to_id: str, drone_id: Optional[str] = None
    ) -> float:
        """
        Get distance between two waypoints for drone_id (at its cruise altitude).

        First tries distance matrix lookup, then falls back to SAM-aware calculation.
        """
        # Try distance matrix first
        dist_data = matrix_for_drone(self._distance_matrix, drone_id)
        if dist_data:
            labels = dist_data.get("labels", [])
            matrix = dist_data.get("matrix", [])

            try:
                i = labels.index(from_id)
                j = labels.index(to_id)
                return matrix[i][j]
            except (ValueError, IndexError):
                pass

        # Fallback: Calculate SAM-aware distance on-the-fly
        if (
            self._waypoint_positions
            and from_id in self._waypoint_positions
            and to_id in self._waypoint_positions
        ):
            start_pos = self._waypoint_positions[from_id]
            end_pos = self._waypoint_positions[to_id]

            # If we have SAM navigation and polygons, use SAM-aware path
            polygons = self._wrapped_polygons
            if dist_data is not self._distance_matrix:
                polygons = (dist_data or {}).get("wrapped_polygons") or []
            if HAS_SAM_NAVIGATION and boundary_plan_path and polygons:
                try:
                    path, distance, method = boundary_plan_path(
                        start_pos, end_pos, polygons=polygons
                    )
                    if path and distance < float("inf"):
                        print(
                            f"WARN Distance matrix fallback: {from_id}->{to_id} = {distance:.2f} (SAM-aware, {method})",
                            flush=True,
                        )
                        return distance
                except Exception as e:
                    print(
                        f"WARN SAM-aware fallback failed for {from_id}->{to_id}: {e}",
                        flush=True,
                    )

            # Per project policy: NEVER use Euclidean. Always use SAM-aware
            # distance. If we got here, the matrix lookup failed AND the
            # SAM-aware fallback also failed (which means we don't have wrapped
            # polygons or boundary navigation isn't available). Refuse to
            # silently corrupt distances - raise so the bug surfaces.
            raise RuntimeError(
                f"Cannot compute SAM-aware distance for {from_id}->{to_id}: "
                f"matrix lookup failed AND SAM-aware fallback unavailable. "
                f"Refusing to fall back to Euclidean (project policy: "
                f"SAM-aware only). Ensure the distance matrix was built with "
                f"this pair, or provide SAM polygons + boundary_plan_path."
            )

        # Absolute fallback if no position data available
        print(
            f"NO No distance data available for {from_id}->{to_id}, returning large sentinel value",
            flush=True,
        )
        return 99999.0

    def _calculate_route_distance(
        self, route: List[str], drone_id: Optional[str] = None
    ) -> float:
        """Calculate total travel distance of a route using SAM-aware matrix.

        Travel only. For the loiter-inclusive value that goes into route_data
        ["distance"] (so feasibility checks and the UI agree with the animation),
        use _calculate_route_distance_with_loiter.
        """
        if len(route) < 2:
            return 0.0

        total = 0.0
        for i in range(len(route) - 1):
            total += self._get_distance(route[i], route[i + 1], drone_id)

        return total

    def _calculate_route_distance_with_loiter(
        self,
        route: List[str],
        drone_id: str,
        target_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> float:
        """Travel distance + loiter fuel for a drone's route.

        This is the canonical "fuel used" value - matches what the animation
        displays and what solver_bridge writes on first solve. Use this whenever
        writing back to route_data["distance"] so the field stays loiter-inclusive
        across pipeline passes.
        """
        travel = self._calculate_route_distance(route, drone_id)
        drone_cfg = (self._drone_configs or {}).get(drone_id, {})
        tbi = target_by_id or self._target_by_id or {}
        loiter = compute_route_loiter_fuel(route, drone_cfg, tbi)
        return travel + loiter

    def _calculate_route_points(
        self, route: List[str], target_by_id: Dict[str, Dict[str, Any]]
    ) -> int:
        """Calculate total priority points for a route."""
        points = 0
        for wp_id in route:
            if str(wp_id).startswith("T") and not str(wp_id).startswith("TK"):
                target = target_by_id.get(str(wp_id))
                if target:
                    points += int(target.get("priority", 5))
        return points


# Global optimizer instance
_optimizer = PostOptimizer()


def post_optimize_solution(
    solution: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Dict[str, Any]],
    distance_matrix: Optional[Dict[str, Any]] = None,
    priority_constraints: Optional[str] = None,
    constraints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Post-optimize a solution to include unvisited targets.

    This is the main entry point for use as a LangGraph tool.

    Args:
        solution: Current solution with drone routes
        env: Environment data
        drone_configs: Drone configurations
        distance_matrix: Pre-calculated SAM-aware distance matrix
        priority_constraints: Optional priority constraints string
                              e.g., "D1,D2: priority>=6; D3,D4: priority<6"

    Returns:
        Optimized solution with updated routes

    DEBUG: Entry point logging
    """
    print("\n POST_OPTIMIZE_SOLUTION CALLED", flush=True)
    print(f"   solution keys: {list(solution.keys())}", flush=True)
    print(f"   env keys: {list(env.keys())}", flush=True)
    print(f"   drone_configs: {list(drone_configs.keys())}", flush=True)
    print(f"   distance_matrix provided: {distance_matrix is not None}", flush=True)
    print(f"   priority_constraints: {priority_constraints}", flush=True)

    # Debug: Check solution routes structure
    routes = solution.get("routes", {})
    print(f"   Solution has {len(routes)} routes:", flush=True)
    for did, rdata in routes.items():
        if isinstance(rdata, dict):
            route = rdata.get("route", [])
            traj = rdata.get("trajectory", [])
            fuel = rdata.get("fuel_budget", "N/A")
            dist = rdata.get("distance", "N/A")
            print(
                f"      D{did}: route={route}, trajectory_pts={len(traj)}, fuel_budget={fuel}, distance={dist}",
                flush=True,
            )
        else:
            print(f"      D{did}: (not a dict) {type(rdata)}", flush=True)

    # Set environment for SAM-aware distance fallback calculations
    _optimizer.set_environment(env)

    # Set distance matrix if available
    if distance_matrix:
        _optimizer.set_distance_matrix(distance_matrix)

    print("   Calling _optimizer.optimize()...", flush=True)
    result = _optimizer.optimize(
        solution, env, drone_configs, priority_constraints, constraints=constraints
    )
    print(
        f"   _optimizer.optimize() returned {len(result.get('routes', {}))} routes",
        flush=True,
    )
    return result


def get_unvisited_targets(
    solution: Dict[str, Any], env: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Get list of unvisited targets from a solution."""
    return _optimizer.find_unvisited_targets(solution, env)


def get_coverage_stats(solution: Dict[str, Any], env: Dict[str, Any]) -> Dict[str, Any]:
    """Get coverage statistics for a solution."""
    return _optimizer.calculate_coverage_stats(solution, env)


def set_optimizer_matrix(matrix_data: Dict[str, Any]):
    """Set the distance matrix for the global optimizer."""
    _optimizer.set_distance_matrix(matrix_data)


# Tool definition for LangGraph
POST_OPTIMIZER_TOOL = {
    "name": "post_optimize_solution",
    "description": """Post-optimize a mission solution by assigning unvisited targets
    to drones with remaining fuel capacity. Inserts targets at optimal positions
    in existing routes to minimize additional fuel consumption while maximizing
    coverage.""",
    "parameters": {
        "type": "object",
        "properties": {
            "solution": {
                "type": "object",
                "description": "Current solution with drone routes",
            },
            "env": {
                "type": "object",
                "description": "Environment data with targets, airports, sams",
            },
            "drone_configs": {
                "type": "object",
                "description": "Drone configurations with fuel budgets",
            },
            "distance_matrix": {
                "type": "object",
                "description": "Pre-calculated SAM-aware distance matrix (optional)",
            },
        },
        "required": ["solution", "env", "drone_configs"],
    },
    "function": post_optimize_solution,
}


COVERAGE_STATS_TOOL = {
    "name": "get_coverage_stats",
    "description": """Get coverage statistics for a mission solution including
    targets visited, points collected, and list of unvisited targets.""",
    "parameters": {
        "type": "object",
        "properties": {
            "solution": {"type": "object", "description": "Solution with drone routes"},
            "env": {"type": "object", "description": "Environment data with targets"},
        },
        "required": ["solution", "env"],
    },
    "function": get_coverage_stats,
}
