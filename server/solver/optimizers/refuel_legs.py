import copy
from typing import Any, Callable, Dict, List, Optional, Tuple

from server.solver.optimizers.common import (
    compute_route_loiter_fuel,
    rebuild_waypoint_indices,
)


def recalculate_leg_distances(
    route: List[str],
    refuel_airport: str,
    drone_cfg: Dict[str, Any],
    target_by_id: Dict[str, Dict[str, Any]],
    distance_calculator: Callable[[List[str]], float],
    known_refuel_idx: int = -1,
) -> Tuple[float, float]:
    """Recalculate leg1_distance and leg2_distance for a refueling drone."""
    if not refuel_airport or refuel_airport == "-":
        return (0.0, 0.0)

    refuel_idx = known_refuel_idx
    if refuel_idx < 0:
        for i, wp in enumerate(route):
            if str(wp) == str(refuel_airport) and i > 0 and i < len(route) - 1:
                refuel_idx = i
                break

    if refuel_idx < 0:
        return (0.0, 0.0)

    leg1_route = route[: refuel_idx + 1]
    leg2_route = route[refuel_idx:]

    leg1_travel = distance_calculator(leg1_route)
    leg2_travel = distance_calculator(leg2_route)
    leg1_loiter = compute_route_loiter_fuel(leg1_route, drone_cfg, target_by_id)
    leg2_loiter = compute_route_loiter_fuel(leg2_route, drone_cfg, target_by_id)

    leg1_total = leg1_travel + leg1_loiter
    leg2_total = leg2_travel + leg2_loiter
    print(
        f"   [LegRecalc] refuel_ap={refuel_airport} "
        f"at idx={refuel_idx}/{len(route)-1}",
        flush=True,
    )
    print(f"      leg1_route={leg1_route}, leg2_route={leg2_route}")
    print(
        f"      leg1={leg1_travel:.1f}+{leg1_loiter:.1f}={leg1_total:.1f}, "
        f"leg2={leg2_travel:.1f}+{leg2_loiter:.1f}={leg2_total:.1f}"
    )
    return (leg1_total, leg2_total)


def split_refueling_drones(
    solution: Dict[str, Any],
    drone_configs: Dict[str, Dict[str, Any]],
    env: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]], Dict[str, str]]:
    """
    Split legacy two-leg refueling drones into virtual leg-drones.

    The production pipeline now uses `virtual_drones.split_solution_to_virtual_legs`
    for N-leg refuel support. This function remains for compatibility.
    """
    routes = solution.get("routes", {})
    split_routes: Dict[str, Any] = {}
    split_configs: Dict[str, Dict[str, Any]] = {}
    leg_mapping: Dict[str, str] = {}

    waypoint_positions: Dict[str, Tuple[float, float]] = {}
    if env:
        for t in env.get("targets", []):
            tid = t.get("id")
            if tid:
                x = t.get("x", t.get("lng", 0))
                y = t.get("y", t.get("lat", 0))
                waypoint_positions[tid] = (x, y)
        for a in env.get("airports", []):
            aid = a.get("id")
            if aid:
                x = a.get("x", a.get("lng", 0))
                y = a.get("y", a.get("lat", 0))
                waypoint_positions[aid] = (x, y)
        print(
            "   [split_refueling_drones] Built waypoint_positions with "
            f"{len(waypoint_positions)} entries",
            flush=True,
        )
    else:
        print(
            "   [split_refueling_drones] env is None/empty - "
            "cannot build waypoint_positions",
            flush=True,
        )

    for did, cfg in drone_configs.items():
        if not cfg.get("enabled"):
            continue

        rd = routes.get(did, {})
        route = rd.get("route", [])
        refuel_ap = rd.get("refuel_airport") or cfg.get("refuel_airport")

        if not refuel_ap or refuel_ap == "":
            split_routes[did] = copy.deepcopy(rd)
            split_configs[did] = copy.deepcopy(cfg)
            leg_mapping[did] = did
            continue

        fuel_budget = rd.get("fuel_budget", 0) or cfg.get("fuel_budget", 0)

        refuel_idx = -1
        for i in range(1, len(route) - 1):
            if route[i] == refuel_ap:
                refuel_idx = i
                break

        if refuel_idx < 0:
            leg1_route = route
            leg2_route = [refuel_ap, route[-1]] if len(route) >= 1 else [refuel_ap]
            leg1_dist = rd.get("distance", 0)
            leg2_dist = 0
            leg1_start = route[0] if route else refuel_ap
            leg1_end = refuel_ap
            leg2_start = refuel_ap
            leg2_end = route[-1] if route else refuel_ap
        else:
            leg1_route = route[: refuel_idx + 1]
            leg2_route = route[refuel_idx:]
            leg1_dist = rd.get("leg1_distance", 0)
            leg2_dist = rd.get("leg2_distance", 0)
            leg1_start = route[0]
            leg1_end = refuel_ap
            leg2_start = refuel_ap
            leg2_end = route[-1]

        traj = rd.get("trajectory", [])
        waypoint_indices = rd.get("waypoint_indices", {})

        print(
            f"   [TrajSplit] D{did}: waypoint_indices={bool(waypoint_indices)} "
            f"({len(waypoint_indices) if waypoint_indices else 0} keys), "
            f"traj={len(traj)} pts, waypoint_positions={len(waypoint_positions)} entries",
            flush=True,
        )

        if not waypoint_indices and traj and waypoint_positions:
            if traj and route:
                first_wp = route[0]
                first_wp_pos = waypoint_positions.get(first_wp)
                first_traj_pt = traj[0] if traj else None
                print(
                    f"   [TrajSplit] D{did}: route[0]={first_wp}, "
                    f"wp_pos={first_wp_pos}, "
                    f"traj[0]={first_traj_pt[:2] if first_traj_pt else None}",
                    flush=True,
                )
            waypoint_indices = rebuild_waypoint_indices(route, traj, waypoint_positions)
            if waypoint_indices:
                print(
                    f"   [TrajSplit] D{did}: Rebuilt waypoint_indices from env: "
                    f"{waypoint_indices}",
                    flush=True,
                )

        if traj and refuel_idx > 0:
            traj_split = waypoint_indices.get(
                str(refuel_idx), waypoint_indices.get(refuel_idx, -1)
            )

            if traj_split >= 0:
                print(
                    f"   [TrajSplit] D{did}: Using waypoint_indices[{refuel_idx}] = "
                    f"{traj_split}",
                    flush=True,
                )
            else:
                traj_split = int((refuel_idx / len(route)) * len(traj))
                print(
                    f"   [TrajSplit] D{did}: No waypoint_indices, "
                    f"using proportional split at {traj_split}",
                    flush=True,
                )

            traj_split = max(1, min(traj_split, len(traj) - 1))
            leg1_traj = traj[: traj_split + 1]
            leg2_traj = traj[traj_split:]
            print(
                f"   [TrajSplit] D{did}: traj_split={traj_split}, "
                f"leg1_traj={len(leg1_traj)} pts, "
                f"leg2_traj={len(leg2_traj)} pts",
                flush=True,
            )
        else:
            leg1_traj = traj
            leg2_traj = []

        leg1_id = f"{did}_L1"
        leg2_id = f"{did}_L2"

        split_routes[leg1_id] = {
            "route": leg1_route,
            "sequence": ",".join(leg1_route),
            "distance": leg1_dist,
            "fuel_budget": fuel_budget,
            "trajectory": leg1_traj,
            "points": sum(
                1
                for w in leg1_route
                if str(w).startswith("T") and not str(w).startswith("TK")
            )
            * 5,
            "_virtual_leg": True,
            "_parent_drone": did,
            "_leg_number": 1,
            "_refuel_airport": refuel_ap,
        }
        split_configs[leg1_id] = {
            **copy.deepcopy(cfg),
            "start_airport": leg1_start,
            "end_airport": leg1_end,
            "refuel_airport": "",
        }
        leg_mapping[leg1_id] = did

        split_routes[leg2_id] = {
            "route": leg2_route,
            "sequence": ",".join(leg2_route),
            "distance": leg2_dist,
            "fuel_budget": fuel_budget,
            "trajectory": leg2_traj,
            "points": sum(
                1
                for w in leg2_route
                if str(w).startswith("T") and not str(w).startswith("TK")
            )
            * 5,
            "_virtual_leg": True,
            "_parent_drone": did,
            "_leg_number": 2,
            "_refuel_airport": refuel_ap,
        }
        split_configs[leg2_id] = {
            **copy.deepcopy(cfg),
            "start_airport": leg2_start,
            "end_airport": leg2_end,
            "refuel_airport": "",
        }
        leg_mapping[leg2_id] = did

        print(
            f"   Split D{did} into {leg1_id} "
            f"({len(leg1_route)} wps, {leg1_dist:.1f}km) and {leg2_id} "
            f"({len(leg2_route)} wps, {leg2_dist:.1f}km)",
            flush=True,
        )

    split_solution = {
        "routes": split_routes,
        "distance_matrix": solution.get("distance_matrix"),
    }

    return split_solution, split_configs, leg_mapping


def merge_virtual_legs(
    solution: Dict[str, Any],
    original_drone_configs: Dict[str, Dict[str, Any]],
    leg_mapping: Dict[str, str],
) -> Dict[str, Any]:
    """Merge legacy two-leg virtual drones back into physical drones."""
    routes = solution.get("routes", {})
    merged_routes: Dict[str, Any] = {}

    parent_legs: Dict[str, Dict[int, Dict[str, Any]]] = {}
    passthrough_routes: Dict[str, Any] = {}

    for vid, rd in routes.items():
        if rd.get("_virtual_leg"):
            parent_id = rd.get("_parent_drone")
            leg_num = rd.get("_leg_number")
            if parent_id not in parent_legs:
                parent_legs[parent_id] = {}
            parent_legs[parent_id][leg_num] = rd
        else:
            passthrough_routes[vid] = rd

    for parent_id, legs in parent_legs.items():
        leg1 = legs.get(1, {})
        leg2 = legs.get(2, {})

        leg1_route = leg1.get("route", [])
        leg2_route = leg2.get("route", [])
        refuel_ap = leg1.get("_refuel_airport") or leg2.get("_refuel_airport")

        if leg1_route and leg2_route:
            merged_route = list(leg1_route)
            if not (merged_route and merged_route[-1] == refuel_ap):
                merged_route.append(refuel_ap)
            if leg2_route and leg2_route[0] == refuel_ap:
                merged_route.extend(leg2_route[1:])
            else:
                merged_route.extend(leg2_route)
        elif leg1_route:
            merged_route = list(leg1_route)
        elif leg2_route:
            merged_route = list(leg2_route)
        else:
            merged_route = []

        leg1_traj = leg1.get("trajectory", [])
        leg2_traj = leg2.get("trajectory", [])
        if leg1_traj and leg2_traj:
            merged_traj = list(leg1_traj) + list(leg2_traj[1:])
        else:
            merged_traj = leg1_traj or leg2_traj

        leg1_dist = leg1.get("distance", 0)
        leg2_dist = leg2.get("distance", 0)
        fuel_budget = leg1.get("fuel_budget", 0) or leg2.get("fuel_budget", 0)

        merged_routes[parent_id] = {
            "route": merged_route,
            "sequence": ",".join(merged_route),
            "distance": leg1_dist + leg2_dist,
            "fuel_budget": fuel_budget,
            "trajectory": merged_traj,
            "points": (leg1.get("points", 0) or 0) + (leg2.get("points", 0) or 0),
            "refuel_airport": refuel_ap,
            "leg1_distance": leg1_dist,
            "leg2_distance": leg2_dist,
        }

        print(
            f"   Merged D{parent_id}: L1({len(leg1_route)} wps, "
            f"{leg1_dist:.1f}km) + L2({len(leg2_route)} wps, "
            f"{leg2_dist:.1f}km) -> {len(merged_route)} wps",
            flush=True,
        )

    merged_routes.update(passthrough_routes)

    result = dict(solution)
    result["routes"] = merged_routes
    return result
