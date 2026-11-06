"""
Target Allocator for Multi-Drone Mission Planning

Distributes targets among available drones based on various strategies
before individual drone route optimization.

This is a LangGraph-compatible tool.
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from .altitude import matrix_for_drone
from .target_access import type_allowed


class AllocationStrategy(str, Enum):
    """Available allocation strategies."""

    GEOGRAPHIC = (
        "geographic"  # Assign targets to geographically closest drone's airport
    )
    INSERTION = "insertion"  # Cheapest insertion - minimize total tour cost
    GREEDY = "greedy"  # Assign highest priority targets to nearest capable drone
    BALANCED = "balanced"  # Distribute targets evenly by count
    EFFICIENT = "efficient"  # Maximize priority/fuel ratio (auction-based)
    EXCLUSIVE = "exclusive"  # Prioritize targets only one drone can reach


# Share of each refuel's tank counted as usable: the rest pays for the detour
# to the tanker, which is not known until the solver places the stop.
REFUEL_TANK_SHARE = 0.8
# Default refuel count when a drone lists refuel points but no cap (the solver's
# own floor in solve_mission.py).
DEFAULT_MAX_REFUELS = 4
_UNREACHABLE_KM = 1_000_000.0


def allocation_fuel(cfg: Dict[str, Any]) -> Optional[float]:
    """Fuel (km) the allocator may plan for a drone, or None = no limit.

    The fuel at the start (the tank, or `start_fuel` on a replan) plus, for a
    drone that may refuel, REFUEL_TANK_SHARE of a tank per refuel it has left.
    """
    if cfg.get("fuel_budget") is None:
        return None
    tank = float(cfg["fuel_budget"])
    start = cfg.get("start_fuel")
    fuel = tank if start is None else float(start)
    options = [
        s
        for s in (cfg.get("refuel_options") or [])
        if s and str(s) not in ("-", "None")
    ]
    if options and cfg.get("opportunistic_refuel", True):
        cap = cfg.get("max_refuels")
        refuels = (
            max(len(set(map(str, options))), DEFAULT_MAX_REFUELS)
            if cap is None
            else int(cap)
        )
        refuels = max(0, refuels - int(cfg.get("refuels_used", 0) or 0))
        fuel += refuels * tank * REFUEL_TANK_SHARE
    return fuel


class _FuelEstimate:
    """Each drone's estimated fuel for the targets it has been given so far.

    The route order is not known until the solver runs, so each drone keeps a
    rough route (start airport -> targets -> end airport) that every new target
    joins at its cheapest place. Its length plus the targets' loiter fuel is the
    estimate. A drone "has room" for a target when the estimate with it stays
    within allocation_fuel(). A drone without a fuel budget always has room.
    """

    def __init__(self, allocator, drone_configs, targets, airports):
        from .optimizers.common import compute_route_loiter_fuel

        self._dist = allocator._get_distance
        self._configs = drone_configs
        self._targets = {str(t["id"]): t for t in targets}
        self._loiter = compute_route_loiter_fuel
        airport_ids = {str(a["id"]) for a in airports}
        starts = allocator._start_positions(drone_configs, airports)
        self.tours: Dict[str, List[str]] = {}
        self.used: Dict[str, float] = {}
        self.fuel: Dict[str, Optional[float]] = {}
        for did, cfg in drone_configs.items():
            start = str(starts[did] or "A1")
            end = str(cfg.get("end_airport") or cfg.get("endAirport") or start)
            # "-" = the solver picks the landing airport: the route stays open.
            tour = (
                [start] if end == "-" else [start, end if end in airport_ids else start]
            )
            self.tours[did] = tour
            self.used[did] = (
                self._leg(did, tour[0], tour[-1]) if len(tour) == 2 else 0.0
            )
            self.fuel[did] = allocation_fuel(cfg)

    def _leg(self, did: str, a: str, b: str) -> float:
        try:
            d = self._dist(a, b, None, drone_id=did)
        except RuntimeError:  # not in the matrix: treat as unreachable
            return _UNREACHABLE_KM
        return _UNREACHABLE_KM if d == float("inf") else d

    def insertion(self, did: str, tid: str) -> Tuple[float, int]:
        """(extra km, position) of the cheapest place for tid in did's route."""
        tour = self.tours[did]
        best = (float("inf"), len(tour))
        for pos in range(1, len(tour)):
            extra = (
                self._leg(did, tour[pos - 1], tid)
                + self._leg(did, tid, tour[pos])
                - self._leg(did, tour[pos - 1], tour[pos])
            )
            best = min(best, (extra, pos))
        if len(tour) == 1 or self._open(did):
            best = min(best, (self._leg(did, tour[-1], tid), len(tour)))
        return best

    def _open(self, did: str) -> bool:
        cfg = self._configs[did]
        return str(cfg.get("end_airport") or cfg.get("endAirport") or "") == "-"

    def _loiter_km(self, did: str, tid: str) -> float:
        return self._loiter([tid], self._configs[did], self._targets)

    def has_room(self, did: str, tid: str, extra: Optional[float] = None) -> bool:
        """Whether did's estimate stays within its fuel with tid added
        (`extra` = tid's insertion km when the caller already has it)."""
        fuel = self.fuel[did]
        if fuel is None:
            return True
        if extra is None:
            extra, _ = self.insertion(did, tid)
        return self.used[did] + extra + self._loiter_km(did, tid) <= fuel + 1e-6

    def add(self, did: str, tid: str) -> None:
        extra, pos = self.insertion(did, tid)
        self.tours[did].insert(pos, tid)
        self.used[did] += extra + self._loiter_km(did, tid)

    def choose(self, tid: str, ranked: List[Tuple[Any, str]]) -> Optional[str]:
        """Best-ranked drone (lowest key) with room for tid; when none has room,
        the best-ranked drone anyway (the solver drops what really doesn't fit)."""
        ranked = sorted(ranked, key=lambda kd: kd[0])
        for _key, did in ranked:
            if self.has_room(did, tid):
                return did
        return ranked[0][1] if ranked else None


class TargetAllocator:
    """
    Allocates targets to drones based on configurable strategies.

    Takes into account:
    - Drone capabilities (which target types each drone can handle)
    - Drone fuel: every strategy gives a target to its best drone WITH ROOM for
      it (see _FuelEstimate); only when no drone has room does it fall back to
      its best drone regardless
    - Target priorities
    - Geographic distribution
    - Distance matrix (SAM-aware if available)
    """

    def __init__(self):
        self._distance_matrix: Optional[Dict[str, Any]] = None

    def set_distance_matrix(self, matrix_data: Dict[str, Any]):
        """Set the pre-calculated distance matrix for allocation decisions."""
        self._distance_matrix = matrix_data

    def allocate(
        self,
        targets: List[Dict[str, Any]],
        drone_configs: Dict[str, Dict[str, Any]],
        airports: List[Dict[str, Any]],
        strategy: AllocationStrategy = AllocationStrategy.EFFICIENT,
    ) -> Dict[str, List[str]]:
        """
        Allocate targets to drones using the specified strategy.

        Args:
            targets: List of target dicts with id, x, y, priority, type
            drone_configs: Dict of drone configs {
                "1": {"fuel_budget": 300, "target_access": {"A": true, ...},
                      "start_airport": "A1", "enabled": true},
                ...
            }
            airports: List of airport dicts
            strategy: Allocation strategy to use

        Returns:
            Dict mapping drone IDs to lists of assigned target IDs:
            {"1": ["T1", "T3"], "2": ["T2", "T4"], ...}
        """
        # Filter to enabled drones only
        active_drones = {
            did: cfg
            for did, cfg in drone_configs.items()
            if cfg.get("enabled", True) is not False
        }

        if not active_drones or not targets:
            return {did: [] for did in drone_configs.keys()}

        # Determine which targets each drone can access
        drone_capabilities = self._calculate_capabilities(targets, active_drones)
        # A target inside a SAM's slice at a drone's altitude is not reachable by it.
        for did in drone_capabilities:
            own = matrix_for_drone(self._distance_matrix, did) or {}
            drone_capabilities[did] -= {str(t) for t in own.get("excluded_targets", [])}

        # Call appropriate strategy
        if strategy == AllocationStrategy.GEOGRAPHIC:
            result = self._allocate_geographic(
                targets, active_drones, airports, drone_capabilities
            )
        elif strategy == AllocationStrategy.GREEDY:
            result = self._allocate_greedy(
                targets, active_drones, airports, drone_capabilities
            )
        elif strategy == AllocationStrategy.BALANCED:
            result = self._allocate_balanced(
                targets, active_drones, airports, drone_capabilities
            )
        elif strategy == AllocationStrategy.EFFICIENT:
            result = self._allocate_efficient(
                targets, active_drones, airports, drone_capabilities
            )
        elif strategy == AllocationStrategy.EXCLUSIVE:
            result = self._allocate_exclusive_first(
                targets, active_drones, airports, drone_capabilities
            )
        elif strategy == AllocationStrategy.INSERTION:
            result = self._allocate_insertion(
                targets, active_drones, airports, drone_capabilities
            )
        else:
            # Default to geographic (most intuitive)
            result = self._allocate_geographic(
                targets, active_drones, airports, drone_capabilities
            )

        # Log any unassigned targets for debugging
        all_target_ids = {str(t["id"]) for t in targets}
        assigned_ids = set()
        for tid_list in result.values():
            assigned_ids.update(tid_list)
        unassigned = all_target_ids - assigned_ids

        if unassigned:
            print(
                f"⚠️ ALLOCATOR: {len(unassigned)} targets NOT assigned: {sorted(unassigned)}",
                flush=True,
            )
            # Debug: check why each was skipped
            for tid in sorted(unassigned):
                capable_drones = [
                    did for did, caps in drone_capabilities.items() if tid in caps
                ]
                if not capable_drones:
                    print(
                        f"   {tid}: No drone has capability (type restriction)",
                        flush=True,
                    )
                else:
                    print(
                        f"   {tid}: Capable drones={capable_drones} but not assigned",
                        flush=True,
                    )

        return result

    def _calculate_capabilities(
        self, targets: List[Dict[str, Any]], drone_configs: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Set[str]]:
        """
        Calculate which targets each drone can access based on type restrictions.

        Returns:
            Dict mapping drone IDs to sets of accessible target IDs
        """
        capabilities: Dict[str, Set[str]] = {}

        for did, cfg in drone_configs.items():
            capabilities[did] = {
                str(target["id"]) for target in targets if type_allowed(cfg, target)
            }

        return capabilities

    def _get_distance(
        self,
        from_id: str,
        to_id: str,
        waypoints: Optional[List[Dict[str, Any]]] = None,
        drone_id: Optional[str] = None,
    ) -> float:
        """SAM-aware distance for drone_id (at its cruise altitude)."""
        dist_data = matrix_for_drone(self._distance_matrix, drone_id)
        if dist_data:
            labels = dist_data.get("labels", [])
            matrix = dist_data.get("matrix", [])

            try:
                i = labels.index(from_id)
                j = labels.index(to_id)
                return matrix[i][j]
            except (ValueError, IndexError):
                pass

        # Per policy: SAM-aware only. If matrix lookup failed, fail loudly
        # rather than silently fall back to Euclidean.
        raise RuntimeError(
            f"target_allocator: cannot find SAM-aware distance for "
            f"{from_id}→{to_id}. Matrix lookup failed; Euclidean fallback "
            f"is disabled (policy)."
        )

    def _start_positions(
        self, drone_configs: Dict[str, Dict[str, Any]], airports: List[Dict[str, Any]]
    ) -> Dict[str, Optional[str]]:
        """Each drone's start airport (unknown ids fall back to the first airport)."""
        airport_ids = {str(a["id"]) for a in airports}
        positions: Dict[str, Optional[str]] = {}
        for did, cfg in drone_configs.items():
            start_id = cfg.get("start_airport", cfg.get("startAirport", f"A{did}"))
            if start_id not in airport_ids:
                start_id = airports[0]["id"] if airports else None
            positions[did] = start_id
        return positions

    def _allocate_geographic(
        self,
        targets: List[Dict[str, Any]],
        drone_configs: Dict[str, Dict[str, Any]],
        airports: List[Dict[str, Any]],
        capabilities: Dict[str, Set[str]],
    ) -> Dict[str, List[str]]:
        """
        Geographic allocation: assign each target to the drone whose starting
        airport is closest to that target (SAM-aware distances), among the drones
        with fuel left for it. Equal distances go to the drone with fewer targets.
        """
        assignments: Dict[str, List[str]] = {did: [] for did in drone_configs.keys()}
        fuel = _FuelEstimate(self, drone_configs, targets, airports)
        starts = self._start_positions(drone_configs, airports)

        for target in targets:
            tid = str(target["id"])
            ranked = []
            for did in drone_configs:
                start = starts[did]
                if tid not in capabilities[did] or not start:
                    continue
                distance = self._get_distance(start, tid, drone_id=did)
                ranked.append(((round(distance, 9), len(assignments[did]), did), did))
            best = fuel.choose(tid, ranked)
            if best:
                assignments[best].append(tid)
                fuel.add(best, tid)

        return assignments

    def _allocate_chained(
        self,
        targets: List[Dict[str, Any]],
        drone_configs: Dict[str, Dict[str, Any]],
        airports: List[Dict[str, Any]],
        capabilities: Dict[str, Set[str]],
        rank,
    ) -> Dict[str, List[str]]:
        """Targets in priority order (highest first); each goes to the drone that
        `rank` scores lowest among those with fuel left for it. A drone's
        position then moves to that target, so each drone builds a chain.

        rank(tid, priority, did, distance_from_drone, assignments) -> sort key.
        """
        assignments: Dict[str, List[str]] = {did: [] for did in drone_configs.keys()}
        fuel = _FuelEstimate(self, drone_configs, targets, airports)
        positions = self._start_positions(drone_configs, airports)

        for target in sorted(
            targets, key=lambda t: int(t.get("priority", 5)), reverse=True
        ):
            tid = str(target["id"])
            priority = int(target.get("priority", 5))
            ranked = []
            for did in drone_configs:
                position = positions[did]
                if tid not in capabilities[did] or not position:
                    continue
                distance = self._get_distance(position, tid, drone_id=did)
                ranked.append((rank(tid, priority, did, distance, assignments), did))
            best = fuel.choose(tid, ranked)
            if best:
                assignments[best].append(tid)
                fuel.add(best, tid)
                positions[best] = tid

        return assignments

    def _allocate_greedy(
        self,
        targets: List[Dict[str, Any]],
        drone_configs: Dict[str, Dict[str, Any]],
        airports: List[Dict[str, Any]],
        capabilities: Dict[str, Set[str]],
    ) -> Dict[str, List[str]]:
        """
        Greedy allocation: highest priority targets first, each to the nearest
        capable drone (nearest to the last target it was given).
        """
        order = {did: i for i, did in enumerate(drone_configs)}
        return self._allocate_chained(
            targets,
            drone_configs,
            airports,
            capabilities,
            lambda tid, p, did, d, asg: (d, order[did]),
        )

    def _allocate_balanced(
        self,
        targets: List[Dict[str, Any]],
        drone_configs: Dict[str, Dict[str, Any]],
        airports: List[Dict[str, Any]],
        capabilities: Dict[str, Set[str]],
    ) -> Dict[str, List[str]]:
        """
        Balanced allocation: distribute targets evenly by count.

        Each drone has a quota of about the same number of targets (the first
        drones take the remainder); the drone with the fewest so far wins, then
        the nearest. Drones past their quota come last: they take a target only
        when no drone within quota has fuel left for it (or can visit it).
        """
        per_drone, extra = divmod(len(targets), len(drone_configs))
        # 1-based ordinal per drone id — virtual ids ("1_L1") aren't int-parseable,
        # so use insertion order instead of int(did) for the "extra" bonus check.
        ordinal = {did: i + 1 for i, did in enumerate(drone_configs)}

        def rank(tid, priority, did, distance, assignments):
            count = len(assignments[did])
            over_quota = count >= per_drone + (1 if ordinal[did] <= extra else 0)
            return (over_quota, count, distance, ordinal[did])

        return self._allocate_chained(
            targets, drone_configs, airports, capabilities, rank
        )

    def _allocate_efficient(
        self,
        targets: List[Dict[str, Any]],
        drone_configs: Dict[str, Dict[str, Any]],
        airports: List[Dict[str, Any]],
        capabilities: Dict[str, Set[str]],
    ) -> Dict[str, List[str]]:
        """
        Best value (shown as "Best value (priority ÷ km)"; id "efficient"):
        highest priority targets first, each to the drone with the most priority
        per km from the last target it was given.
        """
        order = {did: i for i, did in enumerate(drone_configs)}

        def rank(tid, priority, did, distance, assignments):
            if distance == float("inf"):
                value = 0.0
            else:
                value = priority / (distance if distance > 0 else 0.1)
            return (-value, order[did])

        return self._allocate_chained(
            targets, drone_configs, airports, capabilities, rank
        )

    def _insert_cheapest(
        self,
        tids: List[str],
        drone_configs: Dict[str, Dict[str, Any]],
        capabilities: Dict[str, Set[str]],
        fuel: "_FuelEstimate",
        assignments: Dict[str, List[str]],
    ) -> None:
        """Cheapest insertion: repeatedly place the (target, drone) pair that adds
        the fewest km to that drone's route, among drones with fuel left for the
        target; a target no drone has room for goes, last, where it is cheapest."""
        unassigned = list(tids)
        while unassigned:
            fits, anywhere = [], []
            for tid in unassigned:
                for did in drone_configs:
                    if tid not in capabilities[did]:
                        continue
                    extra = fuel.insertion(did, tid)[0]
                    pair = (extra, tid, did)
                    anywhere.append(pair)
                    if fuel.has_room(did, tid, extra):
                        fits.append(pair)
            if not anywhere:
                break
            _cost, tid, did = min(fits or anywhere, key=lambda c: c[0])
            assignments[did].append(tid)
            fuel.add(did, tid)
            unassigned.remove(tid)

    def _allocate_exclusive_first(
        self,
        targets: List[Dict[str, Any]],
        drone_configs: Dict[str, Dict[str, Any]],
        airports: List[Dict[str, Any]],
        capabilities: Dict[str, Set[str]],
    ) -> Dict[str, List[str]]:
        """
        Exclusive-first allocation: targets only one drone can visit go to that
        drone first (highest priority first); the rest by cheapest insertion.
        """
        assignments: Dict[str, List[str]] = {did: [] for did in drone_configs.keys()}
        fuel = _FuelEstimate(self, drone_configs, targets, airports)
        sorted_targets = sorted(
            targets, key=lambda t: int(t.get("priority", 5)), reverse=True
        )

        rest = []
        for target in sorted_targets:
            tid = str(target["id"])
            capable = [did for did in drone_configs if tid in capabilities[did]]
            if len(capable) == 1:
                assignments[capable[0]].append(tid)
                fuel.add(capable[0], tid)
            else:
                rest.append(tid)

        self._insert_cheapest(rest, drone_configs, capabilities, fuel, assignments)
        return assignments

    def _allocate_insertion(
        self,
        targets: List[Dict[str, Any]],
        drone_configs: Dict[str, Dict[str, Any]],
        airports: List[Dict[str, Any]],
        capabilities: Dict[str, Set[str]],
    ) -> Dict[str, List[str]]:
        """
        Cheapest Insertion allocation: minimize total tour cost.

        Each drone starts with the route start airport -> end airport; the
        (target, drone) pair that adds the fewest km joins that drone's route,
        until every target is placed. This naturally creates spatial clusters.
        """
        assignments: Dict[str, List[str]] = {did: [] for did in drone_configs.keys()}
        fuel = _FuelEstimate(self, drone_configs, targets, airports)
        self._insert_cheapest(
            [str(t["id"]) for t in targets],
            drone_configs,
            capabilities,
            fuel,
            assignments,
        )
        return assignments


# Global allocator instance
_allocator = TargetAllocator()


def allocate_targets(
    env: Dict[str, Any],
    drone_configs: Dict[str, Dict[str, Any]],
    strategy: str = "insertion",
    distance_matrix: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[str]]:
    """
    Allocate targets to drones using the specified strategy.

    This is the main entry point for use as a LangGraph tool.

    Args:
        env: Environment dict with targets, airports, sams
        drone_configs: Dict of drone configurations
        strategy: Allocation strategy ("insertion", "greedy", "balanced",
                  "efficient", "exclusive")
        distance_matrix: Pre-calculated SAM-aware distance matrix (optional)

    Returns:
        Dict mapping drone IDs to lists of target IDs
    """
    if distance_matrix:
        _allocator.set_distance_matrix(distance_matrix)

    targets = env.get("targets", [])
    airports = list(env.get("airports", []))  # Make a copy to avoid modifying original

    # Add checkpoints (C1, C2, etc.) for segmented mission replanning
    # Checkpoints act like airports - drones can start from these positions
    for checkpoint in env.get("checkpoints", []):
        airports.append(
            {
                "id": str(checkpoint["id"]),
                "x": float(checkpoint["x"]),
                "y": float(checkpoint["y"]),
                "is_checkpoint": True,
            }
        )

    # Add synthetic start nodes (for checkpoint replanning - legacy support)
    # These act like airports - drones can start from these positions
    for node_id, node_data in env.get("synthetic_starts", {}).items():
        airports.append(
            {
                "id": str(node_id),
                "x": float(node_data["x"]),
                "y": float(node_data["y"]),
                "is_synthetic": True,
            }
        )

    # Filter out excluded targets (e.g., targets inside SAM zones) — only those
    # no drone can reach at its altitude; per-drone exclusions are applied to
    # each drone's capabilities in allocate().
    if distance_matrix:
        excluded = set(distance_matrix.get("excluded_targets", []))
        for other in (distance_matrix.get("by_altitude") or {}).values():
            excluded &= set(other.get("excluded_targets", []))
        if excluded:
            original_count = len(targets)
            targets = [t for t in targets if str(t["id"]) not in excluded]
            if len(targets) < original_count:
                print(
                    f"⚠️ Filtered out {original_count - len(targets)} excluded targets "
                    f"(inside SAM zones): {excluded}",
                    flush=True,
                )

    # Convert strategy string to enum
    try:
        strat_enum = AllocationStrategy(strategy.lower())
    except ValueError:
        strat_enum = AllocationStrategy.INSERTION

    return _allocator.allocate(targets, drone_configs, airports, strat_enum)


def set_allocator_matrix(matrix_data: Dict[str, Any]):
    """Set the distance matrix for the global allocator."""
    _allocator.set_distance_matrix(matrix_data)


def clear_allocator_matrix():
    """Clear the cached distance matrix from the global allocator.

    This should be called before each fresh solve to ensure the allocator
    doesn't use stale distance data from a previous environment state.
    """
    _allocator._distance_matrix = None


# ============================================================================
# Priority-Based Constraint Parsing
# ============================================================================

import re
from typing import Callable


def parse_priority_constraints(
    constraints_str: str,
) -> Dict[str, Callable[[int], bool]]:
    """
    Parse natural language priority constraints into filter functions.

    Supports formats like:
    - "D1,D2: priority>=6; D3,D4: priority<=6"
    - "D1: priority>5; D2,D3: priority<8"
    - "D1,D2: priority=10"

    Args:
        constraints_str: Constraint string with drone:condition pairs separated by semicolons

    Returns:
        Dict mapping drone IDs to filter functions: {"1": lambda p: p >= 6, ...}
    """
    filters: Dict[str, Callable[[int], bool]] = {}

    if not constraints_str or not constraints_str.strip():
        return filters

    # Split by semicolon to get individual constraints
    parts = constraints_str.split(";")

    for part in parts:
        part = part.strip()
        if not part or ":" not in part:
            continue

        # Split into drone list and condition
        try:
            drone_part, condition = part.split(":", 1)
        except ValueError:
            continue

        # Parse drone IDs (e.g., "D1,D2" or "D1, D2" or "1,2")
        drone_ids = []
        for d in drone_part.split(","):
            d = d.strip().upper().replace("D", "")
            if d.isdigit():
                drone_ids.append(d)

        # Parse condition (e.g., "priority>=6" or "priority > 6")
        condition = condition.strip().lower().replace(" ", "")

        # Match patterns like "priority>=6", "priority>5", "priority<=8", "priority<7", "priority=10"
        match = re.match(r"priority\s*(>=|<=|>|<|=)\s*(\d+)", condition)
        if not match:
            print(f"⚠️ Could not parse constraint: {condition}", flush=True)
            continue

        op = match.group(1)
        threshold = int(match.group(2))

        if op not in (">=", "<=", ">", "<", "="):
            continue

        # Create filter function based on operator
        def _make_filter(operator, thresh):
            def _fn(p):
                if operator == ">=":
                    return p >= thresh
                if operator == "<=":
                    return p <= thresh
                if operator == ">":
                    return p > thresh
                if operator == "<":
                    return p < thresh
                return p == thresh

            return _fn

        filter_func = _make_filter(op, threshold)

        # Assign filter to each drone
        for did in drone_ids:
            filters[did] = filter_func
            print(f"📋 D{did}: priority {op} {threshold}", flush=True)

    return filters


def allocate_with_priority_filters(
    env: Dict[str, Any],
    drone_configs: Dict[str, Dict[str, Any]],
    priority_filters: Dict[str, Callable[[int], bool]],
    strategy: str = "insertion",
    distance_matrix: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[str]]:
    """
    Allocate targets with per-drone priority filters applied.

    Args:
        env: Environment dict
        drone_configs: Drone configurations
        priority_filters: Dict mapping drone IDs to priority filter functions
        strategy: Allocation strategy
        distance_matrix: Pre-calculated distance matrix

    Returns:
        Dict mapping drone IDs to lists of target IDs
    """
    if distance_matrix:
        _allocator.set_distance_matrix(distance_matrix)

    targets = env.get("targets", [])
    airports = env.get("airports", [])

    # Filter out excluded targets (e.g., targets inside SAM zones) — only those
    # no drone can reach at its altitude; per-drone exclusions are applied to
    # each drone's capabilities in allocate().
    if distance_matrix:
        excluded = set(distance_matrix.get("excluded_targets", []))
        for other in (distance_matrix.get("by_altitude") or {}).values():
            excluded &= set(other.get("excluded_targets", []))
        if excluded:
            targets = [t for t in targets if str(t["id"]) not in excluded]

    # Apply priority filters to determine which targets each drone can see
    # We'll create a modified drone_configs with adjusted target_access
    modified_configs = {}

    for did, cfg in drone_configs.items():
        if not cfg.get("enabled", True):
            continue

        # Start with existing config
        modified_cfg = dict(cfg)

        # If there's a priority filter for this drone, we need to track allowed targets
        if did in priority_filters:
            filter_func = priority_filters[did]

            # Get targets that pass the priority filter
            allowed_targets = set()
            for t in targets:
                priority = int(t.get("priority", 5))
                if filter_func(priority):
                    allowed_targets.add(str(t["id"]))

            # Store allowed targets in config for the allocator to use
            modified_cfg["_priority_allowed_targets"] = allowed_targets
            print(
                f"🎯 D{did}: {len(allowed_targets)} targets pass priority filter",
                flush=True,
            )

        modified_configs[did] = modified_cfg

    # Use a modified allocator that respects priority filters
    return _allocate_with_priority_internal(
        targets, modified_configs, airports, strategy, distance_matrix
    )


def _allocate_with_priority_internal(
    targets: List[Dict[str, Any]],
    drone_configs: Dict[str, Dict[str, Any]],
    airports: List[Dict[str, Any]],
    strategy: str,
    distance_matrix: Optional[Dict[str, Any]],
) -> Dict[str, List[str]]:
    """
    Internal allocation that respects priority filters stored in configs.
    """
    if distance_matrix:
        _allocator.set_distance_matrix(distance_matrix)

    # Calculate capabilities with priority filter applied
    capabilities: Dict[str, Set[str]] = {}

    for did, cfg in drone_configs.items():
        priority_allowed = cfg.get("_priority_allowed_targets")  # May be None

        # Find all targets this drone can reach
        accessible = set()
        for target in targets:
            tid = str(target["id"])

            # Check category access
            if not type_allowed(cfg, target):
                continue

            # Check priority filter if present
            if priority_allowed is not None and tid not in priority_allowed:
                continue

            accessible.add(tid)

        capabilities[did] = accessible

    # Convert strategy string to enum
    try:
        strat_enum = AllocationStrategy(strategy.lower())
    except ValueError:
        strat_enum = AllocationStrategy.INSERTION

    # Call the appropriate strategy method
    if strat_enum == AllocationStrategy.GREEDY:
        return _allocator._allocate_greedy(
            targets, drone_configs, airports, capabilities
        )
    elif strat_enum == AllocationStrategy.BALANCED:
        return _allocator._allocate_balanced(
            targets, drone_configs, airports, capabilities
        )
    elif strat_enum == AllocationStrategy.EFFICIENT:
        return _allocator._allocate_efficient(
            targets, drone_configs, airports, capabilities
        )
    elif strat_enum == AllocationStrategy.EXCLUSIVE:
        return _allocator._allocate_exclusive_first(
            targets, drone_configs, airports, capabilities
        )
    elif strat_enum == AllocationStrategy.INSERTION:
        return _allocator._allocate_insertion(
            targets, drone_configs, airports, capabilities
        )
    else:
        return _allocator._allocate_insertion(
            targets, drone_configs, airports, capabilities
        )


# Tool definition for LangGraph
TARGET_ALLOCATOR_TOOL = {
    "name": "allocate_targets",
    "description": """Allocate targets to drones using various strategies before route optimization.
    Available strategies:
    - insertion: Cheapest insertion - minimize total tour cost (recommended)
    - greedy: Assign highest priority targets to nearest capable drone
    - balanced: Distribute targets evenly by count
    - efficient: Maximize priority/distance ratio
    - exclusive: Prioritize targets only one drone can reach first""",
    "parameters": {
        "type": "object",
        "properties": {
            "env": {
                "type": "object",
                "description": "Environment data containing targets, airports, and sams",
            },
            "drone_configs": {
                "type": "object",
                "description": "Drone configurations with capabilities and fuel budgets",
            },
            "strategy": {
                "type": "string",
                "description": "Allocation strategy to use",
                "enum": ["insertion", "greedy", "balanced", "efficient", "exclusive"],
                "default": "insertion",
            },
            "distance_matrix": {
                "type": "object",
                "description": "Pre-calculated SAM-aware distance matrix (optional)",
            },
        },
        "required": ["env", "drone_configs"],
    },
    "function": allocate_targets,
}
