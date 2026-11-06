import copy
import math
from typing import Any, Dict, List, Optional

from server.solver.altitude import matrix_for_drone
from server.solver.optimizers.common import (
    _constraints_for_route,
    _ordering_legal,
    effective_leg_budget,
)


def insert_missed_optimize(
    solution: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Dict[str, Any]],
    distance_matrix: Optional[Dict[str, Any]] = None,
    constraints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Insert unvisited targets into existing routes using SAM-aware distances.

    Pure function: does not mutate the caller's solution.
    """
    try:
        routes = copy.deepcopy(solution.get("routes", {}))
        targets = env.get("targets", [])
        target_by_id = {str(t["id"]): t for t in targets}

        visited = set()
        for _did, rd in routes.items():
            for wp in rd.get("route", []):
                if str(wp).startswith("T") and not str(wp).startswith("TK"):
                    visited.add(str(wp))

        all_target_ids = {str(t["id"]) for t in targets}
        unvisited = all_target_ids - visited

        if not unvisited:
            return {
                "success": True,
                "routes": routes,
                "sequences": {
                    d: r.get("sequence", ",".join(r.get("route", [])))
                    for d, r in routes.items()
                },
                "insertions": [],
            }

        unvisited_sorted = sorted(
            unvisited,
            key=lambda tid: int(target_by_id.get(tid, {}).get("priority", 5)),
            reverse=True,
        )

        # Each drone's distances and unreachable targets are at its altitude.
        lookups: Dict[str, Any] = {}

        def lookup(did):
            if did not in lookups:
                dm = matrix_for_drone(distance_matrix, did) or {}
                lookups[did] = (
                    {str(lbl): i for i, lbl in enumerate(dm.get("labels", []))},
                    dm.get("matrix", []),
                    {str(t) for t in dm.get("excluded_targets", [])},
                )
            return lookups[did]

        # Unreachable for every drone: never worth trying.
        excluded_targets = set(
            str(t) for t in (distance_matrix or {}).get("excluded_targets", [])
        )
        for other in ((distance_matrix or {}).get("by_altitude") or {}).values():
            excluded_targets &= {str(t) for t in other.get("excluded_targets", [])}

        def get_dist(from_id, to_id, did):
            label_to_idx, matrix_data, _ = lookup(did)
            fi, ti = label_to_idx.get(from_id), label_to_idx.get(to_id)
            if fi is not None and ti is not None:
                return matrix_data[fi][ti]
            return float("inf")

        drone_fuel: Dict[str, Dict[str, Any]] = {}
        for did, cfg in drone_configs.items():
            if not cfg.get("enabled"):
                continue
            rd = routes.get(did, {})
            route = rd.get("route", [])
            fuel_budget = float(
                rd.get("fuel_budget")
                if rd.get("fuel_budget") is not None
                else cfg.get("fuel_budget", 0) or 0
            )
            start_budget = effective_leg_budget(rd, cfg)

            refuel_ap = rd.get("refuel_airport") or cfg.get("refuel_airport")
            if not refuel_ap or refuel_ap in ("", "-"):
                stops = rd.get("refuel_stops") or cfg.get("refuel_stops") or []
                stops = [s for s in stops if s and s != "-"]
                if stops:
                    refuel_ap = stops[0]

            if refuel_ap and refuel_ap != "" and refuel_ap != "-":
                legs = rd.get("leg_distances")
                if isinstance(legs, list) and len(legs) >= 1:
                    leg1_dist = float(legs[0] or 0)
                    leg2_dist = float(legs[1] or 0) if len(legs) >= 2 else 0.0
                else:
                    leg1_dist = float(rd.get("leg1_distance") or 0)
                    leg2_dist = float(rd.get("leg2_distance") or 0)

                refuel_idx = rd.get("refuel_idx")
                if refuel_idx is None:
                    refuel_indices = rd.get("refuel_indices")
                    refuel_idx = (
                        refuel_indices[0]
                        if isinstance(refuel_indices, list) and refuel_indices
                        else -1
                    )
                if refuel_idx is None or refuel_idx < 0:
                    refuel_idx = -1
                    for i in range(1, len(route) - 1):
                        if route[i] == refuel_ap:
                            refuel_idx = i
                            break

                if refuel_idx < 0 and len(route) >= 2:
                    total_dist = rd.get("distance", 0)
                    leg1_dist = total_dist
                    leg2_dist = 0
                    rd["leg1_distance"] = total_dist
                    rd["leg2_distance"] = 0
                    rd["refuel_airport"] = refuel_ap
                    refuel_idx = len(route) - 2

                drone_fuel[did] = {
                    "type": "refuel",
                    "budget": fuel_budget,
                    "leg1_remaining": start_budget - leg1_dist,
                    "leg2_remaining": fuel_budget - leg2_dist,
                    "refuel_idx": refuel_idx,
                    "refuel_ap": refuel_ap,
                }
            else:
                distance_used = rd.get("distance", 0)
                drone_fuel[did] = {
                    "type": "normal",
                    "budget": start_budget,
                    "remaining": start_budget - distance_used,
                }

        insertions: List[Dict[str, Any]] = []
        remaining_unvisited = [
            tid
            for tid in unvisited_sorted
            if tid not in excluded_targets and tid in target_by_id
        ]

        while remaining_unvisited:
            best_tid: Optional[str] = None
            best_drone: Optional[str] = None
            best_idx: Optional[int] = None
            best_cost = float("inf")
            best_leg: Optional[str] = None
            best_loiter_extra = 0.0
            best_key = None

            for tid in remaining_unvisited:
                target = target_by_id[tid]
                target_type = (target.get("type") or "a").lower()
                priority = int(target.get("priority", 5))

                for did, cfg in drone_configs.items():
                    if not cfg.get("enabled"):
                        continue

                    access = cfg.get("target_access", {})
                    if access and not access.get(target_type, False):
                        continue

                    fuel_info = drone_fuel.get(did)
                    if not fuel_info:
                        continue
                    if tid in lookup(did)[2]:
                        continue  # inside a SAM at this drone's altitude

                    rd = routes.get(did, {})
                    route = rd.get("route", [])
                    if not route or len(route) < 2:
                        continue

                    loiter_extra = 0.0
                    if cfg.get("loiter_enabled"):
                        speed = float(cfg.get("speed", 1))
                        lt = float(cfg.get("loiter_times", {}).get(target_type, 0))
                        loiter_extra = lt * speed

                    for idx in range(1, len(route)):
                        prev_wp = route[idx - 1]
                        next_wp = route[idx]

                        orig_dist = get_dist(prev_wp, next_wp, did)
                        new_dist = get_dist(prev_wp, tid, did) + get_dist(
                            tid, next_wp, did
                        )

                        if math.isinf(new_dist) or math.isnan(new_dist):
                            continue
                        if math.isinf(orig_dist) or math.isnan(orig_dist):
                            continue

                        travel_cost = new_dist - orig_dist
                        total_cost = travel_cost + loiter_extra

                        if total_cost < 0:
                            total_cost = 0

                        if fuel_info["type"] == "refuel":
                            refuel_idx = fuel_info["refuel_idx"]
                            if refuel_idx < 0:
                                break
                            if idx <= refuel_idx:
                                leg_remaining = fuel_info["leg1_remaining"]
                                leg = "L1"
                            else:
                                leg_remaining = fuel_info["leg2_remaining"]
                                leg = "L2"
                            if total_cost > leg_remaining:
                                continue
                        else:
                            if total_cost > fuel_info["remaining"]:
                                continue
                            leg = None

                        did_c = _constraints_for_route(constraints, did)
                        if did_c is not None:
                            trial = route[:idx] + [tid] + route[idx:]
                            if not _ordering_legal(trial, did_c):
                                continue

                        candidate_key = (
                            -priority,
                            total_cost,
                            str(tid),
                            str(did),
                            idx,
                        )
                        if best_key is None or candidate_key < best_key:
                            best_key = candidate_key
                            best_tid = tid
                            best_drone = did
                            best_idx = idx
                            best_cost = total_cost
                            best_leg = leg
                            best_loiter_extra = loiter_extra

            if not best_tid or not best_drone or best_idx is None:
                break

            rd = routes[best_drone]
            route = list(rd.get("route", []))
            fuel_info = drone_fuel[best_drone]

            if fuel_info["type"] == "refuel" and best_leg == "L2":
                refuel_ap = fuel_info["refuel_ap"]
                refuel_in_route = False
                for i in range(1, len(route) - 1):
                    if route[i] == refuel_ap:
                        refuel_in_route = True
                        break
                if not refuel_in_route:
                    refuel_insert_pos = fuel_info["refuel_idx"] + 1
                    route.insert(refuel_insert_pos, refuel_ap)
                    if best_idx >= refuel_insert_pos:
                        best_idx += 1
                    fuel_info["refuel_idx"] = refuel_insert_pos

            route.insert(best_idx, best_tid)
            rd["route"] = route
            rd["sequence"] = ",".join(route)

            if fuel_info["type"] == "refuel":
                if best_leg == "L1":
                    fuel_info["leg1_remaining"] -= best_cost
                    fuel_info["refuel_idx"] += 1
                    rd["refuel_idx"] = fuel_info["refuel_idx"]
                    rd["leg1_distance"] = rd.get("leg1_distance", 0) + best_cost
                else:
                    fuel_info["leg2_remaining"] -= best_cost
                    rd["leg2_distance"] = rd.get("leg2_distance", 0) + best_cost
            else:
                fuel_info["remaining"] -= best_cost
            rd["distance"] = rd.get("distance", 0) + best_cost
            rd["loiter_fuel"] = rd.get("loiter_fuel", 0.0) + best_loiter_extra

            insertions.append({"target": best_tid, "drone": best_drone})
            remaining_unvisited.remove(best_tid)

        return {
            "success": True,
            "routes": routes,
            "sequences": {
                d: r.get("sequence", ",".join(r.get("route", [])))
                for d, r in routes.items()
            },
            "insertions": insertions,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
