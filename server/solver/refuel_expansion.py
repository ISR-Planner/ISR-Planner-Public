"""Pure helpers for refuel-aware target expansion.

These functions do not call the mission solver. They operate on an existing route,
refuel metadata, and a distance matrix so the expansion behavior can be tested
before it is wired into the solver pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set

from .altitude import matrix_for_drone


@dataclass(frozen=True)
class FuelLeg:
    index: int
    start_index: int
    end_index: int
    route: List[str]
    distance: float
    remaining_fuel: float


@dataclass(frozen=True)
class LegInsertion:
    leg_index: int
    insert_index: int
    target_id: str
    added_distance: float
    new_leg_distance: float


@dataclass(frozen=True)
class ExtraSortie:
    base_index: int
    base_id: str
    target_id: str
    sortie_distance: float
    refuel_index: int


def split_route_into_legs(
    route: Sequence[str], refuel_indices: Sequence[int]
) -> List[List[str]]:
    """Split a physical route into fresh-fuel legs.

    Each refuel index is shared: it is the endpoint of one leg and the startpoint
    of the next leg.
    """
    route_list = [str(x) for x in route]
    if not route_list:
        return []

    clean_indices = sorted(
        {int(idx) for idx in refuel_indices if 0 < int(idx) < len(route_list) - 1}
    )
    legs: List[List[str]] = []
    start = 0
    for idx in clean_indices:
        legs.append(route_list[start : idx + 1])
        start = idx
    legs.append(route_list[start:])
    return legs


def path_distance(
    path: Sequence[str], labels: Sequence[str], matrix: Sequence[Sequence[float]]
) -> float:
    """Return matrix distance for a waypoint path."""
    if len(path) < 2:
        return 0.0
    label_index = {str(label): i for i, label in enumerate(labels)}
    total = 0.0
    for a, b in zip(path, path[1:]):
        ia = label_index[str(a)]
        ib = label_index[str(b)]
        total += float(matrix[ia][ib])
    return total


def fuel_legs(
    route: Sequence[str],
    refuel_indices: Sequence[int],
    labels: Sequence[str],
    matrix: Sequence[Sequence[float]],
    fuel_budget: float,
) -> List[FuelLeg]:
    """Return route legs with per-leg distance and remaining fuel."""
    route_list = [str(x) for x in route]
    legs: List[FuelLeg] = []
    start = 0
    for index, leg in enumerate(split_route_into_legs(route_list, refuel_indices)):
        end = start + len(leg) - 1
        distance = path_distance(leg, labels, matrix)
        legs.append(
            FuelLeg(
                index=index,
                start_index=start,
                end_index=end,
                route=leg,
                distance=distance,
                remaining_fuel=float(fuel_budget) - distance,
            )
        )
        start = end
    return legs


def best_leg_insertion(
    route: Sequence[str],
    refuel_indices: Sequence[int],
    target_id: str,
    labels: Sequence[str],
    matrix: Sequence[Sequence[float]],
    fuel_budget: float,
) -> Optional[LegInsertion]:
    """Find the cheapest feasible insertion into an existing fresh-fuel leg."""
    label_index = {str(label): i for i, label in enumerate(labels)}
    target = str(target_id)
    if target not in label_index:
        return None

    best: Optional[LegInsertion] = None
    for leg in fuel_legs(route, refuel_indices, labels, matrix, fuel_budget):
        if len(leg.route) < 2:
            continue
        for offset in range(1, len(leg.route)):
            prev_id = leg.route[offset - 1]
            next_id = leg.route[offset]
            added = (
                float(matrix[label_index[prev_id]][label_index[target]])
                + float(matrix[label_index[target]][label_index[next_id]])
                - float(matrix[label_index[prev_id]][label_index[next_id]])
            )
            new_distance = leg.distance + added
            if new_distance > float(fuel_budget) + 1e-6:
                continue
            candidate = LegInsertion(
                leg_index=leg.index,
                insert_index=leg.start_index + offset,
                target_id=target,
                added_distance=added,
                new_leg_distance=new_distance,
            )
            if best is None or candidate.added_distance < best.added_distance:
                best = candidate
    return best


def apply_leg_insertion(route: Sequence[str], insertion: LegInsertion) -> List[str]:
    route_list = [str(x) for x in route]
    return (
        route_list[: insertion.insert_index]
        + [insertion.target_id]
        + route_list[insertion.insert_index :]
    )


def best_extra_sortie(
    route: Sequence[str],
    refuel_indices: Sequence[int],
    target_id: str,
    refuel_bases: Sequence[str],
    labels: Sequence[str],
    matrix: Sequence[Sequence[float]],
    fuel_budget: float,
    max_refuels: int,
) -> Optional[ExtraSortie]:
    """Find a feasible one-target sortie from the final base.

    This first primitive supports the common shape `... -> base` becoming
    `... -> base -> target -> base`. The old final base becomes a refuel stop.
    """
    route_list = [str(x) for x in route]
    if not route_list or len(refuel_indices) >= int(max_refuels):
        return None

    bases = {str(base) for base in refuel_bases}
    final_base = route_list[-1]
    if final_base not in bases:
        return None

    label_index = {str(label): i for i, label in enumerate(labels)}
    target = str(target_id)
    if final_base not in label_index or target not in label_index:
        return None

    sortie_distance = float(
        matrix[label_index[final_base]][label_index[target]]
    ) + float(matrix[label_index[target]][label_index[final_base]])
    if sortie_distance > float(fuel_budget) + 1e-6:
        return None
    return ExtraSortie(
        base_index=len(route_list) - 1,
        base_id=final_base,
        target_id=target,
        sortie_distance=sortie_distance,
        refuel_index=len(route_list) - 1,
    )


def apply_extra_sortie(
    route: Sequence[str],
    refuel_indices: Sequence[int],
    sortie: ExtraSortie,
) -> Dict[str, List[int] | List[str]]:
    route_list = [str(x) for x in route]
    new_route = route_list + [sortie.target_id, sortie.base_id]
    new_refuels = sorted({int(idx) for idx in refuel_indices} | {sortie.refuel_index})
    return {"route": new_route, "refuel_indices": new_refuels}


def expand_route_with_refuel_sorties(
    route_data: Dict[str, Any],
    target_ids: Sequence[str],
    refuel_bases: Sequence[str],
    labels: Sequence[str],
    matrix: Sequence[Sequence[float]],
    fuel_budget: float,
    max_refuels: int,
    target_priority: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Spend unused refuels on missed targets via same-base sorties.

    This is a structural optimizer: it may create new fuel legs
    `base -> target -> base`, unlike Insert/Swap which only edit existing legs.
    """
    route = [str(x) for x in route_data.get("route", []) or []]
    if len(route) < 1:
        return {"route_data": dict(route_data), "insertions": []}

    bases = [str(b) for b in refuel_bases if b not in ("", "-", None)]
    base_set = set(bases)
    if not bases or not labels or not matrix:
        return {"route_data": dict(route_data), "insertions": []}

    label_index = {str(label): i for i, label in enumerate(labels)}

    def dist(a: str, b: str) -> float:
        try:
            return float(matrix[label_index[a]][label_index[b]])
        except (KeyError, IndexError, TypeError, ValueError):
            return float("inf")

    def sortie_dist(base: str, tid: str) -> float:
        return dist(base, tid) + dist(tid, base)

    refuel_indices = sorted(
        {
            int(i)
            for i in (route_data.get("refuel_indices") or [])
            if isinstance(i, (int, float)) and 0 < int(i) < len(route) - 1
        }
    )
    used_refuels = len(refuel_indices)
    if used_refuels >= int(max_refuels):
        return {"route_data": dict(route_data), "insertions": []}

    target_set = {str(t) for t in target_ids}
    visited = {
        str(w) for w in route if str(w).startswith("T") and not str(w).startswith("TK")
    }
    remaining = [tid for tid in target_set if tid not in visited and tid in label_index]
    priority = target_priority or {}
    insertions: List[Dict[str, Any]] = []

    def shifted_refuel_indices(indices: Sequence[int], insert_after: int) -> List[int]:
        return [idx + 2 if idx > insert_after else idx for idx in indices]

    def sortie_refuel_indices(base_idx: int) -> List[int]:
        shifted = set(shifted_refuel_indices(refuel_indices, base_idx))
        original_continues = base_idx < len(route) - 1
        base_already_refueled = base_idx in set(refuel_indices)

        # Existing base refuels can power the new sortie. Otherwise, a non-start
        # base needs a refuel before departing on the added sortie.
        if not base_already_refueled and base_idx != 0:
            shifted.add(base_idx)

        # If the old route continued after this base, returning from the added
        # sortie must also create a fresh tank for the original continuation.
        if original_continues:
            shifted.add(base_idx + 2)

        return sorted(shifted)

    while remaining and used_refuels < int(max_refuels):
        base_positions = [(idx, wp) for idx, wp in enumerate(route) if wp in base_set]
        candidates = []
        for tid in remaining:
            for base_idx, base in base_positions:
                sd = sortie_dist(base, tid)
                if sd <= float(fuel_budget) + 1e-6:
                    candidate_refuels = sortie_refuel_indices(base_idx)
                    if len(candidate_refuels) > int(max_refuels):
                        continue
                    candidates.append(
                        (
                            -int(priority.get(tid, 5)),
                            sd,
                            route_progress_penalty(route, base_idx),
                            tid,
                            base_idx,
                            base,
                            candidate_refuels,
                        )
                    )
        if not candidates:
            break

        _neg_pri, sd, _penalty, tid, base_idx, base, candidate_refuels = min(candidates)
        route = route[: base_idx + 1] + [tid, base] + route[base_idx + 1 :]
        refuel_indices = candidate_refuels
        used_refuels = len(refuel_indices)

        visited.add(tid)
        remaining = [x for x in remaining if x != tid]
        insertions.append(
            {
                "target": tid,
                "base": base,
                "refuel_indices": candidate_refuels,
                "distance": sd,
            }
        )

    if not insertions:
        return {"route_data": dict(route_data), "insertions": []}

    new_data = dict(route_data)
    new_data["route"] = route
    new_data["sequence"] = ",".join(route)
    new_data["refuel_indices"] = sorted(refuel_indices)
    new_data["refuel_stops"] = [
        route[idx] for idx in new_data["refuel_indices"] if 0 <= idx < len(route)
    ]
    new_data["leg_distances"] = [
        path_distance(leg, labels, matrix)
        for leg in split_route_into_legs(route, new_data["refuel_indices"])
    ]
    new_data["distance"] = sum(float(x) for x in new_data["leg_distances"])
    new_data["visited_targets"] = [
        w for w in route if str(w).startswith("T") and not str(w).startswith("TK")
    ]
    new_data["points"] = sum(
        int(priority.get(t, 5)) for t in new_data["visited_targets"]
    )

    return {"route_data": new_data, "insertions": insertions}


def route_progress_penalty(route: Sequence[str], base_idx: int) -> int:
    """Prefer later equal-cost base opportunities for append-like expansion."""
    return -int(base_idx)


def expand_solution_with_refuel_sorties(
    solution: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Dict[str, Any]],
    distance_matrix: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Expand all physical routes using spare refuel capacity."""
    routes = {str(did): dict(rd) for did, rd in (solution.get("routes") or {}).items()}
    targets = env.get("targets", []) or []
    target_ids = [str(t.get("id")) for t in targets if t.get("id")]
    target_priority = {str(t.get("id")): int(t.get("priority", 5)) for t in targets}

    def is_target(w: Any) -> bool:
        return str(w).startswith("T") and not str(w).startswith("TK")

    # A target already on ANY drone's route is not "missed": a sortie to it
    # would visit it twice (and a later optimizer merges that into T-T).
    taken: Set[str] = {
        str(w)
        for rd in routes.values()
        for w in (rd or {}).get("route") or []
        if is_target(w)
    }

    insertions: List[Dict[str, Any]] = []
    for did, rd in list(routes.items()):
        cfg = drone_configs.get(str(did), {})
        if cfg.get("enabled") is False:
            continue
        # This drone's distances and unreachable targets: at its altitude.
        dist_data = matrix_for_drone(distance_matrix, did) or {}
        labels = dist_data.get("labels", [])
        matrix = dist_data.get("matrix", [])
        excluded: Set[str] = {str(t) for t in dist_data.get("excluded_targets", [])}
        all_target_ids = [
            tid for tid in target_ids if tid not in excluded and tid not in taken
        ]
        refuel_options = [
            str(x)
            for x in (cfg.get("refuel_options") or rd.get("refuel_stops") or [])
            if x not in ("", "-", None)
        ]
        max_refuels = int(
            cfg.get("max_refuels", len(rd.get("refuel_indices") or [])) or 0
        )
        fuel_budget = float(rd.get("fuel_budget") or cfg.get("fuel_budget", 0) or 0)
        if not refuel_options or max_refuels <= len(rd.get("refuel_indices") or []):
            continue
        expanded = expand_route_with_refuel_sorties(
            rd,
            all_target_ids,
            refuel_options,
            labels,
            matrix,
            fuel_budget,
            max_refuels,
            target_priority,
        )
        if expanded.get("insertions"):
            routes[did] = expanded["route_data"]
            for ins in expanded["insertions"]:
                insertions.append({"drone": did, **ins})
                taken.add(str(ins.get("target")))

    return {
        "success": True,
        "routes": routes,
        "sequences": {
            did: rd.get("sequence", ",".join(rd.get("route", [])))
            for did, rd in routes.items()
        },
        "insertions": insertions,
    }
