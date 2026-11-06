"""Refuel-related helper routines for solver orchestration."""

import heapq
from typing import Any, Dict, List, Optional, Set, Tuple

from .airport_rules import REFUEL_STOP_COST_KM, _airport_accepts_drone, is_real_airport
from .constraints import _subset_constraints_for_leg
from .solve_one_leg import solve_one_leg
from .trajectory_planner import clip_route_to_radii


def _refuel_options(cfg: Dict[str, Any]) -> List[str]:
    """Allowed opportunistic refuel-point ids as an ordered, de-duplicated set."""
    opts = cfg.get("refuel_options")
    if isinstance(opts, list):
        seen: List[str] = []
        for s in opts:
            s = str(s)
            if s and s not in ("-", "None") and s not in seen:
                seen.append(s)
        return seen
    return []


def _relax_positions_to_detection_radii(
    route_ids: List[str],
    waypoint_positions: Dict[str, tuple],
    candidate_targets: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    sams: List[Dict[str, Any]],
) -> Dict[str, tuple]:
    """
    Relax this drone's target waypoints within configured detection radii.

    Order stays fixed; SAM zones are respected by `clip_route_to_radii`.
    """
    radius_by_type = (cfg or {}).get("detection_radius") or {}
    if not isinstance(radius_by_type, dict) or not any(
        float(v or 0.0) > 0.0 for v in radius_by_type.values()
    ):
        return waypoint_positions

    target_radii: Dict[str, float] = {}
    for t in candidate_targets:
        tid = str(t.get("id", ""))
        ttype = str(t.get("type", "a")).lower()
        target_radii[tid] = float(radius_by_type.get(ttype, 0.0) or 0.0)

    if not any(v > 0.0 for v in target_radii.values()):
        return waypoint_positions

    return clip_route_to_radii(route_ids, waypoint_positions, target_radii, sams=sams)


def _relocate_refuel_stops(
    route: List[str],
    labels: List[str],
    matrix: List[List[float]],
    budget: float,
    airports: set,
    constraints: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Slide refuel stops to their shortest feasible adjacent position."""
    if not route or len(route) < 3:
        return route
    idx = {l: i for i, l in enumerate(labels)}

    def D(a, b):
        return matrix[idx[a]][idx[b]]

    def rlen(seq):
        return sum(D(seq[i], seq[i + 1]) for i in range(len(seq) - 1))

    def legs_of(r):
        legs = [[r[0]]]
        for wp in r[1:]:
            legs[-1].append(wp)
            if wp in airports and wp != r[-1]:
                legs.append([wp])
        return legs

    def legs_ok(r):
        return all(rlen(leg) <= budget + 1e-6 for leg in legs_of(r))

    first = (constraints or {}).get("first")
    last = (constraints or {}).get("last")
    adjacent = (constraints or {}).get("adjacent") or []

    def ordering_ok(r):
        ts = [w for w in r if w not in airports]
        pos = {t: i for i, t in enumerate(ts)}
        if first and (not ts or ts[0] != first):
            return False
        if last and (not ts or ts[-1] != last):
            return False
        for y, x in adjacent:
            if y in pos and x in pos and pos[x] != pos[y] + 1:
                return False
            if y in r and x in r and r.index(x) != r.index(y) + 1:
                return False
        return True

    best = list(route)
    best_len = rlen(best)
    improved = True
    while improved:
        improved = False
        cur = best
        for i, wp in enumerate(cur):
            if wp in airports and 0 < i < len(cur) - 1:
                if cur[i - 1] not in airports:
                    cand = cur[: i - 1] + [wp, cur[i - 1]] + cur[i + 1 :]
                    if (
                        legs_ok(cand)
                        and ordering_ok(cand)
                        and rlen(cand) < best_len - 1e-6
                    ):
                        best, best_len, improved = cand, rlen(cand), True
                        break
                if cur[i + 1] not in airports:
                    cand = cur[:i] + [cur[i + 1], wp] + cur[i + 2 :]
                    if (
                        legs_ok(cand)
                        and ordering_ok(cand)
                        and rlen(cand) < best_len - 1e-6
                    ):
                        best, best_len, improved = cand, rlen(cand), True
                        break
    return best


def _route_fuel_legs(
    route: List[str],
    labels: List[str],
    matrix: List[List[float]],
    refuel_nodes: Set[str],
    loiter_km: Dict[str, float],
) -> Tuple[float, List[int], List[float]]:
    """(travel km, refuel indices, fuel per leg) of a route. A leg ends at
    every intermediate refuel node; leg fuel = travel + loiter of its targets."""
    idx = {label: i for i, label in enumerate(labels)}
    travel = 0.0
    refuels: List[int] = []
    legs: List[float] = [0.0]
    for i in range(1, len(route)):
        step = matrix[idx[route[i - 1]]][idx[route[i]]]
        travel += step
        legs[-1] += step + loiter_km.get(route[i], 0.0)
        if route[i] in refuel_nodes and i < len(route) - 1:
            refuels.append(i)
            legs.append(0.0)
    return travel, refuels, legs


def _reoptimize_leg_pairs(
    route: List[str],
    labels: List[str],
    matrix: List[List[float]],
    *,
    refuel_nodes: Set[str],
    start_budget: float,
    full_tank: float,
    loiter_km: Dict[str, float],
    max_pair_targets: int = 12,
) -> List[str]:
    """Refuel where a loop ends, not where it started.

    For every two back-to-back fuel legs (start -> targets -> refuel ->
    targets -> end), re-plan their targets together, exactly: the shortest
    start -> some targets -> refuel point -> the rest -> end over EVERY allowed
    refuel point (a loop that returns home to refuel can instead end at a tanker
    next to its last target and refuel there), or one leg with no refuel when it
    fits. Each piece must fit its tank (leg 1: start fuel). Repeats until no
    pair improves. Pairs with more than `max_pair_targets` targets are left as
    they are (Held-Karp over the pair's targets).
    """
    idx = {label: i for i, label in enumerate(labels)}
    if not route or len(route) < 3 or any(w not in idx for w in route):
        return route

    def D(a: str, b: str) -> float:
        return matrix[idx[a]][idx[b]]

    # Split into stops (start, refuels, end) and each leg's targets. Any other
    # node in between (e.g. a transit airport) is left alone.
    stops = [route[0]]
    legs: List[List[str]] = [[]]
    for i, w in enumerate(route[1:], start=1):
        if w in refuel_nodes and i < len(route) - 1:
            stops.append(w)
            legs.append([])
        elif i == len(route) - 1:
            stops.append(w)
        elif w in loiter_km or w.startswith("T"):
            legs[-1].append(w)
        else:
            return route

    def held_karp(start: str, targets: List[str]):
        """best[mask][j]: shortest travel start -> targets in mask, ending at j."""
        n = len(targets)
        inf = float("inf")
        best = [[inf] * n for _ in range(1 << n)]
        parent = [[-1] * n for _ in range(1 << n)]
        for j, t in enumerate(targets):
            best[1 << j][j] = D(start, t)
        for mask in range(1, 1 << n):
            row = best[mask]
            for j in range(n):
                cost = row[j]
                if cost == inf:
                    continue
                for k in range(n):
                    if mask & (1 << k):
                        continue
                    nm = mask | (1 << k)
                    c = cost + D(targets[j], targets[k])
                    if c < best[nm][k]:
                        best[nm][k] = c
                        parent[nm][k] = j
        return best, parent

    def path(start, targets, best, parent, mask, end):
        """(travel, ordered targets) of start -> mask's targets -> end."""
        if mask == 0:
            return D(start, end), []
        cost, j = min(
            (best[mask][j] + D(targets[j], end), j)
            for j in range(len(targets))
            if mask & (1 << j)
        )
        order = []
        m = mask
        while j != -1:
            order.append(targets[j])
            pj = parent[m][j]
            m &= ~(1 << j)
            j = pj
        return cost, order[::-1]

    def loiter(targets, mask):
        return sum(
            loiter_km.get(t, 0.0) for j, t in enumerate(targets) if mask & (1 << j)
        )

    improved = True
    while improved:
        improved = False
        for k in range(len(legs) - 1):
            targets = legs[k] + legs[k + 1]
            if len(targets) > max_pair_targets:
                continue
            start, mid, end = stops[k], stops[k + 1], stops[k + 2]
            cap1 = start_budget if k == 0 else full_tank
            seg = [start] + legs[k] + [mid] + legs[k + 1] + [end]
            current = sum(D(a, b) for a, b in zip(seg, seg[1:]))
            full = (1 << len(targets)) - 1
            best_s, parent_s = held_karp(start, targets)
            choice = None  # (travel, new stops slice, new legs slice)

            # One leg, no refuel, when it fits.
            travel, order = path(start, targets, best_s, parent_s, full, end)
            if travel + loiter(targets, full) <= cap1 + 1e-6:
                choice = (travel, [start, end], [order])

            for r in sorted(refuel_nodes):
                if r not in idx:
                    continue
                best_r, parent_r = held_karp(r, targets)
                for mask in range(full + 1):
                    rest = full ^ mask
                    if (r == start and mask == 0) or (r == end and rest == 0):
                        continue  # a refuel that does nothing
                    t1, o1 = path(start, targets, best_s, parent_s, mask, r)
                    if t1 + loiter(targets, mask) > cap1 + 1e-6:
                        continue
                    t2, o2 = path(r, targets, best_r, parent_r, rest, end)
                    if t2 + loiter(targets, rest) > full_tank + 1e-6:
                        continue
                    if choice is None or t1 + t2 < choice[0] - 1e-9:
                        choice = (t1 + t2, [start, r, end], [o1, o2])

            if choice and choice[0] < current - 1e-6:
                stops[k : k + 3] = choice[1]
                legs[k : k + 2] = choice[2]
                improved = True
                break

    out = [stops[0]]
    for k, leg in enumerate(legs):
        out += leg + [stops[k + 1]]
    return out


def _constraint_target_ids_for_leg(
    constraints: Optional[Dict[str, Any]],
    avail_ids: Set[str],
    *,
    is_first_leg: bool,
    is_last_leg: bool,
) -> Set[str]:
    """Targets that must survive any per-leg candidate cap."""
    if not constraints:
        return set()

    required: Set[str] = set()
    first = constraints.get("first") if is_first_leg else None
    last = constraints.get("last") if is_last_leg else None
    if first and first in avail_ids:
        required.add(str(first))
    if last and last in avail_ids:
        required.add(str(last))

    for a, b in constraints.get("before", []) or []:
        if a in avail_ids:
            required.add(str(a))
        if b in avail_ids:
            required.add(str(b))
    for y, x in constraints.get("adjacent", []) or []:
        if y in avail_ids:
            required.add(str(y))
        if x in avail_ids:
            required.add(str(x))
    return required


def _select_targets_for_refuel_leg(
    env_for_solver: Dict[str, Any],
    target_by_id: Dict[str, Dict[str, Any]],
    start_id: str,
    end_id: Optional[str],
    avail: List[str],
    flexible_endpoint: bool,
    max_targets: int,
    valid_end_airports: List[str],
    constraints: Optional[Dict[str, Any]] = None,
    *,
    is_first_leg: bool = False,
    is_last_leg: bool = False,
) -> List[Dict[str, Any]]:
    """Pick the small target set for one fuel leg, without hiding the full mission.

    Held-Karp still needs a bounded leg size, but the bound belongs here: each
    candidate fuel leg should see the targets relevant to that start/end pair.
    """
    candidates = [str(t) for t in avail if str(t) in target_by_id]
    if not candidates:
        return []

    try:
        limit = int(max_targets or 0)
    except (TypeError, ValueError):
        limit = 12
    if limit <= 0:
        limit = 12

    labels = env_for_solver.get("matrix_labels") or env_for_solver.get("labels") or []
    matrix = env_for_solver.get("distance_matrix") or env_for_solver.get("matrix") or []
    label_idx = {str(label): i for i, label in enumerate(labels)}

    def dist(a: str, b: str) -> float:
        try:
            return float(matrix[label_idx[a]][label_idx[b]])
        except (KeyError, IndexError, TypeError, ValueError):
            return float("inf")

    if flexible_endpoint or end_id is None:
        end_candidates = [e for e in valid_end_airports if e in label_idx]
    else:
        end_candidates = [end_id] if end_id in label_idx else []
    if not end_candidates:
        end_candidates = [start_id] if start_id in label_idx else []

    direct = min((dist(start_id, e) for e in end_candidates), default=0.0)
    required = _constraint_target_ids_for_leg(
        constraints,
        set(candidates),
        is_first_leg=is_first_leg,
        is_last_leg=is_last_leg,
    )
    actual_limit = max(limit, len(required))

    def score(tid: str) -> Tuple[bool, int, float, str]:
        target = target_by_id[tid]
        priority = int(target.get("priority", 5))
        via_target = min(
            (dist(start_id, tid) + dist(tid, e) for e in end_candidates),
            default=float("inf"),
        )
        relevance = max(0.0, via_target - direct)
        return (tid not in required, -priority, relevance, tid)

    selected_ids = sorted(candidates, key=score)[:actual_limit]
    return [target_by_id[tid] for tid in selected_ids]


def _base_progress_from_matrix(
    env_for_solver: Dict[str, Any],
    start_id: str,
    end_ids: List[str],
    bases: List[str],
) -> Dict[str, float]:
    """Estimate base order along the start-to-end corridor from matrix distances."""
    labels = env_for_solver.get("matrix_labels") or env_for_solver.get("labels") or []
    matrix = env_for_solver.get("distance_matrix") or env_for_solver.get("matrix") or []
    idx = {str(label): i for i, label in enumerate(labels)}

    def dist(a: str, b: str) -> float:
        try:
            return float(matrix[idx[a]][idx[b]])
        except (KeyError, IndexError, TypeError, ValueError):
            return float("inf")

    progress: Dict[str, float] = {start_id: 0.0}
    usable_end = next(
        (e for e in end_ids if e in idx and dist(start_id, e) > 1e-9), None
    )
    if usable_end is None:
        for order, base in enumerate([start_id] + list(bases)):
            progress.setdefault(str(base), float(order))
        return progress

    total = dist(start_id, usable_end)
    total_sq = total * total
    progress[usable_end] = 1.0
    for base in bases:
        b = str(base)
        if b not in idx:
            continue
        # Metric projection onto the start/end baseline:
        # p = (d(S,B)^2 + d(S,E)^2 - d(B,E)^2) / (2*d(S,E)^2)
        p = (dist(start_id, b) ** 2 + total_sq - dist(b, usable_end) ** 2) / (
            2.0 * total_sq
        )
        progress[b] = max(0.0, min(1.0, p))
    return progress


def _solve_exact_refuel_small(
    env_for_solver: Dict[str, Any],
    *,
    start_id: str,
    target_ids: List[str],
    target_priority: Dict[str, int],
    cfg: Dict[str, Any],
    refuel_options: List[str],
    tanker_ids: Set[str],
    valid_end_airports: List[str],
    start_budget: float,
    full_tank: float,
    max_refuels: int,
) -> Optional[Dict[str, Any]]:
    """Globally solve small refuel missions over the SAM-aware matrix.

    Held-Karp remains the per-leg engine for larger missions. For small missions,
    this label-setting search optimizes the *whole* refuel-state graph directly:
    visit target, fly to a refuel base, reset fuel, or finish at a real endpoint.
    That prevents greedy per-leg target collection from hiding a lower-fuel equal
    priority assignment across refuel legs.
    """
    if not target_ids:
        return None
    if max_refuels < 0:
        return None

    labels = [
        str(label)
        for label in (
            env_for_solver.get("matrix_labels") or env_for_solver.get("labels") or []
        )
    ]
    matrix = env_for_solver.get("distance_matrix") or env_for_solver.get("matrix") or []
    idx = {label: i for i, label in enumerate(labels)}
    if start_id not in idx:
        return None

    targets = [str(t) for t in target_ids if str(t) in idx]
    if not targets:
        return None
    if len(targets) != len(set(targets)):
        return None

    ends = [
        str(e)
        for e in (valid_end_airports or [])
        if str(e) in idx and str(e) not in tanker_ids
    ]
    if not ends:
        return None

    refuel_nodes = [
        str(r)
        for r in refuel_options
        if str(r) in idx and str(r) not in ("", "-", "None")
    ]
    if not refuel_nodes:
        return None

    target_index = {tid: i for i, tid in enumerate(targets)}
    all_mask = (1 << len(targets)) - 1

    # Loiter FUEL per target: minutes x speed (km/min) = the orbit it flies —
    # the same as every other solver path (optimizers/common.py
    # compute_route_loiter_fuel). Raw minutes under-counted it at any speed
    # other than 1 km/min and planned legs over the tank.
    loiter_cost: Dict[str, float] = {}
    if cfg.get("loiter_enabled"):
        times = cfg.get("loiter_times") or {}
        speed = float(cfg.get("speed", 1) or 1)
        for target in env_for_solver.get("targets", []) or []:
            tid = str(target.get("id", ""))
            if tid in target_index:
                ttype = str(target.get("type", "a")).lower()
                loiter_cost[tid] = float(times.get(ttype, 0) or 0) * speed

    def dist(a: str, b: str) -> float:
        try:
            return float(matrix[idx[a]][idx[b]])
        except (IndexError, TypeError, ValueError):
            return float("inf")

    def score(mask: int, total: float) -> Tuple[int, float, int]:
        points = sum(
            int(target_priority.get(tid, 0))
            for tid, bit in target_index.items()
            if mask & (1 << bit)
        )
        return points, -total, bin(mask).count("1")

    # State is (visited_mask, current_node, refuels_used). Multiple labels can
    # survive per state because future feasibility depends on both total fuel and
    # current-leg fuel.
    labels_by_state: Dict[Tuple[int, str, int], List[Tuple[float, float, int]]] = {}
    parents: Dict[int, Dict[str, Any]] = {}
    heap: List[Tuple[int, float, float, int, int, str, int]] = []
    next_token = 0

    def dominated(
        existing: List[Tuple[float, float, int]], total: float, leg: float
    ) -> bool:
        return any(et <= total + 1e-9 and el <= leg + 1e-9 for et, el, _ in existing)

    def add_label(
        *,
        mask: int,
        node: str,
        refuels: int,
        total: float,
        leg: float,
        prev: Optional[int],
        route_node: Optional[str],
        refuel_index: Optional[int] = None,
        closed_leg: Optional[float] = None,
    ) -> None:
        nonlocal next_token
        if refuels > max_refuels:
            return
        cap = start_budget if refuels == 0 else full_tank
        if leg > cap + 1e-6:
            return
        state = (mask, node, refuels)
        existing = labels_by_state.setdefault(state, [])
        if dominated(existing, total, leg):
            return
        existing[:] = [
            item
            for item in existing
            if not (total <= item[0] + 1e-9 and leg <= item[1] + 1e-9)
        ]
        token = next_token
        next_token += 1
        existing.append((total, leg, token))
        parents[token] = {
            "prev": prev,
            "state": state,
            "route_node": route_node,
            "refuel_index": refuel_index,
            "closed_leg": closed_leg,
            "total": total,
            "leg": leg,
        }
        pts, neg_total, _count = score(mask, total)
        heapq.heappush(heap, (-pts, -neg_total, leg, token, mask, node, refuels))

    def rebuild(token: int) -> Tuple[List[str], List[int], List[float]]:
        route = [start_id]
        refuel_indices: List[int] = []
        leg_distances: List[float] = []
        chain = []
        while token is not None:
            item = parents[token]
            chain.append(item)
            token = item["prev"]
        for item in reversed(chain):
            node = item.get("route_node")
            if node is not None:
                route.append(node)
            if item.get("closed_leg") is not None:
                leg_distances.append(float(item["closed_leg"]))
            if item.get("refuel_index") is not None:
                refuel_indices.append(int(item["refuel_index"]))
        if chain:
            final_leg = float(chain[0]["leg"])
            if not leg_distances or abs(final_leg) > 1e-9:
                leg_distances.append(final_leg)
        return route, refuel_indices, leg_distances

    add_label(
        mask=0,
        node=start_id,
        refuels=0,
        total=0.0,
        leg=0.0,
        prev=None,
        route_node=None,
    )

    best_token: Optional[int] = None
    best_score: Tuple[int, float, int] = (-1, float("-inf"), -1)
    best_total = float("inf")

    while heap:
        _neg_pts, _total_for_heap, _leg, token, mask, node, refuels = heapq.heappop(
            heap
        )
        item = parents.get(token)
        if not item:
            continue
        state = (mask, node, refuels)
        if not any(tok == token for _t, _l, tok in labels_by_state.get(state, [])):
            continue

        total = float(item["total"])
        leg = float(item["leg"])

        # Finish at any valid real airport. Landing is not an intermediate refuel.
        for end in ends:
            step = 0.0 if end == node else dist(node, end)
            if step == float("inf"):
                continue
            end_leg = leg + step
            cap = start_budget if refuels == 0 else full_tank
            if end_leg > cap + 1e-6:
                continue
            finish_total = total + step
            finish_score = score(mask, finish_total)
            if finish_score > best_score or (
                finish_score == best_score and finish_total < best_total - 1e-9
            ):
                finish_token = next_token
                next_token += 1
                parents[finish_token] = {
                    "prev": token,
                    "state": (mask, end, refuels),
                    "route_node": None if end == node else end,
                    "refuel_index": None,
                    "closed_leg": None,
                    "total": finish_total,
                    "leg": end_leg,
                }
                best_token = finish_token
                best_score = finish_score
                best_total = finish_total

        # If this label cannot possibly beat the best points already found, fuel
        # comparisons can still matter only for equal points. Keep exploring labels
        # that may reach unvisited targets.
        if mask == all_mask:
            continue

        for tid in targets:
            bit = 1 << target_index[tid]
            if mask & bit:
                continue
            step = dist(node, tid) + loiter_cost.get(tid, 0.0)
            if step == float("inf"):
                continue
            add_label(
                mask=mask | bit,
                node=tid,
                refuels=refuels,
                total=total + step,
                leg=leg + step,
                prev=token,
                route_node=tid,
            )

        if refuels >= max_refuels:
            continue

        # Refuel at the current base, if we are sitting on one after flying a leg.
        if node in refuel_nodes and leg > 1e-9:
            add_label(
                mask=mask,
                node=node,
                refuels=refuels + 1,
                total=total,
                leg=0.0,
                prev=token,
                route_node=None,
                refuel_index=None,  # patched after route reconstruction below
                closed_leg=leg,
            )

        # Fly to another allowed refuel node and refuel on arrival.
        for refuel_node in refuel_nodes:
            if refuel_node == node:
                continue
            step = dist(node, refuel_node)
            if step == float("inf"):
                continue
            new_leg = leg + step
            cap = start_budget if refuels == 0 else full_tank
            if new_leg > cap + 1e-6:
                continue
            # Route index after appending the refuel node. If route_node is None
            # this is a same-node refuel and handled above.
            route, _, _ = rebuild(token)
            refuel_route_index = len(route)
            add_label(
                mask=mask,
                node=refuel_node,
                refuels=refuels + 1,
                total=total + step,
                leg=0.0,
                prev=token,
                route_node=refuel_node,
                refuel_index=refuel_route_index,
                closed_leg=new_leg,
            )

    if best_token is None:
        return None

    # Reconstruct same-node refuel indices. They close a leg without appending a
    # route node, so their index is the current route position at that parent.
    route_so_far = [start_id]
    rebuilt_refuels: List[int] = []
    rebuilt_legs: List[float] = []
    chain = []
    token = best_token
    while token is not None:
        item = parents[token]
        chain.append(item)
        token = item["prev"]
    for item in reversed(chain):
        if item.get("route_node") is not None:
            route_so_far.append(item["route_node"])
        if item.get("closed_leg") is not None:
            rebuilt_legs.append(float(item["closed_leg"]))
            rebuilt_refuels.append(
                int(item["refuel_index"])
                if item.get("refuel_index") is not None
                else len(route_so_far) - 1
            )
    final_leg = float(chain[0]["leg"]) if chain else 0.0
    if not rebuilt_legs or final_leg > 1e-9:
        rebuilt_legs.append(final_leg)

    # Final landing is not a refuel event.
    rebuilt_refuels = [i for i in rebuilt_refuels if 0 < i < len(route_so_far) - 1]
    visited_targets = [wp for wp in route_so_far if wp in target_index]
    loiter_total = sum(loiter_cost.get(tid, 0.0) for tid in visited_targets)
    return {
        "route": route_so_far,
        "visited_targets": visited_targets,
        # The shared solve wrapper adds loiter for opportunistic-refuel routes.
        # Search uses loiter for feasibility and ranking, but returns travel only.
        "distance": best_total - loiter_total,
        "refuel_indices": rebuilt_refuels,
        "leg_distances": rebuilt_legs,
        "points": best_score[0],
    }


def solve_opportunistic_drone(
    env_for_solver: Dict[str, Any],
    fuel_budget: float,
    start_id: str,
    candidate_targets: List[Dict[str, Any]],
    did: str,
    cfg: Dict[str, Any],
    refuel_options: List[str],
    max_refuels: int,
    tanker_ids: set,
    valid_end_airports: List[str],
    constraints: Optional[Dict[str, Any]] = None,
    solver=None,
) -> Tuple[List[str], set, float, List[int], List[float]]:
    """Solve one drone with opportunistic refueling enabled."""
    from .opportunistic_refuel import solve_opportunistic

    if solver is None:
        raise RuntimeError("solve_opportunistic_drone requires a solver interface")

    tgt_by_id = {str(t["id"]): t for t in candidate_targets}
    target_ids = list(tgt_by_id.keys())
    target_priority = {tid: int(t.get("priority", 5)) for tid, t in tgt_by_id.items()}
    max_targets_per_leg = int(cfg.get("max_targets_per_leg", 8) or 8)

    drone_runway = str(cfg.get("runway", "S")).upper()
    ap_by_id = {str(a.get("id")): a for a in env_for_solver.get("airports", [])}
    usable_options: List[str] = []
    for oid in refuel_options:
        a = ap_by_id.get(str(oid), {})
        if str(oid) in tanker_ids:
            usable_options.append(str(oid))
        elif is_real_airport(a) and _airport_accepts_drone(
            a.get("runway", "L"), drone_runway
        ):
            usable_options.append(str(oid))

    def leg_solver(s, e, avail, budget, flexible):
        sub = _select_targets_for_refuel_leg(
            env_for_solver,
            tgt_by_id,
            s,
            e,
            avail,
            flexible,
            max_targets_per_leg,
            valid_end_airports,
            constraints,
            is_first_leg=(s == start_id),
            is_last_leg=(e is None),
        )
        sub_ids = [str(t["id"]) for t in sub]
        leg_constraints = None
        if constraints:
            leg_constraints = _subset_constraints_for_leg(
                constraints,
                sub_ids,
                is_first_leg=(s == start_id),
                is_last_leg=(e is None),
            )
        return solve_one_leg(
            env_for_solver,
            budget,
            s,
            e,
            sub,
            did,
            "OPP",
            flexible_endpoint=flexible,
            drone_cfg=cfg,
            constraints=leg_constraints,
            solver=solver,
        )

    route_constraints = constraints or {}
    full_tank = float(cfg.get("fuel_budget", fuel_budget) or fuel_budget)
    corridor_progress = _base_progress_from_matrix(
        env_for_solver,
        start_id,
        valid_end_airports,
        usable_options,
    )

    exact_limit = int(cfg.get("exact_refuel_max_targets", 12) or 12)
    exact_res = None
    if (
        not constraints
        and usable_options
        and 0 < len(target_ids) <= exact_limit
        and max_refuels > 0
    ):
        exact_res = _solve_exact_refuel_small(
            env_for_solver,
            start_id=start_id,
            target_ids=target_ids,
            target_priority=target_priority,
            cfg=cfg,
            refuel_options=usable_options,
            tanker_ids=tanker_ids,
            valid_end_airports=valid_end_airports,
            start_budget=fuel_budget,
            full_tank=full_tank,
            max_refuels=max_refuels,
        )
        if exact_res:
            print(
                f"D{did} OPP EXACT: start={start_id} start_budget={fuel_budget} "
                f"full_tank={full_tank} route={exact_res.get('route')} "
                f"visited={exact_res.get('visited_targets')} "
                f"refuel_idx={exact_res.get('refuel_indices')}",
                flush=True,
            )
            return (
                exact_res["route"],
                set(exact_res["visited_targets"]),
                exact_res["distance"],
                exact_res.get("refuel_indices", []),
                exact_res.get("leg_distances", []),
            )

    res = solve_opportunistic(
        start=start_id,
        refuel_options=usable_options,
        tanker_ids=tanker_ids,
        target_ids=target_ids,
        target_priority=target_priority,
        budget=full_tank,
        start_budget=fuel_budget,
        max_refuels=max_refuels,
        leg_solver=leg_solver,
        valid_end_airports=valid_end_airports,
        first_target=route_constraints.get("first"),
        last_target=route_constraints.get("last"),
        max_search_states=int(cfg.get("refuel_search_state_budget", 120) or 120),
        base_progress=corridor_progress,
        use_corridor_planner=bool(cfg.get("refuel_corridor_planner", True)),
    )

    print(
        f"D{did} OPP RAW: start={start_id} start_budget={fuel_budget} "
        f"full_tank={full_tank} valid_ends={valid_end_airports} "
        f"usable_refuel={usable_options} max_refuels={max_refuels} "
        f"route={res.get('route')} visited={res.get('visited_targets')} "
        f"refuel_idx={res.get('refuel_indices')}",
        flush=True,
    )

    try:
        labels = (
            env_for_solver.get("matrix_labels") or env_for_solver.get("labels") or []
        )
        matrix = (
            env_for_solver.get("distance_matrix") or env_for_solver.get("matrix") or []
        )
        visited = set(res.get("visited_targets") or [])
        left = [t for t in target_ids if t not in visited]
        used_refuels = len(res.get("refuel_indices") or [])
        if left and labels and matrix:
            bases_for_reach = list(usable_options) + [start_id]
            reach = []
            for t in left:
                if t not in labels:
                    continue
                target_idx = labels.index(t)
                best_d = min(
                    (
                        float(matrix[labels.index(b)][target_idx])
                        for b in bases_for_reach
                        if b in labels
                    ),
                    default=float("inf"),
                )
                reach.append(
                    f"{t}:{best_d:.0f}km"
                    f"{'<=tank' if best_d <= full_tank else '>tank'}"
                )
            print(
                f"D{did} SUBOPT: left={left} "
                f"refuels_used={used_refuels}/{max_refuels} "
                f"reachability(from nearest base)={reach}",
                flush=True,
            )
    except Exception as exc:
        print(f"D{did} SUBOPT dump error: {exc}", flush=True)

    labels = env_for_solver.get("matrix_labels") or env_for_solver.get("labels") or []
    matrix = env_for_solver.get("distance_matrix") or env_for_solver.get("matrix") or []
    # Only explicitly allowed refuel options may be treated as fuel-reset points.
    # The start airport is always a launch point, and may be a final landing point,
    # but it is not an intermediate refuel unless the user checked it.
    refuel_airports = set(usable_options)
    route = res["route"]
    if labels and matrix and route and len(route) > 2:
        route = _relocate_refuel_stops(
            route,
            labels,
            matrix,
            fuel_budget,
            refuel_airports,
            constraints=route_constraints,
        )
        # Loiter fuel per target: minutes x speed (km/min).
        speed = float(cfg.get("speed", 1) or 1)
        times = (cfg.get("loiter_times") or {}) if cfg.get("loiter_enabled") else {}
        loiter_km = {
            tid: float(times.get(str(t.get("type", "a")).lower(), 0) or 0) * speed
            for tid, t in tgt_by_id.items()
        }
        # Refuel where a loop ends, not where it started: re-plan every two
        # back-to-back legs exactly, over every allowed refuel point. (Ordering
        # rules fix the order, so a constrained route is left as planned.)
        if not route_constraints:
            route = _reoptimize_leg_pairs(
                route,
                labels,
                matrix,
                refuel_nodes=refuel_airports,
                start_budget=fuel_budget,
                full_tank=full_tank,
                loiter_km=loiter_km,
            )
        # The route's travel distance and per-leg fuel, computed once from the
        # final route — the same meaning the exact planner returns: distance =
        # travel only (the caller adds loiter), legs = fuel incl. loiter. (The
        # legs' solver totals included loiter, so the caller counted it twice.)
        travel, refuel_idx, leg_fuel = _route_fuel_legs(
            route, labels, matrix, refuel_airports, loiter_km
        )
        res["route"] = route
        res["distance"] = travel
        res["refuel_indices"] = refuel_idx
        res["leg_distances"] = leg_fuel if refuel_idx else res.get("leg_distances", [])

    return (
        res["route"],
        set(res["visited_targets"]),
        res["distance"],
        res.get("refuel_indices", []),
        res.get("leg_distances", []),
    )


def solve_two_leg_refuel(
    env_for_solver: Dict[str, Any],
    fuel_budget: float,
    start_id: str,
    end_id: Optional[str],
    refuel_airport_id: str,
    candidate_targets: List[Dict[str, Any]],
    flexible_endpoint: bool,
    did: str,
    leg1_targets: Optional[List[str]] = None,
    leg2_targets: Optional[List[str]] = None,
    drone_cfg: Optional[Dict[str, Any]] = None,
    leg2_budget: Optional[float] = None,
    constraints: Optional[Dict[str, Any]] = None,
    solver=None,
) -> Dict[str, Any]:
    """
    Solve a two-leg route with a refueling stop at a specific airport.

    constraints: per-drone normalized ordering constraints. `first` binds leg1,
    `last` binds leg2; before/adjacent pairs are applied to whichever leg holds
    both endpoints (see _subset_constraints_for_leg).

    Tries all possible splits of targets across Leg 1 and Leg 2, solving each
    with Held-Karp, and picks the split with minimum total distance where both
    legs fit within the fuel budget.

    When leg1_targets/leg2_targets are provided, only that specific split is tried.

    Returns dict with keys: route, visited_targets, distance, leg1_distance, leg2_distance
    """
    from itertools import combinations

    if solver is None:
        raise RuntimeError("solve_two_leg_refuel requires a solver interface")

    def _solve_one_leg(*args, **kwargs):
        return solve_one_leg(*args, **kwargs, solver=solver)

    # section 11.10: ONE fuel value - fuel_budget IS the tank (full each leg). leg 1 flies
    # on the fuel at its start (fuel_budget here = start fuel; on a replan the caller
    # passes the reduced start_fuel); leg 2 starts AFTER refueling with a FULL TANK.
    # Caller passes leg2_budget explicitly; the fallback is the tank = fuel_budget.
    # (fuel_capacity retired - it was a duplicate of the tank.)
    if leg2_budget is None:
        leg2_budget = float(
            (drone_cfg or {}).get("fuel_budget", fuel_budget) or fuel_budget
        )

    target_ids = [str(t["id"]) for t in candidate_targets]
    target_map = {str(t["id"]): t for t in candidate_targets}
    n = len(target_ids)

    # --- If caller specified the split, just solve that one ---
    if leg1_targets is not None:
        l1_set = set(leg1_targets)
        l2_set = set(leg2_targets) if leg2_targets else set(target_ids) - l1_set
        l1_targets_list = [target_map[tid] for tid in target_ids if tid in l1_set]
        l2_targets_list = [target_map[tid] for tid in target_ids if tid in l2_set]

        print(
            f"   FUEL D{did} Explicit split: Leg1={[t['id'] for t in l1_targets_list]}, Leg2={[t['id'] for t in l2_targets_list]}",  # noqa: E501
            flush=True,
        )

        # Subset ordering constraints to each leg. first => leg1, last => leg2;
        # before/adjacent pairs go to the leg holding both endpoints.
        l1_ids = [str(t["id"]) for t in l1_targets_list]
        l2_ids = [str(t["id"]) for t in l2_targets_list]
        c1 = _subset_constraints_for_leg(
            constraints, l1_ids, is_first_leg=True, is_last_leg=False
        )
        c2 = _subset_constraints_for_leg(
            constraints, l2_ids, is_first_leg=False, is_last_leg=True
        )

        r1, v1, d1 = _solve_one_leg(
            env_for_solver,
            fuel_budget,
            start_id,
            refuel_airport_id,
            l1_targets_list,
            did,
            "Leg1",
            drone_cfg=drone_cfg,
            constraints=c1,
        )
        r2, v2, d2 = _solve_one_leg(
            env_for_solver,
            leg2_budget,  # section 8.2: leg 2 = full tank after refuel
            refuel_airport_id,
            end_id,
            l2_targets_list,
            did,
            "Leg2",
            flexible_endpoint,
            drone_cfg=drone_cfg,
            constraints=c2,
        )

        combined = list(r1)
        if r2 and combined and combined[-1] == r2[0]:
            combined.extend(r2[1:])
        else:
            combined.extend(r2)

        return {
            "route": combined,
            "visited_targets": list(v1 | v2),
            "distance": d1 + d2 + REFUEL_STOP_COST_KM,
            "leg1_distance": d1,
            "leg2_distance": d2,
            "refuel_airport": refuel_airport_id,
            "refuel_idx": len(r1) - 1,  # Index of refuel point in combined route
        }

    # --- Try all possible splits and pick the best ---
    # Cap at n=10 (1024 splits). For larger sets, fall back to greedy (old behavior).
    MAX_EXHAUSTIVE = 8
    if n > MAX_EXHAUSTIVE:
        print(
            f"   FUEL D{did} Too many targets ({n}) for exhaustive split search, using greedy",
            flush=True,
        )
        # Greedy: solve Leg 1 with all targets, Leg 2 gets leftovers
        r1, v1, d1 = _solve_one_leg(
            env_for_solver,
            fuel_budget,
            start_id,
            refuel_airport_id,
            candidate_targets,
            did,
            "Leg1",
            drone_cfg=drone_cfg,
        )
        remaining = [t for t in candidate_targets if str(t["id"]) not in v1]
        r2, v2, d2 = _solve_one_leg(
            env_for_solver,
            leg2_budget,  # section 8.2: leg 2 = full tank after refuel
            refuel_airport_id,
            end_id,
            remaining,
            did,
            "Leg2",
            flexible_endpoint,
            drone_cfg=drone_cfg,
        )
        combined = list(r1)
        if r2 and combined and combined[-1] == r2[0]:
            combined.extend(r2[1:])
        else:
            combined.extend(r2)
        return {
            "route": combined,
            "visited_targets": list(v1 | v2),
            "distance": d1 + d2 + REFUEL_STOP_COST_KM,
            "leg1_distance": d1,
            "leg2_distance": d2,
            "refuel_airport": refuel_airport_id,
            "refuel_idx": len(r1) - 1,  # Index of refuel point in combined route
        }

    print(
        f"   FUEL D{did} Trying all {2**n} target splits across legs (n={n})...",
        flush=True,
    )

    best_sol = None
    best_dist = float("inf")
    best_visited_count = 0
    splits_tried = 0
    splits_feasible = 0

    # Try every possible subset for Leg 1 (rest goes to Leg 2)
    for k in range(n + 1):  # k = number of targets in Leg 1
        for leg1_combo in combinations(range(n), k):
            leg1_ids_set = {target_ids[i] for i in leg1_combo}
            leg2_ids_set = set(target_ids) - leg1_ids_set

            l1_targets_list = [
                target_map[tid] for tid in target_ids if tid in leg1_ids_set
            ]
            l2_targets_list = [
                target_map[tid] for tid in target_ids if tid in leg2_ids_set
            ]

            splits_tried += 1

            l1_ids = [str(t["id"]) for t in l1_targets_list]
            l2_ids = [str(t["id"]) for t in l2_targets_list]

            # HARD-scope first/last to their legs: `first` must land on leg 1 (so
            # it can be the very first target of the whole route) and `last` on
            # leg 2 (the final target before the end airport). Splits that place
            # them on the wrong leg cannot honor the constraint - skip them.
            if constraints:
                _first = constraints.get("first")
                _last = constraints.get("last")
                if _first and _first in l2_ids:
                    continue
                if _last and _last in l1_ids:
                    continue

            # Subset ordering constraints to THIS split's legs so first/last/
            # adjacent are enforced (contraction) on the right leg. first => leg1,
            # last => leg2; a chain straddling the boundary raises ValueError.
            try:
                c1 = _subset_constraints_for_leg(
                    constraints, l1_ids, is_first_leg=True, is_last_leg=False
                )
                c2 = _subset_constraints_for_leg(
                    constraints, l2_ids, is_first_leg=False, is_last_leg=True
                )
            except ValueError:
                # This split splits an adjacency chain across the refuel boundary
                # (illegal). Skip it; another split keeps the chain intact.
                continue

            # Solve both legs (loiter fuel is deducted inside _solve_one_leg)
            r1, v1, d1 = _solve_one_leg(
                env_for_solver,
                fuel_budget,
                start_id,
                refuel_airport_id,
                l1_targets_list,
                did,
                "L1",
                drone_cfg=drone_cfg,
                constraints=c1,
            )
            if d1 > fuel_budget:
                continue  # Leg 1 infeasible (travel + loiter exceeds budget)

            r2, v2, d2 = _solve_one_leg(
                env_for_solver,
                leg2_budget,  # section 8.2: leg 2 = full tank after refuel
                refuel_airport_id,
                end_id,
                l2_targets_list,
                did,
                "L2",
                flexible_endpoint,
                drone_cfg=drone_cfg,
                constraints=c2,
            )
            if d2 > leg2_budget:
                continue  # Leg 2 infeasible (vs full tank)

            splits_feasible += 1
            total_visited = len(v1) + len(v2)
            total_dist = d1 + d2

            # Better = more targets visited, then less fuel
            if total_visited > best_visited_count or (
                total_visited == best_visited_count and total_dist < best_dist
            ):
                best_visited_count = total_visited
                best_dist = total_dist

                combined = list(r1)
                if r2 and combined and combined[-1] == r2[0]:
                    combined.extend(r2[1:])
                else:
                    combined.extend(r2)

                best_sol = {
                    "route": combined,
                    "visited_targets": list(v1 | v2),
                    "distance": total_dist + REFUEL_STOP_COST_KM,
                    "leg1_distance": d1,
                    "leg2_distance": d2,
                    "refuel_airport": refuel_airport_id,
                    "refuel_idx": len(r1)
                    - 1,  # Index of refuel point in combined route
                }

    print(
        f"   FUEL D{did} Tried {splits_tried} splits, {splits_feasible} feasible, best={best_dist:.1f} ({best_visited_count} targets)",  # noqa: E501
        flush=True,
    )

    if best_sol:
        return best_sol

    # Fallback: no feasible TWO-LEG split (e.g. dynamic replan from a near-empty
    # diamond). section 5 invariant - a replan NEVER hard-fails: degrade to the floor =
    # a single-leg fly-to-end with whatever targets fit (down to zero), and DROP
    # the refuel (refuel_airport omitted) so downstream validates it as a plain
    # single leg, not an infeasible 2-leg route. NOT a fabricated distance:0 route.
    print(
        f"   FUEL D{did} No feasible two-leg split - degrading to single-leg "
        f"fly-to-end floor (no refuel)",
        flush=True,
    )
    floor_end = end_id or refuel_airport_id
    r, v, d = _solve_one_leg(
        env_for_solver,
        fuel_budget,
        start_id,
        floor_end,
        candidate_targets,
        did,
        "Floor",
        flexible_endpoint,
        drone_cfg=drone_cfg,
    )
    return {
        "route": r,
        "visited_targets": list(v),
        "distance": d,
        # No refuel_airport / leg fields: this is a single-leg floor route.
    }
