"""Single-leg solver wrapper used by refuel and mission orchestration."""

import copy
from typing import Any, Dict, List, Optional

from .airport_rules import _airport_accepts_drone, is_real_airport


def solve_one_leg(
    env_for_solver: Dict[str, Any],
    fuel_budget: float,
    start_id: str,
    end_id: Optional[str],
    targets: List[Dict[str, Any]],
    did: str,
    leg_name: str,
    flexible_endpoint: bool = False,
    drone_cfg: Optional[Dict[str, Any]] = None,
    constraints: Optional[Dict[str, Any]] = None,
    solver=None,
):
    """
    Solve a single leg from start to end and return (route, visited, distance).

    Loiter fuel for visited targets is folded into the returned distance. If the
    solver route exceeds the leg budget after loiter is added, the lowest-priority
    loiter target is dropped and the leg is solved again.
    """
    if solver is None:
        raise RuntimeError("solve_one_leg requires an initialized solver interface")

    if not targets:
        labels = (
            env_for_solver.get("matrix_labels") or env_for_solver.get("labels") or []
        )
        matrix = (
            env_for_solver.get("matrix") or env_for_solver.get("distance_matrix") or []
        )

        resolved_end = end_id
        if resolved_end is None:
            valid_ends = [
                e
                for e in (env_for_solver.get("valid_end_airports") or [])
                if e in labels and e != start_id
            ]
            if valid_ends:
                try:
                    start_idx = labels.index(start_id)
                    resolved_end = min(
                        valid_ends,
                        key=lambda e: float(matrix[start_idx][labels.index(e)]),
                    )
                except (ValueError, IndexError, TypeError):
                    resolved_end = valid_ends[0]
            else:
                resolved_end = start_id

        dist = 0.0
        if start_id != resolved_end:
            try:
                i = labels.index(start_id)
                j = labels.index(resolved_end)
                dist = float(matrix[i][j])
            except (ValueError, IndexError, TypeError) as exc:
                raise RuntimeError(
                    "_solve_one_leg empty-targets path: cannot find SAM-aware "
                    f"distance for {start_id}->{resolved_end} in matrix. "
                    "Refusing to fall back to Euclidean (policy)."
                ) from exc
        route = [start_id] if start_id == resolved_end else [start_id, resolved_end]
        return route, set(), dist

    loiter_cost = {}
    if drone_cfg and drone_cfg.get("loiter_enabled"):
        speed = float(drone_cfg.get("speed", 1))
        loiter_times_cfg = drone_cfg.get("loiter_times", {})
        for t in targets:
            target_type = (t.get("type") or "a").lower()
            loiter_time = float(loiter_times_cfg.get(target_type, 0))
            if loiter_time > 0:
                loiter_cost[str(t["id"])] = loiter_time * speed

    current_targets = list(targets)
    max_attempts = len(targets) + 1

    for _attempt in range(max_attempts):
        env_leg = copy.deepcopy(env_for_solver)
        env_leg["targets"] = current_targets
        env_leg["start_airport"] = start_id

        if flexible_endpoint:
            env_leg["mode"] = "best_end"
            env_leg.pop("end_airport", None)
            if "valid_end_airports" not in env_leg:
                drone_runway = str((drone_cfg or {}).get("runway", "S")).upper()
                real_ap_ids = [
                    a["id"]
                    for a in env_leg.get("airports", [])
                    if is_real_airport(a)
                    and _airport_accepts_drone(a.get("runway", "L"), drone_runway)
                ]
                if real_ap_ids:
                    env_leg["valid_end_airports"] = real_ap_ids
        else:
            env_leg.pop("valid_end_airports", None)
            env_leg["end_airport"] = end_id
            env_leg["mode"] = "return" if end_id == start_id else "end"

        target_ids = {str(t["id"]) for t in current_targets}
        airport_ids = {str(a["id"]) for a in env_leg.get("airports", [])}
        allowed_ids = airport_ids | target_ids

        orig_labels = env_leg.get("matrix_labels") or env_leg.get("labels") or []
        orig_matrix = env_leg.get("matrix") or env_leg.get("distance_matrix") or []

        if orig_labels and orig_matrix:
            keep = [i for i, lab in enumerate(orig_labels) if lab in allowed_ids]
            new_labels = [orig_labels[i] for i in keep]
            new_matrix = [[orig_matrix[i][j] for j in keep] for i in keep]

            for key in ("matrix_labels", "labels"):
                if key in env_leg:
                    env_leg[key] = new_labels
            for key in ("matrix", "distance_matrix"):
                if key in env_leg:
                    env_leg[key] = new_matrix
            if "waypoints" in env_leg:
                env_leg["waypoints"] = [
                    wp for wp in env_leg["waypoints"] if wp["id"] in allowed_ids
                ]

        sol = solver.solve(env_leg, fuel_budget, constraints=constraints)
        route = sol.get("route", [])
        visited = set(sol.get("visited_targets", []))
        travel_distance = float(sol.get("distance", 0.0))

        actual_loiter = sum(loiter_cost.get(tid, 0) for tid in visited)
        total_distance = travel_distance + actual_loiter

        if total_distance <= fuel_budget or not visited:
            if actual_loiter > 0:
                print(
                    f"   D{did} {leg_name}: travel={travel_distance:.1f} + "
                    f"loiter={actual_loiter:.1f} = {total_distance:.1f} / "
                    f"{fuel_budget}",
                    flush=True,
                )
            return route, visited, total_distance

        visited_with_loiter = [
            (tid, loiter_cost.get(tid, 0))
            for tid in visited
            if loiter_cost.get(tid, 0) > 0
        ]
        if not visited_with_loiter:
            return route, visited, total_distance

        target_map = {str(t["id"]): t for t in current_targets}
        visited_with_loiter.sort(
            key=lambda item: (
                int(target_map.get(item[0], {}).get("priority", 5)),
                -item[1],
            )
        )
        drop_tid = visited_with_loiter[0][0]
        drop_cost = visited_with_loiter[0][1]

        print(
            f"   D{did} {leg_name}: over budget ({total_distance:.1f} > "
            f"{fuel_budget}), dropping {drop_tid} (loiter={drop_cost:.1f})",
            flush=True,
        )
        current_targets = [t for t in current_targets if str(t["id"]) != drop_tid]

        if not current_targets:
            return [start_id, end_id or start_id], set(), 0.0

    return route, visited, total_distance
