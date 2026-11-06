"""Pure virtual-sortie model for refuel-capable physical drones.

This module is intentionally solver-free. It models the architecture where one
physical drone with refuel capacity becomes multiple bounded sortie slots, each
of which can later be solved by the normal per-leg Held-Karp path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class SortieSlot:
    id: str
    parent_drone: str
    sortie_number: int
    start_base: str
    end_base: Optional[str]
    is_final: bool
    fuel_budget: float
    start_fuel: float
    refuel_after: bool


@dataclass(frozen=True)
class SortieAllocation:
    clusters: Dict[str, List[str]]
    unassigned: List[str]


def build_sortie_slots(
    drone_id: str,
    cfg: Dict[str, Any],
    *,
    max_refuels: Optional[int] = None,
) -> List[SortieSlot]:
    """Build virtual sortie slots for one physical drone.

    Phase A/B keeps base choice conservative: if the start airport is an allowed
    refuel option, same-base sorties use it. Otherwise the first allowed refuel
    option is used; if no refuel options exist, the drone gets one final sortie.
    """
    did = str(drone_id)
    start = str(cfg.get("start_airport") or "")
    raw_end = str(cfg.get("end_airport") or start)
    final_end = None if raw_end == "-" else raw_end
    fuel_budget = float(cfg.get("fuel_budget", 0.0) or 0.0)
    start_fuel = float(cfg.get("start_fuel", fuel_budget) or fuel_budget)
    refuel_options = [
        str(x) for x in (cfg.get("refuel_options") or []) if x not in ("", "-", None)
    ]
    refuel_count = int(
        cfg.get("max_refuels", 0) if max_refuels is None else max_refuels
    )

    if not refuel_options or refuel_count <= 0:
        return [
            SortieSlot(
                id=f"{did}_S1",
                parent_drone=did,
                sortie_number=1,
                start_base=start,
                end_base=final_end,
                is_final=True,
                fuel_budget=fuel_budget,
                start_fuel=start_fuel,
                refuel_after=False,
            )
        ]

    base = start if start in refuel_options else refuel_options[0]
    slots: List[SortieSlot] = []
    total_slots = refuel_count + 1
    for sortie_number in range(1, total_slots + 1):
        is_final = sortie_number == total_slots
        slots.append(
            SortieSlot(
                id=f"{did}_S{sortie_number}",
                parent_drone=did,
                sortie_number=sortie_number,
                start_base=start if sortie_number == 1 else base,
                end_base=final_end if is_final else base,
                is_final=is_final,
                fuel_budget=fuel_budget,
                start_fuel=start_fuel if sortie_number == 1 else fuel_budget,
                refuel_after=not is_final,
            )
        )
    return slots


def split_targets_same_base(
    targets: Sequence[Dict[str, Any]],
    slots: Sequence[SortieSlot],
    labels: Sequence[str],
    matrix: Sequence[Sequence[float]],
    *,
    max_targets_per_sortie: int = 12,
) -> SortieAllocation:
    """Cluster targets across same-base sortie slots.

    This is a bounded, deterministic allocator for same-base virtual sorties. It
    seeds hard targets first, then grows clusters by cheapest approximate route
    insertion while respecting fuel and per-sortie target caps.
    """
    usable_slots = [slot for slot in slots if slot.start_base and slot.end_base]
    clusters: Dict[str, List[str]] = {slot.id: [] for slot in usable_slots}
    if not usable_slots:
        return SortieAllocation(
            clusters={}, unassigned=[_target_id(t) for t in targets]
        )

    target_by_id = {_target_id(t): dict(t) for t in targets}
    candidates = [
        tid
        for tid in target_by_id
        if _round_trip_feasible(
            usable_slots[0].start_base,
            tid,
            labels,
            matrix,
            max(slot.fuel_budget for slot in usable_slots),
        )
    ]
    unassigned = [tid for tid in target_by_id if tid not in candidates]

    candidates.sort(
        key=lambda tid: (
            -_priority(target_by_id[tid]),
            -_distance(usable_slots[0].start_base, tid, labels, matrix),
            tid,
        )
    )

    # Seed each sortie with one hard/high-value target when possible.
    remaining = list(candidates)
    for slot in usable_slots:
        if not remaining:
            break
        if slot.end_base is None:  # never: usable_slots all have one
            continue
        tid = remaining.pop(0)
        if (
            _route_distance(
                [slot.start_base, tid, slot.end_base],
                labels,
                matrix,
            )
            <= slot.fuel_budget + 1e-6
        ):
            clusters[slot.id].append(tid)
        else:
            unassigned.append(tid)

    for tid in remaining:
        best_slot: Optional[SortieSlot] = None
        best_cost = float("inf")
        for slot in usable_slots:
            cluster = clusters[slot.id]
            if len(cluster) >= max_targets_per_sortie or slot.end_base is None:
                continue
            current = _approx_sortie_distance(
                slot.start_base, cluster, slot.end_base, labels, matrix
            )
            candidate = _approx_sortie_distance(
                slot.start_base, cluster + [tid], slot.end_base, labels, matrix
            )
            if candidate > slot.fuel_budget + 1e-6:
                continue
            added = candidate - current
            if added < best_cost:
                best_slot = slot
                best_cost = added
        if best_slot is None:
            unassigned.append(tid)
        else:
            clusters[best_slot.id].append(tid)

    return SortieAllocation(clusters=clusters, unassigned=unassigned)


def merge_sorties_to_physical_route(
    drone_id: str,
    slots: Sequence[SortieSlot],
    sortie_routes: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Merge solved sortie routes back into one physical-drone route."""
    merged_route: List[str] = []
    leg_distances: List[float] = []
    refuel_indices: List[int] = []
    visited: List[str] = []
    points = 0

    used_slots = [
        slot
        for slot in sorted(slots, key=lambda s: s.sortie_number)
        if sortie_routes.get(slot.id, {}).get("route")
    ]

    for slot_index, slot in enumerate(used_slots):
        data = sortie_routes.get(slot.id, {})
        route = [str(x) for x in data.get("route", [])]
        if not route:
            continue
        if merged_route and route and merged_route[-1] == route[0]:
            merged_route.extend(route[1:])
        else:
            merged_route.extend(route)

        leg_distances.append(float(data.get("distance", 0.0) or 0.0))
        points += int(data.get("points", 0) or 0)
        for tid in data.get("visited_targets") or _targets_in_route(route):
            tid = str(tid)
            if tid not in visited:
                visited.append(tid)

        if slot_index < len(used_slots) - 1 and merged_route:
            refuel_indices.append(len(merged_route) - 1)

    refuel_stops = [
        merged_route[idx] for idx in refuel_indices if 0 <= idx < len(merged_route)
    ]
    return {
        "route": merged_route,
        "sequence": ",".join(merged_route),
        "distance": sum(leg_distances),
        "points": points,
        "visited_targets": visited,
        "refuel_indices": refuel_indices,
        "refuel_stops": refuel_stops,
        "leg_distances": leg_distances,
        "fuel_budget": used_slots[0].fuel_budget if used_slots else 0.0,
        "parent_drone": str(drone_id),
    }


def _target_id(target: Dict[str, Any]) -> str:
    return str(target.get("id") or target.get("label") or "")


def _priority(target: Dict[str, Any]) -> int:
    return int(target.get("priority", 0) or 0)


def _targets_in_route(route: Sequence[str]) -> List[str]:
    return [str(x) for x in route if str(x).startswith("T")]


def _distance(
    a: str,
    b: str,
    labels: Sequence[str],
    matrix: Sequence[Sequence[float]],
) -> float:
    label_index = {str(label): i for i, label in enumerate(labels)}
    ia = label_index[str(a)]
    ib = label_index[str(b)]
    return float(matrix[ia][ib])


def _route_distance(
    route: Sequence[str],
    labels: Sequence[str],
    matrix: Sequence[Sequence[float]],
) -> float:
    return sum(
        _distance(str(a), str(b), labels, matrix) for a, b in zip(route, route[1:])
    )


def _round_trip_feasible(
    base: str,
    target_id: str,
    labels: Sequence[str],
    matrix: Sequence[Sequence[float]],
    fuel_budget: float,
) -> bool:
    return (
        _distance(base, target_id, labels, matrix)
        + _distance(target_id, base, labels, matrix)
    ) <= float(fuel_budget) + 1e-6


def _approx_sortie_distance(
    start: str,
    target_ids: Sequence[str],
    end: str,
    labels: Sequence[str],
    matrix: Sequence[Sequence[float]],
) -> float:
    """Nearest-neighbor route length approximation for cluster feasibility."""
    remaining = [str(tid) for tid in target_ids]
    current = str(start)
    total = 0.0
    while remaining:
        next_target = min(
            remaining,
            key=lambda tid: (_distance(current, tid, labels, matrix), tid),
        )
        total += _distance(current, next_target, labels, matrix)
        current = next_target
        remaining.remove(next_target)
    total += _distance(current, str(end), labels, matrix)
    return total
