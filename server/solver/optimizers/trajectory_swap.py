"""Trajectory-swap optimizer."""

import functools
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from path_planning_core.latlng_utils import plane_dist
from server.solver.altitude import matrix_for_drone, sams_for_drone
from server.solver.optimizers.common import (
    _constraints_for_route,
    _ordering_legal,
    compute_route_loiter_fuel,
    effective_leg_budget,
    parse_priority_constraint,
    target_allowed_for_drone,
)
from server.solver.optimizers.refuel_legs import recalculate_leg_distances


class TrajectorySwapOptimizer:
    """
    Reassign targets to drones whose trajectories pass closer to them.

    For each target, calculate its perpendicular distance to the line segment
    connecting its neighbors in the current route. If another drone's trajectory
    passes closer AND that drone can carry the target, move it.
    """

    def __init__(self):
        self._distance_matrix: Optional[Dict[str, Any]] = None
        # Populated in optimize(); enables _calculate_route_distance_with_loiter
        # without threading both maps through every call site.
        self._drone_configs: Dict[str, Dict[str, Any]] = {}
        self._target_by_id: Dict[str, Dict[str, Any]] = {}

    def set_distance_matrix(self, matrix_data: Dict[str, Any]):
        """Set the pre-calculated distance matrix."""
        self._distance_matrix = matrix_data

    def _get_route_signature(self, routes: Dict[str, Any]) -> str:
        """
        Create a unique signature for a route configuration.
        Used for cycle detection.
        """
        # Sort drone IDs for consistent ordering
        parts = []
        for did in sorted(routes.keys()):
            route = routes[did].get("route", [])
            parts.append(f"{did}:{','.join(str(wp) for wp in route)}")
        return "|".join(parts)

    def _calculate_total_distance(self, routes: Dict[str, Any]) -> float:
        """Calculate total distance across all drones."""
        total = 0.0
        for did, route_data in routes.items():
            total += route_data.get("distance", 0.0)
        return total

    def optimize(
        self,
        solution: Dict[str, Any],
        env: Dict[str, Any],
        drone_configs: Dict[str, Dict[str, Any]],
        priority_constraints: Optional[str] = None,
        auto_iterate: bool = True,
        max_iterations: int = 50,
        auto_regen: bool = False,
        debug: bool = False,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Reassign targets to drones with closer trajectories using SAM-avoiding trajectory segments.

        Algorithm (Swap Closer):
        1. For each target in a trajectory:
           - Calculate SSD (Self Segment Distance) from trajectory vertices
           - If SSD = 0: SKIP (NO SSD NO MOVEMENT rule)
           - If SSD > 0: Search for better segments within radius SSD
        2. Find all trajectory segments (OS) within circle of radius SSD
        3. For each OS where OSD < SSD:
           - Check if segment can accept target (capability, fuel, not frozen)
        4. Move target to segment with minimum OSD (if any valid ones exist)

        When auto_iterate=True (default):
        - Runs multiple iterations until no more swaps or cycle detected
        - Tracks fuel/distance at each iteration
        - Returns the solution with lowest total distance

        Args:
            solution: Current solution with routes for each drone
            env: Environment dict with targets, airports, sams
            drone_configs: Drone configurations with capabilities
            priority_constraints: Optional priority constraints string
            auto_iterate: If True, run until convergence/cycle and return best fuel state
            max_iterations: Maximum iterations for auto mode (safety limit)

        Returns:
            Optimized solution with targets moved to closer trajectories
        """
        if auto_iterate:
            return self._optimize_auto(
                solution,
                env,
                drone_configs,
                priority_constraints,
                max_iterations,
                auto_regen,
                debug,
                constraints=constraints,
            )
        else:
            return self._optimize_single(
                solution,
                env,
                drone_configs,
                priority_constraints,
                debug,
                constraints=constraints,
            )

    def _optimize_auto(
        self,
        solution: Dict[str, Any],
        env: Dict[str, Any],
        drone_configs: Dict[str, Dict[str, Any]],
        priority_constraints: Optional[str] = None,
        max_iterations: int = 50,
        auto_regen: bool = False,
        debug: bool = False,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Auto-iterate Swap Closer until convergence or cycle detection.
        Returns the solution with the lowest total distance encountered.
        """
        print(
            f"\nREFRESH [TrajectorySwapOptimizer] AUTO-ITERATE MODE (max {max_iterations} iterations)"
        )

        # Track seen states for cycle detection
        seen_signatures: Set[str] = set()

        # Track best solution (lowest total distance)
        best_solution = None
        best_distance = float("inf")
        best_iteration = 0

        # History for reporting
        all_swaps = []
        iteration_history = []

        current_solution = solution
        iteration = 0

        while iteration < max_iterations:
            iteration += 1

            # Get signature of current state
            current_routes = current_solution.get("routes", {})
            signature = self._get_route_signature(current_routes)

            # Check for cycle
            if signature in seen_signatures:
                print(f"\nCYCLE CYCLE DETECTED at iteration {iteration}")
                print(
                    f"   Returning best solution from iteration {best_iteration} (distance: {best_distance:.2f})"
                )
                break

            seen_signatures.add(signature)

            # Calculate current total distance
            current_distance = self._calculate_total_distance(current_routes)
            iteration_history.append(
                {
                    "iteration": iteration,
                    "distance": current_distance,
                    "signature": (
                        signature[:50] + "..." if len(signature) > 50 else signature
                    ),
                }
            )

            # Track best solution
            if current_distance < best_distance:
                best_distance = current_distance
                best_solution = self._deep_copy_solution(current_solution)
                best_iteration = iteration
                print(
                    f"   NEW Iteration {iteration}: NEW BEST distance = {best_distance:.2f}"
                )
            else:
                print(
                    f"   POINT Iteration {iteration}: distance = {current_distance:.2f}"
                )

            # Run single swap iteration
            result = self._optimize_single(
                current_solution,
                env,
                drone_configs,
                priority_constraints,
                debug,
                constraints=constraints,
            )

            swaps_made = result.get("swaps_made", [])

            # Check for convergence (no more swaps)
            if not swaps_made:
                print(
                    f"\nOK CONVERGED at iteration {iteration} (no more beneficial swaps)"
                )
                print(f"   Final distance: {current_distance:.2f}")
                # Use current solution if it's the best, otherwise use tracked best
                if current_distance <= best_distance:
                    best_solution = self._deep_copy_solution(current_solution)
                    best_distance = current_distance
                    best_iteration = iteration
                break

            # Record swaps
            for swap in swaps_made:
                swap["iteration"] = iteration
                all_swaps.append(swap)

            # Update current solution for next iteration
            current_solution = {
                "routes": result.get("routes", {}),
                "sequences": result.get("sequences", {}),
            }

            # Optionally regenerate SAM-aware trajectories for the updated routes
            # so the next iteration can compute SSD/OSD using fresh trajectory
            # vertices. This is controlled by the `auto_regen` flag to preserve
            # the previous client-click workflow by default.
            if auto_regen:
                try:
                    # Build waypoint positions lookup from env
                    waypoint_positions = {}
                    for a in env.get("airports", []):
                        waypoint_positions[str(a["id"])] = [
                            float(a["x"]),
                            float(a["y"]),
                        ]
                    for t in env.get("targets", []):
                        waypoint_positions[str(t["id"])] = [
                            float(t["x"]),
                            float(t["y"]),
                        ]

                    # Import planner locally to keep top-level imports safe
                    try:
                        from server.solver.trajectory_planner import (
                            ISRTrajectoryPlanner as ServerISRTrajectoryPlanner,
                        )

                        planner_cls = ServerISRTrajectoryPlanner
                    except Exception:
                        # try alternate import path for some environments
                        from trajectory_planner import (
                            ISRTrajectoryPlanner as LocalISRTrajectoryPlanner,
                        )

                        planner_cls = LocalISRTrajectoryPlanner

                    for did, rd in current_solution.get("routes", {}).items():
                        route = rd.get("route", []) or []
                        # Each drone flies around the SAMs at its own altitude.
                        planner = planner_cls(sams_for_drone(env, drone_configs, did))
                        # Generate SAM-aware trajectory (returns list of [x,y])
                        try:
                            traj = planner.generate_trajectory(
                                route, waypoint_positions, did
                            )
                        except Exception as e:
                            print(
                                f"   WARN Trajectory planner failed for D{did}: {e}",
                                flush=True,
                            )
                            traj = [
                                list(waypoint_positions.get(wp, [0, 0])) for wp in route
                            ]

                        # Store trajectory and recompute route distance
                        # (travel + loiter so the field stays consistent with what
                        # solver_bridge wrote on first solve).
                        drone_cfg = (self._drone_configs or {}).get(did, {})
                        rd["trajectory"] = traj
                        rd["loiter_fuel"] = compute_route_loiter_fuel(
                            route, drone_cfg, self._target_by_id
                        )
                        rd["distance"] = self._calculate_route_distance_with_loiter(
                            route, did
                        )

                    print(
                        "   OK Regenerated trajectories for next iteration", flush=True
                    )
                except Exception as e:
                    # Non-fatal: log and continue without trajectories
                    print(
                        f"   WARN Trajectory regeneration skipped due to error: {e}",
                        flush=True,
                    )

        else:
            # Max iterations reached
            print(f"\nWARN MAX ITERATIONS ({max_iterations}) reached")
            print(
                f"   Returning best solution from iteration {best_iteration} (distance: {best_distance:.2f})"
            )

        # Use best solution found
        if best_solution is None:
            best_solution = current_solution

        # Recalculate final metrics
        final_routes = best_solution.get("routes", {})
        targets = env.get("targets", [])
        target_by_id = {str(t["id"]): t for t in targets}

        for did, route_data in final_routes.items():
            route = route_data.get("route", [])
            route_data["sequence"] = ",".join(str(wp) for wp in route)
            # travel + loiter; matches what solver_bridge wrote on first solve
            # and what the animation displays as fuel used.
            drone_cfg = (self._drone_configs or {}).get(did, {})
            route_data["loiter_fuel"] = compute_route_loiter_fuel(
                route, drone_cfg, target_by_id
            )
            route_data["distance"] = self._calculate_route_distance_with_loiter(
                route, did, target_by_id
            )
            route_data["points"] = self._calculate_route_points(route, target_by_id)

            # Recalculate leg distances for refueling drones
            # Note: "-" means "any" (solver picked), so it IS a valid refuel airport
            refuel_ap = route_data.get("refuel_airport")
            if refuel_ap and refuel_ap != "":
                old_leg1 = route_data.get("leg1_distance", 0)
                old_leg2 = route_data.get("leg2_distance", 0)
                leg1, leg2 = recalculate_leg_distances(
                    route,
                    refuel_ap,
                    drone_cfg,
                    target_by_id,
                    functools.partial(self._calculate_route_distance, drone_id=did),
                )
                if leg1 > 0 or leg2 > 0:
                    print(
                        f"   REFRESH D{did} leg recalc: old L1={old_leg1:.1f} L2={old_leg2:.1f} -> new L1={leg1:.1f} L2={leg2:.1f}"
                    )
                    route_data["leg1_distance"] = leg1
                    route_data["leg2_distance"] = leg2

        print("\nSTATS AUTO-ITERATE SUMMARY:")
        print(f"   Total iterations: {iteration}")
        print(f"   Total swaps made: {len(all_swaps)}")
        print(f"   Best iteration: {best_iteration}")
        print(f"   Best total distance: {best_distance:.2f}")

        return {
            "sequences": {
                did: data.get("sequence", "") for did, data in final_routes.items()
            },
            "routes": final_routes,
            "swaps_made": all_swaps,
            "iterations": iteration,
            "best_iteration": best_iteration,
            "best_distance": best_distance,
            "converged": len(all_swaps) == 0
            or (
                iteration < max_iterations
                and not any(s.get("iteration") == iteration for s in all_swaps)
            ),
            "cycle_detected": signature in seen_signatures if iteration > 0 else False,
        }

    def _deep_copy_solution(self, solution: Dict[str, Any]) -> Dict[str, Any]:
        """Create a deep copy of a solution to preserve state."""
        return {
            "routes": {
                did: {
                    "route": list(data.get("route", [])),
                    "sequence": data.get("sequence", ""),
                    "points": data.get("points", 0),
                    "distance": data.get("distance", 0),
                    "fuel_budget": data.get("fuel_budget", 0),
                    "frozen_segments": list(data.get("frozen_segments", [])),
                    # Preserve loiter and refuel metadata
                    "loiter_fuel": data.get("loiter_fuel", 0.0),
                    "trajectory": list(data.get("trajectory", [])),
                    "refuel_airport": data.get("refuel_airport"),
                    "refuel_idx": data.get("refuel_idx", -1),  # Preserve refuel index
                    "leg1_distance": data.get("leg1_distance", 0.0),
                    "leg2_distance": data.get("leg2_distance", 0.0),
                    # Preserve virtual leg markers for merge_virtual_legs
                    "_virtual_leg": data.get("_virtual_leg"),
                    "_parent_drone": data.get("_parent_drone"),
                    "_leg_number": data.get("_leg_number"),
                    "_refuel_airport": data.get("_refuel_airport"),
                }
                for did, data in solution.get("routes", {}).items()
            },
            "sequences": dict(solution.get("sequences", {})),
        }

    def _optimize_single(
        self,
        solution: Dict[str, Any],
        env: Dict[str, Any],
        drone_configs: Dict[str, Dict[str, Any]],
        priority_constraints: Optional[str] = None,
        debug: bool = False,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Perform a SINGLE swap iteration (the original behavior).
        """
        # Parse priority constraints into per-drone filter functions
        priority_filters = (
            parse_priority_constraint(priority_constraints)
            if priority_constraints
            else {}
        )

        targets = env.get("targets", [])
        target_by_id = {str(t["id"]): t for t in targets}
        # Cache for _calculate_route_distance_with_loiter (avoids threading
        # target_by_id through every call site below).
        self._target_by_id = target_by_id

        # Build position lookup for all waypoints
        waypoint_positions = {}
        for t in targets:
            waypoint_positions[str(t["id"])] = (float(t["x"]), float(t["y"]))
        for a in env.get("airports", []):
            waypoint_positions[str(a["id"])] = (float(a["x"]), float(a["y"]))

        # Copy routes for modification, including trajectories and frozen segments
        optimized_routes = {}
        drone_trajectories = {}  # Store the actual SAM-avoiding trajectories
        target_to_traj_index = {}  # Map target_id to (drone_id, traj_vertex_index)

        for did, route_data in solution.get("routes", {}).items():
            optimized_routes[did] = {
                "route": list(route_data.get("route", [])),
                "sequence": route_data.get("sequence", ""),
                "points": route_data.get("points", 0),
                "distance": route_data.get("distance", 0),
                "fuel_budget": route_data.get("fuel_budget", 0),
                "frozen_segments": set(route_data.get("frozen_segments", [])),
                # Preserve loiter_fuel and trajectory so downstream stages
                # (terminal recalc, agent re-pass, replan-from-checkpoint) can
                # still distinguish travel from loiter.
                "loiter_fuel": route_data.get("loiter_fuel", 0.0),
                "trajectory": route_data.get("trajectory", []),
                # Preserve refuel metadata so leg distances can be recalculated
                "refuel_airport": route_data.get("refuel_airport"),
                "refuel_idx": route_data.get("refuel_idx", -1),  # Preserve refuel index
                "leg1_distance": route_data.get("leg1_distance", 0.0),
                "leg2_distance": route_data.get("leg2_distance", 0.0),
                # Multi-stop refuel (N-leg) metadata - preserve through optimize so
                # the merged virtual-drone route keeps its per-leg distances / refuel
                # indices / stops for downstream fuel checks + stats.
                "leg_distances": route_data.get("leg_distances"),
                "refuel_indices": route_data.get("refuel_indices"),
                "refuel_stops": route_data.get("refuel_stops"),
                # Preserve virtual leg markers for merge_virtual_legs
                "_virtual_leg": route_data.get("_virtual_leg"),
                "_parent_drone": route_data.get("_parent_drone"),
                "_leg_number": route_data.get("_leg_number"),
                "_refuel_airport": route_data.get("_refuel_airport"),
            }
            # Extract trajectory as list of (x, y) tuples
            traj = route_data.get("trajectory", [])
            if traj:
                drone_trajectories[did] = [(float(p[0]), float(p[1])) for p in traj]

                # Build mapping from targets to trajectory vertex indices
                route = route_data.get("route", [])
                traj_idx = 0
                for wp_id in route:
                    if str(wp_id).startswith("T") and not str(wp_id).startswith("TK"):
                        # Find this target's position in trajectory
                        wp_pos = waypoint_positions.get(str(wp_id))
                        if wp_pos:
                            # Scan trajectory to find matching vertex
                            for i in range(traj_idx, len(drone_trajectories[did])):
                                if (
                                    abs(drone_trajectories[did][i][0] - wp_pos[0])
                                    < 0.001
                                    and abs(drone_trajectories[did][i][1] - wp_pos[1])
                                    < 0.001
                                ):
                                    target_to_traj_index[str(wp_id)] = (did, i)
                                    traj_idx = i + 1
                                    break
            else:
                drone_trajectories[did] = []

        # Split refueling drone trajectories into virtual legs
        # This allows targets to swap between Leg1 and Leg2 of the same physical drone
        for did, route_data in optimized_routes.items():
            refuel_ap = route_data.get("refuel_airport")
            if refuel_ap and refuel_ap != "" and refuel_ap != "-":
                route = route_data["route"]
                # Use refuel_idx from route data if available (more reliable)
                refuel_idx = route_data.get("refuel_idx", -1)
                # Fallback: search for refuel airport in route (handles legacy data)
                if refuel_idx < 0:
                    for i, wp in enumerate(route):
                        if str(wp) == str(refuel_ap) and i > 0 and i < len(route) - 1:
                            refuel_idx = i
                            break

                if refuel_idx > 0 and did in drone_trajectories:
                    # Create virtual trajectory IDs (not drone IDs!)
                    leg1_traj_id = f"{did}_L1"
                    leg2_traj_id = f"{did}_L2"

                    # Split route into two legs
                    leg1_route = route[: refuel_idx + 1]  # Start -> Refuel (inclusive)
                    leg2_route = route[refuel_idx:]  # Refuel -> End (inclusive)

                    # Create simple straight-line trajectories for each leg
                    # Just use waypoint positions in order (no SAM avoidance needed for swap evaluation)
                    leg1_traj = []
                    for wp_id in leg1_route:
                        wp_pos = waypoint_positions.get(str(wp_id))
                        if wp_pos:
                            leg1_traj.append(wp_pos)

                    leg2_traj = []
                    for wp_id in leg2_route:
                        wp_pos = waypoint_positions.get(str(wp_id))
                        if wp_pos:
                            leg2_traj.append(wp_pos)

                    if len(leg1_traj) >= 2 and len(leg2_traj) >= 2:
                        # Add virtual leg trajectories
                        drone_trajectories[leg1_traj_id] = leg1_traj
                        drone_trajectories[leg2_traj_id] = leg2_traj

                        print(
                            f"   FUEL [Swap] Split D{did} into {leg1_traj_id} ({len(leg1_traj)} pts) and {leg2_traj_id} ({len(leg2_traj)} pts)"
                        )
                    else:
                        print(
                            f"   WARN  [Swap] Failed to create leg trajectories for D{did} (leg1={len(leg1_traj)} pts, leg2={len(leg2_traj)} pts)"
                        )
                        continue

                    # Remap targets to their leg trajectory IDs
                    for target_id, (traj_did, traj_idx) in list(
                        target_to_traj_index.items()
                    ):
                        if traj_did == did:
                            # Find target position in route
                            try:
                                target_route_idx = route.index(target_id)
                                if target_route_idx <= refuel_idx:
                                    # Target is in Leg1 - find its index in leg1_traj
                                    leg1_traj_idx = leg1_route.index(target_id)
                                    target_to_traj_index[target_id] = (
                                        leg1_traj_id,
                                        leg1_traj_idx,
                                    )
                                    print(
                                        f"      TARGET {target_id} -> {leg1_traj_id} (route idx {target_route_idx}, leg traj idx {leg1_traj_idx})"
                                    )
                                else:
                                    # Target is in Leg2 - find its index in leg2_traj
                                    leg2_traj_idx = leg2_route.index(target_id)
                                    target_to_traj_index[target_id] = (
                                        leg2_traj_id,
                                        leg2_traj_idx,
                                    )
                                    print(
                                        f"      TARGET {target_id} -> {leg2_traj_id} (route idx {target_route_idx}, leg traj idx {leg2_traj_idx})"
                                    )
                            except ValueError:
                                pass  # Target not in route

        # Create mapping from trajectory ID to parent drone ID (after splitting legs)
        traj_to_drone = {}  # Maps trajectory_id -> parent_drone_id
        for traj_id in drone_trajectories.keys():
            if "_L1" in str(traj_id) or "_L2" in str(traj_id):
                parent_drone = str(traj_id).split("_L")[0]
                traj_to_drone[traj_id] = parent_drone
            else:
                traj_to_drone[traj_id] = traj_id

        # Store drone_configs and priority_filters for constraint checking
        self._drone_configs = drone_configs
        self._priority_filters = priority_filters

        # Do ONE swap per invocation to avoid stale trajectory data
        # After swap, frontend regenerates trajectories and user can click again
        num_passes = 1
        all_swaps = []  # Track all swaps made

        print("\nTOOL [TrajectorySwapOptimizer] Searching for best swap...")

        for pass_num in range(num_passes):
            print("\n  POINT Evaluating all targets...")

            # Collect ALL possible swap candidates in this pass
            swap_candidates = []

            # Optional diagnostics per target when debug=True
            target_diagnostics: Dict[str, Any] = {}

            # Collect all targets to check in this pass
            targets_to_check = []
            for current_drone, route_data in optimized_routes.items():
                route = route_data["route"]
                for wp_id in route:
                    if str(wp_id).startswith("T") and not str(wp_id).startswith("TK"):
                        targets_to_check.append(wp_id)

            # Evaluate each target to find its best swap option
            for wp_id in targets_to_check:
                # Find current location of this target
                current_drone = None
                current_idx = None
                for did, route_data in optimized_routes.items():
                    route = route_data["route"]
                    if wp_id in route:
                        current_drone = did
                        current_idx = route.index(wp_id)
                        break

                if current_drone is None or current_idx is None:
                    continue  # Target no longer in any route

                route = optimized_routes[current_drone]["route"]
                frozen_segments = optimized_routes[current_drone]["frozen_segments"]

                # Skip if target is in a frozen segment
                if current_idx > 0 and (current_idx - 1) in frozen_segments:
                    continue
                if current_idx < len(route) - 1 and current_idx in frozen_segments:
                    continue

                target = target_by_id.get(str(wp_id))
                if not target:
                    continue

                target_pos = waypoint_positions.get(str(wp_id))
                if not target_pos:
                    continue

                # Use actual trajectory vertices instead of route waypoints
                # This correctly accounts for SAM-avoidance trajectory deviations
                if str(wp_id) not in target_to_traj_index:
                    print(f"    SKIP  SKIP {wp_id}: Not found in trajectory index")
                    continue

                traj_drone_id, traj_idx = target_to_traj_index[str(wp_id)]

                # Extract parent drone ID from trajectory ID (e.g., "1_L1" -> "1")
                if "_L1" in str(traj_drone_id) or "_L2" in str(traj_drone_id):
                    parent_traj_drone = str(traj_drone_id).split("_L")[0]
                else:
                    parent_traj_drone = traj_drone_id

                # Extract parent drone ID from current_drone (may be virtual leg ID)
                if "_L1" in str(current_drone) or "_L2" in str(current_drone):
                    parent_current_drone = str(current_drone).split("_L")[0]
                else:
                    parent_current_drone = current_drone

                # Ensure this target is still on the same physical drone
                if parent_traj_drone != parent_current_drone:
                    print(
                        f"    SKIP  SKIP {wp_id}: Drone mismatch (index:{parent_traj_drone}, current:{parent_current_drone})"
                    )
                    continue

                # Use trajectory ID (may be virtual leg) for trajectory lookup
                trajectory = drone_trajectories.get(traj_drone_id)
                if not trajectory or len(trajectory) < 2:
                    print(
                        f"    SKIP  SKIP {wp_id}: No valid trajectory (len={len(trajectory) if trajectory else 0})"
                    )
                    continue

                # Get actual trajectory vertices immediately before/after this target
                # Handle boundary cases (targets at trajectory endpoints)
                if traj_idx == 0:
                    # Target at start of trajectory - use first two vertices
                    _prev_pos = trajectory[0]
                    _next_pos = trajectory[1] if len(trajectory) > 1 else trajectory[0]
                elif traj_idx >= len(trajectory) - 1:
                    # Target at end of trajectory - use last two vertices
                    _prev_pos = (
                        trajectory[-2] if len(trajectory) > 1 else trajectory[-1]
                    )
                    _next_pos = trajectory[-1]
                else:
                    # Target in middle of trajectory - use surrounding vertices
                    _prev_pos = trajectory[traj_idx - 1]  # noqa: F841
                    _next_pos = trajectory[traj_idx + 1]  # noqa: F841

                # Calculate removal delta: how much distance would be saved by removing this target
                remove_delta = self._removal_delta(
                    wp_id,
                    optimized_routes[current_drone],
                    waypoint_positions,
                    current_drone,
                )

                # Initialize diagnostics entry for this target
                if debug:
                    target_diagnostics[str(wp_id)] = {
                        "remove_delta": remove_delta,
                        "candidates": [],
                    }

                # Get route waypoint IDs for logging
                prev_wp = route[current_idx - 1] if current_idx > 0 else "?"
                next_wp = (
                    route[current_idx + 1] if current_idx < len(route) - 1 else "?"
                )

                if remove_delta is None or remove_delta <= 0.0:
                    print(
                        f"    TARGET {wp_id}: remove_delta=None or <=0 (skip) ({prev_wp}->{wp_id}->{next_wp})"
                    )
                    continue

                # Calculate loiter fuel saved on source drone when removing this target
                source_loiter_saved = 0.0
                source_cfg = drone_configs.get(current_drone, {})
                if source_cfg.get("loiter_enabled"):
                    source_speed = float(source_cfg.get("speed", 1))
                    target_type = target.get("type", "recon")
                    source_lt = float(
                        source_cfg.get("loiter_times", {}).get(target_type, 0)
                    )
                    source_loiter_saved = source_lt * source_speed

                print(
                    f"    TARGET {wp_id}: remove_delta={remove_delta:.2f}, loiter_saved={source_loiter_saved:.2f} (route neighbors {prev_wp}->{wp_id}->{next_wp})"
                )

                # Search for segments where inserting this target yields the best delta (insertion cost)
                best_drone = None
                best_route_segment = None
                best_insert_delta = float("inf")
                best_osd = float("inf")
                best_net_savings = -float(
                    "inf"
                )  # Track maximum gain (remove_delta - insert_delta)

                # Check all trajectory segments (including virtual legs)
                for other_traj_id, other_trajectory in drone_trajectories.items():
                    if not other_trajectory or len(other_trajectory) < 2:
                        continue

                    # Get parent drone ID for route lookups
                    other_drone = traj_to_drone.get(other_traj_id, other_traj_id)
                    other_route_data = optimized_routes.get(other_drone)

                    # If parent drone doesn't exist (pre-split solution), use trajectory ID as drone ID
                    if not other_route_data:
                        other_drone = other_traj_id
                        other_route_data = optimized_routes.get(other_drone)
                        if not other_route_data:
                            continue

                    # For virtual leg trajectories, use leg-specific route segment
                    # Otherwise, use full route
                    if "_L1" in str(other_traj_id) or "_L2" in str(other_traj_id):
                        # Virtual leg: extract route segment for this leg
                        full_route = other_route_data["route"]
                        refuel_ap = other_route_data.get("refuel_airport")

                        if refuel_ap:
                            # Use refuel_idx from route data if available (more reliable)
                            refuel_idx = other_route_data.get("refuel_idx", -1)
                            # Fallback: search for refuel airport in route
                            if refuel_idx < 0:
                                for ri, rwp in enumerate(full_route):
                                    if (
                                        str(rwp) == str(refuel_ap)
                                        and ri > 0
                                        and ri < len(full_route) - 1
                                    ):
                                        refuel_idx = ri
                                        break

                            if refuel_idx > 0:
                                # Split route into legs
                                if "_L1" in str(other_traj_id):
                                    other_route = full_route[
                                        : refuel_idx + 1
                                    ]  # Start -> Refuel (inclusive)
                                elif "_L2" in str(other_traj_id):
                                    other_route = full_route[
                                        refuel_idx:
                                    ]  # Refuel -> End (inclusive)
                                else:
                                    other_route = full_route
                            else:
                                other_route = full_route
                        else:
                            other_route = other_route_data["route"]
                    else:
                        other_route = other_route_data["route"]

                    other_frozen = other_route_data["frozen_segments"]

                    # Check drone capability (use parent drone ID)
                    if other_drone != current_drone:
                        other_cfg = drone_configs.get(other_drone, {})
                        if not target_allowed_for_drone(
                            target, other_drone, other_cfg, priority_filters
                        ):
                            continue

                    # Skip swapping within the same trajectory (targets can't move segments in their own trajectory)
                    if other_traj_id == traj_drone_id:
                        continue

                    # Check each route segment (between waypoints)
                    for j in range(len(other_route) - 1):
                        if j in other_frozen:
                            continue

                        # Skip current target's adjacent segments
                        # Only relevant if target exists in this route segment
                        if other_drone == current_drone:
                            # Find target's position in other_route (may differ from current_idx if using leg routes)
                            target_idx_in_other = -1
                            try:
                                target_idx_in_other = other_route.index(wp_id)
                            except ValueError:
                                pass  # Target not in this route/leg

                            if target_idx_in_other >= 0:
                                # Target is in this route/leg - skip adjacent segments
                                if (
                                    j == target_idx_in_other - 1
                                ):  # Segment: prev->target
                                    continue
                                if j == target_idx_in_other:  # Segment: target->next
                                    continue

                        seg_start_id = str(other_route[j])
                        seg_end_id = str(other_route[j + 1])

                        # For LINEAR routes (start != end airport), we previously skipped
                        # the last segment. But this is WRONG - inserting into T->A creates
                        # T->X->A which is valid. The insertion point (j+1) places the target
                        # BEFORE the ending airport, not after.
                        #
                        # For CYCLIC routes (start == end airport, e.g., A1->...->A1), the
                        # last segment returns to the start, and inserting before it is
                        # perfectly valid.
                        #
                        # Therefore, we should NOT skip the last segment in either case.
                        # The only invalid insertion would be at position len(route), which
                        # would place a target AFTER the end airport - but that's handled
                        # elsewhere in the insertion logic.

                        # Find trajectory indices for this route segment
                        # We need to check all trajectory vertices between these route waypoints
                        start_traj_idx = None
                        end_traj_idx = None

                        # Find start waypoint in trajectory
                        # Use tolerance of 0.1 to account for SAM boundary offsets and floating-point precision
                        start_wp_pos = waypoint_positions.get(seg_start_id)
                        if start_wp_pos:
                            for ti, tv in enumerate(other_trajectory):
                                if (
                                    abs(tv[0] - start_wp_pos[0]) < 0.1
                                    and abs(tv[1] - start_wp_pos[1]) < 0.1
                                ):
                                    start_traj_idx = ti
                                    break

                        # Find end waypoint in trajectory
                        # Search AFTER start waypoint to handle return-to-base routes (e.g., A1->...->A1)
                        end_wp_pos = waypoint_positions.get(seg_end_id)
                        if end_wp_pos and start_traj_idx is not None:
                            for ti in range(start_traj_idx + 1, len(other_trajectory)):
                                tv = other_trajectory[ti]
                                if (
                                    abs(tv[0] - end_wp_pos[0]) < 0.1
                                    and abs(tv[1] - end_wp_pos[1]) < 0.1
                                ):
                                    end_traj_idx = ti
                                    break

                        if start_traj_idx is None or end_traj_idx is None:
                            if str(wp_id) == "T9" and "2_L2" in str(other_traj_id):
                                print(
                                    f"      WARN T9->2_L2 {seg_start_id}->{seg_end_id}: traj_idx lookup failed (start={start_traj_idx}, end={end_traj_idx})"
                                )
                                print(
                                    f"         traj_len={len(other_trajectory)}, end_wp_pos={end_wp_pos}"
                                )
                                if start_traj_idx is not None and start_traj_idx < len(
                                    other_trajectory
                                ):
                                    print(
                                        f"         traj[{start_traj_idx}]={other_trajectory[start_traj_idx]}"
                                    )
                                    if start_traj_idx + 1 < len(other_trajectory):
                                        print(
                                            f"         traj[{start_traj_idx+1}]={other_trajectory[start_traj_idx+1]}"
                                        )
                            continue

                        # Check all trajectory sub-segments between these waypoints
                        # This includes SAM-avoidance tangent points
                        best_osd_in_segment = float("inf")

                        for ti in range(start_traj_idx, end_traj_idx):
                            traj_seg_start = other_trajectory[ti]
                            traj_seg_end = other_trajectory[ti + 1]

                            # Quick bounding box check (use remove_delta as radius)
                            min_x = (
                                min(traj_seg_start[0], traj_seg_end[0]) - remove_delta
                            )
                            max_x = (
                                max(traj_seg_start[0], traj_seg_end[0]) + remove_delta
                            )
                            min_y = (
                                min(traj_seg_start[1], traj_seg_end[1]) - remove_delta
                            )
                            max_y = (
                                max(traj_seg_start[1], traj_seg_end[1]) + remove_delta
                            )

                            if not (
                                min_x <= target_pos[0] <= max_x
                                and min_y <= target_pos[1] <= max_y
                            ):
                                continue  # Trajectory segment too far away

                            # Calculate OSD to this trajectory sub-segment (geometric check)
                            osd = self._point_to_line_distance(
                                target_pos, traj_seg_start, traj_seg_end
                            )
                            if osd < best_osd_in_segment:
                                best_osd_in_segment = osd

                        # Use the best (minimum) OSD found across all sub-segments
                        osd = best_osd_in_segment

                        if osd == float("inf"):
                            continue

                        # Only consider segments that are geometrically closer
                        if osd >= remove_delta:
                            continue

                        # Calculate insertion delta (using distance helper _d)
                        # Receiver's distances: at other_drone's altitude.
                        d_start = self._d(
                            seg_start_id, str(wp_id), waypoint_positions, other_drone
                        )
                        d_end = self._d(
                            str(wp_id), seg_end_id, waypoint_positions, other_drone
                        )
                        d_direct = self._d(
                            seg_start_id, seg_end_id, waypoint_positions, other_drone
                        )
                        if any(math.isinf(x) for x in (d_start, d_end, d_direct)):
                            continue
                        insertion_cost = d_start + d_end - d_direct

                        # Calculate loiter fuel for this target on receiving drone
                        loiter_extra = 0.0
                        other_cfg = drone_configs.get(other_drone, {})
                        if other_cfg.get("loiter_enabled"):
                            other_speed = float(other_cfg.get("speed", 1))
                            target_type = target.get("type", "recon")
                            lt = float(
                                other_cfg.get("loiter_times", {}).get(target_type, 0)
                            )
                            loiter_extra = lt * other_speed

                        total_insertion_cost = insertion_cost + loiter_extra

                        # Check fuel budget for target receiver drone
                        if other_drone != current_drone:
                            other_fuel_budget = effective_leg_budget(
                                other_route_data,
                                other_cfg,
                                default=200,
                            )

                            other_current_distance = other_route_data.get(
                                "distance"
                            ) or self._calculate_route_distance_with_loiter(
                                other_route, other_drone
                            )
                            to_new = other_current_distance + total_insertion_cost
                            if to_new > other_fuel_budget:
                                print(
                                    f"            FUEL FUEL: D{other_drone} {seg_start_id}->{seg_end_id} exceeds budget ({other_current_distance:.1f}+{total_insertion_cost:.1f} > {other_fuel_budget:.1f})"  # noqa: E501
                                )
                                continue
                        else:
                            # Same-drone swap: check per-leg fuel for refueling drones
                            refuel_ap = optimized_routes[current_drone].get(
                                "refuel_airport"
                            )
                            if refuel_ap and refuel_ap != "":
                                # Use refuel_idx from route data if available (more reliable)
                                refuel_idx = optimized_routes[current_drone].get(
                                    "refuel_idx", -1
                                )
                                # Fallback: search for refuel airport in route
                                if refuel_idx < 0:
                                    for ri, rwp in enumerate(route):
                                        if (
                                            rwp == refuel_ap
                                            and ri > 0
                                            and ri < len(route) - 1
                                        ):
                                            refuel_idx = ri
                                            break
                                if refuel_idx > 0:
                                    # Determine which leg source and destination are in
                                    source_in_leg1 = current_idx <= refuel_idx
                                    # j is the segment index; insertion is after seg_start (position j)
                                    dest_in_leg1 = j < refuel_idx

                                    # If moving between legs, check receiving leg capacity
                                    if source_in_leg1 != dest_in_leg1:
                                        route_cfg = drone_configs.get(current_drone, {})
                                        if dest_in_leg1:
                                            fuel_budget = effective_leg_budget(
                                                optimized_routes[current_drone],
                                                route_cfg,
                                                default=200,
                                            )
                                        else:
                                            fuel_budget = float(
                                                optimized_routes[current_drone].get(
                                                    "fuel_budget",
                                                    route_cfg.get("fuel_budget", 200),
                                                )
                                                or route_cfg.get("fuel_budget", 200)
                                            )

                                        if dest_in_leg1:
                                            # Moving to leg1
                                            leg_dist = optimized_routes[
                                                current_drone
                                            ].get("leg1_distance", 0)
                                        else:
                                            # Moving to leg2
                                            leg_dist = optimized_routes[
                                                current_drone
                                            ].get("leg2_distance", 0)

                                        # Check if receiving leg would exceed budget after insertion
                                        # (insertion_cost is the delta, but we also lose source_loiter_saved from source leg)
                                        new_leg_dist = leg_dist + total_insertion_cost
                                        if new_leg_dist > fuel_budget:
                                            print(
                                                f"            FUEL LEG FUEL: D{current_drone} leg{'1' if dest_in_leg1 else '2'} would exceed budget ({leg_dist:.1f}+{total_insertion_cost:.1f} > {fuel_budget:.1f})"  # noqa: E501
                                            )
                                            continue

                        # Calculate net savings including loiter:
                        # Source saves: remove_delta (travel) + source_loiter_saved
                        # Receiver pays: insertion_cost (travel) + loiter_extra
                        net_savings = (
                            remove_delta + source_loiter_saved
                        ) - total_insertion_cost

                        # Record candidate diagnostics
                        if debug:
                            candidate_info = {
                                "to_drone": other_drone,
                                "seg_start": seg_start_id,
                                "seg_end": seg_end_id,
                                "to_segment": j + 1,
                                "osd": osd,
                                "insert_delta": total_insertion_cost,
                                "loiter_saved": source_loiter_saved,
                                "loiter_extra": loiter_extra,
                                "net_savings": net_savings,
                                "fuel_ok": (
                                    True
                                    if (
                                        other_drone == current_drone
                                        or (
                                            other_route_data.get("fuel_budget", 0)
                                            or drone_configs.get(other_drone, {}).get(
                                                "fuel_budget", 0
                                            )
                                        )
                                        >= (
                                            other_route_data.get("distance")
                                            or self._calculate_route_distance_with_loiter(
                                                other_route, other_drone
                                            )
                                            + total_insertion_cost
                                        )
                                    )
                                    else False
                                ),
                            }
                            target_diagnostics[str(wp_id)]["candidates"].append(
                                candidate_info
                            )

                        # Track best option: select target with MAXIMUM net savings
                        if net_savings > best_net_savings:
                            print(
                                f"            REFRESH New best: D{other_drone} {seg_start_id}->{seg_end_id}, gain={net_savings:.2f} (remove={remove_delta:.2f} - insert={total_insertion_cost:.2f})"  # noqa: E501
                            )
                            best_net_savings = net_savings
                            best_osd = osd
                            best_insert_delta = total_insertion_cost
                            best_drone = other_drone
                            best_route_segment = j + 1  # Insert after seg_start

                # If found a better segment, add to candidates (don't execute yet)
                # Use net_savings which accounts for loiter on both source and receiver
                if best_drone and best_net_savings > 0:
                    print(
                        f"       LIST Candidate: {wp_id} from D{current_drone} to D{best_drone}, gain={best_net_savings:.2f}"
                    )

                    swap_candidates.append(
                        {
                            "target": wp_id,
                            "from_drone": current_drone,
                            "to_drone": best_drone,
                            "from_idx": current_idx,
                            "to_segment": best_route_segment,
                            "remove_delta": remove_delta,
                            "loiter_saved": source_loiter_saved,
                            "insert_delta": best_insert_delta,
                            "osd": best_osd,
                            "gain": best_net_savings,
                            "same_drone": (best_drone == current_drone),
                        }
                    )

            # CONSTRAINT GATE: drop any candidate swap whose resulting from/to
            # routes would violate ordering. Build the trial routes exactly as
            # the execution below would, then keep only legal candidates. FREE
            # (unconstrained) swaps are untouched.
            if swap_candidates and constraints:
                legal_candidates = []
                for cand in swap_candidates:
                    fd = cand["from_drone"]
                    td = cand["to_drone"]
                    fd_c = _constraints_for_route(constraints, fd)
                    td_c = _constraints_for_route(constraints, td)
                    if fd_c is None and td_c is None:
                        legal_candidates.append(cand)
                        continue
                    wp = cand["target"]
                    to_seg = cand["to_segment"]
                    from_idx_c = cand["from_idx"]
                    if fd == td:
                        # Same-drone move: remove then insert (mirror execution).
                        trial = list(optimized_routes[fd]["route"])
                        trial.remove(wp)
                        seg = to_seg - 1 if from_idx_c < to_seg else to_seg
                        trial.insert(seg, wp)
                        if fd_c is not None and not _ordering_legal(trial, fd_c):
                            continue
                    else:
                        from_trial = list(optimized_routes[fd]["route"])
                        from_trial.remove(wp)
                        to_trial = list(optimized_routes[td]["route"])
                        to_trial.insert(to_seg, wp)
                        if fd_c is not None and not _ordering_legal(from_trial, fd_c):
                            continue
                        if td_c is not None and not _ordering_legal(to_trial, td_c):
                            continue
                    legal_candidates.append(cand)
                swap_candidates = legal_candidates

            # After evaluating ALL targets, pick the SINGLE best swap
            if swap_candidates:
                # Sort by gain (descending) and pick the best one
                best_swap = max(swap_candidates, key=lambda x: x["gain"])

                print(
                    f"\n  BEST Selected best swap: {best_swap['target']} from D{best_swap['from_drone']} to D{best_swap['to_drone']}, gain={best_swap['gain']:.2f}"  # noqa: E501
                )

                # Execute only this one swap
                wp_id = best_swap["target"]
                from_drone = best_swap["from_drone"]
                to_drone = best_swap["to_drone"]
                to_segment = best_swap["to_segment"]
                from_idx = best_swap["from_idx"]

                # Remove from current route
                optimized_routes[from_drone]["route"].remove(wp_id)

                # For same-drone swaps, adjust insertion index if target was before insertion point
                # (removal shifted all subsequent indices down by 1)
                if from_drone == to_drone and from_idx < to_segment:
                    to_segment -= 1

                # Insert into new route
                optimized_routes[to_drone]["route"].insert(to_segment, wp_id)

                # Update refuel_idx for affected drones
                # When a target is removed from before refuel_idx, shift it down
                # When a target is inserted before/at refuel_idx, shift it up
                from_refuel_idx = optimized_routes[from_drone].get("refuel_idx", -1)
                if from_refuel_idx > 0 and from_idx < from_refuel_idx:
                    optimized_routes[from_drone]["refuel_idx"] = from_refuel_idx - 1
                    print(
                        f"      POINT D{from_drone} refuel_idx: {from_refuel_idx} -> {from_refuel_idx - 1} (target removed before refuel)"
                    )

                to_refuel_idx = optimized_routes[to_drone].get("refuel_idx", -1)
                if to_refuel_idx > 0 and to_segment <= to_refuel_idx:
                    # For same-drone swap, the removal already shifted refuel_idx, so check the updated value
                    if from_drone == to_drone:
                        to_refuel_idx = optimized_routes[to_drone].get("refuel_idx", -1)
                    optimized_routes[to_drone]["refuel_idx"] = to_refuel_idx + 1
                    print(
                        f"      POINT D{to_drone} refuel_idx: {to_refuel_idx} -> {to_refuel_idx + 1} (target inserted before refuel)"
                    )

                # Record the swap
                all_swaps.append(
                    {
                        "target": wp_id,
                        "from_drone": from_drone,
                        "to_drone": to_drone,
                        "remove_delta": best_swap.get("remove_delta"),
                        "insert_delta": best_swap.get("insert_delta"),
                        "osd": best_swap.get("osd"),
                        "savings": best_swap["gain"],
                        "same_drone": best_swap["same_drone"],
                        "pass": pass_num + 1,
                    }
                )
            else:
                print("\n  STOP  No beneficial swaps found in this pass")

        # Recalculate metrics for all routes
        for did, route_data in optimized_routes.items():
            route = route_data["route"]
            route_data["sequence"] = ",".join(route)
            # travel + loiter; matches what solver_bridge wrote on first solve
            # and what the animation displays as fuel used.
            drone_cfg = (self._drone_configs or {}).get(did, {})
            route_data["loiter_fuel"] = compute_route_loiter_fuel(
                route, drone_cfg, target_by_id
            )
            route_data["distance"] = self._calculate_route_distance_with_loiter(
                route, did, target_by_id
            )
            route_data["points"] = self._calculate_route_points(route, target_by_id)

            # Recalculate leg distances for refueling drones
            # Note: "-" means "any" (solver picked), so it IS a valid refuel airport
            refuel_ap = route_data.get("refuel_airport")
            if refuel_ap and refuel_ap != "":
                old_leg1 = route_data.get("leg1_distance", 0)
                old_leg2 = route_data.get("leg2_distance", 0)
                leg1, leg2 = recalculate_leg_distances(
                    route,
                    refuel_ap,
                    drone_cfg,
                    target_by_id,
                    functools.partial(self._calculate_route_distance, drone_id=did),
                )
                if leg1 > 0 or leg2 > 0:
                    print(
                        f"   REFRESH [Swap] D{did} leg recalc: old L1={old_leg1:.1f} L2={old_leg2:.1f} -> new L1={leg1:.1f} L2={leg2:.1f}"
                    )
                    route_data["leg1_distance"] = leg1
                    route_data["leg2_distance"] = leg2

        return {
            "sequences": {
                did: data["sequence"] for did, data in optimized_routes.items()
            },
            "routes": optimized_routes,
            "swaps_made": all_swaps,
            "passes": num_passes,
            "target_diagnostics": target_diagnostics if debug else {},
        }

    def _point_to_line_distance(
        self,
        point: Tuple[float, float],
        line_start: Tuple[float, float],
        line_end: Tuple[float, float],
    ) -> float:
        """
        Calculate perpendicular distance from a point to a line segment.
        """
        px, py = point
        x1, y1 = line_start
        x2, y2 = line_end

        # Line segment length squared
        line_len_sq = (x2 - x1) ** 2 + (y2 - y1) ** 2

        if line_len_sq == 0:
            # Line segment is a point
            return math.sqrt((px - x1) ** 2 + (py - y1) ** 2)

        # Project point onto line, clamped to segment
        t = max(
            0, min(1, ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / line_len_sq)
        )

        # Closest point on segment
        closest_x = x1 + t * (x2 - x1)
        closest_y = y1 + t * (y2 - y1)

        return math.sqrt((px - closest_x) ** 2 + (py - closest_y) ** 2)

    def _d(
        self,
        ida: str,
        idb: str,
        positions: Dict[str, Tuple[float, float]],
        drone_id: Optional[str] = None,
    ) -> float:
        """
        Distance metric between two waypoint ids for drone_id (at its cruise
        altitude). Prefer the SAM-aware distance matrix if available, otherwise
        fall back to Euclidean on provided positions.
        """
        # Use precomputed matrix if available
        dist_data = matrix_for_drone(self._distance_matrix, drone_id)
        if dist_data:
            labels = dist_data.get("labels", [])
            matrix = dist_data.get("matrix", [])
            try:
                idx_a = labels.index(ida)
                idx_b = labels.index(idb)
                return float(matrix[idx_a][idx_b])
            except Exception:
                # Per policy: If matrix exists but lookup failed, the matrix is incomplete
                # - fail loudly rather than corrupt distances with Euclidean fallback.
                raise RuntimeError(
                    f"Cannot compute SAM-aware distance for {ida}->{idb}: "
                    f"matrix lookup failed and Euclidean fallback is disabled (policy)."
                )

        # No distance matrix available: use Euclidean fallback
        # (This is acceptable since there's no SAM-aware data to corrupt)
        pa = positions.get(ida)
        pb = positions.get(idb)
        if pa and pb:
            return plane_dist(pa, pb)
        return float("inf")

    def _removal_delta(
        self,
        tid: str,
        route_data: Dict[str, Any],
        positions: Dict[str, Tuple[float, float]],
        drone_id: Optional[str] = None,
    ) -> Optional[float]:
        """
        Compute the delta (distance change) if `tid` is removed from the given route.
        Returns the extra distance currently incurred by visiting `tid` (positive means
        removing it reduces route distance). Returns None if removal is invalid/undefined.
        """
        route = route_data.get("route", [])
        if not route:
            return None
        try:
            i = route.index(tid)
        except ValueError:
            return None
        # cannot remove if at ends (shouldn't happen for targets if route has airports)
        if i <= 0 or i >= len(route) - 1:
            return None

        prev_id = route[i - 1]
        next_id = route[i + 1]
        if (
            str(prev_id) not in positions
            or str(next_id) not in positions
            or str(tid) not in positions
        ):
            return None

        _prev_p = positions[str(prev_id)]  # noqa: F841
        _t_p = positions[str(tid)]  # noqa: F841
        _next_p = positions[str(next_id)]  # noqa: F841

        # Use distance metric helper for consistency with insertion cost
        d_prev_t = self._d(str(prev_id), str(tid), positions, drone_id)
        d_t_next = self._d(str(tid), str(next_id), positions, drone_id)
        d_prev_next = self._d(str(prev_id), str(next_id), positions, drone_id)

        if any(math.isinf(x) for x in (d_prev_t, d_t_next, d_prev_next)):
            return None

        return d_prev_t + d_t_next - d_prev_next

    def _get_matrix_distance(
        self, from_id: str, to_id: str, drone_id: Optional[str] = None
    ) -> float:
        """
        Get SAM-aware distance between two waypoints from the distance matrix.

        Args:
            from_id: Source waypoint ID (e.g., 'A1', 'T5')
            to_id: Destination waypoint ID

        Returns:
            Distance from the pre-calculated SAM-aware matrix, or large fallback
        """
        dist_data = matrix_for_drone(self._distance_matrix, drone_id)
        if not dist_data:
            return 1000.0  # Large fallback if no matrix

        labels = dist_data.get("labels", [])
        matrix = dist_data.get("matrix", [])

        try:
            idx_from = labels.index(from_id)
            idx_to = labels.index(to_id)
            return matrix[idx_from][idx_to]
        except (ValueError, IndexError):
            return 1000.0  # Large fallback for missing entries

    def _point_to_polyline_distance(
        self, point: Tuple[float, float], polyline: List[Tuple[float, float]]
    ) -> float:
        """
        Calculate minimum distance from a point to a polyline (multiple connected segments).

        This is used to measure distance to actual SAM-avoiding trajectories,
        which may have bends/waypoints to navigate around SAM zones.

        Args:
            point: The point (x, y) to measure from
            polyline: List of (x, y) points forming the trajectory

        Returns:
            Minimum perpendicular distance to any segment of the polyline
        """
        if not polyline:
            return float("inf")

        if len(polyline) == 1:
            # Single point, return distance to that point
            px, py = point
            qx, qy = polyline[0]
            return math.sqrt((px - qx) ** 2 + (py - qy) ** 2)

        min_dist = float("inf")

        # Check distance to each segment in the polyline
        for i in range(len(polyline) - 1):
            seg_start = polyline[i]
            seg_end = polyline[i + 1]
            dist = self._point_to_line_distance(point, seg_start, seg_end)
            if dist < min_dist:
                min_dist = dist

        return min_dist

    def _calculate_route_distance(
        self, route: List[str], drone_id: Optional[str] = None
    ) -> float:
        """Calculate total distance of a route for drone_id (at its altitude)."""
        dist_data = matrix_for_drone(self._distance_matrix, drone_id)
        if len(route) < 2 or not dist_data:
            return 0.0

        labels = dist_data.get("labels", [])
        matrix = dist_data.get("matrix", [])

        total = 0.0
        for i in range(len(route) - 1):
            try:
                idx_from = labels.index(route[i])
                idx_to = labels.index(route[i + 1])
                total += matrix[idx_from][idx_to]
            except (ValueError, IndexError):
                total += 10.0  # Fallback

        return total

    def _calculate_route_distance_with_loiter(
        self,
        route: List[str],
        drone_id: str,
        target_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> float:
        """Travel distance + loiter fuel for this drone's route.

        Matches what solver_bridge wrote on first solve and what the animation
        displays as fuel used. Use this whenever writing back to
        route_data["distance"] so the field stays loiter-inclusive across
        pipeline passes.
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


# Global trajectory swap optimizer instance
_trajectory_optimizer = TrajectorySwapOptimizer()


def trajectory_swap_optimize(
    solution: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Dict[str, Any]],
    distance_matrix: Optional[Dict[str, Any]] = None,
    priority_constraints: Optional[str] = None,
    auto_iterate: bool = True,
    max_iterations: int = 50,
    auto_regen: bool = False,
    debug: bool = False,
    constraints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Reassign targets to drones whose trajectories pass closer to them.

    For each target, if another drone's path passes closer AND that drone
    can carry the target, move it to the closer trajectory.

    When auto_iterate=True (default):
    - Runs multiple iterations until convergence or cycle detection
    - Returns the solution with the lowest total distance encountered

    Args:
        solution: Current solution with drone routes
        env: Environment data with targets, airports
        drone_configs: Drone configurations with capabilities
        distance_matrix: Pre-calculated distance matrix
        priority_constraints: Optional priority constraints string
                              e.g., "D1,D2: priority>=6; D3,D4: priority<6"
        auto_iterate: If True (default), run until convergence/cycle and return best fuel state
        max_iterations: Maximum iterations for auto mode (default 50)

    Returns:
        Optimized solution with targets moved to closer trajectories
    """
    if distance_matrix:
        _trajectory_optimizer.set_distance_matrix(distance_matrix)

    return _trajectory_optimizer.optimize(
        solution,
        env,
        drone_configs,
        priority_constraints,
        auto_iterate=auto_iterate,
        max_iterations=max_iterations,
        auto_regen=auto_regen,
        debug=debug,
        constraints=constraints,
    )


def set_trajectory_optimizer_matrix(matrix_data: Dict[str, Any]):
    """Set the distance matrix for the trajectory swap optimizer."""
    _trajectory_optimizer.set_distance_matrix(matrix_data)


TRAJECTORY_SWAP_TOOL = {
    "name": "trajectory_swap_optimize",
    "description": """Reassign targets to drones whose trajectories pass closer to them.
    For each target, calculates its distance to the line connecting its neighbors
    in the current route. If another capable drone's trajectory passes closer,
    the target is moved to that drone. This reduces total travel distance.""",
    "parameters": {
        "type": "object",
        "properties": {
            "solution": {
                "type": "object",
                "description": "Current solution with drone routes",
            },
            "env": {
                "type": "object",
                "description": "Environment data with targets, airports",
            },
            "drone_configs": {
                "type": "object",
                "description": "Drone configurations with capabilities",
            },
            "distance_matrix": {
                "type": "object",
                "description": "Pre-calculated distance matrix (optional)",
            },
        },
        "required": ["solution", "env", "drone_configs"],
    },
    "function": trajectory_swap_optimize,
}
