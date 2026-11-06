"""Mission metric calculations for the v4 ISR agent workflow."""

from typing import Any, Dict, List

from ..drone_config import resolve_fuel_budget


def compute_mission_metrics(
    env: Dict[str, Any],
    routes: Dict[str, Any],
    allocations: Dict[str, List[str]],
    drone_configs: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compute numerical mission metrics:

    - per-drone fuel usage and margins
    - per-drone points collected
    - total fuel / points
    - visited vs unvisited targets
    """
    targets = {t["id"]: t for t in env.get("targets", [])}
    all_target_ids = set(targets.keys())

    per_drone = {}
    total_fuel = 0.0
    total_points = 0
    visited_targets = set()

    for did, route_info in (routes or {}).items():
        # v4 routes normally look like: {"route": [...], "distance": float, "total_points": int}
        if isinstance(route_info, dict):
            route = route_info.get("route") or []
            distance = float(route_info.get("distance", 0.0))
            # Check both "total_points" (v4 standard) and "points" (legacy)
            points = int(
                route_info.get("total_points") or route_info.get("points") or 0
            )
        else:
            # Fallback if some legacy format leaks through
            route = route_info or []
            distance = 0.0
            points = 0

        # Determine visited targets from route (skip airports)
        visited_on_route = [node for node in route if node.startswith("T")]
        visited_targets.update(visited_on_route)

        # Fuel usage = distance (1:1 by design)
        fuel_used = distance

        # Fuel budget and margin
        cfg = drone_configs.get(str(did)) or drone_configs.get(did) or {}
        budget = resolve_fuel_budget(cfg, drone_id=str(did))
        margin = budget - fuel_used

        per_drone[did] = {
            "route": route,
            "fuel_used": fuel_used,
            "fuel_budget": budget,
            "fuel_margin": margin,
            "points": points,
            "visited_targets": visited_on_route,
        }

        total_fuel += fuel_used
        total_points += points

    unvisited_targets = sorted(all_target_ids - visited_targets)

    return {
        "per_drone": per_drone,
        "total_fuel": total_fuel,
        "total_points": total_points,
        "visited_targets": sorted(visited_targets),
        "unvisited_targets": unvisited_targets,
    }
