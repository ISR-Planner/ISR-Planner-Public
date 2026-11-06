import functools
from typing import Any, Dict, List, Optional, Tuple

from path_planning_core.latlng_utils import plane_dist
from server.solver.altitude import matrix_for_drone
from server.solver.optimizers.common import (
    _constraints_for_route,
    _ordering_legal,
    compute_route_loiter_fuel,
)
from server.solver.optimizers.refuel_legs import recalculate_leg_distances


class CrossingRemovalOptimizer:
    """Remove self-crossings in drone trajectories using 2-opt."""

    def __init__(self):
        self._distance_matrix: Optional[Dict[str, Any]] = None
        self._drone_configs: Dict[str, Dict[str, Any]] = {}
        self._target_by_id: Dict[str, Dict[str, Any]] = {}

    def set_distance_matrix(self, matrix_data: Dict[str, Any]):
        """Set the pre-calculated distance matrix."""
        self._distance_matrix = matrix_data

    def _segments_cross(
        self,
        p1: Tuple[float, float],
        p2: Tuple[float, float],
        p3: Tuple[float, float],
        p4: Tuple[float, float],
    ) -> bool:
        """Check if segment p1-p2 crosses segment p3-p4."""

        def ccw(a, b, c):
            return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])

        if p1 == p3 or p1 == p4 or p2 == p3 or p2 == p4:
            return False

        return ccw(p1, p3, p4) != ccw(p2, p3, p4) and ccw(p1, p2, p3) != ccw(p1, p2, p4)

    def optimize(
        self,
        solution: Dict[str, Any],
        env: Dict[str, Any],
        drone_configs: Dict[str, Dict[str, Any]],
        constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Remove crossings from all drone routes."""
        targets = env.get("targets", [])
        target_by_id = {str(t["id"]): t for t in targets}
        self._drone_configs = drone_configs
        self._target_by_id = target_by_id

        waypoint_positions = {}
        for t in targets:
            waypoint_positions[str(t["id"])] = (float(t["x"]), float(t["y"]))
        for a in env.get("airports", []):
            waypoint_positions[str(a["id"])] = (float(a["x"]), float(a["y"]))

        optimized_routes = {}
        for did, route_data in solution.get("routes", {}).items():
            optimized_routes[did] = {
                "route": list(route_data.get("route", [])),
                "sequence": route_data.get("sequence", ""),
                "points": route_data.get("points", 0),
                "distance": route_data.get("distance", 0),
                "fuel_budget": route_data.get("fuel_budget", 0),
                "frozen_segments": set(route_data.get("frozen_segments", [])),
                "loiter_fuel": route_data.get("loiter_fuel", 0.0),
                "trajectory": route_data.get("trajectory", []),
                "refuel_airport": route_data.get("refuel_airport"),
                "refuel_idx": route_data.get("refuel_idx", -1),
                "leg1_distance": route_data.get("leg1_distance", 0.0),
                "leg2_distance": route_data.get("leg2_distance", 0.0),
                "leg_distances": route_data.get("leg_distances"),
                "refuel_indices": route_data.get("refuel_indices"),
                "refuel_stops": route_data.get("refuel_stops"),
                "_virtual_leg": route_data.get("_virtual_leg"),
                "_parent_drone": route_data.get("_parent_drone"),
                "_leg_number": route_data.get("_leg_number"),
                "_refuel_airport": route_data.get("_refuel_airport"),
            }

        all_fixes = []
        num_passes = 4

        for pass_num in range(num_passes):
            for did, route_data in optimized_routes.items():
                route = route_data["route"]
                frozen_segments = route_data["frozen_segments"]
                did_c = _constraints_for_route(constraints, did)

                if len(route) < 4:
                    continue

                found_crossing = True
                max_iterations = 50
                iterations = 0

                while found_crossing and iterations < max_iterations:
                    found_crossing = False
                    iterations += 1

                    for i in range(len(route) - 1):
                        if found_crossing:
                            break

                        for j in range(i + 2, len(route) - 1):
                            if j == i + 1:
                                continue

                            reversal_affects_frozen = False
                            for k in range(i, j + 1):
                                if k in frozen_segments:
                                    reversal_affects_frozen = True
                                    break

                            if reversal_affects_frozen:
                                continue

                            refuel_idx = route_data.get("refuel_idx", -1)
                            if refuel_idx > 0 and i < refuel_idx <= j:
                                continue

                            p1 = waypoint_positions.get(str(route[i]))
                            p2 = waypoint_positions.get(str(route[i + 1]))
                            p3 = waypoint_positions.get(str(route[j]))
                            p4 = waypoint_positions.get(str(route[j + 1]))

                            if p1 is None or p2 is None or p3 is None or p4 is None:
                                continue

                            if self._segments_cross(p1, p2, p3, p4):
                                old_segment = route[i + 1 : j + 1]
                                if did_c is not None:
                                    trial = (
                                        route[: i + 1]
                                        + old_segment[::-1]
                                        + route[j + 1 :]
                                    )
                                    if not _ordering_legal(trial, did_c):
                                        continue
                                route[i + 1 : j + 1] = old_segment[::-1]

                                all_fixes.append(
                                    {
                                        "drone": did,
                                        "segment_i": i,
                                        "segment_j": j,
                                        "reversed": old_segment,
                                        "pass": pass_num + 1,
                                    }
                                )

                                found_crossing = True
                                break

        for did, route_data in optimized_routes.items():
            route = route_data["route"]
            route_data["sequence"] = ",".join(str(wp) for wp in route)
            if not self._distance_matrix:
                raise RuntimeError(
                    "Cannot compute route distance: no SAM-aware distance matrix "
                    "is set. Euclidean fallback is disabled (policy)."
                )

            drone_cfg = drone_configs.get(did, {})
            route_data["loiter_fuel"] = compute_route_loiter_fuel(
                route, drone_cfg, target_by_id
            )
            route_data["distance"] = (
                self._calculate_route_distance_with_loiter_from_matrix(
                    route, did, target_by_id
                )
            )
            route_data["points"] = self._calculate_route_points(route, target_by_id)

            refuel_ap = route_data.get("refuel_airport")
            if refuel_ap and refuel_ap != "":
                old_leg1 = route_data.get("leg1_distance", 0)
                old_leg2 = route_data.get("leg2_distance", 0)
                leg1, leg2 = recalculate_leg_distances(
                    route,
                    refuel_ap,
                    drone_cfg,
                    target_by_id,
                    functools.partial(
                        self._calculate_route_distance_from_matrix, drone_id=did
                    ),
                )
                if leg1 > 0 or leg2 > 0:
                    print(
                        f"   [NoCross] D{did} leg recalc: "
                        f"old L1={old_leg1:.1f} L2={old_leg2:.1f} -> "
                        f"new L1={leg1:.1f} L2={leg2:.1f}"
                    )
                    route_data["leg1_distance"] = leg1
                    route_data["leg2_distance"] = leg2

        return {
            "sequences": {
                did: data["sequence"] for did, data in optimized_routes.items()
            },
            "routes": optimized_routes,
            "fixes_made": all_fixes,
            "passes": num_passes,
        }

    def _calculate_route_distance_from_matrix(
        self, route: List[str], drone_id: Optional[str] = None
    ) -> float:
        """SAM-aware travel distance of a route for drone_id (at its altitude)."""
        dist_data = matrix_for_drone(self._distance_matrix, drone_id)
        if len(route) < 2 or not dist_data:
            return 0.0

        labels = dist_data.get("labels", [])
        matrix = dist_data.get("matrix", [])

        total = 0.0
        for i in range(len(route) - 1):
            try:
                idx_from = labels.index(str(route[i]))
                idx_to = labels.index(str(route[i + 1]))
                total += matrix[idx_from][idx_to]
            except (ValueError, IndexError):
                total += 10.0

        return total

    def _calculate_route_distance_with_loiter_from_matrix(
        self,
        route: List[str],
        drone_id: str,
        target_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> float:
        """Travel matrix distance plus loiter fuel for a drone route."""
        travel = self._calculate_route_distance_from_matrix(route, drone_id)
        drone_cfg = (self._drone_configs or {}).get(drone_id, {})
        tbi = target_by_id or self._target_by_id or {}
        loiter = compute_route_loiter_fuel(route, drone_cfg, tbi)
        return travel + loiter

    def _calculate_route_distance_euclidean(
        self, route: List[str], waypoint_positions: Dict[str, Tuple[float, float]]
    ) -> float:
        """Calculate total Euclidean distance of a route."""
        if len(route) < 2:
            return 0.0

        total = 0.0
        for i in range(len(route) - 1):
            p1 = waypoint_positions.get(str(route[i]))
            p2 = waypoint_positions.get(str(route[i + 1]))
            if p1 and p2:
                total += plane_dist(p1, p2)

        return total

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


_crossing_optimizer = CrossingRemovalOptimizer()


def crossing_removal_optimize(
    solution: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Dict[str, Any]],
    distance_matrix: Optional[Dict[str, Any]] = None,
    constraints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Remove self-crossings from drone trajectories using 2-opt."""
    if distance_matrix:
        _crossing_optimizer.set_distance_matrix(distance_matrix)

    return _crossing_optimizer.optimize(
        solution, env, drone_configs, constraints=constraints
    )
