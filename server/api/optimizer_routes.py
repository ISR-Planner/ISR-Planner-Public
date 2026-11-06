from typing import Any, Dict, Optional, Tuple, Union

from fastapi import APIRouter, Request

from server.solver import sam_distance_matrix
from server.solver.post_optimizer import (
    crossing_removal_optimize,
    get_coverage_stats,
    insert_missed_optimize,
    or_opt_optimize,
    regenerate_solution_trajectories,
    trajectory_swap_optimize,
)
from server.solver.routing_context import RoutingContext, matrix_from_solution_or_cache
from server.solver.solver_bridge import (
    _project_env_to_local_km,
    _project_solution_trajectories,
    _unproject_solution_trajectories,
    get_current_matrix,
)
from server.solver.virtual_drones import (
    merge_virtual_legs_n,
    split_solution_to_virtual_legs,
)

from .models import (
    CoverageStatsRequest,
    CoverageStatsResponse,
    InsertMissedRequest,
    TrajectorySwapRequest,
)

router = APIRouter()


def _select_distance_matrix(solution: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Prefer request-provided matrix data, with cached matrix as compatibility fallback."""
    explicit_matrix = solution.get("distance_matrix")
    distance_matrix = matrix_from_solution_or_cache(solution, get_current_matrix)
    if explicit_matrix:
        print(
            f"   Using distance matrix from solution with {len(explicit_matrix.get('labels', []))} labels"
        )
    elif distance_matrix:
        print(
            f"   Using cached distance matrix with {len(distance_matrix.get('labels', []))} labels"
        )
    else:
        print("   WARNING: No distance matrix available!")
    return distance_matrix


def _routing_context_from_optimizer_request(
    req: Union[TrajectorySwapRequest, InsertMissedRequest],
) -> RoutingContext:
    return RoutingContext.from_inputs(
        env=req.env,
        drone_configs=req.drone_configs,
        distance_matrix=_select_distance_matrix(req.solution),
    )


@router.post("/api/coverage_stats", response_model=CoverageStatsResponse)
def api_coverage_stats(req: CoverageStatsRequest):
    """
    Get coverage statistics for a mission solution.

    Returns metrics on targets visited, points collected,
    and lists any unvisited targets.
    """
    stats = get_coverage_stats(req.solution, req.env)

    return CoverageStatsResponse(
        targets_visited=stats.get("targets_visited", 0),
        targets_total=stats.get("targets_total", 0),
        coverage_percent=stats.get("coverage_percent", 0.0),
        points_collected=stats.get("points_collected", 0),
        points_possible=stats.get("points_possible", 0),
        points_percent=stats.get("points_percent", 0.0),
        total_distance=stats.get("total_distance", 0.0),
        unvisited_targets=stats.get("unvisited_targets", []),
    )


@router.get("/api/wrapped_polygons")
async def get_wrapped_polygons():
    """
    Get wrapped SAM polygon boundaries from cached calculation.

    Returns the polygon boundaries created by wrapping overlapping SAMs.
    These are used for visualization on the frontend canvas.
    """
    try:
        # Get wrapped polygons from the cached distance matrix
        wrapped_polygons = sam_distance_matrix.get_wrapped_polygons()

        return {"success": True, "polygons": wrapped_polygons}
    except Exception as e:
        return {"success": False, "error": str(e), "polygons": []}


def _exclude_refuel_drones(
    solution: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Remove refueling drones from solution before post-optimization.
    Returns (filtered_solution, excluded_routes) so excluded can be merged back."""
    routes = solution.get("routes", {})
    excluded = {}
    filtered = {}
    for did, r in routes.items():
        if r.get("refuel_airport"):
            excluded[did] = r
            print(
                f"   ⛽ Excluding D{did} from optimization (refueling route — use Agent to optimize)",
                flush=True,
            )
        else:
            filtered[did] = r
    filtered_solution = {**solution, "routes": filtered}
    return filtered_solution, excluded


def _merge_refuel_drones(
    result: Dict[str, Any], excluded: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge excluded refueling drones back into optimizer result."""
    routes = result.get("routes", {})
    for did, r in excluded.items():
        routes[did] = r
    result["routes"] = routes
    return result


@router.post("/api/trajectory_swap_optimize")
async def api_trajectory_swap_optimize(req: TrajectorySwapRequest):
    """
    Optimize a solution by swapping targets to drones whose trajectories pass closer.

    For each target, calculates perpendicular distance to current trajectory
    (line between prev/next waypoints). If another capable drone's trajectory
    passes closer, moves the target to that drone.
    """
    print("\n" + "=" * 60)
    print("📥 TRAJECTORY SWAP ENDPOINT CALLED")
    print(f"   Received routes: {list(req.solution.get('routes', {}).keys())}")
    print("=" * 60)
    print(f"   debug flag: {bool(getattr(req, 'debug', False))}")

    context = _routing_context_from_optimizer_request(req)

    try:
        # Project env + solution from lat/lng into local x/y km, run the optimizer
        # in km, then unproject the result. post_optimizer.py still reads x/y on
        # env objects and compares trajectory point coordinates directly, so both
        # ends must speak the same coordinate system. See solver_bridge for the
        # matching pair of projection helpers.
        #
        # Project every drone (including refuel ones) uniformly so the round-trip
        # through _merge_refuel_drones + _unproject_solution_trajectories stays
        # symmetric — refuel drones go km→lat,lng harmlessly even though the
        # optimizer never touched them.
        import copy

        projected_env, ref_lat, ref_lng = _project_env_to_local_km(context.env)
        projected_context = context.with_projection(
            env=projected_env,
            ref_lat=ref_lat,
            ref_lng=ref_lng,
        )
        projected_solution = copy.deepcopy(req.solution)
        _project_solution_trajectories(projected_solution, ref_lat, ref_lng)

        # Split refueling drones into virtual leg-drones (D1 -> D1_L1, D1_L2)
        # Each virtual drone has its own route, trajectory, and per-leg fuel budget.
        # This ensures optimizers enforce per-leg constraints correctly.
        split_solution, split_configs, leg_mapping = split_solution_to_virtual_legs(
            projected_solution, context.drone_configs, projected_env
        )
        if leg_mapping:
            virtual_ids = [k for k in leg_mapping.keys() if "_L" in k]
            if virtual_ids:
                print(f"   🔀 [Swap] Split into virtual drones: {virtual_ids}")

        # Run optimizer on virtual drones (each leg treated as independent drone)
        result = trajectory_swap_optimize(
            solution=split_solution,
            env=projected_context.env,
            drone_configs=split_configs,
            distance_matrix=projected_context.distance_matrix,
            auto_iterate=bool(getattr(req, "auto_iterate", True)),
            auto_regen=bool(getattr(req, "auto_regen", True)),
            debug=bool(getattr(req, "debug", False)),
        )

        # Merge virtual legs back into physical drones
        if any(_vl != _pid for _vl, _pid in leg_mapping.items()):
            result = merge_virtual_legs_n(result, context.drone_configs, leg_mapping)
            print("   🔀 [Swap] Merged virtual drones back into physical drones")

        # SINGLE server-side trajectory regeneration: rebuild the SAM-aware
        # trajectory for the (possibly merged multi-stop) physical routes so the
        # frontend never has to. Self-resolves a checkpoint start from the old
        # trajectory's first point.
        regenerate_solution_trajectories(
            result,
            projected_context.env,
            context.drone_configs,
            projected_context.distance_matrix,
        )

        # Unproject all routes' trajectories back to lat/lng for the frontend.
        _unproject_solution_trajectories(result, ref_lat, ref_lng)

        # Log swaps made
        swaps = result.get("swaps_made", [])
        print(
            f"\n🔄 TRAJECTORY SWAP OPTIMIZATION: Returning {len(swaps)} swaps to frontend"
        )
        if swaps:
            print(f"   Total swaps made: {len(swaps)}")
            for swap in swaps:
                print(
                    f"   {swap['target']}: Drone {swap['from_drone']} → Drone {swap['to_drone']} "
                    f"(remove {swap.get('remove_delta', 0):.1f} → insert {swap.get('insert_delta', 0):.1f}, "
                    f"savings {swap.get('savings', 0):.1f})"
                )
        else:
            print("   No beneficial swaps found")

        # Debug: Print route changes
        print("\n📋 Routes being returned to frontend:")
        for did, route_data in result.get("routes", {}).items():
            route = route_data.get("route", [])
            print(f"   D{did}: {' -> '.join(str(wp) for wp in route)}")

        return {
            "success": True,
            "routes": result.get("routes", {}),
            "sequences": {
                d: r.get("sequence", "") for d, r in result.get("routes", {}).items()
            },
            "swaps_made": swaps,
            "iterations": result.get("iterations", 0),
            "target_diagnostics": result.get("target_diagnostics", {}),
        }
    except Exception as e:
        import traceback

        traceback.print_exc()
        return {
            "success": False,
            "error": str(e),
        }


@router.post("/api/crossing_removal_optimize")
async def api_crossing_removal_optimize(req: TrajectorySwapRequest):
    """
    Remove self-crossings from drone trajectories using 2-opt.

    Scans each drone's route for segment crossings and reverses the
    middle portion to eliminate them.
    """
    print("\n" + "=" * 60)
    print("📥 CROSSING REMOVAL ENDPOINT CALLED")
    print(f"   Received routes: {list(req.solution.get('routes', {}).keys())}")
    print("=" * 60)

    # The optimizer needs this to compute route distances under SAM-aware policy;
    # without it, it refuses to run (no Euclidean fallback).
    context = _routing_context_from_optimizer_request(req)

    try:
        # Project env + solution from lat/lng into local x/y km. The 2-opt
        # crossing-removal optimizer reads env x/y and compares trajectory
        # coordinates directly, so both must be in the same km-space frame.
        # See the swap endpoint above for the matching pattern.
        import copy

        projected_env, ref_lat, ref_lng = _project_env_to_local_km(context.env)
        projected_context = context.with_projection(
            env=projected_env,
            ref_lat=ref_lat,
            ref_lng=ref_lng,
        )
        projected_solution = copy.deepcopy(req.solution)
        _project_solution_trajectories(projected_solution, ref_lat, ref_lng)

        # Split refueling drones into virtual leg-drones (D1 -> D1_L1, D1_L2)
        # Each virtual drone has its own route and per-leg fuel budget.
        # No-Cross processes each leg independently, preventing reversals that
        # would move targets between legs.
        split_solution, split_configs, leg_mapping = split_solution_to_virtual_legs(
            projected_solution, context.drone_configs, projected_env
        )
        if leg_mapping:
            virtual_ids = [k for k in leg_mapping.keys() if "_L" in k]
            if virtual_ids:
                print(f"   🔀 [NoCross] Split into virtual drones: {virtual_ids}")

        # Run optimizer on virtual drones
        result = crossing_removal_optimize(
            solution=split_solution,
            env=projected_context.env,
            drone_configs=split_configs,
            distance_matrix=projected_context.distance_matrix,
        )

        # Merge virtual legs back into physical drones
        if any(_vl != _pid for _vl, _pid in leg_mapping.items()):
            result = merge_virtual_legs_n(result, context.drone_configs, leg_mapping)
            print("   🔀 [NoCross] Merged virtual drones back into physical drones")

        # Server-side SAM-aware trajectory regeneration (2-opt reversed the route
        # but kept the old trajectory; rebuild it here so the frontend doesn't).
        regenerate_solution_trajectories(
            result,
            projected_context.env,
            context.drone_configs,
            projected_context.distance_matrix,
        )

        # Unproject trajectories back to lat/lng for the frontend.
        _unproject_solution_trajectories(result, ref_lat, ref_lng)

        # Log fixes made
        fixes = result.get("fixes_made", [])
        if fixes:
            print(f"\n✂️ CROSSING REMOVAL: {len(fixes)} crossings fixed")
            for fix in fixes:
                print(
                    f"   Drone {fix['drone']}: reversed segment {fix['segment_i']}-{fix['segment_j']} (pass {fix['pass']})"  # noqa: E501
                )
        else:
            print("\n✂️ CROSSING REMOVAL: No crossings found")

        return {
            "success": True,
            "routes": result.get("routes", {}),
            "sequences": {
                d: r.get("sequence", "") for d, r in result.get("routes", {}).items()
            },
            "fixes_made": fixes,
            "passes": result.get("passes", 0),
        }
    except Exception as e:
        import traceback

        traceback.print_exc()
        return {
            "success": False,
            "error": str(e),
        }


@router.post("/api/or_opt_optimize")
async def api_or_opt_optimize(req: TrajectorySwapRequest):
    """Or-opt: relocate contiguous runs of targets (length 1..3, forward+reversed)
    between drones/legs — catches multi-target moves the single-target SWAP misses.

    Same split→optimize→merge→regen→unproject pipeline as the swap/crossing
    endpoints, so a run can move across a refuel boundary (virtual legs)."""
    print("\n" + "=" * 60)
    print("📥 OR-OPT ENDPOINT CALLED")
    print(f"   Received routes: {list(req.solution.get('routes', {}).keys())}")
    print("=" * 60)

    context = _routing_context_from_optimizer_request(req)

    try:
        import copy

        projected_env, ref_lat, ref_lng = _project_env_to_local_km(context.env)
        projected_context = context.with_projection(
            env=projected_env,
            ref_lat=ref_lat,
            ref_lng=ref_lng,
        )
        projected_solution = copy.deepcopy(req.solution)
        _project_solution_trajectories(projected_solution, ref_lat, ref_lng)

        split_solution, split_configs, leg_mapping = split_solution_to_virtual_legs(
            projected_solution, context.drone_configs, projected_env
        )
        if any(_vl != _pid for _vl, _pid in leg_mapping.items()):
            print(f"   🔀 [Or-opt] Split into virtual legs: {list(leg_mapping.keys())}")

        # Or-opt runs on per-leg virtual drones (each leg a drone) so a contiguous
        # run can relocate from one leg to another.
        result = or_opt_optimize(
            solution=split_solution,
            env=projected_context.env,
            drone_configs=split_configs,
            distance_matrix=projected_context.distance_matrix,
        )

        if any(_vl != _pid for _vl, _pid in leg_mapping.items()):
            result = merge_virtual_legs_n(result, context.drone_configs, leg_mapping)
            print("   🔀 [Or-opt] Merged virtual legs back into physical drones")

        # Single server-side SAM-aware trajectory regen (route changed; rebuild it).
        regenerate_solution_trajectories(
            result,
            projected_context.env,
            context.drone_configs,
            projected_context.distance_matrix,
        )
        _unproject_solution_trajectories(result, ref_lat, ref_lng)

        moves = result.get("or_opt_moves", [])
        print(f"\n🔧 OR-OPT: {len(moves)} run move(s)")
        for m in moves:
            print(
                f"   {m['run']}: D{m['from_drone']}→D{m['to_drone']} "
                f"(rev={m['reversed']}, gain={m['gain']})"
            )
        print("\n📋 Routes being returned to frontend:")
        for did, rd in result.get("routes", {}).items():
            print(f"   D{did}: {' -> '.join(str(w) for w in rd.get('route', []))}")

        return {
            "success": True,
            "routes": result.get("routes", {}),
            "sequences": {
                d: r.get("sequence", "") for d, r in result.get("routes", {}).items()
            },
            "or_opt_moves": moves,
        }
    except Exception as e:
        import traceback

        traceback.print_exc()
        return {"success": False, "error": str(e)}


@router.post("/api/insert_missed_optimize")
async def api_insert_missed_optimize(req: InsertMissedRequest):
    """
    Insert unvisited targets into existing routes.

    Takes the current solution and attempts to insert any unvisited targets
    into drone routes that have remaining fuel capacity. Targets are inserted
    at optimal positions to minimize additional fuel consumption.

    Thin wrapper around insert_missed_optimize() in post_optimizer.py — handles
    HTTP I/O and observability prints; the optimizer function itself is pure
    and reusable from the pipeline helper (see apply_optimizer_pipeline).
    """
    print("\n" + "=" * 80, flush=True)
    print("📥 INSERT MISSED ENDPOINT CALLED", flush=True)
    print(
        f"   Received routes: {list(req.solution.get('routes', {}).keys())}", flush=True
    )

    print("\n🔍 ROUTES BEFORE INSERT MISSED:", flush=True)
    for did in sorted(req.solution.get("routes", {}).keys()):
        route = req.solution["routes"][did].get("route", [])
        print(f"   D{did}: {route} (length={len(route)})", flush=True)
    print("=" * 80, flush=True)

    context = _routing_context_from_optimizer_request(req)

    try:
        # Split refueling drones into per-leg virtual drones so INSERT checks each
        # leg's remaining fuel independently. Use the N-leg splitter (matches the
        # merge_virtual_legs_n below and the other optimizer endpoints) — the
        # legacy split_refueling_drones only understood the 2-leg refuel_airport
        # model, so an OPPORTUNISTIC route (refuel structure in route.refuel_indices)
        # wasn't split at all → INSERT saw one leg and packed a target past a single
        # tank (a 35 km leg grew to 89 km > 70). split_solution_to_virtual_legs now
        # reads route-level refuel_indices, so each leg is budget-checked.
        split_solution, split_configs, leg_mapping = split_solution_to_virtual_legs(
            req.solution, context.drone_configs, context.env
        )
        if leg_mapping:
            virtual_ids = [k for k in leg_mapping.keys() if "_L" in k]
            if virtual_ids:
                print(f"   🔀 [Insert] Split into virtual drones: {virtual_ids}")

        # Run optimizer on virtual drones
        result = insert_missed_optimize(
            solution=split_solution,
            env=context.env,
            drone_configs=split_configs,
            distance_matrix=context.distance_matrix,
        )

        if not result.get("success"):
            print(
                f"   ❌ insert_missed_optimize failed: {result.get('error')}",
                flush=True,
            )
            return result

        # Merge virtual legs back into physical drones
        if any(_vl != _pid for _vl, _pid in leg_mapping.items()):
            result = merge_virtual_legs_n(result, context.drone_configs, leg_mapping)
            print("   🔀 [Insert] Merged virtual drones back into physical drones")

        insertions = result.get("insertions", [])
        routes = result.get("routes", {})

        print("\n✅ ROUTES AFTER INSERT MISSED:", flush=True)
        for did in sorted(routes.keys()):
            route = routes[did].get("route", [])
            print(f"   D{did}: {route}", flush=True)

        if insertions:
            print(f"\n➕ INSERT: {len(insertions)} targets inserted", flush=True)
            for ins in insertions:
                print(f"   {ins['target']} → D{ins['drone']}", flush=True)
        else:
            print("\n➕ INSERT: No insertions possible", flush=True)
        print("=" * 80 + "\n", flush=True)

        return result
    except Exception as e:
        import traceback

        traceback.print_exc()
        return {
            "success": False,
            "error": str(e),
        }


@router.post("/api/calculate_wrapping")
async def calculate_wrapping(request: Request):
    """
    Calculate wrapped SAM polygon boundaries for the current environment.

    This endpoint recalculates wrapping on-demand when SAMs are edited.
    """
    try:
        data = await request.json()
        sams = data.get("sams", [])

        if not sams:
            return {"success": True, "polygons": []}

        # Use the SAM wrapping module directly
        import path_planning_core.sam_wrapping as sam_wrapping

        # Normalize SAM format for wrap_sams:
        #   expects: {'x': float, 'y': float, 'range': float, ...}
        # Normalize SAM format for wrap_sams:
        #   expects: {'x': float, 'y': float, 'range': float, ...}
        normalized_sams = []
        for sam in sams:
            # Get a position as [x, y]
            pos = None
            if "pos" in sam and sam["pos"] is not None:
                pos = sam["pos"]
            elif "position" in sam and sam["position"] is not None:
                pos = sam["position"]
            elif "x" in sam and "y" in sam:
                pos = [float(sam["x"]), float(sam["y"])]

            if pos is None or len(pos) < 2:
                continue

            x = float(pos[0])
            y = float(pos[1])

            norm = {
                "x": x,
                "y": y,
                "range": float(sam.get("range", sam.get("radius", 15.0))),
            }

            if "id" in sam:
                norm["id"] = sam["id"]

            normalized_sams.append(norm)

        # Calculate wrapping
        polygons, cluster_info = sam_wrapping.wrap_sams(normalized_sams)
        _ = cluster_info

        # Convert numpy arrays to Python lists
        polygons_as_lists = [
            polygon.tolist() if hasattr(polygon, "tolist") else polygon
            for polygon in polygons
        ]

        return {"success": True, "polygons": polygons_as_lists}
    except Exception as e:
        import traceback

        traceback.print_exc()
        return {"success": False, "error": str(e), "polygons": []}
