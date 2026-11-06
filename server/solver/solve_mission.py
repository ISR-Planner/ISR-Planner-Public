"""Mission-level solver orchestration."""

import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple, cast

from path_planning_core.latlng_utils import polyline_km

# Import polygon wrapping for visualization
from path_planning_core.sam_wrapping import wrap_sams

from .airport_rules import (
    REFUEL_STOP_COST_KM,
    _airport_accepts_drone,
    is_real_airport,
)
from .altitude import (
    altitude_signature,
    attach_altitude_matrices,
    env_for_drone,
    matrix_for_drone,
    with_primary_altitude,
)
from .constraints import (
    _constraints_for_drone,
    _subset_constraints_for_leg,
)

# Import new solver components
from .flight_profile import without_unreachable_tankers
from .matrix_cache import (
    _compute_env_hash,
)
from .mission_parser import _parse_env_for_solver
from .post_optimizer import (
    post_optimize_solution,
    set_optimizer_matrix,
)
from .projection import (
    _project_env_to_local_km,
    _unproject_solution_trajectories,
)
from .refuel_solver import (
    _refuel_options,
    _relax_positions_to_detection_radii,
)
from .refuel_solver import solve_opportunistic_drone as _solve_opportunistic_drone_impl
from .refuel_solver import solve_two_leg_refuel as _solve_two_leg_refuel_impl
from .sam_distance_matrix import (
    calculate_sam_aware_matrix,
    clear_matrix_cache,
    get_cached_matrix,
    set_cached_matrix,
)
from .solve_config import _normalize_fuel_budget_keys
from .solve_one_leg import solve_one_leg as _solve_one_leg_impl
from .solver_interface import OrienteeringSolverInterface, create_solver_interface
from .target_access import type_allowed
from .target_allocator import (
    allocate_targets,
    clear_allocator_matrix,
    set_allocator_matrix,
)
from .trajectory_planner import ISRTrajectoryPlanner

# Single global solver instance (only if interface is available)
_solver = create_solver_interface(OrienteeringSolverInterface)

# Cached distance matrix for current environment
_cached_env_hash: Optional[str] = None


def _require_solver() -> Any:
    """Return the initialized solver interface or fail with a clear error."""
    if _solver is None:
        raise RuntimeError("OrienteeringSolverInterface is not available")
    return _solver


def _legacy_refuel_airport(cfg: Dict[str, Any]) -> str:
    """Return an explicit legacy refuel airport, treating retired sentinels as off."""
    value = str((cfg or {}).get("refuel_airport") or "").strip()
    if value in ("-", "None", "none", "null"):
        return ""
    return value


def _constraint_target_assignments(
    constraints: Optional[Dict[str, Any]],
    drone_configs: Dict[str, Any],
    target_ids: set[str],
) -> Dict[str, str]:
    """Map hard per-drone constrained targets to their required drone.

    Allocation happens before route solving. If a constraint says D1 must visit
    T5 first, T5 must be allocated to D1 before the per-drone solver sees it.
    """
    if not constraints:
        return {}
    if any(k in constraints for k in ("first", "last", "before", "adjacent")):
        return {}

    out: Dict[str, str] = {}
    for did in drone_configs.keys():
        c = _constraints_for_drone(constraints, str(did))
        if not c:
            continue
        tids: set[str] = set()
        for key in ("first", "last"):
            if c.get(key):
                tids.add(str(c[key]))
        for a, b in c.get("before", []) or []:
            tids.update([str(a), str(b)])
        for y, x in c.get("adjacent", []) or []:
            tids.update([str(y), str(x)])
        for tid in tids:
            if tid not in target_ids:
                continue
            prev = out.get(tid)
            if prev and prev != str(did):
                raise ValueError(
                    f"target {tid} is constrained to both D{prev} and D{did}"
                )
            out[tid] = str(did)
    return out


def _apply_constraint_allocations(
    allocations: Dict[str, List[str]],
    constraints: Optional[Dict[str, Any]],
    drone_configs: Dict[str, Any],
    target_ids: set[str],
) -> None:
    forced = _constraint_target_assignments(constraints, drone_configs, target_ids)
    if not forced:
        return
    for tid, required_did in forced.items():
        allocations.setdefault(required_did, [])
        for did, tids in allocations.items():
            if did != required_did and tid in tids:
                tids[:] = [x for x in tids if x != tid]
        if tid not in allocations[required_did]:
            allocations[required_did].append(tid)
    print(
        "🔒 constraint allocation overrides: "
        + ", ".join(f"{tid}->D{did}" for tid, did in sorted(forced.items())),
        flush=True,
    )


def _positions_as_tuples(
    waypoint_positions: Dict[str, List[float]],
) -> Dict[str, Tuple[float, float]]:
    return {
        str(wp_id): (float(pos[0]), float(pos[1]))
        for wp_id, pos in waypoint_positions.items()
    }


def _positions_as_lists(
    waypoint_positions: Dict[str, Tuple[float, float]],
) -> Dict[str, List[float]]:
    return {
        str(wp_id): [float(pos[0]), float(pos[1])]
        for wp_id, pos in waypoint_positions.items()
    }


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _solve_one_leg(
    env_for_solver,
    fuel_budget,
    start_id,
    end_id,
    targets,
    did,
    leg_name,
    flexible_endpoint=False,
    drone_cfg=None,
    constraints=None,
):
    """Compatibility wrapper for the extracted single-leg solver."""
    return _solve_one_leg_impl(
        env_for_solver,
        fuel_budget,
        start_id,
        end_id,
        targets,
        did,
        leg_name,
        flexible_endpoint=flexible_endpoint,
        drone_cfg=drone_cfg,
        constraints=constraints,
        solver=_solver,
    )


def _solve_opportunistic_drone(
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
) -> Tuple[List[str], set, float, List[int], List[float]]:
    """Compatibility wrapper for the extracted opportunistic refuel solver."""
    return _solve_opportunistic_drone_impl(
        env_for_solver,
        fuel_budget,
        start_id,
        candidate_targets,
        did,
        cfg,
        refuel_options,
        max_refuels,
        tanker_ids,
        valid_end_airports,
        constraints=constraints,
        solver=_solver,
    )


def _solve_two_leg_refuel(
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
) -> Dict[str, Any]:
    """Compatibility wrapper for the extracted two-leg refuel solver."""
    return _solve_two_leg_refuel_impl(
        env_for_solver,
        fuel_budget,
        start_id,
        end_id,
        refuel_airport_id,
        candidate_targets,
        flexible_endpoint,
        did,
        leg1_targets=leg1_targets,
        leg2_targets=leg2_targets,
        drone_cfg=drone_cfg,
        leg2_budget=leg2_budget,
        constraints=constraints,
        solver=_solver,
    )


def solve_mission(
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Core entry point for the ISR web backend.

    Inputs:
      - env: environment JSON from the frontend
      - drone_configs: {
            "1": { "fuel_budget": 140, "start_airport": "A1", "end_airport": "A1", ... },
            ...
        }

    Outputs:
      {
        "sequences": { "1": "A1,T3,T11,A1", "2": "...", ... },
        "routes": {
           "1": {
               "route": ["A1","T3","T11","A1"],
               "sequence": "A1,T3,T11,A1",
               "points": 23,
               "distance": 87.4,
               "fuel_budget": 140.0
           },
           ...
        }
      }
    """
    print(
        f"\n🚀 solve_mission() called with {len(drone_configs)} drone configs",
        flush=True,
    )
    solver = _require_solver()

    drone_configs = _normalize_fuel_budget_keys(drone_configs)

    # Project lat/lng env to local km coordinates for internal processing
    env, ref_lat, ref_lng = _project_env_to_local_km(env)
    print(
        f"📐 Projected env to local km (ref: {ref_lat:.4f}, {ref_lng:.4f})", flush=True
    )

    airports, targets, sams = _parse_env_for_solver(env)
    print(
        f"📍 Parsed: {len(airports)} airports, {len(targets)} targets, {len(sams)} SAMs",
        flush=True,
    )

    # DEBUG: Show which airports have is_synthetic flag
    synthetic_airports = [a["id"] for a in airports if a.get("is_synthetic", False)]
    real_airports = [a["id"] for a in airports if is_real_airport(a)]
    print(f"   🔍 Real airports: {real_airports}", flush=True)
    print(f"   🔍 Synthetic airports: {synthetic_airports}", flush=True)

    if not airports:
        print("⚠️ No airports, returning empty", flush=True)
        return {
            "sequences": {},
            "routes": {},
        }

    # Note: We continue even if targets is empty - drones still need
    # routes to return from checkpoints to airports when all targets are visited

    # Build a global distance matrix; we'll reuse it per drone
    # ALWAYS use SAM-aware distances for consistency and exclusion detection
    sam_count = len(sams) if sams else 0
    print(f"🎯 Calculating fresh distance matrix ({sam_count} SAMs)...", flush=True)
    clear_matrix_cache()
    dist_data = calculate_sam_aware_matrix(env)

    # Get excluded targets (inside SAM polygons)
    excluded_targets = set(dist_data.get("excluded_targets", []))
    if excluded_targets:
        print(
            f"🚫 [DEBUG] EXCLUDED TARGETS from distance matrix: {sorted(excluded_targets)}",
            flush=True,
        )

    sequences: Dict[str, str] = {}
    routes_detail: Dict[str, Dict[str, Any]] = {}

    # For now we assume 4 drones max; later you can extend to N
    max_drone_id = max([int(did) for did in drone_configs.keys()] or [4])

    # Check if targets are pre-assigned
    any_assigned = any(t.get("assigned_drone") is not None for t in targets)

    # Track allocations for return value
    final_allocations: Dict[str, List[str]] = {}

    # If no pre-assignment, use the allocator to distribute targets to drones
    # This prevents each drone from solving ALL targets (exponential blowup)
    if not any_assigned:
        # Get enabled drones
        enabled_drones = [
            str(did_int)
            for did_int in range(1, max_drone_id + 1)
            if drone_configs.get(str(did_int), {}).get("enabled") is not False
        ]

        if len(enabled_drones) > 1:
            # Use the proper allocator with balanced strategy for even distribution
            allocations = allocate_targets(env, drone_configs, "balanced", dist_data)
            final_allocations = allocations

            # Debug: Print allocation sets
            print("\n" + "=" * 60, flush=True)
            print("🎯 TARGET ALLOCATION RESULTS (balanced strategy)", flush=True)
            print("=" * 60, flush=True)
            total_allocated = 0
            for did in sorted(allocations.keys(), key=lambda x: int(x)):
                target_ids = allocations[did]
                total_allocated += len(target_ids)
                if target_ids:
                    print(f"  Drone {did}: {target_ids}", flush=True)
                else:
                    cfg = drone_configs.get(did, {})
                    if cfg.get("enabled") is not False:
                        print(f"  Drone {did}: [] (enabled but no targets)", flush=True)
            print(
                f"  Total targets allocated: {total_allocated}/{len(targets)}",
                flush=True,
            )
            print("=" * 60 + "\n", flush=True)

            # Apply allocations to targets
            for did, target_ids in allocations.items():
                for tid in target_ids:
                    for t in targets:
                        if str(t["id"]) == tid:
                            t["assigned_drone"] = did
                            break
            any_assigned = True

    for did_int in range(1, max_drone_id + 1):
        did = str(did_int)
        cfg = drone_configs.get(did, {})

        # Skip drones that are "disabled" (caller can enforce that)
        if cfg.get("enabled") is False:
            sequences[did] = ""
            routes_detail[did] = {
                "route": [],
                "sequence": "",
                "points": 0,
                "distance": 0.0,
                "fuel_budget": float(cfg.get("fuel_budget", 999.0)),
            }
            continue

        fuel_budget = float(cfg.get("fuel_budget", 999.0))
        # §11.10: ONE fuel value — fuel_budget IS the tank (full at the start of
        # every leg). Leg 1 flies on the fuel the drone has at its start: a FULL
        # tank on a fresh solve, or the REDUCED remaining on a dynamic replan,
        # which the caller passes as the transient `start_fuel` (NOT stored on the
        # config — fuel_budget stays = the tank). Leg 2 starts AFTER refueling with
        # a FULL TANK = fuel_budget. (fuel_capacity retired.)
        start_fuel = cfg.get("start_fuel")
        leg1_budget = float(fuel_budget if start_fuel is None else start_fuel)
        leg2_budget = fuel_budget
        # Downstream single-leg solves + feasibility use `fuel_budget` as "the fuel
        # this drone flies on now" → that's the leg-1 start fuel (reduced on a
        # replan). The tank for any post-refuel leg is leg2_budget. Reassign so all
        # existing `fuel_budget` references below use the correct start fuel.
        fuel_budget = leg1_budget
        refuel_airport = _legacy_refuel_airport(cfg)

        # Start/end airports: default A1, A2, A3, A4 if they exist,
        # otherwise fall back to the first airport.
        airport_by_id = {a["id"]: a for a in airports}
        default_start = f"A{did_int}"

        # DEBUG: Check what start_airport is in the config
        cfg_start_raw = cfg.get("start_airport")
        cfg_start = str(cfg_start_raw) if cfg_start_raw else None
        print(
            f"🔍 D{did} DEBUG: cfg.start_airport='{cfg_start}', airport_by_id keys={list(airport_by_id.keys())}",
            flush=True,
        )

        # Check if cfg_start is a synthetic start (e.g., D1_START) or checkpoint (e.g., C1-1, C2-3)
        # These should be used directly, not fall back to real airports
        # Checkpoint format: C{segment}-{drone} (e.g., C1-1 = Checkpoint 1 for Drone 1)
        is_synthetic_start = (
            cfg_start
            and cfg_start.endswith("_START")
            and cfg_start not in airport_by_id
        )
        is_checkpoint_start = cfg_start and bool(re.match(r"^C\d+-\d+$", cfg_start))

        if is_synthetic_start or is_checkpoint_start:
            # Use the synthetic start or checkpoint directly - it's in matrix_labels but not necessarily in airport_by_id  # noqa: E501
            if cfg_start is None:
                raise RuntimeError(
                    "Synthetic/checkpoint start missing start airport id"
                )
            start_id = cfg_start
            start_type = "CHECKPOINT" if is_checkpoint_start else "SYNTHETIC"
            print(
                f"🔍 D{did} DEBUG: Using {start_type} start '{start_id}' for checkpoint replanning",
                flush=True,
            )
        else:
            start_id = cfg_start or (
                default_start if default_start in airport_by_id else airports[0]["id"]
            )

        # DEBUG: Final start_id decision
        print(
            f"🔍 D{did} DEBUG: final start_id='{start_id}' (cfg_start was {'set' if cfg_start else 'NOT set'}, synthetic={is_synthetic_start})",  # noqa: E501
            flush=True,
        )

        # Handle flexible endpoint: "-" means solver chooses optimal endpoint.
        # The end airport is sanitized UI-side (readDroneConfigsFromUI enforces a real
        # airport or "-"), so end→start only fires for a genuinely absent end.
        raw_end_raw = cfg.get("end_airport") or start_id
        raw_end_id = str(raw_end_raw)
        flexible_endpoint = raw_end_id == "-"
        # Don't set end_id to start_id for flexible endpoints - solver will choose
        end_id = raw_end_id if not flexible_endpoint else None

        print(
            f"🔧 D{did} endpoint config: cfg.end_airport={cfg.get('end_airport')}, start_id={start_id}, raw_end_id={raw_end_id}, end_id={end_id}, flexible={flexible_endpoint}",  # noqa: E501
            flush=True,
        )

        # Runway compatibility. Skip synthetic starts and checkpoints — they
        # carry a drone forward from a previous segment and aren't subject to
        # runway constraints. For real airports, refuse to plan if the
        # configured start or end is incompatible with the drone's runway.
        drone_runway = str(cfg.get("runway", "S")).upper()
        if not (is_synthetic_start or is_checkpoint_start):
            start_ap = airport_by_id.get(start_id)
            if start_ap and not _airport_accepts_drone(
                start_ap.get("runway", "L"), drone_runway
            ):
                print(
                    f"⚠️  D{did} DISABLED: start airport {start_id} runway "
                    f"{start_ap.get('runway')} cannot accept drone (needs {drone_runway})",
                    flush=True,
                )
                sequences[did] = ""
                routes_detail[did] = {
                    "route": [],
                    "sequence": "",
                    "points": 0,
                    "distance": 0.0,
                    "fuel_budget": fuel_budget,
                    "error": (
                        f"runway mismatch: drone needs {drone_runway}, "
                        f"start airport {start_id} is {start_ap.get('runway')}"
                    ),
                }
                continue
        if end_id and end_id != start_id and end_id != "-":
            end_ap = airport_by_id.get(end_id)
            if end_ap and not _airport_accepts_drone(
                end_ap.get("runway", "L"), drone_runway
            ):
                print(
                    f"⚠️  D{did} DISABLED: end airport {end_id} runway "
                    f"{end_ap.get('runway')} cannot accept drone (needs {drone_runway})",
                    flush=True,
                )
                sequences[did] = ""
                routes_detail[did] = {
                    "route": [],
                    "sequence": "",
                    "points": 0,
                    "distance": 0.0,
                    "fuel_budget": fuel_budget,
                    "error": (
                        f"runway mismatch: drone needs {drone_runway}, "
                        f"end airport {end_id} is {end_ap.get('runway')}"
                    ),
                }
                continue

        # Filter targets by category access (target_access.py: the one rule;
        # it allowed only A-D here when a drone had no access set) AND exclude
        # those inside SAM polygons.
        candidate_targets = [
            t
            for t in targets
            if type_allowed(cfg, t) and str(t.get("id", "")) not in excluded_targets
        ]

        # DEBUG: Check if any excluded targets would have been included
        excluded_here = [
            t["id"] for t in targets if str(t.get("id", "")) in excluded_targets
        ]
        if excluded_here:
            print(
                f"   🚫 D{did}: Excluded targets (inside SAM polygons): {excluded_here}",
                flush=True,
            )

        # Additionally filter by assigned_drone if any targets have assignments
        any_assigned = any(t.get("assigned_drone") is not None for t in targets)
        if any_assigned:
            candidate_targets = [
                t
                for t in candidate_targets
                if t.get("assigned_drone") is None
                or str(t.get("assigned_drone")) == did
            ]

        if not candidate_targets:
            # No targets: trivial route start->end
            if start_id and end_id:
                no_target_route_ids = [start_id, end_id]
            else:
                no_target_route_ids = []
            seq = ",".join(no_target_route_ids) if no_target_route_ids else ""
            sequences[did] = seq
            routes_detail[did] = {
                "route": no_target_route_ids,
                "sequence": seq,
                "points": 0,
                "distance": 0.0,
                "fuel_budget": fuel_budget,
            }
            print(f"🚁 Drone {did}: No targets assigned, trivial route", flush=True)
            continue

        # Debug: Print drone solve info
        target_ids = [t["id"] for t in candidate_targets]
        print(
            f"🚁 Drone {did}: Solving with {len(candidate_targets)} targets: {target_ids}",
            flush=True,
        )

        # Build a FILTERED distance matrix containing only airports + this drone's targets
        # This is critical - the solver uses matrix_labels to determine which nodes to visit
        airport_ids = [a["id"] for a in airports]
        desired_ids = airport_ids + target_ids

        # Create index mapping from original labels
        orig_labels = dist_data["labels"]
        orig_matrix = dist_data["matrix"]

        # Build filtered matrix - ONLY include IDs that are actually in orig_labels
        # This ensures labels and matrix dimensions match
        # CRITICAL: Always include start_id and end_id
        filtered_indices = []
        filtered_ids = []  # Track which IDs are actually included
        for fid in desired_ids:
            if fid in orig_labels:
                filtered_indices.append(orig_labels.index(fid))
                filtered_ids.append(fid)

        # Ensure start and end airports are ALWAYS in the filtered matrix
        # (skip end_id if None, which happens for flexible endpoints)
        for required_id in [start_id, end_id]:
            if (
                required_id
                and required_id not in filtered_ids
                and required_id in orig_labels
            ):
                filtered_indices.append(orig_labels.index(required_id))
                filtered_ids.append(required_id)
                print(
                    f"   ⚠️ Added missing required airport {required_id} to filtered matrix",
                    flush=True,
                )

        n = len(filtered_indices)
        filtered_matrix = [[0.0] * n for _ in range(n)]
        for i, orig_i in enumerate(filtered_indices):
            for j, orig_j in enumerate(filtered_indices):
                filtered_matrix[i][j] = orig_matrix[orig_i][orig_j]

        filtered_dist_data = {
            "matrix": filtered_matrix,
            "labels": filtered_ids,  # Use the filtered list, not desired_ids
            "waypoints": [
                wp for wp in dist_data.get("waypoints", []) if wp["id"] in filtered_ids
            ],
            "excluded_targets": (
                list(excluded_targets) if excluded_targets else []
            ),  # Propagate excluded targets
        }

        # Count actual airports and targets in filtered list
        actual_airport_count = sum(1 for fid in filtered_ids if fid in airport_ids)
        actual_target_count = len(filtered_ids) - actual_airport_count
        print(
            f"   📊 Filtered matrix: {len(filtered_ids)} nodes ({actual_airport_count} airports + {actual_target_count} targets)",  # noqa: E501
            flush=True,
        )

        # Build airports list for solver - start with real airports
        solver_airports = list(airports)  # Copy to avoid mutating original
        # CRITICAL: Real airports are those that are NOT synthetic/checkpoint (no is_synthetic/is_checkpoint flag AND not ending in _START or starting with C)  # noqa: E501
        # Synthetic starts and checkpoints should NEVER be valid endpoints.
        # Also enforce runway compatibility — this list flows to valid_end_airports
        # so a drone is never offered an endpoint at an airport too short for it.
        real_airport_ids = [
            a["id"]
            for a in airports
            if is_real_airport(a)
            and _airport_accepts_drone(a.get("runway", "L"), drone_runway)
        ]

        # CRITICAL FIX: Add ALL synthetic starts and checkpoints that are in the filtered matrix
        # This ensures every node in matrix_labels is classified as either airport or target
        # Without this, the solver guard will reject unknown labels like D1_START, D2_START, C1-1, C1-2
        waypoint_positions_for_synthetic = {
            wp["id"]: wp for wp in dist_data.get("waypoints", [])
        }
        for fid in filtered_ids:
            # Check for synthetic starts (D1_START, D2_START, etc.)
            if fid.endswith("_START") and fid not in real_airport_ids:
                # This is a synthetic start - add it as an airport
                if fid in waypoint_positions_for_synthetic:
                    wp = waypoint_positions_for_synthetic[fid]
                    synthetic_airport = {
                        "id": fid,
                        "x": wp["x"],
                        "y": wp["y"],
                        "is_synthetic": True,  # Mark so we can filter for endpoints
                    }
                    solver_airports.append(synthetic_airport)
                    print(
                        f"   ✅ Added synthetic start '{fid}' to airports: ({wp['x']:.1f}, {wp['y']:.1f})",
                        flush=True,
                    )
                else:
                    print(
                        f"   ⚠️ WARNING: Synthetic start '{fid}' not found in waypoints!",
                        flush=True,
                    )
            # Check for checkpoints (C1-1, C1-2, C2-1, etc.)
            elif re.match(r"^C\d+-\d+$", fid) and fid not in [
                a["id"] for a in solver_airports
            ]:
                # This is a checkpoint - add it as an airport
                if fid in waypoint_positions_for_synthetic:
                    wp = waypoint_positions_for_synthetic[fid]
                    checkpoint_airport = {
                        "id": fid,
                        "x": wp["x"],
                        "y": wp["y"],
                        "is_checkpoint": True,  # Mark so we can filter for endpoints
                    }
                    solver_airports.append(checkpoint_airport)
                    print(
                        f"   ✅ Added checkpoint '{fid}' to airports: ({wp['x']:.1f}, {wp['y']:.1f})",
                        flush=True,
                    )
                else:
                    print(
                        f"   ⚠️ WARNING: Checkpoint '{fid}' not found in waypoints!",
                        flush=True,
                    )

        print(
            f"   🔍 DEBUG: solver_airports: {[a['id'] for a in solver_airports]}",
            flush=True,
        )

        # Build a per-drone environment using your existing interface
        env_for_solver = solver.build_environment_for_solver(
            airports=solver_airports,  # ← Include synthetic starts as airports
            targets=candidate_targets,
            sams=sams,
            distance_matrix_data=filtered_dist_data,
        )
        env_for_solver["start_airport"] = start_id

        # Handle flexible endpoint vs fixed endpoint
        if flexible_endpoint:
            print(
                "   🔄 Flexible endpoint: solver will choose optimal end airport",
                flush=True,
            )
            env_for_solver["mode"] = "best_end"

            # CRITICAL: Remove end_airport from env if it was set by build_environment_for_solver
            # This prevents the solver from using a synthetic start as the endpoint
            if "end_airport" in env_for_solver:
                print(
                    f"   🔍 DEBUG: Removing end_airport '{env_for_solver['end_airport']}' from env_for_solver",
                    flush=True,
                )
                del env_for_solver["end_airport"]

            # CRITICAL: Filter out synthetic starts from valid end airports
            # Synthetic starts (e.g., D1_START) are used for checkpoint replanning
            # but should NEVER be valid endpoints - only real airports should be endpoints
            # Check both the is_synthetic flag AND the ID pattern (ends with _START)
            print(
                f"   🔍 DEBUG: All airports: {[a['id'] for a in airports]}", flush=True
            )
            real_airports = [a for a in airports if is_real_airport(a)]
            synthetic_or_checkpoint = [
                a
                for a in airports
                if a.get("is_synthetic", False)
                or a.get("is_checkpoint", False)
                or str(a.get("id", "")).endswith("_START")
                or str(a.get("id", "")).startswith("C")
            ]
            print(
                f"   🔍 DEBUG: Real airports (non-synthetic/checkpoint): {[a['id'] for a in real_airports]}",
                flush=True,
            )
            print(
                f"   🔍 DEBUG: Synthetic/checkpoint: {[a['id'] for a in synthetic_or_checkpoint]}",
                flush=True,
            )
            if real_airports:
                # Filter to runway-compatible airports only.
                runway_ok = [
                    a
                    for a in real_airports
                    if _airport_accepts_drone(a.get("runway", "L"), drone_runway)
                ]
                env_for_solver["valid_end_airports"] = [a["id"] for a in runway_ok]
                print(
                    f"   ✈️ Valid end airports (runway-compatible, excluding synthetic): {env_for_solver['valid_end_airports']}",
                    flush=True,
                )
            else:
                print(
                    "   ⚠️ WARNING: No real airports found! All airports are synthetic.",
                    flush=True,
                )
        else:
            # Fixed endpoint
            env_for_solver["end_airport"] = end_id
            env_for_solver["mode"] = "return" if end_id == start_id else "end"

        # Call solver (with optional two-leg refueling)
        # Refuel modes: refuel_options enables opportunistic refuel; an explicit
        # legacy refuel_airport enables a fixed two-leg refuel. The retired "-"
        # sentinel is treated as no refuel.
        solve_start = time.time()
        if refuel_airport and refuel_airport != "":
            # --- Two-leg refueling solve ---
            if refuel_airport == "-":
                # "Any" mode: greedy scan to find best airport, then exhaustive on the winner.
                # Only consider real airports compatible with this drone's runway.
                real_ap_ids = [
                    a["id"]
                    for a in airports
                    if is_real_airport(a)
                    and _airport_accepts_drone(a.get("runway", "L"), drone_runway)
                ]
                print(
                    f"   ⛽ D{did} ANY refuel: scanning {len(real_ap_ids)} runway-compatible airports (greedy)",
                    flush=True,
                )
                best_greedy_sol = None
                best_greedy_ap = None
                for rap_id in real_ap_ids:
                    r1, v1, d1 = _solve_one_leg(
                        env_for_solver,
                        leg1_budget,  # §8.2: leg 1 = fuel at the leg-1 start
                        start_id,
                        rap_id,
                        candidate_targets,
                        did,
                        "L1",
                        drone_cfg=cfg,
                    )
                    remaining = [t for t in candidate_targets if str(t["id"]) not in v1]
                    r2, v2, d2 = _solve_one_leg(
                        env_for_solver,
                        leg2_budget,  # §8.2: leg 2 starts with a FULL TANK (refueled)
                        rap_id,
                        end_id,
                        remaining,
                        did,
                        "L2",
                        flexible_endpoint,
                        drone_cfg=cfg,
                    )
                    pts = len(v1) + len(v2)
                    total_dist = d1 + d2 + REFUEL_STOP_COST_KM
                    # §5: only a candidate whose BOTH legs fit their own budgets is
                    # eligible (leg 1 = leg1_budget, leg 2 = full tank leg2_budget).
                    # Never store an infeasible leg as the "winner".
                    leg_feasible = d1 <= leg1_budget + 1e-6 and d2 <= leg2_budget + 1e-6
                    if leg_feasible and (
                        best_greedy_sol is None
                        or pts > len(best_greedy_sol.get("visited_targets", []))
                        or (
                            pts == len(best_greedy_sol.get("visited_targets", []))
                            and total_dist < best_greedy_sol["distance"]
                        )
                    ):
                        combined = list(r1)
                        if r2 and combined and combined[-1] == r2[0]:
                            combined.extend(r2[1:])
                        else:
                            combined.extend(r2)
                        best_greedy_sol = {
                            "route": combined,
                            "visited_targets": list(v1 | v2),
                            "distance": total_dist,
                            "leg1_distance": d1,
                            "leg2_distance": d2,
                            "refuel_airport": rap_id,
                            "refuel_idx": len(r1)
                            - 1,  # Index of refuel point in combined route
                        }
                        best_greedy_ap = rap_id
                if best_greedy_sol is None:
                    # §5: no feasible refuel candidate at ANY airport → degrade to the
                    # single-leg fly-to-end floor (drop refuel, take whatever fits down
                    # to zero targets). Never hard-fail / never an infeasible route.
                    print(
                        f"   ⛽ D{did} No feasible refuel at any airport — single-leg "
                        f"fly-to-end floor",
                        flush=True,
                    )
                    floor_end = end_id or start_id
                    fr, fv, fd = _solve_one_leg(
                        env_for_solver,
                        leg1_budget,
                        start_id,
                        floor_end,
                        candidate_targets,
                        did,
                        "Floor",
                        flexible_endpoint,
                        drone_cfg=cfg,
                    )
                    sol = {
                        "route": fr,
                        "visited_targets": list(fv),
                        "distance": fd,
                    }
                else:
                    if best_greedy_ap is None:
                        raise RuntimeError("ANY refuel winner missing airport id")

                    # Exhaustive on the winner
                    sol = _solve_two_leg_refuel(
                        env_for_solver=env_for_solver,
                        fuel_budget=fuel_budget,
                        start_id=start_id,
                        end_id=end_id,
                        refuel_airport_id=best_greedy_ap,
                        candidate_targets=candidate_targets,
                        flexible_endpoint=flexible_endpoint,
                        did=did,
                        drone_cfg=cfg,
                        leg2_budget=leg2_budget,  # §11.10 full tank for post-refuel leg
                    )
                    if len(sol.get("visited_targets", [])) < len(
                        best_greedy_sol.get("visited_targets", [])
                    ) or (
                        len(sol.get("visited_targets", []))
                        == len(best_greedy_sol.get("visited_targets", []))
                        and sol["distance"] >= best_greedy_sol["distance"]
                    ):
                        sol = best_greedy_sol
                print(
                    f"   ⛽ D{did} ANY refuel winner: refuel at {sol.get('refuel_airport')}, {len(sol.get('visited_targets', []))} targets, {sol.get('distance', 0):.1f} dist",  # noqa: E501
                    flush=True,
                )
            else:
                # Specific airport mode
                sol = _solve_two_leg_refuel(
                    env_for_solver=env_for_solver,
                    fuel_budget=fuel_budget,
                    start_id=start_id,
                    end_id=end_id,
                    refuel_airport_id=refuel_airport,
                    candidate_targets=candidate_targets,
                    flexible_endpoint=flexible_endpoint,
                    did=did,
                    drone_cfg=cfg,
                    leg2_budget=leg2_budget,  # §11.10 full tank for post-refuel leg
                )
        else:
            # --- Normal single-leg solve ---
            # Solve with full budget, then check travel + loiter feasibility
            sol = solver.solve(env_for_solver, fuel_budget)

            # Check if travel + loiter exceeds budget, iteratively drop if needed
            if cfg.get("loiter_enabled"):
                speed = float(cfg.get("speed", 1))
                loiter_times_cfg = cfg.get("loiter_times", {})
                loiter_cost_map = {}
                for t in candidate_targets:
                    t_type = (t.get("type") or "a").lower()
                    lt = float(loiter_times_cfg.get(t_type, 0))
                    if lt > 0:
                        loiter_cost_map[str(t["id"])] = lt * speed

                remaining_targets = list(candidate_targets)
                for _ in range(len(candidate_targets)):
                    travel_dist = float(sol.get("distance", 0.0))
                    vis = sol.get("visited_targets", [])
                    actual_loiter = sum(loiter_cost_map.get(tid, 0) for tid in vis)
                    if travel_dist + actual_loiter <= fuel_budget or not vis:
                        break
                    # Drop lowest-priority visited target with loiter cost
                    vis_with_loiter = [
                        (tid, loiter_cost_map.get(tid, 0))
                        for tid in vis
                        if loiter_cost_map.get(tid, 0) > 0
                    ]
                    if not vis_with_loiter:
                        break
                    target_map = {str(t["id"]): t for t in remaining_targets}
                    vis_with_loiter.sort(
                        key=lambda x: (
                            int(target_map.get(x[0], {}).get("priority", 5)),
                            -x[1],
                        )
                    )
                    drop_tid = vis_with_loiter[0][0]
                    print(
                        f"   🕐 D{did}: over budget ({travel_dist + actual_loiter:.1f} > {fuel_budget}), dropping {drop_tid}",  # noqa: E501
                        flush=True,
                    )
                    remaining_targets = [
                        t for t in remaining_targets if str(t["id"]) != drop_tid
                    ]
                    # Rebuild env and re-solve
                    env_for_solver_copy = {
                        **env_for_solver,
                        "targets": remaining_targets,
                    }
                    sol = solver.solve(env_for_solver_copy, fuel_budget)

        solve_time = time.time() - solve_start

        # The solver returns 'route' (optimal ordered path) and 'visited_targets' (target IDs)
        # Use 'route' directly as it contains the optimal ordering from Held-Karp
        route_ids: List[str] = sol.get("route", [])
        visited_targets: List[str] = sol.get("visited_targets", [])

        # Fallback if solver didn't return route
        if not route_ids:
            fallback_end_id = end_id or start_id
            route_ids = [start_id] + visited_targets + [fallback_end_id]
        elif len(route_ids) == 1 and end_id and route_ids[-1] != end_id:
            # Solver returned only start (no targets fit), add the end airport
            route_ids = route_ids + [end_id]
            print(
                f"   📍 Added end airport {end_id} to single-waypoint route", flush=True
            )

        # Calculate total points from visited targets
        target_priorities = {
            t["id"]: int(t.get("priority", 1)) for t in candidate_targets
        }
        total_points: int = sum(
            target_priorities.get(tid, 0) for tid in visited_targets
        )
        travel_distance: float = float(sol.get("distance", 0.0))

        print(
            f"   ✅ Drone {did} solved in {solve_time:.2f}s: {len(route_ids)} waypoints, {total_points} pts, {travel_distance:.1f} dist",  # noqa: E501
            flush=True,
        )

        seq = ",".join(route_ids) if route_ids else ""

        # Generate trajectory using clean ISRTrajectoryPlanner (like Delivery Planner)
        trajectory_planner = ISRTrajectoryPlanner(sams)
        # Build waypoint positions from dist_data AND airports (for checkpoints)
        waypoint_positions: Dict[str, List[float]] = {
            wp["id"]: [wp["x"], wp["y"]] for wp in dist_data.get("waypoints", [])
        }
        # Also add airports (including checkpoints) that might not be in waypoints
        for a in airports:
            aid = str(a.get("id", ""))
            if aid and aid not in waypoint_positions:
                waypoint_positions[aid] = [float(a["x"]), float(a["y"])]
        # Detection-radius taut-string: slide target waypoints to the path-closest
        # point inside their detection circle (order fixed, SAM-aware) BEFORE the
        # trajectory is generated. No-op unless this drone has a radius configured.
        relaxed_positions = _relax_positions_to_detection_radii(
            route_ids,
            _positions_as_tuples(waypoint_positions),
            candidate_targets,
            cfg,
            sams,
        )
        waypoint_positions = _positions_as_lists(relaxed_positions)
        trajectory, waypoint_indices = cast(
            Tuple[List[List[float]], Dict[int, int]],
            trajectory_planner.generate_trajectory(
                route_ids,
                waypoint_positions,
                drone_id=did,
                return_waypoint_indices=True,
            ),
        )

        print(
            f"   🛫 Generated trajectory: {len(trajectory)} points for route {route_ids}",
            flush=True,
        )

        sequences[did] = seq
        route_info = {
            "route": route_ids,
            "sequence": seq,
            "points": total_points,
            "distance": travel_distance,  # SAM-aware distance from solver
            "fuel_budget": fuel_budget,
            "trajectory": trajectory,  # Full path with SAM avoidance
            "waypoint_indices": waypoint_indices,  # Maps route index -> trajectory index
        }
        # Attach refueling metadata whenever refuel is configured
        # (even if Leg2 is empty, INSERT needs this to know it's a refueling drone)
        if refuel_airport and refuel_airport != "":
            route_info["refuel_airport"] = sol.get("refuel_airport", refuel_airport)
            route_info["leg1_distance"] = sol.get("leg1_distance", 0.0)
            route_info["leg2_distance"] = sol.get("leg2_distance", 0.0)
            route_info["refuel_idx"] = sol.get("refuel_idx", -1)
        routes_detail[did] = route_info

    # Compute wrapped polygons for visualization (like solve_mission_with_allocation)
    wrapped_polygons: list = []
    if sams:
        # Convert SAMs to simple format for wrapping
        sams_for_wrapping = []
        for sam in sams:
            pos = sam.get("pos") or sam.get("position")
            if pos:
                sams_for_wrapping.append(
                    {
                        "x": pos[0] if isinstance(pos, (list, tuple)) else pos,
                        "y": pos[1] if isinstance(pos, (list, tuple)) else pos,
                        "radius": float(sam.get("range", sam.get("radius", 15))),
                    }
                )
        if sams_for_wrapping:
            wrapped_polygon_arrays, _ = wrap_sams(sams_for_wrapping)
            for poly in wrapped_polygon_arrays:
                # If it's a NumPy array, convert to list; if it's already a list, keep it
                if hasattr(poly, "tolist"):
                    wrapped_polygons.append(poly.tolist())
                else:
                    wrapped_polygons.append(poly)

    result = {
        "sequences": sequences,
        "routes": routes_detail,
        "allocations": final_allocations,
        "wrapped_polygons": wrapped_polygons,
    }

    # Unproject trajectories back to lat/lng before returning
    _unproject_solution_trajectories(result, ref_lat, ref_lng)

    return result


def solve_mission_with_allocation(
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    allocation_strategy: str = "geographic",
    use_sam_aware_distances: bool = True,  # IGNORED — distances are ALWAYS SAM-aware (policy)
    post_optimize: bool = True,
    leg_split: Optional[Dict[str, Dict[str, List[str]]]] = None,
    constraints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Enhanced mission solver with allocation, SAM-aware distances, and post-optimization.

    This is the recommended entry point for multi-drone mission solving.

    Pipeline:
    1. Calculate SAM-aware distance matrix (if enabled)
    2. Allocate targets to drones using specified strategy
    3. Solve orienteering for each drone with assigned targets
    4. Post-optimize to include any unassigned targets (if enabled)

    Args:
        env: Environment JSON from frontend
        drone_configs: Drone configurations
        allocation_strategy: "greedy", "balanced", "efficient", "geographic", "exclusive"
        use_sam_aware_distances: Whether to calculate SAM-aware distances
        post_optimize: Whether to run post-optimization for unvisited targets

    Returns:
        Solution dict with sequences and routes for each drone
    """
    global _cached_env_hash
    solver = _require_solver()

    drone_configs = _normalize_fuel_budget_keys(drone_configs)
    # Mission matrix at the altitude most drones fly (agents call this directly).
    env, drone_configs = with_primary_altitude(env, drone_configs)
    # A drone may only refuel at tankers within reach of its altitude.
    drone_configs = without_unreachable_tankers(env, drone_configs)

    # MULTI-STOP REFUEL (virtual drones): if any drone declares refuel_stops, expand
    # each such drone into per-leg virtual drones (1_L1, 1_L2, ...) BEFORE allocation.
    # The rest of the pipeline (allocate -> Held-Karp -> optimize) then treats them
    # as ordinary drones with fixed start/end — "just more drones". After solving we
    # merge the legs back to the physical drone. Drones WITHOUT refuel_stops are
    # untouched (they still hit the legacy refuel_airport path below). This gate is
    # off until the frontend emits refuel_stops, so existing behavior is unchanged.
    from .virtual_drones import _refuel_stops as _vd_refuel_stops
    from .virtual_drones import (
        expand_to_virtual_drones,
        merge_virtual_to_physical,
        parent_of,
    )
    from .virtual_drones import sort_key as _vd_sort_key

    # OPPORTUNISTIC refuel is handled PER-DRONE inside the solve loop (it decides
    # its own sorties), so it must NOT pre-expand into fixed virtual legs. A drone
    # is virtual-expanded only when it uses the legacy prescriptive path (no
    # refuel_options / opportunistic disabled).
    def _is_opportunistic(c):
        c = c or {}
        opts = c.get(
            "refuel_options"
        )  # opt-in ONLY via refuel_options (not legacy refuel_stops)
        has_opts = isinstance(opts, list) and any(
            s and str(s) not in ("-", "None") for s in opts
        )
        return has_opts and c.get("opportunistic_refuel", True)

    _use_virtual_drones = any(
        _vd_refuel_stops(c or {}) and not _is_opportunistic(c)
        for c in (drone_configs or {}).values()
    )
    _physical_drone_configs = drone_configs
    if _use_virtual_drones:
        drone_configs = expand_to_virtual_drones(drone_configs)
        print(
            f"⛽ Multi-stop refuel: expanded to {len(drone_configs)} virtual drones "
            f"[{', '.join(sorted(drone_configs.keys()))}]",
            flush=True,
        )

    # Project lat/lng env to local km coordinates for internal processing
    env, ref_lat, ref_lng = _project_env_to_local_km(env)
    print(
        f"📐 Projected env to local km (ref: {ref_lat:.4f}, {ref_lng:.4f})", flush=True
    )

    # NOTE: cruise-altitude SAM shrinking happens once, upstream, at the API
    # boundary (server/api/solve_routes.py) on the raw lat/lng env — NOT here.
    # This function is called by the main solve AND reached again indirectly
    # via the post-solve optimizer pipeline's own re-projection of the same
    # env; shrinking here as well would apply the altitude formula twice.

    airports, targets, sams = _parse_env_for_solver(env)

    if not airports:
        return {
            "sequences": {},
            "routes": {},
        }

    # Note: We don't return early if targets is empty - drones still need
    # routes to return from checkpoints to airports when all targets are visited

    # Step 1: Get distance matrix
    # ALWAYS use SAM-aware matrix calculator for consistency
    # This ensures targets inside SAM circles are always excluded
    # When no SAMs present, it still computes correct Euclidean distances
    # Note: _compute_env_hash operates on the ORIGINAL lat/lng env but we've already
    # deep-copied it, so we need to compute the hash from the projected env
    # Drone altitudes are part of the key: each altitude has its own matrix.
    current_hash = _compute_env_hash(env) + "|" + altitude_signature(drone_configs, env)

    # Only recalculate if environment changed - caching saves significant time
    # when agent tries multiple strategies on same environment
    sam_count = len(sams) if sams else 0
    cached_matrix = get_cached_matrix()
    if _cached_env_hash == current_hash and cached_matrix is not None:
        print(f"✅ Reusing cached distance matrix ({sam_count} SAMs)", flush=True)
        dist_data = cached_matrix
    else:
        print(f"🎯 Calculating fresh distance matrix ({sam_count} SAMs)...", flush=True)
        # Debug: Check if checkpoints are in env before matrix calculation
        checkpoints_in_env = env.get("checkpoints", [])
        if checkpoints_in_env:
            print(
                f"📍 [MATRIX] Checkpoints in env: {[c.get('id') for c in checkpoints_in_env]}",
                flush=True,
            )
        else:
            print("⚠️ [MATRIX] No checkpoints in env!", flush=True)
        clear_matrix_cache()
        clear_allocator_matrix()  # Also clear allocator's cached matrix
        dist_data = calculate_sam_aware_matrix(env)
        # Drones flying another altitude get their own matrix (SAMs sliced at
        # their altitude); matrix_for_drone() picks it. Building those replaces
        # the calculator's cache, so cache the full bundle again.
        dist_data = attach_altitude_matrices(
            dist_data, env, drone_configs, calculate_sam_aware_matrix
        )
        set_cached_matrix(dist_data)
        _cached_env_hash = current_hash

    # Always set distance matrices for allocator and optimizer
    # Even Euclidean distances are needed for fuel budget calculations
    set_allocator_matrix(dist_data)
    set_optimizer_matrix(dist_data)

    # DEBUG: Log excluded targets from distance matrix
    excluded_targets = set(dist_data.get("excluded_targets", []))
    if excluded_targets:
        print(
            f"🚫 [DEBUG] EXCLUDED TARGETS from distance matrix: {sorted(excluded_targets)}",
            flush=True,
        )
    else:
        print("🔍 [DEBUG] No excluded targets from distance matrix", flush=True)

    # Step 2: Allocate targets to drones
    allocations = allocate_targets(env, drone_configs, allocation_strategy, dist_data)
    _apply_constraint_allocations(
        allocations,
        constraints,
        drone_configs,
        {str(t.get("id")) for t in targets},
    )

    # Honor an explicit leg_split: when the caller (e.g. the agent's
    # commit_mission_plan) hands us a specific target-to-leg assignment, that is a
    # HARD constraint — override the allocator's geographic distribution across the
    # virtual legs. Without this, expand_to_virtual_drones + allocate_targets
    # re-distribute targets across 1_L1/1_L2 and silently discard the split.
    if leg_split:
        for parent_did, split in leg_split.items():
            if not isinstance(split, dict):
                continue
            # Ordered leg keys: leg1, leg2, ... (only leg1/leg2 today — commit_mission_plan
            # caps at one refuel = two legs. legN support would extend this list.)
            leg_keys = sorted(
                (k for k in split.keys() if k.startswith("leg")),
                key=lambda k: int(k[3:]) if k[3:].isdigit() else 0,
            )
            expanded = f"{parent_did}_L1" in allocations
            if expanded:
                for i, leg_key in enumerate(leg_keys, start=1):
                    alloc_key = f"{parent_did}_L{i}"
                    if alloc_key in allocations:
                        allocations[alloc_key] = [
                            str(t) for t in (split.get(leg_key) or [])
                        ]
            elif parent_did in allocations:
                # Not expanded (no refuel) — all legs collapse onto the parent.
                merged: List[str] = []
                for leg_key in leg_keys:
                    merged.extend(str(t) for t in (split.get(leg_key) or []))
                allocations[parent_did] = merged
            print(
                f"🔒 leg_split honored for drone {parent_did}: "
                + " ".join(f"{k}={split.get(k)}" for k in leg_keys),
                flush=True,
            )

    # Log allocation results
    print("\n" + "=" * 60, flush=True)
    print(f"🎯 TARGET ALLOCATION RESULTS (strategy: {allocation_strategy})", flush=True)
    print("=" * 60, flush=True)
    total_allocated = 0
    for did in sorted(allocations.keys(), key=_vd_sort_key):
        target_ids = allocations[did]
        total_allocated += len(target_ids)
        cfg = drone_configs.get(did, {})
        if cfg.get("enabled") is not False:
            if target_ids:
                print(f"  Drone {did}: {target_ids}", flush=True)
            else:
                print(f"  Drone {did}: [] (enabled but no targets)", flush=True)
    print(f"  Total targets allocated: {total_allocated}/{len(targets)}", flush=True)
    print("=" * 60 + "\n", flush=True)

    # Step 3: Solve for each drone with their assigned targets
    sequences: Dict[str, str] = {}
    routes_detail: Dict[str, Dict[str, Any]] = {}

    # Drone ids to solve. Normally the fixed 1..max integer range (so absent drones
    # get an empty entry for the frontend's 5-drone display). With multi-stop refuel
    # the configs are keyed by VIRTUAL ids ("1_L1", ...) which aren't integers, so
    # iterate the actual config keys instead. merge_virtual_to_physical collapses
    # them back to physical ids afterward.
    if _use_virtual_drones:
        drone_ids_to_solve = sorted(drone_configs.keys())
    else:
        max_drone_id = max([int(did) for did in drone_configs.keys()] or [4])
        drone_ids_to_solve = [str(i) for i in range(1, max_drone_id + 1)]

    mission_dist_data, mission_sams, mission_excluded = (
        dist_data,
        sams,
        excluded_targets,
    )
    for did in drone_ids_to_solve:
        cfg = drone_configs.get(did, {})

        # This drone plans at its own cruise altitude: its own matrix and SAM
        # slices (the mission's when it flies the mission altitude).
        dist_data = matrix_for_drone(mission_dist_data, did) or mission_dist_data
        sams = _parse_env_for_solver(env_for_drone(env, cfg))[2]
        excluded_targets = set(dist_data.get("excluded_targets", []))

        # Resolve the ordering constraints that apply to this physical drone (or
        # its virtual-leg parent). None => unconstrained (original) behavior.
        drone_constraints = _constraints_for_drone(constraints, did)

        # For a refuel expanded into virtual legs (1_L1, 1_L2, ...), the physical
        # drone's constraints must be SUBSET to each leg: `first` binds only leg 1,
        # `last` only the final leg, and before/adjacent pairs are kept only when
        # both endpoints land on this leg. Without this, `first`/`last` leak onto the
        # wrong leg (or a `first` meant for leg 1 never DP-prunes because it was
        # dropped). Mirrors how _solve_two_leg_refuel subsets its legs.
        if drone_constraints and cfg.get("is_virtual"):
            _leg_no = int(cfg.get("leg_number", 1))
            _total_legs = int(cfg.get("total_legs", 1))
            drone_constraints = _subset_constraints_for_leg(
                drone_constraints,
                allocations.get(did, []),
                is_first_leg=(_leg_no == 1),
                is_last_leg=(_leg_no == _total_legs),
            )

        # Skip disabled drones
        if cfg.get("enabled") is False:
            sequences[did] = ""
            routes_detail[did] = {
                "route": [],
                "sequence": "",
                "points": 0,
                "distance": 0.0,
                "fuel_budget": float(cfg.get("fuel_budget", 999.0)),
            }
            continue

        fuel_budget = float(cfg.get("fuel_budget", 999.0))
        # §11.10: ONE fuel value — fuel_budget IS the tank (full each leg). leg 1 =
        # the fuel at its start: full tank on a fresh solve, or the REDUCED remaining
        # on a dynamic replan, passed as transient `start_fuel` (config not mutated).
        # Every post-refuel leg = a FULL TANK = fuel_budget. (fuel_capacity retired.)
        start_fuel = cfg.get("start_fuel")
        leg1_budget = float(fuel_budget if start_fuel is None else start_fuel)
        leg2_budget = fuel_budget
        fuel_budget = leg1_budget  # downstream single-leg/feasibility use start fuel
        refuel_airport = _legacy_refuel_airport(cfg)

        # Get start/end airports. Default fallback uses the PHYSICAL drone number
        # (parent of a virtual "1_L2" is "1" → "A1"); virtual legs always carry an
        # explicit start_airport so this fallback only matters for plain drones.
        airport_by_id = {a["id"]: a for a in airports}
        default_start = f"A{parent_of(did)}"
        cfg_start = cfg.get("start_airport")

        # Check if cfg_start is a synthetic start (e.g., D1_START) or checkpoint (e.g., C1-1, C2-3)
        # These should be used directly, not fall back to real airports
        # Checkpoint format: C{segment}-{drone} (e.g., C1-1 = Checkpoint 1 for Drone 1)
        is_synthetic_start = (
            cfg_start
            and cfg_start.endswith("_START")
            and cfg_start not in airport_by_id
        )
        is_checkpoint_start = cfg_start and bool(re.match(r"^C\d+-\d+$", cfg_start))

        if is_synthetic_start or is_checkpoint_start:
            # Use the synthetic start or checkpoint directly - it's in matrix_labels but not necessarily in airport_by_id  # noqa: E501
            if cfg_start is None:
                raise RuntimeError(
                    "Synthetic/checkpoint start missing start airport id"
                )
            start_id = cfg_start
            start_type = "CHECKPOINT" if is_checkpoint_start else "SYNTHETIC"
            print(
                f"🔍 [solve_mission_with_allocation] D{did}: Using {start_type} start '{start_id}'",
                flush=True,
            )
        else:
            start_id = cfg_start or (
                default_start if default_start in airport_by_id else airports[0]["id"]
            )

        # Handle flexible endpoint: "-" means solver chooses optimal endpoint.
        # The end airport is sanitized UI-side (readDroneConfigsFromUI enforces a real
        # airport or "-"), so end→start only fires for a genuinely absent end.
        raw_end_id = cfg.get("end_airport") or start_id
        flexible_endpoint = raw_end_id == "-"
        # Don't set end_id to start_id for flexible endpoints - solver will choose
        end_id = raw_end_id if not flexible_endpoint else None

        # Runway compatibility (real airports only — synthetic/checkpoint are
        # carry-forward starts and not subject to runway constraints).
        drone_runway = str(cfg.get("runway", "S")).upper()
        if not (is_synthetic_start or is_checkpoint_start):
            start_ap = airport_by_id.get(start_id)
            if start_ap and not _airport_accepts_drone(
                start_ap.get("runway", "L"), drone_runway
            ):
                print(
                    f"⚠️  [solve_mission_with_allocation] D{did} DISABLED: start airport "
                    f"{start_id} runway {start_ap.get('runway')} cannot accept drone "
                    f"(needs {drone_runway})",
                    flush=True,
                )
                sequences[did] = ""
                routes_detail[did] = {
                    "route": [],
                    "sequence": "",
                    "points": 0,
                    "distance": 0.0,
                    "fuel_budget": fuel_budget,
                    "error": (
                        f"runway mismatch: drone needs {drone_runway}, "
                        f"start airport {start_id} is {start_ap.get('runway')}"
                    ),
                }
                continue
        if end_id and end_id != start_id and end_id != "-":
            end_ap = airport_by_id.get(end_id)
            if end_ap and not _airport_accepts_drone(
                end_ap.get("runway", "L"), drone_runway
            ):
                print(
                    f"⚠️  [solve_mission_with_allocation] D{did} DISABLED: end airport "
                    f"{end_id} runway {end_ap.get('runway')} cannot accept drone "
                    f"(needs {drone_runway})",
                    flush=True,
                )
                sequences[did] = ""
                routes_detail[did] = {
                    "route": [],
                    "sequence": "",
                    "points": 0,
                    "distance": 0.0,
                    "fuel_budget": fuel_budget,
                    "error": (
                        f"runway mismatch: drone needs {drone_runway}, "
                        f"end airport {end_id} is {end_ap.get('runway')}"
                    ),
                }
                continue

        # Debug logging
        print(f"🔍 [Run Planner] D{did} config: {cfg}", flush=True)
        print(
            f"🔍 [Run Planner] D{did} start_id={start_id}, raw_end_id={raw_end_id}, flexible={flexible_endpoint}, end_id={end_id}, is_synthetic={is_synthetic_start}, is_checkpoint={is_checkpoint_start}",  # noqa: E501
            flush=True,
        )

        # Get this drone's assigned targets
        assigned_target_ids = set(allocations.get(did, []))

        if not assigned_target_ids:
            # No targets assigned: trivial route from start to end airport
            # For flexible endpoint with no targets, return to NEAREST REAL airport
            # (not the synthetic start or checkpoint - that would be a zero-length route)
            if flexible_endpoint:
                # Find nearest real airport for return (excludes tankers, synthetic
                # starts, checkpoints — is_real_airport is the single rule).
                real_airports = [a for a in airports if is_real_airport(a)]
                if real_airports and start_id:
                    # Get start position
                    start_pos = None
                    for wp in dist_data.get("waypoints", []):
                        if wp["id"] == start_id:
                            start_pos = (wp["x"], wp["y"])
                            break
                    if start_pos:
                        # Find nearest real airport
                        nearest_airport = min(
                            real_airports,
                            key=lambda a: math.sqrt(
                                (a["x"] - start_pos[0]) ** 2
                                + (a["y"] - start_pos[1]) ** 2
                            ),
                        )
                        effective_end = nearest_airport["id"]
                        print(
                            f"   🔄 D{did}: No targets, flexible endpoint → returning to nearest airport '{effective_end}'",  # noqa: E501
                            flush=True,
                        )
                    else:
                        effective_end = real_airports[0]["id"]
                        print(
                            f"   🔄 D{did}: No targets, using first real airport '{effective_end}' as endpoint",
                            flush=True,
                        )
                else:
                    effective_end = start_id  # Fallback
            else:
                effective_end = end_id
            no_target_route_ids = (
                [start_id, effective_end] if start_id and effective_end else []
            )
            seq = ",".join(no_target_route_ids)

            # Still need to generate trajectory for airport-to-airport route!
            trajectory: List[List[float]] = []
            waypoint_indices: Dict[int, int] = {}
            route_distance = 0.0
            if no_target_route_ids and len(no_target_route_ids) >= 2:
                trajectory_planner = ISRTrajectoryPlanner(sams)
                # Build waypoint positions from dist_data AND airports (for checkpoints)
                waypoint_positions: Dict[str, List[float]] = {
                    wp["id"]: [wp["x"], wp["y"]]
                    for wp in dist_data.get("waypoints", [])
                }
                # Also add airports (including checkpoints) that might not be in waypoints
                for a in airports:
                    aid = str(a.get("id", ""))
                    if aid and aid not in waypoint_positions:
                        waypoint_positions[aid] = [float(a["x"]), float(a["y"])]
                        print(
                            f"   📍 Added checkpoint/airport position: {aid} at ({a['x']:.1f}, {a['y']:.1f})",
                            flush=True,
                        )
                trajectory, waypoint_indices = cast(
                    Tuple[List[List[float]], Dict[int, int]],
                    trajectory_planner.generate_trajectory(
                        no_target_route_ids,
                        waypoint_positions,
                        drone_id=did,
                        return_waypoint_indices=True,
                    ),
                )

                # Calculate distance - prefer trajectory distance over matrix when path is blocked
                orig_labels = dist_data.get("labels", [])
                orig_matrix = dist_data.get("matrix", [])

                for i in range(len(no_target_route_ids) - 1):
                    from_id = no_target_route_ids[i]
                    to_id = no_target_route_ids[i + 1]
                    segment_dist = None

                    # Try to get distance from matrix
                    if from_id in orig_labels and to_id in orig_labels:
                        from_idx = orig_labels.index(from_id)
                        to_idx = orig_labels.index(to_id)
                        segment_dist = orig_matrix[from_idx][to_idx]

                    # If matrix says blocked (99999) or not in matrix, calculate from trajectory
                    if segment_dist is None or segment_dist >= 99999:
                        if trajectory and len(trajectory) >= 2:
                            # Calculate actual distance from trajectory points
                            traj_dist = polyline_km(trajectory)
                            route_distance = (
                                traj_dist  # Use trajectory distance for entire route
                            )
                            print(
                                f"   🔄 D{did}: Using trajectory distance ({traj_dist:.1f}) for {from_id}→{to_id} (matrix was blocked)",  # noqa: E501
                                flush=True,
                            )
                        else:
                            # Per policy: NEVER fall back to Euclidean. If no
                            # trajectory exists for a "blocked" segment, the
                            # matrix value (which we computed via SAM-aware
                            # boundary navigation) is the truthful answer —
                            # use that instead of Euclidean.
                            if segment_dist is None:
                                raise RuntimeError(
                                    f"Cannot compute SAM-aware distance for {from_id}->{to_id}"
                                )
                            print(
                                f"   ⚠️ D{did}: Using matrix SAM-aware value ({segment_dist:.2f}) for {from_id}→{to_id} (no trajectory; Euclidean fallback disabled by policy)",  # noqa: E501
                                flush=True,
                            )
                            route_distance += float(segment_dist)
                    else:
                        route_distance += float(segment_dist)

            sequences[did] = seq
            routes_detail[did] = {
                "route": no_target_route_ids,
                "sequence": seq,
                "points": 0,
                "distance": route_distance,
                "fuel_budget": fuel_budget,
                "trajectory": trajectory,
                "waypoint_indices": waypoint_indices,
            }
            continue

        # Filter targets to only those assigned to this drone
        # CRITICAL: Also filter out excluded targets (inside SAM polygons)
        candidate_targets = [
            t
            for t in targets
            if str(t["id"]) in assigned_target_ids
            and str(t["id"]) not in excluded_targets
        ]

        # DEBUG: Check if any excluded targets were in the assignment
        excluded_in_assignment = assigned_target_ids & excluded_targets
        if excluded_in_assignment:
            print(
                f"   🚫 [DEBUG] D{did}: Filtering out EXCLUDED targets from assignment: {sorted(excluded_in_assignment)}",  # noqa: E501
                flush=True,
            )

        _opp_options = _refuel_options(cfg)
        # Default max_refuels: prefer an explicit cap; else use the COUNT of stops
        # the user configured (legacy refuel_stops length, which may repeat the same
        # tanker for multiple sorties) — NOT the de-duplicated options count. A
        # generous floor lets the solver do enough sorties to cover reachable
        # targets; it self-limits (a refuel with no target gain is never taken).
        _cfg_max = cfg.get("max_refuels")
        if _cfg_max is None:
            _legacy = cfg.get("refuel_stops")
            _legacy_n = (
                len([s for s in _legacy if s and str(s) not in ("-", "None")])
                if isinstance(_legacy, list)
                else 0
            )
            _opp_max = max(_legacy_n, len(_opp_options), 4)
        else:
            _opp_max = int(_cfg_max or 0)
        # WHOLE-MISSION refuel cap: max_refuels counts refuels across the ENTIRE
        # mission (start → final landing), NOT per replan. After cuts the drone has
        # already refueled `refuels_used` times, so a replan may only use the REMAINING
        # budget. Without this each segment reset the cap to the full max.
        _refuels_used = int(cfg.get("refuels_used", 0) or 0)
        if _refuels_used > 0:
            _opp_max = max(0, _opp_max - _refuels_used)
            print(
                f"   ⛽ D{did} whole-mission cap: {_refuels_used} refuels used → "
                f"{_opp_max} remaining this replan",
                flush=True,
            )
        _use_opportunistic = (
            bool(_opp_options)
            and _opp_max > 0
            and bool(
                cfg.get("opportunistic_refuel", True)  # default ON when options present
            )
        )

        # CRITICAL: Limit targets to prevent exponential solver blowup. For
        # opportunistic refuel, do NOT cap the physical drone's whole mission here:
        # each fuel leg applies its own Held-Karp cap after choosing the targets most
        # relevant to that start/end pair. A global cap hides later reachable targets
        # (for example T13/T15 near A4) before the refuel planner can value A4.
        MAX_TARGETS_PER_SOLVE = 12
        if len(candidate_targets) > MAX_TARGETS_PER_SOLVE and not _use_opportunistic:
            # Sort by priority (highest first) and take top N
            candidate_targets = sorted(
                candidate_targets, key=lambda t: int(t.get("priority", 5)), reverse=True
            )[:MAX_TARGETS_PER_SOLVE]
            print(
                f"  ⚠️ Drone {did}: Limited to {MAX_TARGETS_PER_SOLVE} highest-priority targets (had {len(assigned_target_ids)})",  # noqa: E501
                flush=True,
            )
        elif len(candidate_targets) > MAX_TARGETS_PER_SOLVE and _use_opportunistic:
            _leg_cap = int(cfg.get("max_targets_per_leg", 8) or 8)
            print(
                f"  ⛽ Drone {did}: Keeping {len(candidate_targets)} targets visible "
                f"for refuel planning; each fuel leg is capped at {_leg_cap}",
                flush=True,
            )

        # Debug: Print drone solve info
        target_ids = [t["id"] for t in candidate_targets]
        print(
            f"🚁 Drone {did}: Solving with {len(candidate_targets)} targets: {target_ids}",
            flush=True,
        )

        # Build a FILTERED distance matrix containing only airports + this drone's targets
        # This is critical - the solver uses matrix_labels to determine which nodes to visit
        airport_ids = [a["id"] for a in airports]
        desired_ids = airport_ids + target_ids

        # Create index mapping from original labels
        orig_labels = dist_data["labels"]
        orig_matrix = dist_data["matrix"]

        # Build filtered matrix - ONLY include IDs that are actually in orig_labels
        # This ensures labels and matrix dimensions match
        # CRITICAL: Always include start_id and end_id
        filtered_indices = []
        filtered_ids = []  # Track which IDs are actually included
        for fid in desired_ids:
            if fid in orig_labels:
                filtered_indices.append(orig_labels.index(fid))
                filtered_ids.append(fid)

        # Ensure start and end airports are ALWAYS in the filtered matrix
        # (skip end_id if None, which happens for flexible endpoints)
        for required_id in [start_id, end_id]:
            if (
                required_id
                and required_id not in filtered_ids
                and required_id in orig_labels
            ):
                filtered_indices.append(orig_labels.index(required_id))
                filtered_ids.append(required_id)
                print(
                    f"   ⚠️ Added missing required airport {required_id} to filtered matrix",
                    flush=True,
                )

        n = len(filtered_indices)
        filtered_matrix = [[0.0] * n for _ in range(n)]
        for i, orig_i in enumerate(filtered_indices):
            for j, orig_j in enumerate(filtered_indices):
                filtered_matrix[i][j] = orig_matrix[orig_i][orig_j]

        filtered_dist_data = {
            "matrix": filtered_matrix,
            "labels": filtered_ids,  # Use the filtered list, not desired_ids
            "waypoints": [
                wp for wp in dist_data.get("waypoints", []) if wp["id"] in filtered_ids
            ],
            "excluded_targets": (
                list(excluded_targets) if excluded_targets else []
            ),  # Propagate excluded targets
        }

        # Count actual airports and targets in filtered list
        actual_airport_count = sum(1 for fid in filtered_ids if fid in airport_ids)
        actual_target_count = len(filtered_ids) - actual_airport_count
        print(
            f"   📊 Filtered matrix: {len(filtered_ids)} nodes ({actual_airport_count} airports + {actual_target_count} targets)",  # noqa: E501
            flush=True,
        )

        # Build airports list for solver - start with real airports
        solver_airports = list(airports)  # Copy to avoid mutating original
        # CRITICAL: Real airports are those that are NOT synthetic/checkpoint (no is_synthetic/is_checkpoint flag AND not ending in _START or starting with C)  # noqa: E501
        # Synthetic starts and checkpoints should NEVER be valid endpoints.
        # Also enforce runway compatibility — this list flows to valid_end_airports
        # so a drone is never offered an endpoint at an airport too short for it.
        real_airport_ids = [
            a["id"]
            for a in airports
            if is_real_airport(a)
            and _airport_accepts_drone(a.get("runway", "L"), drone_runway)
        ]

        # CRITICAL FIX: Add ALL synthetic starts and checkpoints that are in the filtered matrix
        # This ensures every node in matrix_labels is classified as either airport or target
        # Without this, the solver guard will reject unknown labels like D1_START, D2_START, C1-1, C1-2
        waypoint_positions_for_synthetic = {
            wp["id"]: wp for wp in dist_data.get("waypoints", [])
        }
        for fid in filtered_ids:
            # Check for synthetic starts (D1_START, D2_START, etc.)
            if fid.endswith("_START") and fid not in real_airport_ids:
                # This is a synthetic start - add it as an airport
                if fid in waypoint_positions_for_synthetic:
                    wp = waypoint_positions_for_synthetic[fid]
                    synthetic_airport = {
                        "id": fid,
                        "x": wp["x"],
                        "y": wp["y"],
                        "is_synthetic": True,  # Mark so we can filter for endpoints
                    }
                    solver_airports.append(synthetic_airport)
                    print(
                        f"   ✅ Added synthetic start '{fid}' to airports: ({wp['x']:.1f}, {wp['y']:.1f})",
                        flush=True,
                    )
                else:
                    print(
                        f"   ⚠️ WARNING: Synthetic start '{fid}' not found in waypoints!",
                        flush=True,
                    )
            # Check for checkpoints (C1-1, C1-2, C2-1, etc.)
            elif re.match(r"^C\d+-\d+$", fid) and fid not in [
                a["id"] for a in solver_airports
            ]:
                # This is a checkpoint - add it as an airport
                if fid in waypoint_positions_for_synthetic:
                    wp = waypoint_positions_for_synthetic[fid]
                    checkpoint_airport = {
                        "id": fid,
                        "x": wp["x"],
                        "y": wp["y"],
                        "is_checkpoint": True,  # Mark so we can filter for endpoints
                    }
                    solver_airports.append(checkpoint_airport)
                    print(
                        f"   ✅ Added checkpoint '{fid}' to airports: ({wp['x']:.1f}, {wp['y']:.1f})",
                        flush=True,
                    )
                else:
                    print(
                        f"   ⚠️ WARNING: Checkpoint '{fid}' not found in waypoints!",
                        flush=True,
                    )

        print(
            f"   🔍 DEBUG: solver_airports: {[a['id'] for a in solver_airports]}",
            flush=True,
        )

        # Build environment for solver with FILTERED matrix
        env_for_solver = solver.build_environment_for_solver(
            airports=solver_airports,  # ← Include synthetic starts as airports
            targets=candidate_targets,
            sams=sams,
            distance_matrix_data=filtered_dist_data,
        )
        env_for_solver["start_airport"] = start_id

        print(
            f"   🔍 DEBUG: After build_environment_for_solver, env_for_solver has end_airport: {env_for_solver.get('end_airport', 'NOT SET')}",  # noqa: E501
            flush=True,
        )
        print(f"   🔍 DEBUG: flexible_endpoint={flexible_endpoint}", flush=True)

        # If flexible endpoint, use best_end mode to let solver choose optimal end
        if flexible_endpoint:
            print(
                "   🔄 Flexible endpoint: solver will choose optimal end airport",
                flush=True,
            )
            env_for_solver["mode"] = "best_end"

            # CRITICAL: Remove end_airport from env if it was set by build_environment_for_solver
            # This prevents the solver from using a synthetic start as the endpoint
            if "end_airport" in env_for_solver:
                print(
                    f"   🔍 DEBUG: Removing end_airport '{env_for_solver['end_airport']}' from env_for_solver",
                    flush=True,
                )
                del env_for_solver["end_airport"]

            # CRITICAL: Filter out synthetic starts from valid end airports
            # Synthetic starts (e.g., D1_START) are used for checkpoint replanning
            # but should NOT be valid endpoints - only real airports should be endpoints
            # Use real_airport_ids we defined earlier (before adding synthetic starts)
            if real_airport_ids:
                env_for_solver["valid_end_airports"] = real_airport_ids
                print(
                    f"   ✈️ Valid end airports (excluding synthetic): {env_for_solver['valid_end_airports']}",
                    flush=True,
                )
        else:
            env_for_solver["end_airport"] = end_id
            env_for_solver["mode"] = "return" if end_id == start_id else "end"

        # --- Solve (with optional refueling) ---
        # Refuel modes: "" = no refuel, "-" = any (solver picks), "A1" = specific airport
        sol: Dict[str, Any]
        if _use_opportunistic:
            # OPPORTUNISTIC: solver decides whether/when/where to refuel to maximize
            # points. Replaces the prescriptive forced-leg path. (Gated on
            # refuel_options/max_refuels; falls through to legacy paths otherwise.)
            _tanker_ids = {
                str(a.get("id"))
                for a in env_for_solver.get("airports", [])
                if a.get("is_tanker") or str(a.get("id", "")).startswith("TK")
            }
            print(
                f"   ⛽ D{did} OPPORTUNISTIC refuel: options={_opp_options} "
                f"max_refuels={_opp_max}",
                flush=True,
            )
            _route, _vis, _dist, _ridx, _legd = _solve_opportunistic_drone(
                env_for_solver,
                fuel_budget,
                start_id,
                candidate_targets,
                did,
                cfg,
                _opp_options,
                _opp_max,
                _tanker_ids,
                real_airport_ids if flexible_endpoint else [end_id or start_id],
                constraints=drone_constraints,
            )
            sol = {
                "route": _route,
                "visited_targets": sorted(_vis),
                "distance": _dist,
                "end_airport": _route[-1] if _route else start_id,
            }
            if _ridx:
                sol["refuel_indices"] = _ridx
                sol["leg_distances"] = _legd
                sol["refuel_stops"] = [_route[i] for i in _ridx if 0 <= i < len(_route)]
        elif refuel_airport and refuel_airport != "":
            print(
                f"   🔧 REFUEL ACTIVE: D{did} refuel_airport='{refuel_airport}', candidate_targets={[t['id'] for t in candidate_targets]}",  # noqa: E501
                flush=True,
            )
            if refuel_airport == "-":
                # "Any" mode: greedy scan to find best airport, then exhaustive on the winner.
                # Only consider real airports compatible with this drone's runway.
                real_ap_ids = [
                    a["id"]
                    for a in airports
                    if is_real_airport(a)
                    and _airport_accepts_drone(a.get("runway", "L"), drone_runway)
                ]
                print(
                    f"   ⛽ D{did} ANY refuel: scanning {len(real_ap_ids)} runway-compatible airports (greedy)",
                    flush=True,
                )

                # Phase 1: Greedy solve per airport to find the best one
                best_greedy_sol = None
                best_greedy_ap = None
                for rap_id in real_ap_ids:
                    r1, v1, d1 = _solve_one_leg(
                        env_for_solver,
                        leg1_budget,  # §8.2: leg 1 = fuel at the leg-1 start
                        start_id,
                        rap_id,
                        candidate_targets,
                        did,
                        "L1",
                        drone_cfg=cfg,
                    )
                    remaining = [t for t in candidate_targets if str(t["id"]) not in v1]
                    r2, v2, d2 = _solve_one_leg(
                        env_for_solver,
                        leg2_budget,  # §8.2: leg 2 starts with a FULL TANK (refueled)
                        rap_id,
                        end_id,
                        remaining,
                        did,
                        "L2",
                        flexible_endpoint,
                        drone_cfg=cfg,
                    )
                    pts = len(v1) + len(v2)
                    total_dist = d1 + d2 + REFUEL_STOP_COST_KM
                    # §5: only a candidate whose BOTH legs fit their own budgets is
                    # eligible (leg 1 = leg1_budget, leg 2 = full tank leg2_budget).
                    # Never store an infeasible leg as the "winner".
                    leg_feasible = d1 <= leg1_budget + 1e-6 and d2 <= leg2_budget + 1e-6
                    if leg_feasible and (
                        best_greedy_sol is None
                        or pts > len(best_greedy_sol.get("visited_targets", []))
                        or (
                            pts == len(best_greedy_sol.get("visited_targets", []))
                            and total_dist < best_greedy_sol["distance"]
                        )
                    ):
                        combined = list(r1)
                        if r2 and combined and combined[-1] == r2[0]:
                            combined.extend(r2[1:])
                        else:
                            combined.extend(r2)
                        best_greedy_sol = {
                            "route": combined,
                            "visited_targets": list(v1 | v2),
                            "distance": total_dist,
                            "leg1_distance": d1,
                            "leg2_distance": d2,
                            "refuel_airport": rap_id,
                            "refuel_idx": len(r1)
                            - 1,  # Index of refuel point in combined route
                        }
                        best_greedy_ap = rap_id

                if best_greedy_sol is None:
                    # §5: no feasible refuel candidate at ANY airport → degrade to the
                    # single-leg fly-to-end floor. Never hard-fail / infeasible route.
                    print(
                        f"   ⛽ D{did} No feasible refuel at any airport — single-leg "
                        f"fly-to-end floor",
                        flush=True,
                    )
                    floor_end = end_id or start_id
                    fr, fv, fd = _solve_one_leg(
                        env_for_solver,
                        leg1_budget,
                        start_id,
                        floor_end,
                        candidate_targets,
                        did,
                        "Floor",
                        flexible_endpoint,
                        drone_cfg=cfg,
                    )
                    sol = {
                        "route": fr,
                        "visited_targets": list(fv),
                        "distance": fd,
                    }
                else:
                    if best_greedy_ap is None:
                        raise RuntimeError("ANY refuel winner missing airport id")

                    # Phase 2: Exhaustive split on the winning airport
                    sol = _solve_two_leg_refuel(
                        env_for_solver=env_for_solver,
                        fuel_budget=fuel_budget,
                        start_id=start_id,
                        end_id=end_id,
                        refuel_airport_id=best_greedy_ap,
                        candidate_targets=candidate_targets,
                        flexible_endpoint=flexible_endpoint,
                        did=did,
                        drone_cfg=cfg,
                        leg2_budget=leg2_budget,  # §11.10 full tank for post-refuel leg
                        constraints=drone_constraints,
                    )
                    # Keep the better result
                    if len(sol.get("visited_targets", [])) < len(
                        best_greedy_sol.get("visited_targets", [])
                    ) or (
                        len(sol.get("visited_targets", []))
                        == len(best_greedy_sol.get("visited_targets", []))
                        and sol["distance"] >= best_greedy_sol["distance"]
                    ):
                        sol = best_greedy_sol
                print(
                    f"   ⛽ D{did} ANY refuel winner: refuel at {sol.get('refuel_airport')}, {len(sol.get('visited_targets', []))} targets, {sol.get('distance', 0):.1f} dist",  # noqa: E501
                    flush=True,
                )
            else:
                # Specific airport mode — pass explicit leg split if provided
                drone_leg_split = (leg_split or {}).get(did)
                sol = _solve_two_leg_refuel(
                    env_for_solver=env_for_solver,
                    fuel_budget=fuel_budget,
                    start_id=start_id,
                    end_id=end_id,
                    refuel_airport_id=refuel_airport,
                    candidate_targets=candidate_targets,
                    flexible_endpoint=flexible_endpoint,
                    did=did,
                    leg1_targets=(
                        drone_leg_split.get("leg1") if drone_leg_split else None
                    ),
                    leg2_targets=(
                        drone_leg_split.get("leg2") if drone_leg_split else None
                    ),
                    drone_cfg=cfg,
                    leg2_budget=leg2_budget,  # §11.10 full tank for post-refuel leg
                    constraints=drone_constraints,
                )
            if flexible_endpoint:
                solved_route = _string_list(sol.get("route"))
                end_id = solved_route[-1] if solved_route else start_id
                print(f"   🎯 Refuel solve chose endpoint: {end_id}", flush=True)
        else:
            # --- Normal single-leg solve ---
            # Account for loiter fuel in non-refueling drones
            loiter_fuel = 0.0
            if cfg.get("loiter_enabled"):
                speed = float(cfg.get("speed", 1))
                loiter_times = cfg.get("loiter_times", {})
                for t in candidate_targets:
                    t_type = (t.get("type") or "a").lower()
                    lt = float(loiter_times.get(t_type, 0))
                    loiter_fuel += lt * speed
                if loiter_fuel > 0:
                    print(
                        f"   🕐 D{did}: loiter fuel={loiter_fuel:.1f} (speed={speed}), effective budget={fuel_budget - loiter_fuel:.1f}",  # noqa: E501
                        flush=True,
                    )

            effective_budget = fuel_budget - loiter_fuel
            if flexible_endpoint:
                # Don't set end_airport - let solver choose from valid_end_airports
                sol = solver.solve(
                    env_for_solver, effective_budget, constraints=drone_constraints
                )
                # Extract the end airport chosen by solver
                end_id = sol.get("end_airport", start_id)
                print(f"   🎯 Solver chose endpoint: {end_id}", flush=True)
                # STRANDED-BUT-CAN-GO-HOME: when NO target fits the fuel, the Held-Karp
                # solver returns just [start] and reports end_airport = start. On a replan
                # `start` is the CUT NODE (a virtual airport) — the drone must NEVER end
                # there. It has fuel to reach a real airport (e.g. C5-1→A1 = 2.7 km on 8
                # km fuel), it just can't visit more targets. Redirect the endpoint to the
                # nearest REACHABLE real end airport so the drone flies HOME instead of
                # parking mid-air (an empty [C5-1] route froze the accept: "no valid
                # routes to animate").
                _sol_route = sol.get("route") or []
                _ends_at_start = (not _sol_route) or (_sol_route[-1] == start_id)
                if _ends_at_start and (start_id not in real_airport_ids):
                    _labels = (
                        env_for_solver.get("matrix_labels")
                        or env_for_solver.get("labels")
                        or []
                    )
                    _matrix = (
                        env_for_solver.get("matrix")
                        or env_for_solver.get("distance_matrix")
                        or []
                    )
                    if _labels and _matrix and start_id in _labels:
                        _si = _labels.index(start_id)
                        _reachable = []
                        for _ap in real_airport_ids:
                            if _ap in _labels:
                                _d = float(_matrix[_si][_labels.index(_ap)])
                                if _d <= effective_budget + 1e-6:
                                    _reachable.append((_d, _ap))
                        if _reachable:
                            _reachable.sort()
                            end_id = _reachable[0][1]
                            print(
                                f"   🏠 D{did} stranded (no targets fit) — flying HOME to "
                                f"nearest reachable airport {end_id} "
                                f"({_reachable[0][0]:.1f} km ≤ {effective_budget:.1f} fuel)",
                                flush=True,
                            )
            else:
                print(
                    f"   🎯 Solving with start={start_id}, end={end_id}, mode={env_for_solver['mode']}",
                    flush=True,
                )
                # Solve orienteering for this drone
                sol = solver.solve(
                    env_for_solver, effective_budget, constraints=drone_constraints
                )

        # The solver returns BOTH 'visited_targets' (unordered target IDs) AND 'route' (optimized order)
        # CRITICAL: Use 'route' for the optimized sequence from Held-Karp, NOT visited_targets!
        visited_targets: List[str] = _string_list(sol.get("visited_targets"))

        # Use the optimized route directly from solver (includes start/end airports in optimal order)
        route_ids: List[str] = _string_list(sol.get("route"))
        if not route_ids:
            # Fallback if solver didn't return route
            fallback_end_id = end_id or start_id
            route_ids = [start_id] + visited_targets + [fallback_end_id]
        elif len(route_ids) == 1 and end_id and route_ids[-1] != end_id:
            # Solver returned only start (no targets fit), add the end airport
            route_ids = route_ids + [end_id]
            print(
                f"   📍 Added end airport {end_id} to single-waypoint route", flush=True
            )

        # Calculate total points from visited targets
        target_priorities = {
            t["id"]: int(t.get("priority", 1)) for t in candidate_targets
        }
        total_points: int = sum(
            target_priorities.get(tid, 0) for tid in visited_targets
        )
        travel_distance: float = float(sol.get("distance", 0.0))

        # Add loiter fuel for non-refueling drones (refueling drones already include it via _solve_one_leg)
        actual_loiter_fuel = 0.0
        if not (refuel_airport and refuel_airport != "") and cfg.get("loiter_enabled"):
            speed = float(cfg.get("speed", 1))
            loiter_times_cfg = cfg.get("loiter_times", {})
            for tid in visited_targets:
                # Find target type
                t_data = next(
                    (t for t in candidate_targets if str(t["id"]) == tid), None
                )
                if t_data:
                    t_type = (t_data.get("type") or "a").lower()
                    lt = float(loiter_times_cfg.get(t_type, 0))
                    actual_loiter_fuel += lt * speed
            if actual_loiter_fuel > 0:
                print(
                    f"   🕐 D{did}: actual loiter fuel for visited targets = {actual_loiter_fuel:.1f}",
                    flush=True,
                )
            travel_distance += actual_loiter_fuel

        # DEBUG: Log solver output
        print(f"🔍 [DEBUG] Drone {did} solver returned:", flush=True)
        print(f"   route_ids = {route_ids}", flush=True)
        print(
            f"   total_points = {total_points}, distance = {travel_distance:.2f}",
            flush=True,
        )

        # Validate: check if route ends at expected endpoint
        if route_ids:
            actual_end = route_ids[-1]
            expected_end = (
                end_id if end_id else start_id
            )  # flexible endpoint defaults to start
            if actual_end != expected_end:
                print(
                    f"   ⚠️ ENDPOINT MISMATCH: Route ends at '{actual_end}' but expected '{expected_end}'",
                    flush=True,
                )
            else:
                print(f"   ✅ Route correctly ends at '{actual_end}'", flush=True)

        seq = ",".join(route_ids) if route_ids else ""

        # Generate SAM-avoiding trajectory using ISRTrajectoryPlanner
        trajectory_planner = ISRTrajectoryPlanner(sams)
        # Build waypoint positions from dist_data AND airports (for checkpoints)
        allocated_waypoint_positions: Dict[str, List[float]] = {
            wp["id"]: [wp["x"], wp["y"]] for wp in dist_data.get("waypoints", [])
        }
        # Also add airports (including checkpoints) that might not be in waypoints
        for a in airports:
            aid = str(a.get("id", ""))
            if aid and aid not in allocated_waypoint_positions:
                allocated_waypoint_positions[aid] = [float(a["x"]), float(a["y"])]
        # Detection-radius taut-string: slide target waypoints to the path-closest
        # point inside their detection circle (order fixed, SAM-aware) BEFORE the
        # trajectory is generated. No-op unless this drone has a radius configured.
        relaxed_positions = _relax_positions_to_detection_radii(
            route_ids,
            _positions_as_tuples(allocated_waypoint_positions),
            candidate_targets,
            cfg,
            sams,
        )
        allocated_waypoint_positions = _positions_as_lists(relaxed_positions)
        trajectory, waypoint_indices = cast(
            Tuple[List[List[float]], Dict[int, int]],
            trajectory_planner.generate_trajectory(
                route_ids,
                allocated_waypoint_positions,
                drone_id=did,
                return_waypoint_indices=True,
            ),
        )

        # DEBUG: Log trajectory endpoints vs expected airport position
        if trajectory and len(trajectory) >= 2:
            _traj_start = trajectory[0]  # noqa: F841
            traj_end = trajectory[-1]
            # Find expected airport position
            if route_ids:
                end_id_in_route = route_ids[-1]
                expected_pos = allocated_waypoint_positions.get(end_id_in_route)
                if expected_pos:
                    end_diff = (
                        (traj_end[0] - expected_pos[0]) ** 2
                        + (traj_end[1] - expected_pos[1]) ** 2
                    ) ** 0.5
                    if end_diff > 1.0:  # More than 1 unit off
                        print(
                            f"   ⚠️ TRAJECTORY ENDPOINT MISMATCH: traj ends at ({traj_end[0]:.1f},{traj_end[1]:.1f}) but {end_id_in_route} is at ({expected_pos[0]:.1f},{expected_pos[1]:.1f}), diff={end_diff:.1f}",  # noqa: E501
                            flush=True,
                        )
                    else:
                        print(
                            f"   ✅ Trajectory correctly ends near {end_id_in_route}",
                            flush=True,
                        )

        sequences[did] = seq
        route_info = {
            "route": route_ids,
            "sequence": seq,
            "points": total_points,
            "distance": travel_distance,  # Total: travel + loiter fuel
            "fuel_budget": fuel_budget,
            "trajectory": trajectory,  # Full path with SAM avoidance
            "waypoint_indices": waypoint_indices,  # Maps route index -> trajectory index
            "loiter_fuel": actual_loiter_fuel,
            "speed": float(cfg.get("speed", 1)),
        }
        # Attach refueling metadata whenever refuel is configured
        # (even if Leg2 is empty, INSERT needs this to know it's a refueling drone)
        if refuel_airport and refuel_airport != "":
            route_info["refuel_airport"] = sol.get("refuel_airport", refuel_airport)
            route_info["leg1_distance"] = sol.get("leg1_distance", 0.0)
            route_info["leg2_distance"] = sol.get("leg2_distance", 0.0)
            route_info["refuel_idx"] = sol.get("refuel_idx", -1)
        # Opportunistic refuel emits the multi-leg arrays the UI stats/animation use
        # (leg_distances[] / refuel_indices[] / refuel_stops[]); carry them through.
        if sol.get("refuel_indices"):
            route_info["leg_distances"] = sol.get("leg_distances", [])
            route_info["refuel_indices"] = sol.get("refuel_indices", [])
            route_info["refuel_stops"] = sol.get("refuel_stops", [])
        routes_detail[did] = route_info

    dist_data, sams, excluded_targets = (
        mission_dist_data,
        mission_sams,
        mission_excluded,
    )

    # Compute wrapped polygons for visualization (same as delivery system)
    wrapped_polygons: List[Any] = []
    if sams:
        # Convert SAMs to simple format for wrapping
        sams_for_wrapping = []
        for sam in sams:
            pos = sam.get("pos") or sam.get("position")
            if pos:
                sams_for_wrapping.append(
                    {
                        "x": pos[0] if isinstance(pos, (list, tuple)) else pos,
                        "y": pos[1] if isinstance(pos, (list, tuple)) else pos,
                        "radius": float(sam.get("range", sam.get("radius", 15))),
                    }
                )
        if sams_for_wrapping:
            wrapped_polygon_arrays, _ = wrap_sams(sams_for_wrapping)
            wrapped_polygons = []
            for poly in wrapped_polygon_arrays:
                # If it's a NumPy array, convert to list; if it's already a list, keep it
                if hasattr(poly, "tolist"):
                    wrapped_polygons.append(poly.tolist())
                else:
                    wrapped_polygons.append(poly)

    # Build initial solution. With multi-stop refuel the routes are still keyed by
    # VIRTUAL drone id here — each leg is a plain single-leg route, so post-optimize
    # treats them as ordinary drones (it can even rebalance targets across legs).
    # We merge to physical AFTER post-optimize.
    solution: Dict[str, Any] = {
        "sequences": sequences,
        "routes": routes_detail,
        "wrapped_polygons": wrapped_polygons,
        "allocations": allocations,  # initial allocator view
        "distance_matrix": dist_data,  # Include distance matrix for downstream use
    }

    # Step 4: Post-optimize to include unvisited targets (operates on virtual drones
    # when multi-stop refuel is active — they look like ordinary drones to it).
    if post_optimize:
        solution = post_optimize_solution(solution, env, drone_configs, dist_data)

    # MULTI-STOP REFUEL: now collapse the per-leg virtual-drone routes back to
    # physical drones, emitting route + leg_distances[] + refuel_indices[] +
    # fuel_budget. After this, all downstream code sees physical integer-keyed
    # routes. (No-op when virtual drones weren't used.)
    if _use_virtual_drones:
        virtual_routes = cast(Dict[str, Dict[str, Any]], solution.get("routes", {}))
        merged_routes = merge_virtual_to_physical(
            virtual_routes, _physical_drone_configs
        )
        solution["routes"] = merged_routes
        solution["sequences"] = {
            did: ",".join(str(w) for w in rd.get("route", []))
            for did, rd in merged_routes.items()
        }
        drone_configs = _physical_drone_configs  # downstream uses physical configs
        print(
            f"⛽ Multi-stop refuel: merged {len(merged_routes)} physical drone routes",
            flush=True,
        )

    # Compute route-derived allocations for comparison (what's actually in trajectories)
    route_allocations: Dict[str, List[str]] = {}
    solved_routes = cast(Dict[str, Dict[str, Any]], solution.get("routes", {}))
    for did, route_data in solved_routes.items():
        route = route_data.get("route", [])
        route_allocations[did] = [
            node_id
            for node_id in route
            if isinstance(node_id, str) and node_id.startswith("T")
        ]

    # Debug print to compare original allocator vs route-derived
    print("\n" + "=" * 60, flush=True)
    print("🎯 ROUTE-DERIVED TARGETS (what solver actually included)", flush=True)
    print("=" * 60, flush=True)
    total_in_routes = 0
    for did in sorted(route_allocations.keys(), key=lambda x: int(x)):
        tids = route_allocations[did]
        total_in_routes += len(tids)
        print(f"  Drone {did}: {tids}", flush=True)
    print(f"  Total targets in routes: {total_in_routes}", flush=True)
    print("=" * 60 + "\n", flush=True)

    # Keep the ORIGINAL allocator assignments (not route-derived)
    # UI will show what the allocator assigned, even if solver dropped some
    # Also include route_allocations for reference
    solution["route_allocations"] = route_allocations

    # Unproject trajectories back to lat/lng before returning
    _unproject_solution_trajectories(solution, ref_lat, ref_lng)

    return solution
