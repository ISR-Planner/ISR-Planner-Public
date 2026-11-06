import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ..target_access import type_allowed


def _ordering_legal(route, constraints):
    """True when a full per-drone route satisfies ordering constraints."""
    if not constraints:
        return True
    pos = {node: i for i, node in enumerate(route)}
    first = constraints.get("first")
    if first is not None:
        if len(route) < 2 or route[1] != first:
            return False
    last = constraints.get("last")
    if last is not None:
        if len(route) < 2 or route[-2] != last:
            return False
    for a, b in constraints.get("before", []):
        if a in pos and b in pos and pos[a] > pos[b]:
            return False
    for y, x in constraints.get("adjacent", []):
        if y in pos and x in pos and pos[x] != pos[y] + 1:
            return False
    return True


def _parent_drone_id(did: str) -> str:
    """Physical-drone id for a possibly-virtual leg id. '1_L2' -> '1'."""
    s = str(did)
    if "_L" in s:
        return s.split("_L", 1)[0]
    return s


def _constraints_for_route(constraints, did):
    """Resolve ordering constraints for a physical or virtual drone route."""
    if not constraints:
        return None
    if any(k in constraints for k in ("first", "last", "before", "adjacent")):
        c = constraints
    else:
        phys = _parent_drone_id(did)
        c = constraints.get(str(did)) or constraints.get(str(phys))
        if not c:
            return None
    if not any(c.get(k) for k in ("first", "last", "before", "adjacent")):
        return None
    return {
        "first": c.get("first"),
        "last": c.get("last"),
        "before": c.get("before") or [],
        "adjacent": c.get("adjacent") or [],
    }


def _numeric_or_none(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def effective_leg_budget(
    route_data: Dict[str, Any],
    drone_config: Dict[str, Any],
    *,
    default: float = 0.0,
) -> float:
    """Fuel available for the route's current leg.

    Dynamic checkpoint replans pass reduced fuel as `start_fuel`. A numeric zero
    is meaningful and must not fall back to the full tank. If no start fuel is
    present, use the route/config tank budget.
    """
    start_fuel = _numeric_or_none((drone_config or {}).get("start_fuel"))
    if start_fuel is not None:
        return start_fuel

    route_budget = _numeric_or_none((route_data or {}).get("fuel_budget"))
    if route_budget is not None:
        return route_budget

    cfg_budget = _numeric_or_none((drone_config or {}).get("fuel_budget"))
    if cfg_budget is not None:
        return cfg_budget

    return float(default)


def parse_priority_constraint(constraint_str: str) -> Dict[str, Callable[[int], bool]]:
    """
    Parse priority constraints string into per-drone filter functions.

    Format: "D1,D2: priority>=6; D3,D4: priority<6"
    """
    if not constraint_str or not constraint_str.strip():
        return {}

    drone_filters: Dict[str, Callable[[int], bool]] = {}
    parts = [p.strip() for p in constraint_str.split(";") if p.strip()]

    for part in parts:
        if ":" not in part:
            continue

        drones_part, condition_part = part.split(":", 1)
        drones_part = drones_part.strip()
        condition_part = condition_part.strip().lower()

        drone_ids = []
        for d in drones_part.split(","):
            d = d.strip().upper()
            if d.startswith("D"):
                drone_ids.append(d[1:])
            else:
                drone_ids.append(d)

        match = re.search(r"priority\s*(>=|<=|>|<|=)\s*(\d+)", condition_part)
        if not match:
            continue

        op = match.group(1)
        threshold = int(match.group(2))

        def _make_filter(operator, thresh):
            def _fn(p):
                if operator == ">=":
                    return p >= thresh
                if operator == ">":
                    return p > thresh
                if operator == "<=":
                    return p <= thresh
                if operator == "<":
                    return p < thresh
                return p == thresh

            return _fn

        filter_fn = _make_filter(op, threshold)

        for did in drone_ids:
            drone_filters[did] = filter_fn

    return drone_filters


def target_allowed_for_drone(
    target: Dict[str, Any],
    drone_id: str,
    drone_config: Dict[str, Any],
    priority_filters: Optional[Dict[str, Callable[[int], bool]]] = None,
) -> bool:
    """Check if a target is allowed for a drone based on access constraints."""
    if drone_config.get("enabled") is False:
        return False

    if not type_allowed(drone_config, target):
        return False

    if priority_filters and drone_id in priority_filters:
        priority = int(target.get("priority", 5))
        filter_fn = priority_filters[drone_id]
        if not filter_fn(priority):
            return False

    return True


def compute_route_loiter_fuel(
    route: List[str],
    drone_cfg: Dict[str, Any],
    target_by_id: Dict[str, Dict[str, Any]],
) -> float:
    """Sum loiter fuel for all visited targets in a route.

    Units: the planner's time unit is the minute. `loiter_times` are minutes
    and `speed` is km per minute, so loiter fuel = minutes x km/min = the km
    the drone flies orbiting the target. (The Asset tab shows speed in km/h.)
    """
    if not drone_cfg.get("loiter_enabled"):
        return 0.0
    loiter_times = drone_cfg.get("loiter_times", {}) or {}
    speed = float(drone_cfg.get("speed", 1))
    total = 0.0
    for wp in route:
        if not (str(wp).startswith("T") and not str(wp).startswith("TK")):
            continue
        t = target_by_id.get(str(wp))
        if not t:
            continue
        t_type = (t.get("type") or "a").lower()
        total += float(loiter_times.get(t_type, 0)) * speed
    return total


def rebuild_waypoint_indices(
    route: List[str],
    trajectory: List[Any],
    waypoint_positions: Dict[str, Tuple[float, float]],
    tolerance: float = 0.01,
) -> Dict[int, int]:
    """Rebuild waypoint_indices by searching trajectory for waypoint coords."""
    if not route or not trajectory:
        return {}

    indices: Dict[int, int] = {0: 0}
    traj_search_start = 0

    for route_idx, wp_id in enumerate(route):
        if route_idx == 0:
            continue

        wp_pos = waypoint_positions.get(wp_id)
        if not wp_pos:
            continue

        for traj_idx in range(traj_search_start, len(trajectory)):
            traj_pt = trajectory[traj_idx]
            if len(traj_pt) >= 2:
                dx = abs(traj_pt[0] - wp_pos[0])
                dy = abs(traj_pt[1] - wp_pos[1])
                if dx < tolerance and dy < tolerance:
                    indices[route_idx] = traj_idx
                    traj_search_start = traj_idx + 1
                    break

    return indices


def collect_route_stats(solution: Dict[str, Any]) -> Tuple[int, float]:
    """Return (distinct target count, total route distance) for a solution."""
    visited: Set[str] = set()
    total_fuel = 0.0
    for _did, rd in (solution.get("routes") or {}).items():
        for wp in rd.get("route", []):
            if str(wp).startswith("T") and not str(wp).startswith("TK"):
                visited.add(str(wp))
        total_fuel += float(rd.get("distance", 0.0) or 0.0)
    return len(visited), total_fuel
