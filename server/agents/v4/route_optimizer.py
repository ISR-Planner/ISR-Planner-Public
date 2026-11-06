"""Route optimizer node for the v4 ISR agent workflow."""

import sys
from typing import Any, Dict, List, Optional, cast

from langchain_core.messages import AIMessage

from ...memory.algorithm_registry import AlgorithmExecution, get_algorithm_info
from ...solver.constraints import _constraints_for_drone, _subset_constraints_for_leg
from ...solver.solver_bridge import (
    REFUEL_STOP_COST_KM,
    _refuel_options,
    _solve_one_leg,
    _solve_opportunistic_drone,
    _solve_two_leg_refuel,
)
from ...solver.solver_interface import OrienteeringSolverInterface
from ..drone_config import resolve_fuel_budget
from .state import MissionState, set_state
from .state import trace_event as _trace_event

# Sentinel for cfg.refuel_airport meaning "let solver pick any real airport".
REFUEL_ANY = "-"

# ============================================================================
# ROUTE OPTIMIZER AGENT - Computes routes, checks feasibility
# ============================================================================

ROUTE_OPTIMIZER_PROMPT = """You are the ROUTE OPTIMIZER agent in an ISR mission planning system.

Your job is to compute optimal routes for each drone given their allocated targets.
You use the Held-Karp algorithm (optimal TSP solver) for route computation.

For each drone, you must:
1. Compute the shortest route visiting all allocated targets
2. Verify the route fits within fuel budget
3. If infeasible, explain why and what would need to change

OUTPUT FORMAT:
ROUTE_ANALYSIS:
- D1: [route] uses [X] fuel (budget: [Y]) - [feasible/infeasible]
  Reason: [why this route was chosen]
- D2: ...

FEASIBILITY_ISSUES:
[List any routes that exceed fuel budget and why]

ROUTES_COMPUTED: YES/NO
"""


def _state_dict(state: MissionState, key: str) -> Dict[str, Any]:
    value = state.get(key)
    return value if isinstance(value, dict) else {}


def _state_list(state: MissionState, key: str) -> List[Any]:
    value = state.get(key)
    return value if isinstance(value, list) else []


def _drone_configs_from_state(state: MissionState) -> Dict[str, Dict[str, Any]]:
    return cast(Dict[str, Dict[str, Any]], state.get("drone_configs") or {})


def _allocation_from_state(state: MissionState) -> Dict[str, List[str]]:
    return cast(Dict[str, List[str]], state.get("allocation") or {})


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def route_optimizer_node(state: MissionState) -> Dict[str, Any]:
    """Route optimizer computes and validates routes."""
    import time as _time

    solver_start = _time.time()

    print("\n🛣️ [ROUTE_OPTIMIZER] Computing routes...", file=sys.stderr)
    sys.stderr.flush()

    set_state(state)

    env = _state_dict(state, "environment")
    configs = _drone_configs_from_state(state)
    allocation = _allocation_from_state(state)
    dist_matrix = _state_dict(state, "distance_matrix")

    # Read Coordinator policy for solver_mode override
    policy = state.get("policy") or {}
    policy_solver_mode = policy.get("solver_mode")  # May override per-drone mode

    # Read drone contracts from state (set by Coordinator pre-pass)
    # Fallback to policy.drone_contracts for backwards compatibility
    drone_contracts = cast(
        Dict[str, Dict[str, Any]],
        state.get("drone_contracts") or policy.get("drone_contracts", {}),
    )

    # Source synthetic starts from environment (authoritative data)
    # NOT from guardrails (which are for validation outcomes only)
    synthetic_starts = env.get("synthetic_starts", {}) or {}

    # Build real airport IDs set for defensive home_airport validation
    # A real airport must:
    # 1. NOT have is_synthetic=True flag
    # 2. NOT have an ID ending in _START (synthetic start pattern)
    real_airport_ids: set[str] = set()
    for a in env.get("airports") or []:
        aid = str(a.get("id", a.get("label", "")))
        # Skip if marked as synthetic
        if a.get("is_synthetic", False):
            continue
        # Skip if ID matches synthetic start pattern (e.g., D1_START, D2_START)
        if aid.endswith("_START"):
            continue
        real_airport_ids.add(aid)

    routes: Dict[str, Dict[str, Any]] = {}
    route_analysis_lines = ["ROUTE_ANALYSIS:"]
    feasibility_issues: List[str] = []

    # Get solver instance
    solver = None
    if OrienteeringSolverInterface:
        solver = OrienteeringSolverInterface()

    # Read sequencing hints for visit order constraints
    sequencing_hints = _state_dict(state, "sequencing_hints")
    start_with_raw = sequencing_hints.get(
        "start_with"
    )  # {priority_gte: N} or {targets: [...]}
    start_with_hint = start_with_raw if isinstance(start_with_raw, dict) else None

    # Read HARD ordering constraints (first/last/before/adjacent), already
    # validated at parse time by validate_and_normalize_constraints — see
    # isr_agent_multi_v4.py's parse_constraints() call, which stores the
    # ConstraintProgram.to_dict() output (including ordering_constraints)
    # into state["constraint_program"]. Threaded into solver.solve(...) /
    # the refuel solve helpers below via `constraints=`, letting the DP
    # prune to only orderings that satisfy them exactly — replaces the
    # post-hoc start_with reorder hack that used to run after an
    # UNCONSTRAINED solve (see the removed block below for why that could
    # produce a worse route than solving with the constraint honored from
    # the start).
    constraint_program_state = _state_dict(state, "constraint_program")
    ordering_constraints = constraint_program_state.get("ordering_constraints")
    if ordering_constraints:
        print(
            f"🔒 [ROUTE_OPTIMIZER] Hard ordering constraints: {ordering_constraints}",
            file=sys.stderr,
        )

    # Build target priority lookup from environment
    target_priority_map: Dict[str, int] = {}
    for t in env.get("targets", []):
        tid = str(t.get("id", t.get("label", "")))
        target_priority_map[tid] = int(t.get("priority", 5))

    if start_with_hint:
        print(
            f"🎯 [ROUTE_OPTIMIZER] Sequencing hint: start_with = {start_with_hint}",
            file=sys.stderr,
        )

    for did, target_ids in allocation.items():
        cfg = configs.get(did, {})
        if not cfg.get("enabled", did == "1"):
            continue

        fuel_budget = resolve_fuel_budget(cfg, drone_id=str(did))

        # Resolve this drone's hard ordering constraints. Safe to pass the
        # same global dict to every drone's solve — the DP's constraint
        # localizer (orienteering_with_matrix.py's _to_local) silently no-ops
        # any first/last/before/adjacent entry naming a target outside that
        # drone's candidate set, so it only actually constrains whichever
        # drone's allocation happens to include the named target(s).
        drone_ordering_constraints = _constraints_for_drone(
            ordering_constraints, str(did)
        )

        refuel_airport = (cfg.get("refuel_airport") or "").strip()
        is_refuel_drone = bool(refuel_airport)
        is_any_refuel = refuel_airport == REFUEL_ANY
        opportunistic_options = _refuel_options(cfg)
        cfg_max_refuels = cfg.get("max_refuels")
        if cfg_max_refuels is None:
            legacy_refuel_stops = cfg.get("refuel_stops")
            legacy_refuel_count = (
                len(
                    [
                        stop
                        for stop in legacy_refuel_stops
                        if stop and str(stop) not in ("-", "None")
                    ]
                )
                if isinstance(legacy_refuel_stops, list)
                else 0
            )
            opportunistic_max_refuels = max(
                legacy_refuel_count, len(opportunistic_options), 4
            )
        else:
            opportunistic_max_refuels = int(cfg_max_refuels or 0)
        opportunistic_max_refuels = max(
            0,
            opportunistic_max_refuels - int(cfg.get("refuels_used", 0) or 0),
        )
        uses_opportunistic_refuel = (
            bool(opportunistic_options)
            and opportunistic_max_refuels > 0
            and bool(cfg.get("opportunistic_refuel", True))
        )

        # Get drone contract from Coordinator (handles synthetic starts correctly)
        contract = drone_contracts.get(str(did), {})

        # start_id: Coordinator contract is authoritative, then cfg.start_id, then home_airport
        # Avoid cfg.start_airport to prevent older UI mismatch issues
        start_id = (
            contract.get("start_id")
            or cfg.get("start_id")
            or cfg.get("home_airport")
            or "A1"
        )

        is_synthetic_start = contract.get(
            "is_synthetic_start", start_id.endswith("_START") if start_id else False
        )

        # home_airport: MUST be a real airport (never synthetic)
        # Defensive enforcement even if upstream misconfigures
        home_airport = contract.get("home_airport") or cfg.get("home_airport")
        if not home_airport or str(home_airport) not in real_airport_ids:
            home_airport = (
                start_id
                if start_id in real_airport_ids
                else next(iter(sorted(real_airport_ids)), "A1")
            )

        # end_airport from config (contract may have None for flexible)
        cfg_end = cfg.get("end_airport", home_airport)
        flexible_endpoint = contract.get("flexible_endpoint", cfg_end == "-")

        # Determine solver mode
        if policy_solver_mode:
            mode = policy_solver_mode
        elif flexible_endpoint:
            mode = "best_end"
        elif cfg_end and cfg_end != "-" and cfg_end != start_id:
            mode = "end"
        else:
            mode = "return"

        # For synthetic starts with return mode, we need best_end (can't return to synthetic)
        if is_synthetic_start and mode == "return":
            mode = "best_end"
            print(
                f"[v4][ROUTE_OPT] D{did}: Synthetic start detected, switching mode to best_end",
                flush=True,
            )

        end_airport = cfg_end if mode == "end" else None

        print(
            f"[v4][ROUTE_OPT] D{did} start_id={start_id} home={home_airport} end={end_airport} "
            f"synthetic={is_synthetic_start} flexible={flexible_endpoint} mode={mode}",
            flush=True,
        )

        if not target_ids:
            # No targets: for synthetic starts, need to go to nearest real airport
            effective_end = home_airport if is_synthetic_start else start_id
            routes[did] = {
                "route": (
                    [start_id, effective_end]
                    if start_id != effective_end
                    else [start_id]
                ),
                "distance": 0,
                "total_points": 0,
                "visited_targets": [],
            }
            route_analysis_lines.append(
                f"- D{did}: No targets assigned, goes from {start_id} to {effective_end}"
            )
            continue

        # Build solver environment
        if solver and dist_matrix:
            try:
                # --- Build FILTERED matrix data ---
                all_labels = list(dist_matrix.keys())

                # Build requested labels based on mode
                # Always include start_id (may be synthetic)
                if flexible_endpoint or mode == "best_end" or is_any_refuel:
                    # For best_end mode (or "any" refuel), include all real airports
                    # so solver can choose optimal end / refuel stop.
                    submatrix_airport_ids = [
                        a["id"]
                        for a in env.get("airports", [])
                        if not a.get("is_synthetic", False)
                    ]
                    requested_labels = (
                        [start_id] + submatrix_airport_ids + list(target_ids)
                    )
                else:
                    # For fixed end, include start, end, and targets
                    requested_labels = [start_id]
                    if end_airport and end_airport != start_id:
                        requested_labels.append(end_airport)
                    requested_labels.extend(target_ids)

                if uses_opportunistic_refuel:
                    requested_labels.extend(opportunistic_options)

                # Specific refuel airport must be in the submatrix or the two-leg
                # solver cannot route through it.
                if is_refuel_drone and not is_any_refuel:
                    requested_labels.append(refuel_airport)

                # Keep only labels that actually exist in the distance matrix
                labels = [lab for lab in requested_labels if lab in all_labels]
                # Deduplicate while preserving order
                labels = list(dict.fromkeys(labels))

                if not labels or start_id not in labels:
                    print(
                        f"⚠️ [ROUTE_OPTIMIZER] No valid labels for D{did} "
                        f"(start_id={start_id}, targets={target_ids})",
                        file=sys.stderr,
                    )
                    routes[did] = {
                        "route": [start_id],
                        "distance": 0.0,
                        "total_points": 0,
                        "visited_targets": [],
                    }
                    continue

                # Build submatrix only over these labels
                matrix = [[dist_matrix[r].get(c, 1000) for c in labels] for r in labels]

                # Filter targets to only those allocated (env target dicts)
                # Also boost priority of required targets to ensure solver includes them
                required_targets = policy.get("required_targets", [])
                targets = []
                for t in env.get("targets", []):
                    tid = str(t.get("id", t.get("label")))
                    if tid in target_ids:
                        # Make a copy to avoid mutating original
                        target_copy = dict(t)
                        # Boost priority of required targets to 1000 so solver always picks them
                        if tid in required_targets:
                            original_priority = target_copy.get("priority", 5)
                            target_copy["priority"] = (
                                1000  # Very high priority forces inclusion
                            )
                            print(
                                f"   🎯 [ROUTE_OPTIMIZER] Boosted {tid} priority: {original_priority} → 1000 (required)",
                                file=sys.stderr,
                            )
                        targets.append(target_copy)

                # Build airports list - include synthetic starts as airports
                solver_airports = list(env.get("airports", []))
                if is_synthetic_start and start_id not in [
                    a["id"] for a in solver_airports
                ]:
                    # Add synthetic start as an airport
                    synth_coords = synthetic_starts.get(start_id, {"x": 0, "y": 0})
                    solver_airports.append(
                        {
                            "id": start_id,
                            "x": synth_coords.get("x", 0),
                            "y": synth_coords.get("y", 0),
                            "is_synthetic": True,
                        }
                    )
                    print(
                        f"[v4][ROUTE_OPT] Added synthetic start {start_id} to airports",
                        flush=True,
                    )

                print(
                    f"[v4][ROUTE_OPT] D{did} start_id={start_id} end={end_airport} mode={mode} "
                    f"labels={labels[:5]}{'...' if len(labels) > 5 else ''}",
                    flush=True,
                )

                solver_env: Dict[str, Any] = {
                    "airports": solver_airports,
                    "targets": targets,
                    "matrix": matrix,
                    "matrix_labels": labels,
                    "start_airport": start_id,  # Use start_id (may be synthetic)
                    "mode": mode,
                }

                # For best_end mode, set valid_end_airports to real airports only.
                # CRITICAL: Synthetic starts (e.g., D1_START, D2_START) must NEVER be endpoints.
                # Also enforce runway compatibility — drone can only land where its
                # required runway is supported. Ordering: S < M < L.
                if mode == "best_end":
                    _runway_rank = {"S": 1, "M": 2, "L": 3}
                    _drone_rwy = str(cfg.get("runway", "S")).upper()
                    _d_rank = _runway_rank.get(_drone_rwy, 1)
                    valid_end_airports_for_solver = [
                        a["id"]
                        for a in env.get("airports", [])
                        if not a.get("is_synthetic", False)
                        and not str(a.get("id", "")).endswith("_START")
                        and _runway_rank.get(str(a.get("runway", "L")).upper(), 3)
                        >= _d_rank
                    ]
                    solver_env["valid_end_airports"] = valid_end_airports_for_solver
                    print(
                        f"   ✈️ [v4] Valid end airports (real, runway-compatible {_drone_rwy}+): {valid_end_airports_for_solver}",
                        flush=True,
                    )

                # Only set end_airport if fixed end mode
                if mode == "end" and end_airport:
                    solver_env["end_airport"] = end_airport

                # ---------------------------------------------------------------
                # Solve: refuel-aware path if user configured tanker-only
                # opportunistic options or a fixed refuel airport; otherwise use
                # the standard single-leg orienteering solve.
                # ---------------------------------------------------------------
                solution: Dict[str, Any]
                if uses_opportunistic_refuel:
                    tanker_ids = {
                        str(a.get("id"))
                        for a in solver_env.get("airports", [])
                        if a.get("is_tanker")
                        or str(a.get("type", "")).lower() == "tanker"
                        or str(a.get("id", "")).startswith("TK")
                    }
                    print(
                        f"   ⛽ [v4] D{did}: opportunistic refuel "
                        f"options={opportunistic_options}, "
                        f"max_refuels={opportunistic_max_refuels}",
                        flush=True,
                    )
                    (
                        opp_route,
                        opp_visited,
                        opp_distance,
                        opp_refuel_indices,
                        opp_leg_distances,
                    ) = _solve_opportunistic_drone(
                        solver_env,
                        fuel_budget,
                        start_id,
                        targets,
                        str(did),
                        cfg,
                        opportunistic_options,
                        opportunistic_max_refuels,
                        tanker_ids,
                        (
                            sorted(real_airport_ids)
                            if flexible_endpoint
                            else [end_airport or start_id]
                        ),
                        constraints=drone_ordering_constraints,
                    )
                    solution = {
                        "route": opp_route,
                        "visited_targets": sorted(opp_visited),
                        "distance": opp_distance,
                        "end_airport": opp_route[-1] if opp_route else start_id,
                    }
                    if opp_refuel_indices:
                        solution["refuel_indices"] = opp_refuel_indices
                        solution["leg_distances"] = opp_leg_distances
                        solution["refuel_stops"] = [
                            opp_route[idx]
                            for idx in opp_refuel_indices
                            if 0 <= idx < len(opp_route)
                        ]
                    visited = _string_list(solution.get("visited_targets", []))
                    points = sum(target_priority_map.get(tid, 0) for tid in visited)
                elif is_refuel_drone:
                    # End id for the two-leg solver:
                    #   - mode=end:      end_airport (fixed alternate endpoint)
                    #   - mode=return:   start_id (drone loops back to start)
                    #   - flexible/best: None (solver picks)
                    if flexible_endpoint or mode == "best_end":
                        leg_end_id = None
                    elif mode == "end":
                        leg_end_id = end_airport
                    else:  # mode == "return"
                        leg_end_id = start_id
                    print(
                        f"   ⛽ [v4] D{did}: refuel mode "
                        f"(airport={refuel_airport}, flexible={flexible_endpoint})",
                        flush=True,
                    )

                    if is_any_refuel:
                        # "Any" mode: greedy scan all real airports, then exhaustive
                        # split on the winner. Same shape as the bridge's any-refuel
                        # path in solve_mission_with_allocation.
                        labels_set = set(labels)
                        any_candidates = [
                            a["id"]
                            for a in env.get("airports", [])
                            if not a.get("is_synthetic", False)
                            and not str(a.get("id", "")).endswith("_START")
                            and a["id"] in labels_set
                        ]

                        best_greedy: Optional[Dict[str, Any]] = None
                        best_greedy_ap: Optional[str] = None
                        leg1_ordering_constraints = _subset_constraints_for_leg(
                            drone_ordering_constraints,
                            [str(t["id"]) for t in targets],
                            is_first_leg=True,
                            is_last_leg=False,
                        )
                        for rap in any_candidates:
                            r1, v1, d1 = _solve_one_leg(
                                solver_env,
                                fuel_budget,
                                start_id,
                                rap,
                                targets,
                                str(did),
                                "L1",
                                drone_cfg=cfg,
                                constraints=leg1_ordering_constraints,
                            )
                            if d1 > fuel_budget:
                                continue
                            remaining = [t for t in targets if str(t["id"]) not in v1]
                            leg2_ordering_constraints = _subset_constraints_for_leg(
                                drone_ordering_constraints,
                                [str(t["id"]) for t in remaining],
                                is_first_leg=False,
                                is_last_leg=True,
                            )
                            r2, v2, d2 = _solve_one_leg(
                                solver_env,
                                fuel_budget,
                                rap,
                                leg_end_id,
                                remaining,
                                str(did),
                                "L2",
                                flexible_endpoint,
                                drone_cfg=cfg,
                                constraints=leg2_ordering_constraints,
                            )
                            if d2 > fuel_budget:
                                continue
                            pts = len(v1) + len(v2)
                            total = d1 + d2 + REFUEL_STOP_COST_KM
                            if (
                                best_greedy is None
                                or pts > len(best_greedy.get("visited_targets", []))
                                or (
                                    pts == len(best_greedy.get("visited_targets", []))
                                    and total < best_greedy["distance"]
                                )
                            ):
                                combined = list(r1)
                                if r2 and combined and combined[-1] == r2[0]:
                                    combined.extend(r2[1:])
                                else:
                                    combined.extend(r2)
                                best_greedy = {
                                    "route": combined,
                                    "visited_targets": list(v1 | v2),
                                    "distance": total,
                                    "leg1_distance": d1,
                                    "leg2_distance": d2,
                                    "refuel_airport": rap,
                                }
                                best_greedy_ap = rap

                        if best_greedy_ap is not None:
                            # Exhaustive split on the winning airport
                            sol_exhaustive = _solve_two_leg_refuel(
                                env_for_solver=solver_env,
                                fuel_budget=fuel_budget,
                                start_id=start_id,
                                end_id=leg_end_id,
                                refuel_airport_id=best_greedy_ap,
                                candidate_targets=targets,
                                flexible_endpoint=flexible_endpoint,
                                did=str(did),
                                drone_cfg=cfg,
                                constraints=drone_ordering_constraints,
                            )
                            # Keep whichever is better (more visited, then less fuel).
                            keep_greedy = best_greedy is not None and (
                                len(sol_exhaustive.get("visited_targets", []))
                                < len(best_greedy.get("visited_targets", []))
                                or (
                                    len(sol_exhaustive.get("visited_targets", []))
                                    == len(best_greedy.get("visited_targets", []))
                                    and sol_exhaustive.get("distance", 0)
                                    >= best_greedy.get("distance", 0)
                                )
                            )
                            if keep_greedy and best_greedy is not None:
                                solution = best_greedy
                            else:
                                solution = sol_exhaustive
                        else:
                            # No feasible airport found — fall through with empty route.
                            solution = {
                                "route": [start_id],
                                "visited_targets": [],
                                "distance": 0.0,
                                "leg1_distance": 0.0,
                                "leg2_distance": 0.0,
                                "refuel_airport": "",
                            }
                    else:
                        # Specific refuel airport — exhaustive split for ≤8 targets,
                        # greedy fallback above that. Solver_bridge handles both.
                        solution = _solve_two_leg_refuel(
                            env_for_solver=solver_env,
                            fuel_budget=fuel_budget,
                            start_id=start_id,
                            end_id=leg_end_id,
                            refuel_airport_id=refuel_airport,
                            candidate_targets=targets,
                            flexible_endpoint=flexible_endpoint,
                            did=str(did),
                            drone_cfg=cfg,
                            constraints=drone_ordering_constraints,
                        )

                    # Two-leg solver omits total_points; recompute from priorities.
                    visited = _string_list(solution.get("visited_targets", []))
                    points = sum(target_priority_map.get(tid, 0) for tid in visited)
                    leg1_distance = _float_value(solution.get("leg1_distance"))
                    leg2_distance = _float_value(solution.get("leg2_distance"))
                    print(
                        f"   ⛽ [v4] D{did}: refuel solve → "
                        f"L1={leg1_distance:.1f} + "
                        f"L2={leg2_distance:.1f} + stop, "
                        f"refuel@{solution.get('refuel_airport', '')}, "
                        f"{len(visited)} targets",
                        flush=True,
                    )
                else:
                    solution = solver.solve(
                        solver_env, fuel_budget, constraints=drone_ordering_constraints
                    )
                    points = int(solution.get("total_points", 0))
                    visited = _string_list(solution.get("visited_targets", []))

                # Extract the actual end airport from solution (solver may have chosen it)
                actual_end = solution.get(
                    "end_airport",
                    end_airport if not flexible_endpoint else home_airport,
                )
                # Two-leg solver doesn't populate end_airport; fall back to last waypoint.
                if is_refuel_drone:
                    sol_route = _string_list(solution.get("route")) or [start_id]
                    if sol_route:
                        actual_end = sol_route[-1]
                if flexible_endpoint or mode == "best_end":
                    print(f"   🎯 [v4] Solver chose endpoint: {actual_end}", flush=True)

                route = _string_list(solution.get("route")) or [start_id]
                distance = _float_value(solution.get("distance"))
                solved_refuel_ap = solution.get("refuel_airport") or ""
                leg_distances_raw = solution.get("leg_distances")
                leg_distances = (
                    [_float_value(d) for d in leg_distances_raw]
                    if isinstance(leg_distances_raw, list)
                    else []
                )

                # ---------------------------------------------------------------
                # Enforce start_with sequencing hint (POST-HOC reorder — a fallback
                # heuristic, NOT the primary path anymore).
                #
                # A single, specifically-named target ("visit T7 first") is now
                # parsed as a HARD ordering_constraints["first"] (see
                # server/memory/constraint_parser.py) and solved DP-exact via
                # `constraints=` passed into solver.solve()/the refuel helpers
                # above — the route is correct from the start, no reorder needed,
                # and this block's "targets" branch below will simply never see
                # a single-target list for that phrasing anymore.
                #
                # This block still legitimately handles what CAN'T be a hard
                # constraint: "start with priority >= N" (no single correct
                # target — any qualifying one will do) and a multi-target
                # "start with T5 or T8" where the solver should pick whichever
                # is closer, not visit BOTH first. Reordering an already-optimal
                # route post-hoc is the right tool for "any of these" semantics;
                # it just isn't for "this exact one."
                # ---------------------------------------------------------------
                if start_with_hint and len(route) > 2:
                    # Find the first target in the route (skip airport at index 0)
                    first_target_idx = None
                    for idx, wp in enumerate(route):
                        if wp.startswith("T"):
                            first_target_idx = idx
                            break

                    if first_target_idx is not None:
                        first_target = route[first_target_idx]
                        first_target_priority = target_priority_map.get(first_target, 5)

                        # Check if constraint is satisfied
                        needs_reorder = False
                        qualifying_targets: List[str] = []

                        if "priority_gte" in start_with_hint:
                            threshold = float(start_with_hint["priority_gte"])
                            if first_target_priority < threshold:
                                needs_reorder = True
                                # Find all qualifying targets in this route
                                for wp in route:
                                    if wp.startswith("T"):
                                        wp_priority = target_priority_map.get(wp, 5)
                                        if wp_priority >= threshold:
                                            qualifying_targets.append(wp)

                        elif "targets" in start_with_hint:
                            required_first = _string_list(start_with_hint["targets"])
                            if first_target not in required_first:
                                needs_reorder = True
                                qualifying_targets = [
                                    t for t in required_first if t in route
                                ]

                        if needs_reorder and qualifying_targets:
                            # Reorder: move the best qualifying target to first position
                            # Best = closest to start airport (minimize detour)
                            best_qual_target = qualifying_targets[0]
                            if len(qualifying_targets) > 1 and dist_matrix:
                                # Find closest qualifying target to start
                                best_dist = float("inf")
                                for qt in qualifying_targets:
                                    d = dist_matrix.get(start_id, {}).get(
                                        qt, float("inf")
                                    )
                                    if d < best_dist:
                                        best_dist = d
                                        best_qual_target = qt

                            # Remove the qualifying target and insert after airport
                            old_route = route.copy()
                            route = [start_id]
                            route.append(best_qual_target)
                            for wp in old_route[1:]:  # Skip the old start
                                if wp != best_qual_target:
                                    route.append(wp)

                            print(
                                f"🔄 [ROUTE_OPT] D{did}: Reordered route to start with {best_qual_target} "
                                f"(priority={target_priority_map.get(best_qual_target, '?')})",
                                file=sys.stderr,
                            )
                            print(
                                f"   Old: {' → '.join(old_route[:5])}{'...' if len(old_route) > 5 else ''}",
                                file=sys.stderr,
                            )
                            print(
                                f"   New: {' → '.join(route[:5])}{'...' if len(route) > 5 else ''}",
                                file=sys.stderr,
                            )
                        elif needs_reorder:
                            print(
                                f"⚠️ [ROUTE_OPT] D{did}: No qualifying targets for start_with constraint in allocation",
                                file=sys.stderr,
                            )

                # Determine frozen segments based on sequencing constraints
                # If start_with constraint was applied, freeze segment 0 (start -> first target)
                frozen_segments: List[int] = []
                if start_with_hint and len(route) > 1:
                    # Freeze segment 0: the trajectory from start to first target
                    frozen_segments = [0]
                    print(
                        f"🔒 [ROUTE_OPT] D{did}: Freezing segment 0 (start_with constraint)",
                        file=sys.stderr,
                    )

                routes[did] = {
                    "route": route,
                    "distance": distance,
                    "total_points": points,
                    "visited_targets": visited,
                    "start_id": start_id,  # Track start for trajectory generation
                    "end_airport": actual_end,
                    "is_synthetic_start": is_synthetic_start,
                    "frozen_segments": frozen_segments,
                    "fuel_budget": fuel_budget,
                }

                # Refuel routes are validated per-leg (tank refills at the stop),
                # not against the trip total which would always exceed a single tank.
                if leg_distances:
                    refuel_indices = solution.get("refuel_indices", [])
                    refuel_stops = solution.get("refuel_stops", [])
                    routes[did]["leg_distances"] = leg_distances
                    routes[did]["refuel_indices"] = (
                        refuel_indices if isinstance(refuel_indices, list) else []
                    )
                    routes[did]["refuel_stops"] = (
                        refuel_stops if isinstance(refuel_stops, list) else []
                    )
                    feasible = all(leg <= fuel_budget for leg in leg_distances)
                    status = "FEASIBLE" if feasible else "OVER BUDGET"
                    legs_text = " + ".join(f"{leg:.0f}" for leg in leg_distances)
                    route_analysis_lines.append(
                        f"- D{did}: {' → '.join(route)} uses legs={legs_text} fuel "
                        f"(budget per leg: {fuel_budget}) - {status}"
                    )
                    for idx, leg in enumerate(leg_distances, start=1):
                        if leg > fuel_budget:
                            feasibility_issues.append(
                                f"D{did} leg {idx} exceeds fuel by {leg - fuel_budget:.0f} units"
                            )
                elif solved_refuel_ap:
                    leg1 = _float_value(solution.get("leg1_distance"))
                    leg2 = _float_value(solution.get("leg2_distance"))
                    routes[did]["refuel_airport"] = solved_refuel_ap
                    routes[did]["leg1_distance"] = leg1
                    routes[did]["leg2_distance"] = leg2
                    feasible = leg1 <= fuel_budget and leg2 <= fuel_budget
                    status = "FEASIBLE" if feasible else "OVER BUDGET"
                    route_analysis_lines.append(
                        f"- D{did}: {' → '.join(route)} uses "
                        f"L1={leg1:.0f} + L2={leg2:.0f} fuel "
                        f"(budget per leg: {fuel_budget}) - {status}"
                    )
                    if leg1 > fuel_budget:
                        feasibility_issues.append(
                            f"D{did} leg 1 exceeds fuel by {leg1 - fuel_budget:.0f} units"
                        )
                    if leg2 > fuel_budget:
                        feasibility_issues.append(
                            f"D{did} leg 2 exceeds fuel by {leg2 - fuel_budget:.0f} units"
                        )
                else:
                    status = "FEASIBLE" if distance <= fuel_budget else "OVER BUDGET"
                    route_analysis_lines.append(
                        f"- D{did}: {' → '.join(route)} uses {distance:.0f} fuel "
                        f"(budget: {fuel_budget}) - {status}"
                    )
                    if distance > fuel_budget:
                        feasibility_issues.append(
                            f"D{did} exceeds fuel by {distance - fuel_budget:.0f} units"
                        )
            except Exception as e:
                print(
                    f"⚠️ [ROUTE_OPTIMIZER] Error solving D{did}: {e}", file=sys.stderr
                )
                # Fallback route uses start_id (may be synthetic)
                fallback_end = home_airport if is_synthetic_start else start_id
                routes[did] = {
                    "route": [start_id] + list(target_ids) + [fallback_end],
                    "distance": 999,
                    "total_points": 0,
                    "visited_targets": [],
                    "error": str(e),
                    "start_id": start_id,
                    "is_synthetic_start": is_synthetic_start,
                    "frozen_segments": [],
                    "fuel_budget": fuel_budget,
                }
        else:
            # Fallback: simple route without optimization
            # Use start_id (may be synthetic) and go to home_airport
            fallback_end = home_airport if is_synthetic_start else start_id
            routes[did] = {
                "route": [start_id] + list(target_ids) + [fallback_end],
                "distance": 0,
                "total_points": sum(
                    t.get("priority", 5)
                    for t in env.get("targets", [])
                    if str(t.get("id", t.get("label"))) in target_ids
                ),
                "visited_targets": list(target_ids),
                "start_id": start_id,
                "is_synthetic_start": is_synthetic_start,
                "frozen_segments": [],
                "fuel_budget": fuel_budget,
            }

    # =========================================================================
    # PHASE 2: Extend routes to include deferred targets
    # After Phase 1 is complete, continue from endpoints to visit remaining targets
    # =========================================================================
    phase2_target_ids = [str(tid) for tid in _state_list(state, "phase2_target_ids")]
    original_env = _state_dict(
        state, "environment"
    )  # Full environment with all targets

    if phase2_target_ids and routes:
        print(
            f"\n🎯 [ROUTE_OPT] PHASE 2: Extending routes to include {len(phase2_target_ids)} deferred targets",
            file=sys.stderr,
        )
        print(f"   Deferred targets: {phase2_target_ids}", file=sys.stderr)

        # Get Phase 2 target data from original environment
        phase2_targets_data = {
            str(t.get("id", t.get("label"))): t
            for t in original_env.get("targets", [])
            if str(t.get("id", t.get("label"))) in phase2_target_ids
        }

        # Allocate Phase 2 targets to drones based on proximity to their Phase 1 endpoints
        phase2_allocation: Dict[str, List[str]] = {did: [] for did in routes.keys()}

        for target_id in phase2_target_ids:
            target_data = phase2_targets_data.get(target_id)
            if not target_data:
                continue

            # Find drone whose Phase 1 endpoint is closest to this target
            best_drone = None
            best_dist = float("inf")

            for did, route_data in routes.items():
                route = _string_list(route_data.get("route", []))
                if len(route) < 2:
                    continue

                # Get last target (waypoint before ending airport)
                phase1_endpoint = None
                for wp in reversed(route[:-1]):  # Skip ending airport
                    if wp.startswith("T"):
                        phase1_endpoint = wp
                        break

                if not phase1_endpoint:
                    phase1_endpoint = route[0]  # Use start if no targets

                # Calculate distance from Phase 1 endpoint to this target
                if dist_matrix:
                    d = float(
                        dist_matrix.get(phase1_endpoint, {}).get(
                            target_id, float("inf")
                        )
                    )
                    if d < best_dist:
                        best_dist = d
                        best_drone = did

            if best_drone:
                phase2_allocation[best_drone].append(target_id)

        print(f"   Phase 2 allocation: {phase2_allocation}", file=sys.stderr)

        # Extend each drone's route to include its Phase 2 targets
        for did, p2_targets in phase2_allocation.items():
            if not p2_targets:
                continue

            route_data = routes[did]
            phase1_route = _string_list(route_data.get("route", []))
            if len(phase1_route) < 2:
                continue

            # Get Phase 1 ending info
            end_airport = phase1_route[-1]
            fuel_budget = float(route_data.get("fuel_budget", 200) or 200)
            phase1_distance = float(route_data.get("distance", 0) or 0.0)
            remaining_fuel = fuel_budget - phase1_distance

            # Get drone config
            cfg = configs.get(did, {})

            # Solve Phase 2: from last Phase 1 target through Phase 2 targets to airport
            phase1_last_target = None
            for wp in reversed(phase1_route[:-1]):
                if wp.startswith("T"):
                    phase1_last_target = wp
                    break

            if not phase1_last_target:
                phase1_last_target = phase1_route[0]

            print(
                f"   D{did}: Extending from {phase1_last_target} through {p2_targets} to {end_airport}",
                file=sys.stderr,
            )

            if solver and dist_matrix:
                try:
                    # Build mini-environment for Phase 2 solve
                    p2_env = {
                        "airports": [
                            {
                                "id": phase1_last_target + "_START",
                                "x": 0,
                                "y": 0,
                                "is_synthetic": True,
                            }
                        ]
                        + [
                            a
                            for a in original_env.get("airports", [])
                            if a.get("id") == end_airport
                        ],
                        "targets": [
                            phase2_targets_data[tid]
                            for tid in p2_targets
                            if tid in phase2_targets_data
                        ],
                        "sams": original_env.get("sams", []),
                    }

                    # Build filtered matrix for Phase 2
                    p2_labels = [phase1_last_target] + p2_targets + [end_airport]
                    p2_matrix_data = {
                        "labels": p2_labels,
                        "matrix": [
                            [dist_matrix.get(f, {}).get(t, 1000) for t in p2_labels]
                            for f in p2_labels
                        ],
                    }

                    # Solve Phase 2 route
                    p2_result = solver.solve(
                        start=phase1_last_target,
                        targets=p2_targets,
                        end=end_airport,
                        fuel_limit=remaining_fuel,
                        env=p2_env,
                        mode="end",
                        matrix_data=p2_matrix_data,
                    )

                    if p2_result:
                        p2_route = _string_list(p2_result.get("route", []))
                        p2_distance = float(p2_result.get("distance", 0) or 0.0)
                        p2_visited = [wp for wp in p2_route if wp.startswith("T")]

                        # Merge Phase 1 and Phase 2 routes
                        # Phase 1 route ends at airport, Phase 2 starts from last target
                        # New route: Phase1[:-1] + Phase2[1:]  (skip duplicate airport and duplicate last target)
                        merged_route = (
                            phase1_route[:-1] + p2_route[1:]
                        )  # Skip airport from P1, skip start from P2

                        # Update route data
                        routes[did] = {
                            **route_data,
                            "route": merged_route,
                            "distance": phase1_distance + p2_distance,
                            "visited_targets": _string_list(
                                route_data.get("visited_targets", [])
                            )
                            + p2_visited,
                            "total_points": int(route_data.get("total_points", 0) or 0)
                            + sum(
                                int(phase2_targets_data.get(tid, {}).get("priority", 5))
                                for tid in p2_visited
                            ),
                            "phase2_targets": p2_visited,
                        }

                        route_analysis_lines.append(
                            f"- D{did} PHASE 2: Added {len(p2_visited)} targets, +{p2_distance:.0f} fuel"
                        )
                        print(
                            f"   ✅ D{did} Phase 2 complete: {p2_visited}",
                            file=sys.stderr,
                        )

                except Exception as e:
                    print(f"   ⚠️ D{did} Phase 2 failed: {e}", file=sys.stderr)
                    # Fallback: just append targets in order
                    merged_route = phase1_route[:-1] + p2_targets + [end_airport]
                    routes[did] = {
                        **route_data,
                        "route": merged_route,
                        "phase2_targets": p2_targets,
                    }
            else:
                # No solver: just append targets
                merged_route = phase1_route[:-1] + p2_targets + [end_airport]
                routes[did] = {
                    **route_data,
                    "route": merged_route,
                    "phase2_targets": p2_targets,
                }

        print("✅ [ROUTE_OPT] Phase 2 complete", file=sys.stderr)

    route_analysis = "\n".join(route_analysis_lines)
    if feasibility_issues:
        route_analysis += "\n\nFEASIBILITY_ISSUES:\n" + "\n".join(feasibility_issues)
    route_analysis += "\n\nROUTES_COMPUTED: YES"

    # Calculate solver runtime
    solver_runtime_ms = int((_time.time() - solver_start) * 1000)
    solver_type = "orienteering_exact" if solver else "heuristic"

    print(
        f"📋 [ROUTE_OPTIMIZER] Routes computed for {len(routes)} drones in {solver_runtime_ms}ms",
        file=sys.stderr,
    )

    # Track solver algorithm used
    total_targets_visited = sum(
        len(r.get("visited_targets", [])) for r in routes.values()
    )
    solver_algorithm_id = "held_karp" if solver else "greedy_tsp"
    solver_info = get_algorithm_info(solver_algorithm_id)
    solver_name = solver_info["name"] if solver_info else "Orienteering Solver"

    solver_execution = AlgorithmExecution(
        algorithm_id=solver_algorithm_id,
        algorithm_name=solver_name,
        agent="Route Optimizer",
        iterations=len(routes),  # One solve per drone
        operations_performed=total_targets_visited,
        result_summary=f"Computed routes for {len(routes)} drones, {total_targets_visited} targets visited",
        duration_ms=float(solver_runtime_ms),
    )

    # Get existing algorithms_used from state and append
    existing_algorithms = state.get("algorithms_used") or []
    updated_algorithms = existing_algorithms + [solver_execution.to_dict()]

    route_summary = {
        did: rd.get("route", []) if isinstance(rd, dict) else rd
        for did, rd in routes.items()
        if rd
    }
    return {
        "messages": [AIMessage(content=f"[ROUTE_OPTIMIZER]\n{route_analysis}")],
        "route_analysis": route_analysis,
        "routes": routes,
        "solver_type": solver_type,
        "solver_runtime_ms": solver_runtime_ms,
        "algorithms_used": updated_algorithms,
        "node_trace": [
            _trace_event(
                "route_optimizer",
                f"Computed routes for {len(routes)} drones in {solver_runtime_ms}ms",
                solver=solver_type,
                routes=route_summary,
            )
        ],
    }
