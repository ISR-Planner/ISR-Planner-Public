import copy
from typing import Any, Callable, Dict, List, Optional

from server.solver.altitude import matrix_for_drone
from server.solver.optimizers.common import (
    parse_priority_constraint,
    target_allowed_for_drone,
)
from server.solver.optimizers.crossing_removal import crossing_removal_optimize


class UnifiedOptimizer:
    """
    Legacy interleaved Insert + Swap optimizer.

    The production pipeline lives in `apply_optimizer_pipeline`; this class is
    retained for exploratory workflows and tests.
    """

    def __init__(self):
        self._distance_matrix: Optional[Dict[str, Any]] = None
        self._env: Optional[Dict[str, Any]] = None
        self._waypoint_positions: Optional[Dict[str, List[float]]] = None
        self._target_by_id: Dict[str, Dict[str, Any]] = {}

    def set_distance_matrix(self, matrix_data: Dict[str, Any]):
        """Set the pre-calculated distance matrix."""
        self._distance_matrix = matrix_data

    def set_environment(self, env: Dict[str, Any]):
        """Set environment data for waypoint positions and target lookup."""
        self._env = env

        waypoint_positions = {}
        airports = env.get("airports", [])
        targets = env.get("targets", [])

        for a in airports:
            waypoint_positions[str(a["id"])] = [float(a["x"]), float(a["y"])]
        for t in targets:
            waypoint_positions[str(t["id"])] = [float(t["x"]), float(t["y"])]
            self._target_by_id[str(t["id"])] = t

        self._waypoint_positions = waypoint_positions

    def _get_distance(
        self, from_id: str, to_id: str, drone_id: Optional[str] = None
    ) -> float:
        """SAM-aware distance for drone_id (at its cruise altitude)."""
        labels: List[str] = []
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

        raise RuntimeError(
            f"_get_distance: cannot find SAM-aware distance for {from_id}->{to_id}. "
            f"Matrix labels: {labels[:10] if dist_data else 'no matrix'}. "
            "Euclidean fallback is disabled (policy)."
        )

    def _calculate_insertion_cost(
        self, prev_wp: str, target_id: str, next_wp: str, drone_id: Optional[str] = None
    ) -> float:
        """Calculate cost to insert target between two waypoints."""
        d_ax = self._get_distance(prev_wp, target_id, drone_id)
        d_xb = self._get_distance(target_id, next_wp, drone_id)
        d_ab = self._get_distance(prev_wp, next_wp, drone_id)
        return d_ax + d_xb - d_ab

    def _calculate_route_distance(
        self, route: List[str], drone_id: Optional[str] = None
    ) -> float:
        """Calculate total distance of a route."""
        if len(route) < 2:
            return 0.0
        total = 0.0
        for i in range(len(route) - 1):
            total += self._get_distance(route[i], route[i + 1], drone_id)
        return total

    def _get_remaining_fuel(
        self, route_data: Dict[str, Any], drone_config: Dict[str, Any]
    ) -> float:
        """Calculate remaining fuel for a drone after its current route."""
        fuel_budget = float(
            drone_config.get("fuel_budget", drone_config.get("fuel_capacity", 100))
        )
        distance_used = route_data.get("distance", 0.0)
        return max(0.0, fuel_budget - distance_used)

    def _deep_copy_solution(self, solution: Dict[str, Any]) -> Dict[str, Any]:
        """Create a deep copy of solution to avoid mutation issues."""
        return copy.deepcopy(solution)

    def optimize(
        self,
        solution: Dict[str, Any],
        env: Dict[str, Any],
        drone_configs: Dict[str, Dict[str, Any]],
        priority_constraints: Optional[str] = None,
        max_iterations: int = 100,
        run_crossing_removal: bool = True,
        debug: bool = False,
    ) -> Dict[str, Any]:
        """Run interleaved Insert + Swap optimization."""
        print(
            "\n[UnifiedOptimizer] Starting interleaved Insert+Swap optimization",
            flush=True,
        )
        print(
            f"   max_iterations={max_iterations}, "
            f"run_crossing_removal={run_crossing_removal}",
            flush=True,
        )

        self.set_environment(env)

        priority_filters = (
            parse_priority_constraint(priority_constraints)
            if priority_constraints
            else {}
        )

        current_solution = self._deep_copy_solution(solution)
        operations_log = []
        total_inserts = 0
        total_swaps = 0

        for iteration in range(1, max_iterations + 1):
            insert_candidates = self._evaluate_insert_candidates(
                current_solution, env, drone_configs, priority_filters
            )
            swap_candidates = self._evaluate_swap_candidates(
                current_solution, env, drone_configs, priority_filters
            )

            all_candidates = insert_candidates + swap_candidates

            if not all_candidates:
                print(
                    f"   Converged at iteration {iteration} - no more beneficial operations",
                    flush=True,
                )
                break

            best = max(all_candidates, key=lambda c: c["score"])

            if debug:
                print(
                    f"   Iteration {iteration}: {len(insert_candidates)} insert "
                    f"candidates, {len(swap_candidates)} swap candidates",
                    flush=True,
                )
                print(
                    f"   Best: {best['type']} {best.get('target', 'N/A')} "
                    f"score={best['score']:.3f}",
                    flush=True,
                )

            if best["type"] == "insert":
                self._execute_insert(current_solution, best, drone_configs)
                total_inserts += 1
                op_desc = (
                    f"Insert {best['target']} into D{best['drone']} at pos "
                    f"{best['position']} (cost={best['cost']:.2f}, "
                    f"priority={best['priority']})"
                )
            else:
                self._execute_swap(current_solution, best, drone_configs)
                total_swaps += 1
                op_desc = (
                    f"Swap {best['target']} from D{best['from_drone']} to "
                    f"D{best['to_drone']} (gain={best['gain']:.2f})"
                )

            operations_log.append(
                {"iteration": iteration, "type": best["type"], "description": op_desc}
            )

            if debug or iteration <= 5 or iteration % 10 == 0:
                print(f"   [{iteration}] {op_desc}", flush=True)

        print(
            f"\n   Unified optimization complete: {total_inserts} inserts, "
            f"{total_swaps} swaps in {len(operations_log)} iterations",
            flush=True,
        )

        if run_crossing_removal:
            print("   Running crossing removal as final cleanup...", flush=True)
            current_solution = crossing_removal_optimize(
                current_solution, env, drone_configs, self._distance_matrix
            )

        if "metadata" not in current_solution:
            current_solution["metadata"] = {}
        current_solution["metadata"]["unified_optimizer"] = {
            "total_iterations": len(operations_log),
            "total_inserts": total_inserts,
            "total_swaps": total_swaps,
            "operations": operations_log,
        }

        return current_solution

    def _evaluate_insert_candidates(
        self,
        solution: Dict[str, Any],
        env: Dict[str, Any],
        drone_configs: Dict[str, Dict[str, Any]],
        priority_filters: Dict[str, Callable[[int], bool]],
    ) -> List[Dict[str, Any]]:
        """Find all viable insert operations for unvisited targets."""
        candidates = []
        targets = env.get("targets", [])

        visited = set()
        for _did, route_data in solution.get("routes", {}).items():
            for wp in route_data.get("route", []):
                if str(wp).startswith("T") and not str(wp).startswith("TK"):
                    visited.add(str(wp))

        unvisited = [t for t in targets if str(t["id"]) not in visited]

        for target in unvisited:
            target_id = str(target["id"])
            target_priority = int(target.get("priority", 5))

            for drone_id, cfg in drone_configs.items():
                if not target_allowed_for_drone(
                    target, drone_id, cfg, priority_filters
                ):
                    continue

                route_data = solution.get("routes", {}).get(drone_id, {})
                route = route_data.get("route", [])
                frozen_segments = set(route_data.get("frozen_segments", []))
                remaining_fuel = self._get_remaining_fuel(route_data, cfg)

                if remaining_fuel <= 0 or len(route) < 2:
                    continue

                best_cost = float("inf")
                best_position = None

                for i in range(1, len(route)):
                    if (i - 1) in frozen_segments:
                        continue

                    prev_wp = str(route[i - 1])
                    next_wp = str(route[i])

                    cost = self._calculate_insertion_cost(
                        prev_wp, target_id, next_wp, drone_id
                    )

                    if cost < best_cost and cost <= remaining_fuel:
                        best_cost = cost
                        best_position = i

                if best_position is not None and best_cost < float("inf"):
                    score = target_priority * 10 / max(best_cost, 1.0)

                    candidates.append(
                        {
                            "type": "insert",
                            "target": target_id,
                            "drone": drone_id,
                            "position": best_position,
                            "cost": best_cost,
                            "priority": target_priority,
                            "score": score,
                        }
                    )

        return candidates

    def _evaluate_swap_candidates(
        self,
        solution: Dict[str, Any],
        env: Dict[str, Any],
        drone_configs: Dict[str, Dict[str, Any]],
        priority_filters: Dict[str, Callable[[int], bool]],
    ) -> List[Dict[str, Any]]:
        """Find all viable swap operations for visited targets."""
        candidates = []

        for drone_id, route_data in solution.get("routes", {}).items():
            route = route_data.get("route", [])
            frozen_segments = set(route_data.get("frozen_segments", []))

            for i, wp_id in enumerate(route):
                wp_id = str(wp_id)

                if not (wp_id.startswith("T") and not wp_id.startswith("TK")):
                    continue

                if i <= 0 or i >= len(route) - 1:
                    continue

                if (i - 1) in frozen_segments or i in frozen_segments:
                    continue

                prev_wp = str(route[i - 1])
                next_wp = str(route[i + 1])

                removal_delta = self._calculate_insertion_cost(
                    prev_wp, wp_id, next_wp, drone_id
                )

                if removal_delta <= 0:
                    continue

                target = self._target_by_id.get(wp_id)
                if not target:
                    continue

                for other_drone_id, other_route_data in solution.get(
                    "routes", {}
                ).items():
                    other_route = other_route_data.get("route", [])
                    other_frozen = set(other_route_data.get("frozen_segments", []))
                    other_cfg = drone_configs.get(other_drone_id, {})

                    if other_drone_id != drone_id:
                        if not target_allowed_for_drone(
                            target, other_drone_id, other_cfg, priority_filters
                        ):
                            continue

                    for j in range(1, len(other_route)):
                        if (j - 1) in other_frozen:
                            continue

                        if other_drone_id == drone_id:
                            if j == i or j == i + 1:
                                continue

                        seg_start = str(other_route[j - 1])
                        seg_end = str(other_route[j])

                        insertion_cost = self._calculate_insertion_cost(
                            seg_start, wp_id, seg_end, other_drone_id
                        )

                        if other_drone_id != drone_id:
                            other_remaining = self._get_remaining_fuel(
                                other_route_data, other_cfg
                            )
                            if insertion_cost > other_remaining:
                                continue

                        gain = removal_delta - insertion_cost

                        if gain > 0:
                            candidates.append(
                                {
                                    "type": "swap",
                                    "target": wp_id,
                                    "from_drone": drone_id,
                                    "to_drone": other_drone_id,
                                    "from_idx": i,
                                    "to_position": j,
                                    "removal_delta": removal_delta,
                                    "insertion_cost": insertion_cost,
                                    "gain": gain,
                                    "score": gain,
                                }
                            )

        return candidates

    def _execute_insert(
        self,
        solution: Dict[str, Any],
        candidate: Dict[str, Any],
        drone_configs: Dict[str, Dict[str, Any]],
    ):
        """Apply an insert operation to the solution."""
        drone_id = candidate["drone"]
        target_id = candidate["target"]
        position = candidate["position"]

        route_data = solution["routes"][drone_id]
        route = route_data["route"]

        route.insert(position, target_id)
        route_data["distance"] = self._calculate_route_distance(route, drone_id)

        target = self._target_by_id.get(target_id, {})
        current_points = route_data.get("points", 0)
        route_data["points"] = current_points + int(target.get("priority", 5))

    def _execute_swap(
        self,
        solution: Dict[str, Any],
        candidate: Dict[str, Any],
        drone_configs: Dict[str, Dict[str, Any]],
    ):
        """Apply a swap operation to the solution."""
        target_id = candidate["target"]
        from_drone = candidate["from_drone"]
        to_drone = candidate["to_drone"]
        from_idx = candidate["from_idx"]
        to_position = candidate["to_position"]

        from_route_data = solution["routes"][from_drone]
        from_route = from_route_data["route"]

        from_route.pop(from_idx)

        if to_drone == from_drone and to_position > from_idx:
            to_position -= 1

        to_route_data = solution["routes"][to_drone]
        to_route = to_route_data["route"]

        to_route.insert(to_position, target_id)

        from_route_data["distance"] = self._calculate_route_distance(
            from_route, from_drone
        )
        to_route_data["distance"] = self._calculate_route_distance(to_route, to_drone)

        if from_drone != to_drone:
            target = self._target_by_id.get(target_id, {})
            priority = int(target.get("priority", 5))
            from_route_data["points"] = from_route_data.get("points", 0) - priority
            to_route_data["points"] = to_route_data.get("points", 0) + priority


_unified_optimizer = UnifiedOptimizer()


def unified_optimize(
    solution: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Dict[str, Any]],
    distance_matrix: Optional[Dict[str, Any]] = None,
    priority_constraints: Optional[str] = None,
    max_iterations: int = 100,
    run_crossing_removal: bool = True,
    debug: bool = False,
) -> Dict[str, Any]:
    """Unified optimization with interleaved Insert + Swap operations."""
    if distance_matrix:
        _unified_optimizer.set_distance_matrix(distance_matrix)

    return _unified_optimizer.optimize(
        solution,
        env,
        drone_configs,
        priority_constraints=priority_constraints,
        max_iterations=max_iterations,
        run_crossing_removal=run_crossing_removal,
        debug=debug,
    )
