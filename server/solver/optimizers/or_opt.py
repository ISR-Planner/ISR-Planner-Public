from typing import Any, Dict, List, Optional

from server.solver.altitude import matrix_for_drone
from server.solver.optimizers.common import (
    _constraints_for_route,
    _ordering_legal,
    compute_route_loiter_fuel,
    target_allowed_for_drone,
)


def or_opt_optimize(
    solution: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Dict[str, Any]],
    distance_matrix: Optional[Dict[str, Any]] = None,
    max_segment_len: int = 3,
    max_passes: int = 8,
    constraints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Or-opt local search: relocate CONTIGUOUS runs of targets (length 1..L).

    SWAP moves one target at a time and so misses beneficial multi-target moves
    (e.g. a cluster the solver already sequenced together that belongs on another
    drone/leg). Or-opt complements it: for every contiguous run of targets of
    length 1..max_segment_len, try removing it and re-inserting it (forward AND
    reversed) at every gap in EVERY route - including across the refuel boundary
    when routes are virtual legs. Keep the best improving move; repeat until no
    pass improves. Cost is O(L * n^2) per pass - no subset/permutation blow-up.

    Operates on whatever routes `solution` carries; the endpoint splits a refuel
    drone into per-leg virtual drones first (each leg is a drone), so a run can
    move from one leg to another. All costs are matrix distances; moves respect
    target-type access (target_allowed_for_drone) and per-route fuel budget.

    Returns a solution dict with the optimized routes + an "or_opt_moves" list.
    """
    targets_by_id = {str(t["id"]): t for t in env.get("targets", [])}
    # (labels index, matrix) per drone: each drone's distances are at its altitude.
    lookups: Dict[str, Any] = {}

    def lookup(did: str):
        if did not in lookups:
            dm = matrix_for_drone(distance_matrix, did) or {}
            labels = dm.get("labels", [])
            lookups[did] = (
                {str(l): i for i, l in enumerate(labels)},
                dm.get("matrix", []),
            )
        return lookups[did]

    def D(a: str, b: str, did: str) -> float:
        idx, matrix = lookup(did)
        ia, ib = idx.get(str(a)), idx.get(str(b))
        if ia is None or ib is None:
            return float("inf")  # unknown pair -> never choose this move
        return float(matrix[ia][ib])

    def route_travel(route: List[str], did: str) -> float:
        return sum(D(route[i], route[i + 1], did) for i in range(len(route) - 1))

    def route_cost(route: List[str], did: str) -> float:
        # A leg's TRUE fuel = travel + loiter. or-opt's feasibility must use this,
        # not travel alone: with loiter on, relocating targets onto a leg adds
        # their loiter fuel, which a travel-only check ignored.
        cfg = (drone_configs or {}).get(str(did), {}) or {}
        return route_travel(route, did) + compute_route_loiter_fuel(
            route, cfg, targets_by_id
        )

    def budget_of(did: str) -> float:
        rd = solution["routes"].get(did, {}) or {}
        cfg = (drone_configs or {}).get(str(did), {}) or {}
        return float(rd.get("fuel_budget", cfg.get("fuel_budget", 0)) or 0)

    def is_target(w: str) -> bool:
        # A real target is "T<n>". A TANKER "TK<n>" also starts with "T" but is a
        # refuel stop, NOT a mission objective.
        s = str(w)
        return s.startswith("T") and not s.startswith("TK")

    routes: Dict[str, List[str]] = {
        did: list((rd or {}).get("route", []) or [])
        for did, rd in solution.get("routes", {}).items()
    }
    moves: List[Dict[str, Any]] = []

    for _pass in range(max_passes):
        best = None  # (gain, src, i, L, dst, j, reversed)

        for src, sroute in routes.items():
            n = len(sroute)
            # Run must be INTERIOR targets only (never move the start/end airport).
            for i in range(1, n - 1):
                for L in range(1, max_segment_len + 1):
                    j_end = i + L - 1
                    if j_end >= n - 1:
                        break
                    run = sroute[i : j_end + 1]
                    if not all(is_target(w) for w in run):
                        continue

                    prev_w, next_w = sroute[i - 1], sroute[j_end + 1]
                    # Cost removed from the source by deleting the run.
                    removed = (
                        D(prev_w, run[0], src)
                        + route_travel(run, src)
                        + D(run[-1], next_w, src)
                    )
                    rejoin = D(prev_w, next_w, src)
                    src_saving = removed - rejoin

                    for dst, droute in routes.items():
                        dcfg = (drone_configs or {}).get(str(dst), {}) or {}
                        # Destination must accept every target type in the run.
                        if not all(
                            target_allowed_for_drone(
                                targets_by_id.get(str(w), {"type": "a"}), str(dst), dcfg
                            )
                            for w in run
                        ):
                            continue

                        # Insertion gaps in the destination. When dst==src, skip
                        # gaps that overlap the run being removed.
                        m = len(droute)
                        for k in range(0, m - 1):
                            if dst == src and (i - 1 <= k <= j_end):
                                continue
                            a, b = droute[k], droute[k + 1]
                            for rev in (False, True):
                                seg = list(reversed(run)) if rev else run
                                insert_cost = (
                                    D(a, seg[0], dst)
                                    + route_travel(seg, dst)
                                    + D(seg[-1], b, dst)
                                    - D(a, b, dst)
                                )
                                gain = src_saving - insert_cost
                                if gain <= 1e-6:
                                    continue
                                if dst == src:
                                    base = [w for w in sroute if w not in set(run)]
                                    kk = base.index(a) if a in base else k
                                    cand = base[: kk + 1] + seg + base[kk + 1 :]
                                else:
                                    cand = droute[: k + 1] + seg + droute[k + 1 :]
                                if route_cost(cand, dst) > budget_of(dst) + 1e-6:
                                    continue

                                dst_c = _constraints_for_route(constraints, dst)
                                if dst_c and not _ordering_legal(cand, dst_c):
                                    continue
                                if dst != src:
                                    src_c = _constraints_for_route(constraints, src)
                                    if src_c:
                                        src_after = [
                                            w for w in sroute if w not in set(run)
                                        ]
                                        if not _ordering_legal(src_after, src_c):
                                            continue
                                if best is None or gain > best[0]:
                                    best = (gain, src, i, L, dst, k, rev)

        if best is None:
            break

        gain, src, i, L, dst, k, rev = best
        sroute = routes[src]
        run = sroute[i : i + L]
        seg = list(reversed(run)) if rev else run
        new_src = sroute[:i] + sroute[i + L :]
        if dst == src:
            kk = new_src.index(routes[dst][k]) if routes[dst][k] in new_src else k
            routes[src] = new_src[: kk + 1] + seg + new_src[kk + 1 :]
        else:
            routes[src] = new_src
            droute = routes[dst]
            routes[dst] = droute[: k + 1] + seg + droute[k + 1 :]
        moves.append(
            {
                "run": run,
                "from_drone": src,
                "to_drone": dst,
                "reversed": rev,
                "gain": round(gain, 3),
            }
        )

    out = dict(solution)
    out_routes = dict(solution.get("routes", {}))
    for did, route in routes.items():
        rd = dict(out_routes.get(did, {}) or {})
        rd["route"] = route
        rd["sequence"] = ",".join(str(w) for w in route)
        rd["distance"] = route_travel(route, did)
        out_routes[did] = rd
    out["routes"] = out_routes
    out["or_opt_moves"] = moves
    out["success"] = True
    return out
