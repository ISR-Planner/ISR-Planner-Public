"""Fixed-order optimizer pipeline."""

import copy
from typing import Any, Dict, Optional

from server.solver.optimizers.common import collect_route_stats
from server.solver.optimizers.crossing_removal import crossing_removal_optimize
from server.solver.optimizers.insert_missed import insert_missed_optimize
from server.solver.optimizers.or_opt import or_opt_optimize
from server.solver.optimizers.trajectory_swap import trajectory_swap_optimize
from server.solver.refuel_expansion import expand_solution_with_refuel_sorties
from server.solver.virtual_drones import (
    merge_virtual_legs_n,
    split_solution_to_virtual_legs,
)

# ============================================================================
# Fixed-order optimizer pipeline (Insert -> Swap -> No-Cross)
# ============================================================================


def apply_optimizer_pipeline(
    solution: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Dict[str, Any]],
    distance_matrix: Optional[Dict[str, Any]] = None,
    debug: bool = False,
    max_passes: int = 10,
    constraints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run the post-solve optimization pipeline iteratively until convergence:

        Loop until no improvement or max_passes reached:
            1. Insert  - pack unvisited targets into routes with fuel slack
            2. Swap    - re-balance targets between drones
            3. No-Cross - eliminate self-crossings via 2-opt

    The pipeline iterates because changes in one step may enable opportunities
    in previous steps (e.g., NO-CROSS shortens routes, enabling more INSERTs).
    Convergence is detected by tracking a cost function (more targets = better,
    less fuel = better). Stops when cost doesn't improve.

    Args:
        solution: Current solution with drone routes (output of the solver)
        env: Environment dict with targets/airports/SAMs (already in km if
             called from an endpoint that pre-projected lat/lng - pipeline does
             not project itself)
        drone_configs: Drone configurations
        distance_matrix: Pre-calculated SAM-aware distance matrix. Required -
                         several sub-steps refuse to run without it.
        debug: Verbose logging on sub-steps
        max_passes: Maximum number of full pipeline iterations (default 10)

    Returns:
        {
            "success": bool,
            "routes": {...},                  # final optimized routes
            "sequences": {did: "A1,T1,..."},
            "pipeline_summary": {
                "targets_before": int,
                "targets_after": int,
                "fuel_before": float,
                "fuel_after": float,
                "insertions": int,            # total targets added by Insert (all passes)
                "swaps": int,                 # total swaps applied (all passes)
                "swap_iterations": int,       # total Swap internal iterations
                "crossings_fixed": int,       # total crossings removed (all passes)
                "pipeline_passes": int,       # number of full INSERT->SWAP->NO-CROSS passes
                "failed_step": str | None,    # name of the step that errored, if any
            },
            "error": str (only on failure)
        }
    """
    targets_before, fuel_before = collect_route_stats(solution)

    summary: Dict[str, Any] = {
        "targets_before": targets_before,
        "targets_after": targets_before,
        "fuel_before": round(fuel_before, 2),
        "fuel_after": round(fuel_before, 2),
        "refuel_expansions": 0,
        "insertions": 0,
        "swaps": 0,
        "swap_iterations": 0,
        "or_opt_moves": 0,
        "crossings_fixed": 0,
        "pipeline_passes": 0,
        "failed_step": None,
    }

    # -------------------------------------------------------------------------
    # REFUEL EXPANSION: Before splitting physical routes into virtual fuel legs,
    # spend unused refuel capacity on missed targets that require a brand-new
    # base -> target -> base leg. Insert/Swap/No-Cross cannot create those fuel
    # legs once the route has already been split.
    # -------------------------------------------------------------------------
    refuel_expansion_result = expand_solution_with_refuel_sorties(
        solution=solution,
        env=env,
        drone_configs=drone_configs,
        distance_matrix=distance_matrix,
    )
    refuel_expansions = len(refuel_expansion_result.get("insertions", []))
    if refuel_expansions:
        solution = {
            "routes": refuel_expansion_result.get("routes", solution.get("routes", {})),
            "distance_matrix": distance_matrix,
        }
        summary["refuel_expansions"] = refuel_expansions
        print(
            f"   REFUEL-EXPAND [Pipeline] Added {refuel_expansions} target(s) "
            f"via spare refuel leg(s): "
            f"{[ins.get('target') for ins in refuel_expansion_result.get('insertions', [])]}"
        )

    # -------------------------------------------------------------------------
    # VIRTUAL DRONE SPLIT: Treat each leg of a refueling drone as a separate
    # virtual drone (D1 -> D1_L1, D1_L2) so optimizers work without special-case
    # per-leg logic.
    # -------------------------------------------------------------------------
    # N-leg split: each refuel leg becomes a plain virtual drone so the optimizers
    # move targets across legs with no refuel-awareness (a leg IS a drone). Replaces
    # the old 2-leg split_refueling_drones. (virtual_drones.split_solution_to_virtual_legs)
    _physical_drone_configs = drone_configs  # keep for the merge-back
    split_solution, split_configs, leg_mapping = split_solution_to_virtual_legs(
        solution, drone_configs, env
    )
    # Virtual configs (per-leg start/end/fuel/access) drive the optimizers below.
    drone_configs = split_configs
    if any(_vl != _pid for _vl, _pid in leg_mapping.items()):
        print(
            f"   SPLIT [Pipeline] Split into virtual legs: {list(leg_mapping.keys())}"
        )

    current = split_solution

    # Cost function: maximize targets, minimize fuel
    # Higher is better: (targets * 1000) - fuel
    def calculate_cost(sol: Dict[str, Any]) -> float:
        targets, fuel = collect_route_stats(sol)
        return (targets * 1000) - fuel

    best_cost = calculate_cost(current)
    best_solution = copy.deepcopy(current)

    print(
        f"\nREFRESH [Pipeline] Starting iterative optimization (max {max_passes} passes)"
    )
    print(
        f"   Initial: {targets_before} targets, {fuel_before:.1f} fuel, cost={best_cost:.1f}"
    )

    for pass_num in range(max_passes):
        pass_insertions = 0
        pass_swaps = 0
        pass_swap_iters = 0
        pass_crossings = 0

        print(f"\n   POINT Pass {pass_num + 1}/{max_passes}")

        # --- 1. Insert ------------------------------------------------------
        # Debug: show what targets INSERT sees as available
        _routes = current.get("routes", {})
        _visited = set()
        for _rd in _routes.values():
            for wp in _rd.get("route", []):
                if str(wp).startswith("T") and not str(wp).startswith("TK"):
                    _visited.add(str(wp))
        _all_targets = {str(t["id"]) for t in env.get("targets", [])}
        _unvisited = _all_targets - _visited
        _excluded = set(
            str(t) for t in (distance_matrix or {}).get("excluded_targets", [])
        )
        _insertable = _unvisited - _excluded
        print(
            f"      INSERT input: {len(_visited)} visited, {len(_unvisited)} unvisited, {len(_excluded)} excluded -> {len(_insertable)} insertable"
        )
        if _insertable:
            print(f"         Insertable targets: {sorted(_insertable)}")

        insert_result = insert_missed_optimize(
            solution=current,
            env=env,
            drone_configs=split_configs,
            distance_matrix=distance_matrix,
            constraints=constraints,
        )
        if not insert_result.get("success"):
            summary["failed_step"] = "insert"
            summary["pipeline_passes"] = pass_num + 1
            return {
                "success": False,
                "error": f"insert failed: {insert_result.get('error')}",
                "routes": best_solution.get("routes", {}),
                "sequences": {
                    d: r.get("sequence", ",".join(r.get("route", [])))
                    for d, r in best_solution.get("routes", {}).items()
                },
                "pipeline_summary": summary,
            }
        pass_insertions = len(insert_result.get("insertions", []))
        summary["insertions"] += pass_insertions
        current = {
            "routes": insert_result.get("routes", {}),
            "distance_matrix": distance_matrix,
        }
        if pass_insertions > 0:
            print(
                f"      INSERT: +{pass_insertions} targets -> {[ins.get('target') for ins in insert_result.get('insertions', [])]}"
            )
        else:
            print("      INSERT: no insertions (fuel/access constraints?)")

        # --- 2. Swap --------------------------------------------------------
        swap_result = trajectory_swap_optimize(
            solution=current,
            env=env,
            drone_configs=split_configs,
            distance_matrix=distance_matrix,
            auto_iterate=True,
            auto_regen=True,
            debug=debug,
            constraints=constraints,
        )
        if "routes" not in swap_result:
            summary["failed_step"] = "swap"
            summary["pipeline_passes"] = pass_num + 1
            return {
                "success": False,
                "error": f"swap failed: {swap_result}",
                "routes": best_solution.get("routes", {}),
                "sequences": {
                    d: r.get("sequence", ",".join(r.get("route", [])))
                    for d, r in best_solution.get("routes", {}).items()
                },
                "pipeline_summary": summary,
            }
        pass_swaps = len(swap_result.get("swaps_made", []))
        pass_swap_iters = int(swap_result.get("iterations", 0))
        summary["swaps"] += pass_swaps
        summary["swap_iterations"] += pass_swap_iters
        current = {
            "routes": swap_result.get("routes", {}),
            "distance_matrix": distance_matrix,
        }
        if pass_swaps > 0:
            print(f"      SWAP: {pass_swaps} swaps in {pass_swap_iters} iterations")

        # --- 3. Or-opt ------------------------------------------------------
        # Relocate contiguous runs (1-3 targets) Swap's single-target move can't
        # reach. Runs AFTER Swap (single moves first, then multi-target runs),
        # BEFORE No-Cross (crossings cleaned last). Operates on the same virtual
        # legs as the other stages, so a run can cross a refuel boundary.
        oropt_result = or_opt_optimize(
            solution=current,
            env=env,
            drone_configs=split_configs,
            distance_matrix=distance_matrix,
            constraints=constraints,
        )
        pass_oropt = 0
        if oropt_result.get("success") and "routes" in oropt_result:
            pass_oropt = len(oropt_result.get("or_opt_moves", []))
            summary["or_opt_moves"] = summary.get("or_opt_moves", 0) + pass_oropt
            current = {
                "routes": oropt_result.get("routes", {}),
                "distance_matrix": distance_matrix,
            }
            if pass_oropt > 0:
                print(f"      OR-OPT: {pass_oropt} run move(s)")
        else:
            # Non-fatal: Or-opt is an enhancement; a failure leaves Swap's result
            # intact and the pass continues to No-Cross (unlike Insert/Swap, which
            # abort the pipeline - those are load-bearing).
            print(f"      OR-OPT: skipped ({oropt_result.get('error', 'no result')})")

        # --- 4. No-Cross ----------------------------------------------------
        nocross_result = crossing_removal_optimize(
            solution=current,
            env=env,
            drone_configs=split_configs,
            distance_matrix=distance_matrix,
            constraints=constraints,
        )
        if "routes" not in nocross_result:
            summary["failed_step"] = "no_cross"
            summary["pipeline_passes"] = pass_num + 1
            return {
                "success": False,
                "error": f"no_cross failed: {nocross_result}",
                "routes": best_solution.get("routes", {}),
                "sequences": {
                    d: r.get("sequence", ",".join(r.get("route", [])))
                    for d, r in best_solution.get("routes", {}).items()
                },
                "pipeline_summary": summary,
            }
        pass_crossings = len(nocross_result.get("fixes_made", []))
        summary["crossings_fixed"] += pass_crossings
        current = {
            "routes": nocross_result.get("routes", {}),
            "distance_matrix": distance_matrix,
        }
        if pass_crossings > 0:
            print(f"      NO-CROSS: fixed {pass_crossings} crossings")

        # --- Check for improvement ------------------------------------------
        current_cost = calculate_cost(current)
        targets_now, fuel_now = collect_route_stats(current)
        print(
            f"      Result: {targets_now} targets, {fuel_now:.1f} fuel, cost={current_cost:.1f}"
        )

        # Always track the best solution seen
        if current_cost > best_cost:
            best_cost = current_cost
            best_solution = copy.deepcopy(current)
            print(f"      OK New best! cost={best_cost:.1f}")

        # Continue if ANY changes were made - SWAP/OR-OPT/NO-CROSS changes might
        # enable new INSERT opportunities in the next pass
        if (
            pass_insertions == 0
            and pass_swaps == 0
            and pass_oropt == 0
            and pass_crossings == 0
        ):
            print("      STOP  No changes made - converged")
            summary["pipeline_passes"] = pass_num + 1
            break
        # else: changes were made, continue to next pass
    else:
        # Reached max_passes
        print(f"      WARN  Reached max passes ({max_passes})")
        summary["pipeline_passes"] = max_passes

    # Always use the best solution found across all passes
    current = best_solution

    final_routes = current.get("routes", {})

    # -------------------------------------------------------------------------
    # VIRTUAL DRONE MERGE: Reassemble virtual legs (D1_L1, D1_L2) back into
    # physical refueling drones (D1) with refuel airport in the middle.
    # -------------------------------------------------------------------------
    if any(_vl != _pid for _vl, _pid in leg_mapping.items()):
        merged_solution = merge_virtual_legs_n(
            {"routes": final_routes}, _physical_drone_configs, leg_mapping
        )
        final_routes = merged_solution.get("routes", final_routes)
        print("   SPLIT [Pipeline] Merged virtual legs back into physical drones")

    # Recompute final stats from the post-pipeline routes
    targets_after, fuel_after = collect_route_stats({"routes": final_routes})
    summary["targets_after"] = targets_after
    summary["fuel_after"] = round(fuel_after, 2)

    print(
        f"\nOK [Pipeline] Complete: {summary['pipeline_passes']} passes, "
        f"{targets_before}->{targets_after} targets, "
        f"{fuel_before:.1f}->{fuel_after:.1f} fuel"
    )

    return {
        "success": True,
        "routes": final_routes,
        "sequences": {
            d: r.get("sequence", ",".join(r.get("route", [])))
            for d, r in final_routes.items()
        },
        "pipeline_summary": summary,
    }
