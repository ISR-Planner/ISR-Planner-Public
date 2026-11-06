"""Post-solve route timing and simple time-to-target synchronization."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .altitude import matrix_for_drone
from .route_timeline import route_clock_inputs


def _target_ids(env: Dict[str, Any]) -> set[str]:
    return {str(t.get("id")) for t in env.get("targets", []) if t.get("id")}


def _target_by_id(env: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(t.get("id")): t for t in env.get("targets", []) if t.get("id")}


def _matrix_lookup(
    distance_matrix: Optional[Dict[str, Any]],
) -> Dict[Tuple[str, str], float]:
    if not distance_matrix:
        return {}
    labels = [str(label) for label in distance_matrix.get("labels", [])]
    matrix = distance_matrix.get("matrix", [])
    lookup: Dict[Tuple[str, str], float] = {}
    for i, from_id in enumerate(labels):
        if i >= len(matrix):
            continue
        row = matrix[i]
        for j, to_id in enumerate(labels):
            if j >= len(row):
                continue
            try:
                lookup[(from_id, to_id)] = float(row[j])
            except (TypeError, ValueError):
                continue
    return lookup


def _leg_distance(
    from_id: str,
    to_id: str,
    matrix_lookup: Dict[Tuple[str, str], float],
) -> float:
    return matrix_lookup.get((from_id, to_id), matrix_lookup.get((to_id, from_id), 0.0))


def _service_time(
    waypoint_id: str,
    drone_cfg: Dict[str, Any],
    target_by_id: Dict[str, Dict[str, Any]],
) -> float:
    if not drone_cfg.get("loiter_enabled"):
        return 0.0
    target = target_by_id.get(str(waypoint_id))
    if not target:
        return 0.0
    target_type = str(target.get("type") or "a").lower()
    try:
        return float((drone_cfg.get("loiter_times") or {}).get(target_type, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _schedule_route(
    route: List[str],
    drone_cfg: Dict[str, Any],
    target_by_id: Dict[str, Dict[str, Any]],
    matrix_lookup: Dict[Tuple[str, str], float],
    wait_before: Optional[Dict[str, float]] = None,
    start_delay: float = 0.0,
    travel_spans: Optional[List[float]] = None,
    hold_distances: Optional[Dict[int, float]] = None,
) -> Dict[str, Any]:
    speed = float(drone_cfg.get("speed") or 1.0)
    if speed <= 0:
        speed = 1.0
    wait_before = wait_before or {}

    elapsed = max(0.0, float(start_delay or 0.0))
    arrival_times: Dict[str, float] = {}
    departure_times: Dict[str, float] = {}
    service_times: Dict[str, float] = {}
    wait_times: Dict[str, float] = {}
    waypoint_times: List[Dict[str, Any]] = []

    previous: Optional[str] = None
    for index, waypoint in enumerate(route):
        waypoint_id = str(waypoint)
        travel_distance = 0.0
        travel_time = 0.0
        if previous is not None:
            travel_distance = (
                travel_spans[index - 1]
                if travel_spans is not None
                else _leg_distance(previous, waypoint_id, matrix_lookup)
            )
            travel_time = travel_distance / speed
            elapsed += travel_time

        wait_time = max(0.0, float(wait_before.get(waypoint_id, 0.0) or 0.0))
        if wait_time:
            elapsed += wait_time

        service = (
            hold_distances.get(index, 0.0) / speed
            if hold_distances is not None
            else _service_time(waypoint_id, drone_cfg, target_by_id)
        )
        arrival = elapsed
        departure = arrival + service

        arrival_times[waypoint_id] = arrival
        departure_times[waypoint_id] = departure
        service_times[waypoint_id] = service
        if wait_time:
            wait_times[waypoint_id] = wait_time

        waypoint_times.append(
            {
                "index": index,
                "waypoint": waypoint_id,
                "arrival": arrival,
                "departure": departure,
                "travel_from_previous": travel_time,
                "travel_distance_from_previous": travel_distance,
                "wait_before": wait_time,
                "service_time": service,
            }
        )

        elapsed = departure
        previous = waypoint_id

    return {
        "arrival_times": arrival_times,
        "departure_times": departure_times,
        "service_times": service_times,
        "wait_times": wait_times,
        "waypoint_times": waypoint_times,
        "mission_time": elapsed,
    }


def _store_schedule(route_data: Dict[str, Any], schedule: Dict[str, Any]) -> None:
    """Round display metadata only; synchronization uses the unrounded clock."""
    for key in ("arrival_times", "departure_times", "service_times", "wait_times"):
        route_data[key] = {wp: round(value, 3) for wp, value in schedule[key].items()}
    route_data["waypoint_times"] = [
        {
            key: round(value, 3) if isinstance(value, float) else value
            for key, value in row.items()
        }
        for row in schedule["waypoint_times"]
    ]
    route_data["mission_time"] = round(schedule["mission_time"], 3)


def _clear_previous_sync(route_data: Dict[str, Any]) -> None:
    """Re-synchronize the flown route, without carrying an earlier wait/fuel."""
    if route_data.get("sync_wait_strategy") == "hover_wait":
        old_wait = float(route_data.get("sync_wait_fuel") or 0.0)
        route_data["distance"] = max(
            0.0, float(route_data.get("distance") or 0.0) - old_wait
        )
    for field in (
        "arrival_times",
        "departure_times",
        "service_times",
        "wait_times",
        "waypoint_times",
        "mission_time",
        "sync_target",
        "sync_wait_strategy",
        "sync_wait_time",
        "launch_delay",
        "sync_wait_fuel",
    ):
        route_data.pop(field, None)
    # Rebuilt by the response adapter from the unchanged travel/loiter inputs.
    route_data.pop("anim_frame", None)


def _first_target(route: List[str], target_ids: set[str]) -> Optional[str]:
    for waypoint in route:
        waypoint_id = str(waypoint)
        if waypoint_id in target_ids:
            return waypoint_id
    return None


def _selected_sync_members(
    *,
    routes: Dict[str, Any],
    target_ids: set[str],
    options: Dict[str, Any],
    natural: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    members: List[Dict[str, Any]] = []
    warnings: List[str] = []
    raw_members = options.get("members") or []
    if not isinstance(raw_members, list):
        return [], ["Selected sync members must be a list."]

    seen: set[Tuple[str, str]] = set()
    for raw in raw_members:
        if not isinstance(raw, dict):
            warnings.append("Ignored malformed selected sync member.")
            continue
        did = str(raw.get("drone_id") or raw.get("drone") or "").lstrip("D")
        waypoint = str(
            raw.get("target_id") or raw.get("waypoint") or raw.get("target") or ""
        )
        if not did or not waypoint:
            warnings.append(
                "Ignored selected sync member with missing drone or target."
            )
            continue
        if waypoint not in target_ids:
            warnings.append(
                f"D{did} selected sync target {waypoint} is not a known target."
            )
            continue
        route_data = routes.get(did) or routes.get(str(did))
        if not isinstance(route_data, dict):
            warnings.append(f"D{did} has no route for selected sync target {waypoint}.")
            continue
        route = [str(wp) for wp in route_data.get("route", [])]
        if waypoint not in route:
            warnings.append(
                f"D{did} route does not include selected sync target {waypoint}."
            )
            continue
        schedule = natural.get(did)
        if not schedule:
            warnings.append(
                f"D{did} has no timing schedule for selected sync target {waypoint}."
            )
            continue
        key = (did, waypoint)
        if key in seen:
            continue
        seen.add(key)
        members.append(
            {
                "drone_id": did,
                "waypoint": waypoint,
                "natural_arrival": schedule["arrival_times"].get(waypoint, 0.0),
            }
        )

    return members, warnings


def _sync_wait_fuel_check(
    *,
    route_data: Dict[str, Any],
    drone_cfg: Dict[str, Any],
    route: List[str],
    waypoint: str,
    wait_fuel: float,
    base_distance: float,
) -> Optional[str]:
    if wait_fuel <= 0:
        return None

    fuel_budget = float(
        route_data.get("fuel_budget", drone_cfg.get("fuel_budget", 0.0)) or 0.0
    )
    if fuel_budget <= 0:
        return None

    try:
        waypoint_index = route.index(str(waypoint))
    except ValueError:
        waypoint_index = 0

    leg_distances = list(route_data.get("leg_distances") or [])
    refuel_indices = [
        int(i)
        for i in (route_data.get("refuel_indices") or [])
        if isinstance(i, (int, float))
    ]
    if leg_distances and refuel_indices:
        leg_index = sum(1 for idx in refuel_indices if idx < waypoint_index)
        if leg_index < len(leg_distances):
            base_leg_distance = float(leg_distances[leg_index] or 0.0)
            full_tank = float(drone_cfg.get("fuel_budget", fuel_budget) or fuel_budget)
            # A numeric 0 is real (a landed, parked drone) — only None means "full tank".
            raw_start_fuel = drone_cfg.get("start_fuel")
            start_fuel = full_tank if raw_start_fuel is None else float(raw_start_fuel)
            leg_budget = start_fuel if leg_index == 0 else full_tank
            synced_leg_distance = base_leg_distance + wait_fuel
            if synced_leg_distance > leg_budget + 1e-6:
                return (
                    f"sync wait exceeds leg {leg_index + 1} fuel budget "
                    f"({synced_leg_distance:.2f} > {leg_budget:.2f})."
                )
            return None

    synced_distance = base_distance + wait_fuel
    if synced_distance > fuel_budget + 1e-6:
        return (
            f"exceeds fuel budget after sync wait "
            f"({synced_distance:.2f} > {fuel_budget:.2f})."
        )
    return None


def apply_time_sync(
    *,
    solution: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    distance_matrix: Optional[Dict[str, Any]],
    options: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Attach route timing and synchronize first target arrivals when enabled.

    The MVP synchronization mode delays early drones before their first target so
    all enabled drones with at least one target arrive at their first target at
    the same mission time. Routes are not reordered.
    """
    if not options or not options.get("enabled"):
        return solution

    routes = solution.get("routes") or {}
    targets = _target_ids(env)
    target_by_id = _target_by_id(env)
    try:
        tolerance = max(0.0, float(options.get("tolerance", 1.0)))
    except (TypeError, ValueError):
        tolerance = 1.0
    requested_mode = str(options.get("mode") or "first_target_arrival")
    raw_mode = "selected_targets" if options.get("members") else requested_mode
    strategy_from_mode = (
        raw_mode if raw_mode in {"launch_delay", "hover_wait"} else None
    )
    mode = "first_target_arrival" if strategy_from_mode else raw_mode
    if mode not in {"first_target_arrival", "selected_targets"}:
        mode = "first_target_arrival"
    wait_strategy = str(
        options.get("wait_strategy")
        or options.get("strategy")
        or strategy_from_mode
        or "launch_delay"
    )
    if wait_strategy not in {"launch_delay", "hover_wait"}:
        wait_strategy = "launch_delay"

    for route_data in routes.values():
        if isinstance(route_data, dict):
            _clear_previous_sync(route_data)

    natural: Dict[str, Dict[str, Any]] = {}
    clocks: Dict[str, Dict[str, Any]] = {}
    sync_members: List[Dict[str, Any]] = []

    for did, route_data in routes.items():
        did_str = str(did)
        if not isinstance(route_data, dict):
            continue
        cfg = drone_configs.get(did_str) or drone_configs.get(did) or {}
        if cfg.get("enabled") is False:
            continue
        route = [str(wp) for wp in route_data.get("route", [])]
        spans, holds = route_clock_inputs(
            route_data, distance_matrix, did_str, env, cfg
        )
        clock = {
            "matrix_lookup": _matrix_lookup(matrix_for_drone(distance_matrix, did_str)),
            "travel_spans": spans,
            "hold_distances": holds,
        }
        clocks[did_str] = clock
        schedule = _schedule_route(route, cfg, target_by_id, **clock)
        natural[did_str] = schedule
        if mode == "first_target_arrival":
            first_target = _first_target(route, targets)
            if first_target:
                sync_members.append(
                    {
                        "drone_id": did_str,
                        "waypoint": first_target,
                        "natural_arrival": schedule["arrival_times"].get(
                            first_target, 0.0
                        ),
                    }
                )

    selection_warnings: List[str] = []
    if mode == "selected_targets":
        sync_members, selection_warnings = _selected_sync_members(
            routes=routes,
            target_ids=targets,
            options=options,
            natural=natural,
        )

    if len(sync_members) < 2:
        for did, schedule in natural.items():
            _store_schedule(routes[did], schedule)
        reason = (
            "Need at least two valid selected drone-target pairs."
            if mode == "selected_targets"
            else "Need at least two enabled routes with target visits."
        )
        solution["time_sync"] = {
            "enabled": True,
            "mode": mode,
            "wait_strategy": wait_strategy,
            "status": "skipped",
            "reason": reason,
            "tolerance": tolerance,
            "synchronized_waypoints": sync_members,
            "warnings": selection_warnings,
        }
        return solution

    sync_time = max(member["natural_arrival"] for member in sync_members)
    synchronized: List[Dict[str, Any]] = []
    warnings: List[str] = list(selection_warnings)
    feasible = True

    for member in sync_members:
        did = member["drone_id"]
        route_data = routes[did]
        cfg = drone_configs.get(did) or {}
        waypoint = member["waypoint"]
        natural_arrival = float(member["natural_arrival"])
        route = [str(wp) for wp in route_data.get("route", [])]
        base_distance = float(route_data.get("distance", 0.0) or 0.0)
        # The latest natural arrival anchors the synchronization window. Earlier
        # drones only wait long enough to fall within `tolerance` of that anchor;
        # a zero tolerance retains exact simultaneous arrival.
        wait_time = max(0.0, sync_time - natural_arrival - tolerance)
        start_delay = wait_time if wait_strategy == "launch_delay" else 0.0
        hover_wait = wait_time if wait_strategy == "hover_wait" else 0.0
        schedule = _schedule_route(
            route,
            cfg,
            target_by_id,
            **clocks[did],
            wait_before={waypoint: hover_wait},
            start_delay=start_delay,
        )
        speed = float(cfg.get("speed") or 1.0)
        if speed <= 0:
            speed = 1.0
        wait_fuel = hover_wait * speed
        synchronized_arrival = schedule["arrival_times"].get(waypoint, sync_time)
        error = abs(float(synchronized_arrival) - sync_time)

        _store_schedule(route_data, schedule)
        route_data["sync_target"] = waypoint
        route_data["sync_wait_strategy"] = wait_strategy
        route_data["sync_wait_time"] = round(wait_time, 3)
        route_data["launch_delay"] = start_delay
        route_data["sync_wait_fuel"] = wait_fuel
        if wait_strategy == "hover_wait":
            route_data["distance"] = base_distance + wait_fuel
        else:
            route_data["distance"] = base_distance

        if error > tolerance + 1e-6:
            feasible = False
            warnings.append(
                f"D{did} {waypoint} arrival is outside tolerance "
                f"({error:.2f} > {tolerance:.2f})."
            )
        if wait_strategy == "hover_wait":
            fuel_warning = _sync_wait_fuel_check(
                route_data=route_data,
                drone_cfg=cfg,
                route=route,
                waypoint=waypoint,
                wait_fuel=wait_fuel,
                base_distance=base_distance,
            )
            if fuel_warning:
                feasible = False
                warnings.append(f"D{did} {fuel_warning}")

        synchronized.append(
            {
                "drone_id": did,
                "waypoint": waypoint,
                "natural_arrival": round(natural_arrival, 3),
                "synchronized_arrival": round(float(synchronized_arrival), 3),
                "wait_time": round(wait_time, 3),
                "launch_delay": round(start_delay, 3),
                "wait_fuel": round(wait_fuel, 3),
            }
        )

    for did, schedule in natural.items():
        if did not in {member["drone_id"] for member in sync_members}:
            _store_schedule(routes[did], schedule)

    solution["time_sync"] = {
        "enabled": True,
        "mode": mode,
        "wait_strategy": wait_strategy,
        "status": "synchronized" if feasible else "infeasible",
        "tolerance": tolerance,
        "sync_time": round(sync_time, 3),
        "synchronized_waypoints": synchronized,
        "warnings": warnings,
    }
    return solution
