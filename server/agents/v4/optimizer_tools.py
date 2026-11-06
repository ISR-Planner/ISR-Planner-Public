"""Optimizer tool callbacks for the v4 ISR agent workflow."""

from typing import Any, Dict, List, Optional, cast

from langchain_core.tools import tool

from ...solver.optimizers.crossing_removal import crossing_removal_optimize
from ...solver.optimizers.post_insert import post_optimize_solution
from ...solver.optimizers.trajectory_swap import trajectory_swap_optimize
from ..drone_config import resolve_fuel_budget
from .state import get_state

# ============================================================================
# OPTIMIZER TOOLS - Post-optimization operations
# ============================================================================


def _state_dict(state: Any, key: str) -> Dict[str, Any]:
    value = state.get(key)
    return value if isinstance(value, dict) else {}


def _drone_configs_from_state(state: Any) -> Dict[str, Dict[str, Any]]:
    return cast(Dict[str, Dict[str, Any]], state.get("drone_configs") or {})


def _routes_from_state(state: Any) -> Dict[str, Dict[str, Any]]:
    return cast(Dict[str, Dict[str, Any]], state.get("routes") or {})


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _matrix_data_from_state(state: Any) -> Optional[Dict[str, Any]]:
    dist_matrix = state.get("distance_matrix")
    if not isinstance(dist_matrix, dict) or not dist_matrix:
        return None
    labels = list(dist_matrix.keys())
    matrix = [[dist_matrix[f].get(t, 1000) for t in labels] for f in labels]
    return {"labels": labels, "matrix": matrix}


@tool
def insert_unvisited_tool() -> str:
    """
    Insert unvisited targets into drone routes.

    This optimization finds targets that were not assigned to any drone
    and inserts them into routes where:
    - The drone has remaining fuel capacity
    - The drone is allowed to visit that target type
    - The insertion minimizes additional fuel cost

    Only applies the optimization if it improves efficiency (increases points covered).

    Returns:
        Updated routes with unvisited targets inserted, or rejection message if no improvement.
    """
    state = get_state()
    env = _state_dict(state, "environment")
    configs = _drone_configs_from_state(state)
    routes = _routes_from_state(state)

    if not routes:
        return "ERROR: No routes to optimize"

    # Build solution dict
    targets = {str(t.get("id", t.get("label"))): t for t in env.get("targets", [])}
    solution: Dict[str, Any] = {"routes": {}}

    for did, route_data in routes.items():
        route = _string_list(route_data.get("route", []))
        cfg = configs.get(did, {})
        points = sum(targets[wp].get("priority", 5) for wp in route if wp in targets)
        distance = route_data.get("distance", 0)
        # CRITICAL: Include frozen_segments so optimizer won't modify them
        frozen_segments = route_data.get("frozen_segments", [])

        solution["routes"][did] = {
            "route": route,
            "points": points,
            "distance": distance,
            "fuel_budget": resolve_fuel_budget(cfg, drone_id=str(did)),
            "frozen_segments": frozen_segments,
        }

    # Build matrix
    matrix_data = _matrix_data_from_state(state)

    # Calculate initial metrics
    initial_fuel = sum(r.get("distance", 0) for r in solution["routes"].values())
    initial_points = sum(r.get("points", 0) for r in solution["routes"].values())

    # Run post_optimize_solution which handles inserting unvisited targets
    optimized = post_optimize_solution(solution, env, configs, matrix_data)

    # Calculate optimized metrics
    optimized_fuel = sum(
        r.get("distance", 0) for r in optimized.get("routes", {}).values()
    )
    optimized_points = sum(
        r.get("points", 0) for r in optimized.get("routes", {}).values()
    )

    # Count targets before and after
    original_visited = set()
    for did, route_data in routes.items():
        for wp in route_data.get("route", []):
            if wp in targets:
                original_visited.add(wp)

    optimized_visited = set()
    for did, route_data in optimized.get("routes", {}).items():
        for wp in route_data.get("route", []):
            if wp in targets:
                optimized_visited.add(wp)

    inserted_count = len(optimized_visited) - len(original_visited)

    # Check if optimization improves efficiency
    improves_points = optimized_points > initial_points
    same_points_less_fuel = (
        optimized_points == initial_points and optimized_fuel < initial_fuel
    )

    if not (improves_points or same_points_less_fuel):
        # Reject optimization - no improvement
        all_target_ids = set(targets.keys())
        remaining_unvisited = len(all_target_ids) - len(original_visited)
        return f"""INSERT UNVISITED OPTIMIZATION REJECTED

Reason: Optimization does not improve efficiency
Points: {initial_points} → {optimized_points} (no increase)
Fuel: {initial_fuel:.1f} → {optimized_fuel:.1f}
Targets that could be inserted: {inserted_count}
Remaining unvisited: {remaining_unvisited}

Original routes preserved (no changes applied)."""

    # Update state - optimization improves efficiency
    new_routes: Dict[str, Dict[str, Any]] = {}
    for did, route_data in optimized.get("routes", {}).items():
        route = _string_list(route_data.get("route", []))
        new_routes[did] = {
            "route": route,
            "sequence": ",".join(route),
            "points": route_data.get("points", 0),
            "distance": route_data.get("distance", 0),
            "fuel_budget": route_data.get("fuel_budget", 200),
            "frozen_segments": route_data.get("frozen_segments", []),
        }
    state["routes"] = new_routes

    # Count remaining unvisited
    all_target_ids = set(targets.keys())
    remaining_unvisited = len(all_target_ids) - len(optimized_visited)

    return f"""INSERT UNVISITED TARGETS COMPLETE

Targets inserted: {inserted_count}
Points gained: {optimized_points - initial_points} ({initial_points} → {optimized_points})
Fuel change: {optimized_fuel - initial_fuel:.1f} ({initial_fuel:.1f} → {optimized_fuel:.1f})
Remaining unvisited: {remaining_unvisited}

Optimization applied successfully."""


@tool
def swap_closer_tool() -> str:
    """
    Run trajectory swap optimization - moves targets to closer drone trajectories.

    This specifically does the "Swap Closer" optimization where targets are
    reassigned to drones whose trajectory passes closer to them.

    Only applies the optimization if it improves efficiency (reduces fuel).

    Returns:
        Optimized routes with swap improvements, or rejection message if no improvement.
    """
    state = get_state()
    env = _state_dict(state, "environment")
    configs = _drone_configs_from_state(state)
    routes = _routes_from_state(state)

    if not routes:
        return "ERROR: No routes to optimize"

    # Build solution dict
    targets = {str(t.get("id", t.get("label"))): t for t in env.get("targets", [])}
    solution: Dict[str, Any] = {"routes": {}}

    for did, route_data in routes.items():
        route = _string_list(route_data.get("route", []))
        cfg = configs.get(did, {})
        points = sum(targets[wp].get("priority", 5) for wp in route if wp in targets)
        distance = route_data.get("distance", 0)
        # CRITICAL: Include frozen_segments so optimizer won't modify them
        frozen_segments = route_data.get("frozen_segments", [])

        solution["routes"][did] = {
            "route": route,
            "points": points,
            "distance": distance,
            "fuel_budget": resolve_fuel_budget(cfg, drone_id=str(did)),
            "frozen_segments": frozen_segments,
        }

    # Build matrix
    matrix_data = _matrix_data_from_state(state)

    # Calculate initial metrics
    initial_fuel = sum(r.get("distance", 0) for r in solution["routes"].values())
    initial_points = sum(r.get("points", 0) for r in solution["routes"].values())

    # Run swap optimization only
    optimized = trajectory_swap_optimize(solution, env, configs, matrix_data)

    # Calculate optimized metrics
    optimized_fuel = sum(
        r.get("distance", 0) for r in optimized.get("routes", {}).values()
    )
    optimized_points = sum(
        r.get("points", 0) for r in optimized.get("routes", {}).values()
    )

    # Check if optimization improves efficiency
    improves_fuel = optimized_fuel < initial_fuel
    maintains_points = optimized_points >= initial_points

    if not (improves_fuel and maintains_points):
        # Reject optimization - no improvement
        return f"""SWAP CLOSER OPTIMIZATION REJECTED

Reason: Optimization does not improve efficiency
Fuel: {initial_fuel:.1f} → {optimized_fuel:.1f} (would {'save' if improves_fuel else 'increase'} {abs(initial_fuel - optimized_fuel):.1f})  # noqa: E501
Points: {initial_points} → {optimized_points}

Original routes preserved (no changes applied)."""

    # Update state - optimization improves efficiency
    new_routes: Dict[str, Dict[str, Any]] = {}
    for did, route_data in optimized.get("routes", {}).items():
        route = _string_list(route_data.get("route", []))
        new_routes[did] = {
            "route": route,
            "sequence": ",".join(route),
            "points": route_data.get("points", 0),
            "distance": route_data.get("distance", 0),
            "fuel_budget": route_data.get("fuel_budget", 200),
            "frozen_segments": route_data.get("frozen_segments", []),
        }
    state["routes"] = new_routes

    return f"""SWAP CLOSER OPTIMIZATION COMPLETE

Fuel saved: {initial_fuel - optimized_fuel:.1f} ({initial_fuel:.1f} → {optimized_fuel:.1f})
Points: {initial_points} → {optimized_points}

Optimization applied successfully."""


@tool
def remove_crossings_tool() -> str:
    """
    Run 2-opt crossing removal optimization.

    This removes self-crossings in drone trajectories where segments
    cross over each other. Uses the 2-opt algorithm.

    Only applies the optimization if it improves efficiency (reduces fuel).

    Returns:
        Optimized routes with crossings removed, or rejection message if no improvement.
    """
    state = get_state()
    env = _state_dict(state, "environment")
    configs = _drone_configs_from_state(state)
    routes = _routes_from_state(state)

    if not routes:
        return "ERROR: No routes to optimize"

    # Build solution dict
    targets = {str(t.get("id", t.get("label"))): t for t in env.get("targets", [])}
    solution: Dict[str, Any] = {"routes": {}}

    for did, route_data in routes.items():
        route = _string_list(route_data.get("route", []))
        cfg = configs.get(did, {})
        points = sum(targets[wp].get("priority", 5) for wp in route if wp in targets)
        distance = route_data.get("distance", 0)
        # CRITICAL: Include frozen_segments so optimizer won't modify them
        frozen_segments = route_data.get("frozen_segments", [])

        solution["routes"][did] = {
            "route": route,
            "points": points,
            "distance": distance,
            "fuel_budget": resolve_fuel_budget(cfg, drone_id=str(did)),
            "frozen_segments": frozen_segments,
        }

    # Build matrix
    matrix_data = _matrix_data_from_state(state)

    # Calculate initial metrics
    initial_fuel = sum(r.get("distance", 0) for r in solution["routes"].values())
    initial_points = sum(r.get("points", 0) for r in solution["routes"].values())

    # Run crossing removal only
    optimized = crossing_removal_optimize(solution, env, configs, matrix_data)

    # Calculate optimized metrics
    optimized_fuel = sum(
        r.get("distance", 0) for r in optimized.get("routes", {}).values()
    )
    optimized_points = sum(
        r.get("points", 0) for r in optimized.get("routes", {}).values()
    )

    # Check if optimization improves efficiency
    improves_fuel = optimized_fuel < initial_fuel
    maintains_points = optimized_points >= initial_points

    if not (improves_fuel and maintains_points):
        # Reject optimization - no improvement
        return f"""CROSSING REMOVAL OPTIMIZATION REJECTED

Reason: Optimization does not improve efficiency
Fuel: {initial_fuel:.1f} → {optimized_fuel:.1f} (would {'save' if improves_fuel else 'increase'} {abs(initial_fuel - optimized_fuel):.1f})  # noqa: E501
Points: {initial_points} → {optimized_points}

Original routes preserved (no changes applied)."""

    # Update state - optimization improves efficiency
    new_routes: Dict[str, Dict[str, Any]] = {}
    for did, route_data in optimized.get("routes", {}).items():
        route = _string_list(route_data.get("route", []))
        new_routes[did] = {
            "route": route,
            "sequence": ",".join(route),
            "points": route_data.get("points", 0),
            "distance": route_data.get("distance", 0),
            "fuel_budget": route_data.get("fuel_budget", 200),
            "frozen_segments": route_data.get("frozen_segments", []),
        }
    state["routes"] = new_routes

    return f"""CROSSING REMOVAL COMPLETE

Fuel saved: {initial_fuel - optimized_fuel:.1f} ({initial_fuel:.1f} → {optimized_fuel:.1f})
Points: {initial_points} → {optimized_points}

Optimization applied successfully."""


# Define optimizer tools list for binding
OPTIMIZER_TOOLS = [insert_unvisited_tool, swap_closer_tool, remove_crossings_tool]
