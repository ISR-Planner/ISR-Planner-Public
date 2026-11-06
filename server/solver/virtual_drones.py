"""Virtual-drone expansion for multi-stop refueling.

A refuel stop is a fixed airport that splits a physical drone into LEGS; each leg
is treated as a first-class "virtual drone" with its own start/end airport. The
existing allocate -> Held-Karp -> optimize pipeline then handles the legs as if
they were just more drones (no greedy, no special two-leg solver). After solving,
the legs are merged back into one physical-drone route.

  D1, refuel_stops=[A2, A3]  ->  D1_L1: start->A2
                                 D1_L2: A2->A3
                                 D1_L3: A3->end

Virtual-drone id format: "{parent}_L{n}", 1-based leg number. A drone with no
refuel stops is NOT expanded — it stays under its own id (single leg).

Fuel (§11.10): every leg gets a FULL TANK (fuel_budget) because a refuel tops it
up. The FIRST leg of a dynamic replan inherits the physical drone's transient
`start_fuel` (the reduced remaining); later legs always get the full tank.
"""

from typing import Any, Dict, List, Optional, Tuple

from .altitude import matrix_for_drone
from .optimizers.common import compute_route_loiter_fuel

VLEG_SEP = "_L"


def _refuel_stops(cfg: Dict[str, Any]) -> List[str]:
    """Normalized list of refuel-stop airport ids for a drone config.

    Accepts the new `refuel_stops` array; falls back to the legacy single
    `refuel_airport` (treating "" / "-" as no refuel). "-" (the retired "ANY")
    becomes no refuel — see the multi-stop spec migration note.
    """
    stops = cfg.get("refuel_stops")
    if isinstance(stops, list):
        # Keep repeated stops, INCLUDING consecutive ones: ['TK1','TK1'] means the
        # drone uses TK1 as a forward base — refuel, fly a SORTIE to targets and
        # back to TK1, refuel again, continue. The middle leg starts and ends at
        # TK1 but visits targets in between (NOT a zero-length leg). Collapsing
        # consecutive duplicates here destroyed that legitimate route.
        return [str(s) for s in stops if s not in ("", "-", None)]
    legacy = cfg.get("refuel_airport")
    if legacy and legacy not in ("", "-"):
        return [str(legacy)]
    return []


def parent_of(vid: str) -> str:
    """Physical-drone id for a (possibly virtual) drone id. '1_L2' -> '1'."""
    s = str(vid)
    return s.split(VLEG_SEP, 1)[0] if VLEG_SEP in s else s


def is_virtual(vid: str) -> bool:
    return VLEG_SEP in str(vid)


def sort_key(vid: str) -> Tuple[int, int]:
    """Stable sort key for a (possibly virtual) drone id. '1' -> (1, 0);
    '1_L2' -> (1, 2). Safe where code used `int(did)` before virtual ids existed."""
    s = str(vid)
    if VLEG_SEP in s:
        parent, leg = s.split(VLEG_SEP, 1)
        try:
            return (int(parent), int(leg))
        except ValueError:
            return (0, 0)
    try:
        return (int(s), 0)
    except ValueError:
        return (0, 0)


def leg_start_fuel(cfg: Dict[str, Any], leg_number: int, full_tank: float) -> float:
    """Fuel available at the START of a leg (§11.10) — THE one place this rule lives.

    Every leg refuels to a full tank, so a leg starts with `full_tank`. The ONLY
    exception is leg 1 of a dynamic replan, which starts with the REDUCED transient
    `start_fuel` (the fuel left at the cut). Both virtual-leg builders
    (`expand_to_virtual_drones` pre-solve, `split_solution_to_virtual_legs`
    post-solve) call this so the leg-1 budget can never drift between them.

    `leg_number` is 1-based.
    """
    if leg_number == 1:
        start_fuel = cfg.get("start_fuel")
        return float(full_tank if start_fuel is None else start_fuel)
    return float(full_tank)


def strip_post_leg1_start_fuel(cfg: Dict[str, Any], leg_number: int) -> None:
    """Drop the transient `start_fuel` from any leg past leg 1 (mutates cfg).

    Later legs refuel to a full tank, so the reduced leg-1 start fuel must not
    ride along on them. Paired with `leg_start_fuel` — the same rule, the same
    home, used by both virtual-leg builders. 1-based `leg_number`."""
    if leg_number != 1:
        cfg.pop("start_fuel", None)


def _route_distance_from_matrix(
    route: List[str], distance_matrix: Dict[str, Any]
) -> float:
    """Sum the SAM-aware matrix distance along a route. 0.0 if no usable matrix.

    Used to seed a split leg's already-flown distance when the source route did
    not carry leg_distances[] (legacy/edge data) — otherwise the optimizer would
    read the leg as empty and over-pack it past its fuel budget."""
    if not distance_matrix or len(route) < 2:
        return 0.0
    labels = distance_matrix.get("labels", [])
    matrix = distance_matrix.get("matrix", [])
    if not labels or not matrix:
        return 0.0
    total = 0.0
    for a, b in zip(route, route[1:]):
        try:
            total += float(matrix[labels.index(str(a))][labels.index(str(b))])
        except (ValueError, IndexError):
            return 0.0  # incomplete matrix → caller keeps its own fallback
    return total


def expand_to_virtual_drones(
    drone_configs: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Expand physical drones with refuel stops into per-leg virtual drones.

    Returns a NEW drone_configs dict keyed by virtual-drone id. A drone with no
    stops passes through under its own id. Each virtual config carries the leg's
    start_airport / end_airport, the full-tank fuel_budget, and leg metadata
    (parent_drone, leg_number, total_legs, is_virtual). The first leg also keeps
    the parent's transient `start_fuel` (reduced leg-1 fuel on a replan).
    """
    out: Dict[str, Dict[str, Any]] = {}

    for did, cfg in (drone_configs or {}).items():
        cfg = cfg or {}
        if cfg.get("enabled") is False:
            # Keep disabled drones as-is (the solver skips them) — do not expand.
            out[str(did)] = dict(cfg)
            continue

        stops = _refuel_stops(cfg)
        if not stops:
            # No refuel — single leg, unchanged id.
            base = dict(cfg)
            base["parent_drone"] = str(did)
            base["leg_number"] = 1
            base["total_legs"] = 1
            base["is_virtual"] = False
            out[str(did)] = base
            continue

        points = [cfg.get("start_airport")] + stops + [cfg.get("end_airport")]
        num_legs = len(points) - 1
        for leg in range(num_legs):
            vid = f"{did}{VLEG_SEP}{leg + 1}"
            v = dict(cfg)
            v["parent_drone"] = str(did)
            v["leg_number"] = leg + 1
            v["total_legs"] = num_legs
            v["is_virtual"] = True
            v["start_airport"] = points[leg]
            v["end_airport"] = points[leg + 1]
            # Each leg refuels to a full tank; only leg 1 of a replan keeps the
            # reduced transient start_fuel (§11.10, shared rule).
            strip_post_leg1_start_fuel(v, leg + 1)
            # A virtual leg does not itself plan further refuels.
            v.pop("refuel_stops", None)
            v.pop("refuel_airport", None)
            out[vid] = v

    return out


def merge_virtual_to_physical(
    virtual_routes: Dict[str, Dict[str, Any]],
    drone_configs: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Merge per-leg virtual-drone routes back into physical-drone routes.

    For a drone with stops, concatenate its legs L1..Ln in order, dropping the
    duplicated refuel-airport joint between consecutive legs, and emit:
      route, distance (sum of legs), visited_targets (union), refuel_stops,
      leg_distances[] (per-leg distance), refuel_indices[] (route index of each
      refuel airport).
    A no-refuel drone passes its single route through unchanged.

    `virtual_routes` is keyed by virtual id ("1_L1", "1_L2", ... or "2"). Each
    value is a route dict with at least: route (list of waypoint ids), distance,
    visited_targets.
    """
    physical: Dict[str, Dict[str, Any]] = {}

    for did, cfg in (drone_configs or {}).items():
        did = str(did)
        cfg = cfg or {}
        stops = _refuel_stops(cfg)

        if not stops:
            if did in virtual_routes:
                physical[did] = dict(virtual_routes[did])
            continue

        num_legs = len(stops) + 1
        merged_route: List[str] = []
        leg_distances: List[float] = []
        refuel_indices: List[int] = []
        visited: List[str] = []
        merged_traj: List[Any] = []
        points = 0

        for leg in range(1, num_legs + 1):
            vid = f"{did}{VLEG_SEP}{leg}"
            vroute = virtual_routes.get(vid) or {}
            leg_route = list(vroute.get("route") or [])
            leg_traj = list(vroute.get("trajectory") or [])
            leg_distances.append(float(vroute.get("distance") or 0.0))
            points += int(vroute.get("points") or 0)
            for t in vroute.get("visited_targets") or []:
                if t not in visited:
                    visited.append(t)

            if leg == 1:
                merged_route.extend(leg_route)
                merged_traj.extend(leg_traj)
            else:
                # Drop the first waypoint if it duplicates the join (the refuel
                # airport that ended the previous leg and starts this one).
                if (
                    merged_route
                    and leg_route
                    and str(merged_route[-1]) == str(leg_route[0])
                ):
                    merged_route.extend(leg_route[1:])
                else:
                    merged_route.extend(leg_route)
                # Stitch trajectories, dropping the duplicated join point.
                if merged_traj and leg_traj:
                    merged_traj.extend(leg_traj[1:])
                else:
                    merged_traj.extend(leg_traj)

            # Mark the refuel index (the join) for every leg except the last.
            if leg < num_legs and merged_route:
                refuel_indices.append(len(merged_route) - 1)

        physical[did] = {
            "route": merged_route,
            "sequence": ",".join(str(w) for w in merged_route),
            "distance": sum(leg_distances),
            "trajectory": merged_traj,
            "points": points,
            "visited_targets": visited,
            "refuel_stops": stops,
            "refuel_indices": refuel_indices,
            "leg_distances": leg_distances,
            # The tank (full each leg) — needed by downstream fuel checks/stats.
            "fuel_budget": float(cfg.get("fuel_budget", 0) or 0),
        }

    return physical


# ---------------------------------------------------------------------------
# OPTIMIZER path: split an EXISTING solution's routes into per-leg virtual drones
# so the post-optimizer treats each leg as an ordinary drone (cross-leg target
# moves "just work"), then merge back. Generalizes the post_optimizer's old
# 2-leg split_refueling_drones / merge_virtual_legs to N legs.
# ---------------------------------------------------------------------------


def _route_refuel_positions(route: List[str], stops: List[str]) -> List[int]:
    """Indices in `route` where each refuel stop is found, in route order.

    Walks the route once, matching stops in the order they were declared so a
    repeated airport id maps to the right occurrence. Returns the interior split
    points (never index 0 or the final index)."""
    positions: List[int] = []
    cursor = 1  # never split at the start airport
    for stop in stops:
        for i in range(cursor, len(route) - 1):
            if str(route[i]) == str(stop):
                positions.append(i)
                cursor = i + 1
                break
    return positions


def split_solution_to_virtual_legs(
    solution: Dict[str, Any],
    drone_configs: Dict[str, Dict[str, Any]],
    env: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]], Dict[str, str]]:
    """Split each refueling drone's route + trajectory into per-leg virtual drones.

    Returns (split_solution, split_configs, leg_mapping) mirroring the
    post_optimizer's old split_refueling_drones contract, but for N legs. Each
    virtual route carries _virtual_leg / _parent_drone / _leg_number markers so
    merge_virtual_legs_n can group + reorder them. A non-refuel drone passes
    through under its own id. Trajectory is split at each refuel waypoint using
    waypoint_indices when present, else proportionally.

    Each leg's `distance` is the FUEL it already uses — flying plus loiter —
    because the optimizers read a leg's free fuel as fuel_budget - distance.
    env (its targets' types) is needed to count loiter on a leg the solver did
    not measure.
    """
    import copy

    routes = solution.get("routes", {})
    split_routes: Dict[str, Any] = {}
    split_configs: Dict[str, Dict[str, Any]] = {}
    leg_mapping: Dict[str, str] = {}

    for did, cfg in (drone_configs or {}).items():
        did = str(did)
        if cfg.get("enabled") is False:
            # A disabled drone has no leg to split, but it must still pass
            # through unchanged — dropping it here drops it from the whole
            # pipeline's output (this function's routes/configs/leg_mapping
            # are the only handles later merge steps have), which silently
            # removed a disabled drone from the checkpoint-replan response
            # entirely instead of returning it with its (correct) empty
            # route, leaving the CLIENT's last-known route for that drone on
            # screen with nothing to overwrite it.
            split_routes[did] = copy.deepcopy(routes.get(did, {}) or {})
            split_configs[did] = copy.deepcopy(cfg)
            leg_mapping[did] = did
            continue
        rd = routes.get(did, {}) or {}
        route = list(rd.get("route", []) or [])
        route_refuel_airport = rd.get("refuel_airport")
        stops = _refuel_stops(cfg) or (
            [str(route_refuel_airport)]
            if route_refuel_airport not in (None, "", "-")
            else []
        )

        # OPPORTUNISTIC routes carry their refuel structure on the ROUTE, not the
        # cfg: refuel_indices[] are the route positions where the drone refuels
        # (and refuel_stops[] the matching airport ids). The cfg-based `stops`
        # above is empty for them, so without this the route was treated as ONE
        # leg — and INSERT then packed targets past a single leg's tank (e.g. a
        # 35 km leg grew to 89 km > 70 tank). Prefer the route's own refuel_indices
        # as the exact cut points (they disambiguate a base visited twice, which
        # matching stop ids cannot).
        route_refuel_idx = [
            int(i)
            for i in (rd.get("refuel_indices") or [])
            if isinstance(i, (int, float)) and 0 < int(i) < len(route) - 1
        ]

        if not (stops or route_refuel_idx) or len(route) < 2:
            split_routes[did] = copy.deepcopy(rd)
            split_configs[did] = copy.deepcopy(cfg)
            leg_mapping[did] = did
            continue

        cut_idx = (
            sorted(set(route_refuel_idx))
            if route_refuel_idx
            else _route_refuel_positions(route, stops)
        )
        # Boundaries: 0, each refuel index, last index. Legs = boundaries-1.
        bounds = [0] + cut_idx + [len(route) - 1]
        traj = list(rd.get("trajectory", []) or [])
        wpi = rd.get("waypoint_indices", {}) or {}
        # Per-leg budget (§11.10): full tank each leg, except leg 1 of a replan
        # which keeps the reduced start_fuel — `leg_start_fuel` is the shared rule.
        # The optimizer reads each leg's budget from the route's fuel_budget
        # (post_optimizer _get_refuel_leg_info), so leg 1 must carry start_fuel, not
        # the full tank, or INSERT over-fills it and verify_solution_basic rejects it.
        full_tank = float(rd.get("fuel_budget", 0) or cfg.get("fuel_budget", 0) or 0)
        # Per-leg distance ALREADY flown — the optimizer computes a leg's remaining
        # fuel as (fuel_budget − route.distance), so a 0.0 here would make INSERT see
        # a full-empty leg and over-pack it. Seed each leg's distance from the
        # physical route's leg_distances[]; recompute from the matrix when absent.
        phys_leg_dists = rd.get("leg_distances") or []
        # This drone's distances: at its cruise altitude.
        _dist_matrix = (
            matrix_for_drone(solution.get("distance_matrix") or {}, did) or {}
        )
        _targets = {str(t.get("id")): t for t in (env or {}).get("targets", []) or []}

        def _traj_at(route_idx: int) -> int:
            v = wpi.get(str(route_idx), wpi.get(route_idx, -1))
            if isinstance(v, (int, float)) and v >= 0:
                return int(v)
            if route:
                return max(
                    0, min(len(traj) - 1, int(route_idx / len(route) * len(traj)))
                )
            return 0

        num_legs = len(bounds) - 1
        for leg in range(num_legs):
            a, b = bounds[leg], bounds[leg + 1]
            leg_route = route[a : b + 1]
            if traj:
                ta, tb = _traj_at(a), _traj_at(b)
                ta = max(0, min(ta, len(traj) - 1))
                tb = max(ta, min(tb, len(traj) - 1))
                leg_traj = traj[ta : tb + 1]
            else:
                leg_traj = []
            vid = f"{did}{VLEG_SEP}{leg + 1}"
            leg_number = leg + 1
            leg_budget = leg_start_fuel(cfg, leg_number, full_tank)
            # Fuel already spent on this leg (flying + loiter): the route's own
            # leg_distances[]; a single-leg route's own total; else the matrix
            # plus the loiter at its targets (never flying alone — the free
            # fuel the optimizer sees would include the loiter it must still
            # fly, and INSERT would over-pack the leg).
            if leg < len(phys_leg_dists):
                leg_dist = float(phys_leg_dists[leg])
            elif num_legs == 1 and rd.get("distance"):
                leg_dist = float(rd["distance"])
            else:
                leg_dist = _route_distance_from_matrix(
                    leg_route, _dist_matrix
                ) + compute_route_loiter_fuel(leg_route[1:], cfg, _targets)
            split_routes[vid] = {
                "route": leg_route,
                "sequence": ",".join(str(w) for w in leg_route),
                "distance": leg_dist,  # fuel already spent on this leg
                "fuel_budget": leg_budget,
                "trajectory": leg_traj,
                "points": sum(1 for w in leg_route if str(w).startswith("T")) * 5,
                "_virtual_leg": True,
                "_parent_drone": did,
                "_leg_number": leg_number,
            }
            leg_cfg = {
                **copy.deepcopy(cfg),
                "start_airport": route[a],
                "end_airport": route[b],
                "fuel_budget": leg_budget,
                # A virtual leg does not itself refuel.
                "refuel_airport": "",
                "refuel_stops": [],
            }
            strip_post_leg1_start_fuel(leg_cfg, leg_number)
            split_configs[vid] = leg_cfg
            leg_mapping[vid] = did

    split_solution = {
        "routes": split_routes,
        "distance_matrix": solution.get("distance_matrix"),
    }
    return split_solution, split_configs, leg_mapping


def merge_virtual_legs_n(
    solution: Dict[str, Any],
    original_drone_configs: Dict[str, Dict[str, Any]],
    leg_mapping: Dict[str, str],
) -> Dict[str, Any]:
    """Merge per-leg virtual drones (any N) back into physical-drone routes.

    Groups by _parent_drone, orders by _leg_number, concatenates routes +
    trajectories (dropping the duplicated refuel-airport joint), and emits
    leg_distances[] + refuel_indices[] + refuel_stops. Non-virtual routes pass
    through. Replaces the old 2-leg merge_virtual_legs."""
    routes = solution.get("routes", {})
    merged_routes: Dict[str, Any] = {}
    parent_legs: Dict[str, Dict[int, Dict[str, Any]]] = {}
    passthrough: Dict[str, Any] = {}

    for vid, rd in routes.items():
        if rd.get("_virtual_leg"):
            pid = str(rd.get("_parent_drone"))
            parent_legs.setdefault(pid, {})[int(rd.get("_leg_number", 1))] = rd
        else:
            passthrough[str(vid)] = rd

    for pid, legs in parent_legs.items():
        order = sorted(legs.keys())
        merged_route: List[str] = []
        merged_traj: List[Any] = []
        leg_distances: List[float] = []
        refuel_indices: List[int] = []
        points = 0
        fuel_budget = 0

        # Skip EMPTY legs: a base->base hop that carries no targets (e.g. an
        # optimizer move relocated all of a sortie's targets, leaving a bare
        # [TK1, TK1]). Merging it would (a) append a duplicate base to the route
        # -> "…,TK1,TK1,…", (b) push a 0 into leg_distances, and (c) add a phantom
        # refuel — inflating the leg count (⛽…+0+…, budget 4×tank instead of 3×).
        # A base->base leg with no target in between is not a refuel and not a
        # flight; drop it — the FIRST leg too (it showed as "L1 A4→A4 0"). Only
        # when every leg is empty is the first one kept, so the drone has a route.
        def _is_empty_leg(lr: List[str]) -> bool:
            has_target = any(
                str(w).upper().startswith("T") and not str(w).upper().startswith("TK")
                for w in lr
            )
            return (
                not has_target
                and len(lr) <= 2
                and (not lr or str(lr[0]) == str(lr[-1]))
            )

        kept = [
            n for n in order if not _is_empty_leg(list(legs[n].get("route", []) or []))
        ]
        if not kept:
            kept = order[:1]
        for n in kept:
            leg = legs[n]
            lr = list(leg.get("route", []) or [])
            lt = list(leg.get("trajectory", []) or [])
            leg_distances.append(float(leg.get("distance", 0) or 0))
            points += int(leg.get("points", 0) or 0)
            fuel_budget = fuel_budget or leg.get("fuel_budget", 0) or 0
            if not merged_route:
                merged_route = list(lr)
                merged_traj = list(lt)
            else:
                # Drop a duplicated join waypoint / trajectory point.
                if lr and merged_route and str(merged_route[-1]) == str(lr[0]):
                    merged_route.extend(lr[1:])
                else:
                    merged_route.extend(lr)
                if lt and merged_traj:
                    merged_traj.extend(lt[1:])
                else:
                    merged_traj.extend(lt)
            if n != kept[-1] and merged_route:
                refuel_indices.append(len(merged_route) - 1)

        # The stops this merged route ACTUALLY refuels at — not the configured
        # refuel_stops list, which differs under opportunistic refuel (the planner
        # picks from refuel_options) or when an empty leg was dropped.
        stops = [
            str(merged_route[i]) for i in refuel_indices if 0 <= i < len(merged_route)
        ]
        merged_routes[pid] = {
            "route": merged_route,
            "sequence": ",".join(str(w) for w in merged_route),
            "distance": sum(leg_distances),
            "fuel_budget": fuel_budget,
            "trajectory": merged_traj,
            "points": points,
            "refuel_stops": stops,
            "refuel_indices": refuel_indices,
            "leg_distances": leg_distances,
        }

    merged_routes.update(passthrough)
    result = dict(solution)
    result["routes"] = merged_routes
    return result
