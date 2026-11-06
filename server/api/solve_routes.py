import copy
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter

from server.solver.altitude import (
    altitude_warnings,
    matrix_for_drone,
    with_primary_altitude,
)
from server.solver.detection_radius import apply_detection_radius
from server.solver.flight_profile import without_unreachable_tankers
from server.solver.optimizers.pipeline import apply_optimizer_pipeline
from server.solver.optimizers.regenerate import regenerate_solution_trajectories
from server.solver.projection import (
    _apply_cruise_altitude_to_sams,
    _project_env_to_local_km,
    _project_solution_trajectories,
    _unproject_solution_trajectories,
)
from server.solver.routing_context import RoutingContext
from server.solver.solver_bridge import (
    run_pipeline_on_solution,
    solve_mission,
    solve_mission_with_allocation,
)
from server.solver.time_sync import apply_time_sync

from .mission_state import (
    get_or_create_mission,
    set_mission_name,
    upsert_mission,
    verify_solution_basic,
)
from .models import SolveRequest, SolveResponse, SolveWithAllocationRequest
from .run_ledger_service import RunLedgerService


def _routing_context_from_solver_result(
    *,
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    result: Dict[str, Any],
) -> RoutingContext:
    return RoutingContext.from_inputs(
        env=env,
        drone_configs=drone_configs,
        distance_matrix=result.get("distance_matrix"),
    )


def _attach_anim_frame(
    rd: Dict[str, Any],
    distance_matrix: Optional[Dict[str, Any]] = None,
    drone_id: Optional[str] = None,
    env: Optional[Dict[str, Any]] = None,
    drone_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Attach the authoritative densified animation frame to a response route,
    with the altitude of every point when the env is given."""
    try:
        from server.solver.route_timeline import build_route_frame

        rd["anim_frame"] = build_route_frame(
            rd, distance_matrix, drone_id, env, drone_config
        )
        if env is not None:
            from server.solver.flight_profile import attach_flight_profile

            attach_flight_profile(
                rd["anim_frame"],
                rd.get("route", []),
                env,
                drone_config,
                rd.get("refuel_indices") or None,
                drone_id or "",
            )
    except Exception as _afe:  # pragma: no cover - defensive
        print(f"   ⚠️ anim_frame build failed: {_afe}", flush=True)


_TIME_SYNC_ROUTE_FIELDS = (
    "arrival_times",
    "departure_times",
    "service_times",
    "wait_times",
    "waypoint_times",
    "mission_time",
    "sync_target",
    "sync_wait_strategy",
    "sync_wait_time",
    "launch_delay",
    "sync_wait_fuel",
)


def _attach_time_sync_route_fields(
    rd: Dict[str, Any], route_data: Dict[str, Any]
) -> None:
    for field in _TIME_SYNC_ROUTE_FIELDS:
        if field in route_data:
            rd[field] = copy.deepcopy(route_data[field])


def _route_value(
    routes: Dict[str, Any],
    env: Dict[str, Any],
) -> Tuple[int, int, float]:
    target_points = {
        str(t.get("id")): int(t.get("priority", 5) or 0) for t in env.get("targets", [])
    }
    visited = {
        str(wp)
        for rd in (routes or {}).values()
        for wp in rd.get("route", [])
        if str(wp) in target_points
    }
    fuel = sum(float(rd.get("distance", 0.0) or 0.0) for rd in routes.values())
    return len(visited), sum(target_points[tid] for tid in visited), fuel


def _sum_route_distance(route: list, distance_matrix: Dict[str, Any]) -> float:
    labels = [str(label) for label in (distance_matrix or {}).get("labels", [])]
    matrix = (distance_matrix or {}).get("matrix", [])
    idx = {label: i for i, label in enumerate(labels)}
    total = 0.0
    for a, b in zip(route, route[1:]):
        ia = idx.get(str(a))
        ib = idx.get(str(b))
        if ia is None or ib is None:
            return 0.0
        total += float(matrix[ia][ib])
    return total


def _seed_repair_route_metadata(
    routes: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    distance_matrix: Dict[str, Any],
) -> None:
    target_points = {
        str(t.get("id")): int(t.get("priority", 5) or 0) for t in env.get("targets", [])
    }
    for did, rd in routes.items():
        route = [str(wp) for wp in rd.get("route", [])]
        rd["route"] = route
        rd["sequence"] = ",".join(route)
        cfg = drone_configs.get(str(did)) or drone_configs.get(did) or {}
        rd["fuel_budget"] = float(
            rd.get("fuel_budget", cfg.get("fuel_budget", 0.0)) or 0.0
        )
        rd["points"] = sum(target_points.get(wp, 0) for wp in route)
        own_matrix = matrix_for_drone(distance_matrix, did) or {}
        if not rd.get("distance"):
            rd["distance"] = _sum_route_distance(route, own_matrix)

        refuel_indices = [
            int(i)
            for i in (rd.get("refuel_indices") or [])
            if isinstance(i, (int, float)) and 0 < int(i) < len(route) - 1
        ]
        if refuel_indices:
            bounds = [0] + refuel_indices + [len(route) - 1]
            rd["leg_distances"] = [
                _sum_route_distance(route[bounds[i] : bounds[i + 1] + 1], own_matrix)
                for i in range(len(bounds) - 1)
            ]
            rd["refuel_stops"] = [route[i] for i in refuel_indices]


def _run_checkpoint_repair_candidate(
    *,
    repair_routes: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    distance_matrix: Dict[str, Any],
    run_pipeline: bool = True,
) -> Dict[str, Any]:
    projected_env, ref_lat, ref_lng = _project_env_to_local_km(env)
    projected_solution: Dict[str, Any] = {
        "routes": copy.deepcopy(repair_routes),
        "distance_matrix": distance_matrix,
    }
    _project_solution_trajectories(projected_solution, ref_lat, ref_lng)
    _seed_repair_route_metadata(
        projected_solution["routes"],
        projected_env,
        drone_configs,
        distance_matrix,
    )
    regenerate_solution_trajectories(
        projected_solution,
        projected_env,
        drone_configs,
        distance_matrix,
    )
    _seed_repair_route_metadata(
        projected_solution["routes"],
        projected_env,
        drone_configs,
        distance_matrix,
    )

    if not run_pipeline:
        out = {"routes": projected_solution.get("routes", {})}
        _unproject_solution_trajectories(out, ref_lat, ref_lng)
        projected_solution["routes"] = out["routes"]
        projected_solution["success"] = True
        projected_solution["pipeline_summary"] = {
            "selected_candidate": "preserve_baseline",
            "pipeline_passes": 0,
            "targets_before": _route_value(projected_solution["routes"], env)[0],
            "targets_after": _route_value(projected_solution["routes"], env)[0],
            "fuel_before": round(_route_value(projected_solution["routes"], env)[2], 2),
            "fuel_after": round(_route_value(projected_solution["routes"], env)[2], 2),
            "insertions": 0,
            "swaps": 0,
            "crossings_fixed": 0,
        }
        return projected_solution

    pipeline_result = apply_optimizer_pipeline(
        solution=projected_solution,
        env=projected_env,
        drone_configs=drone_configs,
        distance_matrix=distance_matrix,
    )
    if not pipeline_result.get("success"):
        return pipeline_result

    regenerate_solution_trajectories(
        pipeline_result,
        projected_env,
        drone_configs,
        distance_matrix,
    )

    out = {"routes": pipeline_result.get("routes", {})}
    _unproject_solution_trajectories(out, ref_lat, ref_lng)
    pipeline_result["routes"] = out["routes"]
    return pipeline_result


def _candidate_rank(
    routes: Dict[str, Any], env: Dict[str, Any]
) -> Tuple[int, float, int]:
    target_count, points, fuel = _route_value(routes, env)
    return points, -fuel, target_count


def _select_best_checkpoint_candidate(
    candidates: list[Dict[str, Any]],
    env: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    valid = [
        c
        for c in candidates
        if c.get("success") and not c.get("errors") and c.get("routes")
    ]
    if not valid:
        return None
    return max(valid, key=lambda c: _candidate_rank(c["routes"], env))


def _repair_is_better(
    repair_routes: Dict[str, Any],
    full_routes: Dict[str, Any],
    env: Dict[str, Any],
) -> bool:
    """Return True when repair is the better feasible checkpoint candidate.

    Mission objective is priority-first: maximize total priority points, then
    minimize fuel. Target count is not value; it is only a final deterministic
    tie-breaker when points and fuel are effectively equal.
    """
    repair_targets, repair_points, repair_fuel = _route_value(repair_routes, env)
    full_targets, full_points, full_fuel = _route_value(full_routes, env)
    if repair_points < full_points:
        return False
    if repair_points > full_points:
        return True
    if repair_fuel + 1e-6 < full_fuel:
        return True
    if full_fuel + 1e-6 < repair_fuel:
        return False
    return repair_targets > full_targets


def run_solve_with_allocation(
    req: SolveWithAllocationRequest,
    *,
    run_ledger: Optional[RunLedgerService] = None,
) -> SolveResponse:
    """Shared Run Planner execution path used by the API and reasoning agent."""
    env = req.env
    drone_configs = req.drone_configs
    if any(s.get("weather_ring") for s in env.get("sams", [])):
        try:
            _project_env_to_local_km(env)
        except ValueError as exc:
            return SolveResponse(
                success=False,
                routes={},
                sequences={},
                errors=[
                    "Weather geometry cannot be routed on this mission map. "
                    "Update the mission area and forecast. " + str(exc)
                ],
            )
    solver_constraints = req.constraints or None

    mission_id, continuity, env_hash, cfg_hash, mismatch_reason = get_or_create_mission(
        req.mission_id, env, drone_configs
    )
    if mismatch_reason:
        print(
            "[solve_with_allocation] mission continuity broken "
            f"({mismatch_reason}); starting new solve",
            flush=True,
        )

    missing = []
    if not env.get("airports"):
        missing.append("env.airports")
    if "targets" not in env:
        missing.append("env.targets")
    if not drone_configs:
        missing.append("drone_configs")
    if missing:
        return SolveResponse(
            success=False,
            routes={},
            sequences={},
            wrapped_polygons=None,
            allocations=None,
            distance_matrix=None,
            mission_id=mission_id,
            env_hash=env_hash,
            continuity=continuity,
            errors=["Missing required fields: " + ", ".join(missing)],
        )

    run_id = None
    if run_ledger is not None:
        run_id = run_ledger.create_solver_run(
            allocation_strategy=req.allocation_strategy,
            is_checkpoint_replan=req.is_checkpoint_replan,
        )

    if run_ledger is not None and run_id:
        num_targets = len(req.env.get("targets", []))
        num_drones = len(req.drone_configs) if req.drone_configs else 0
        run_ledger.log_event(
            run_id,
            "SOLVE_REQUESTED",
            {
                "num_targets": num_targets,
                "num_drones": num_drones,
                "allocation_strategy": req.allocation_strategy,
                "is_checkpoint_replan": req.is_checkpoint_replan,
                "visited_targets": req.visited_targets or [],
            },
        )

    env_to_solve = req.env
    if req.visited_targets:
        visited_set = set(req.visited_targets)
        env_to_solve = dict(req.env)
        original_targets = env_to_solve.get("targets", [])
        env_to_solve["targets"] = [
            t for t in original_targets if t.get("id") not in visited_set
        ]
        label = (
            "CHECKPOINT REPLAN" if req.is_checkpoint_replan else "SOLVE WITH VISITED"
        )
        print(
            f"🔄 {label}: Filtered {len(visited_set)} visited targets, "
            f"{len(env_to_solve['targets'])} remaining",
            flush=True,
        )
        print(f"   Visited: {req.visited_targets}", flush=True)
        print(
            "   Synthetic starts: "
            f"{list(env_to_solve.get('synthetic_starts', {}).keys())}",
            flush=True,
        )
        print(
            f"   Checkpoints: {[c.get('id') for c in env_to_solve.get('checkpoints', [])]}",
            flush=True,
        )

    checkpoints_in_env = env_to_solve.get("checkpoints", [])
    if checkpoints_in_env:
        print(
            f"📍 Checkpoints in request: {[c.get('id') for c in checkpoints_in_env]}",
            flush=True,
        )
    else:
        print("⚠️ No checkpoints in request env", flush=True)

    # Altitude is per drone. The env's mission altitude becomes the one most
    # drones fly (their matrix is the mission matrix; other altitudes get their
    # own). Then slice every SAM at it ONCE here, before env_to_solve is used by
    # the main solve or the post-solve optimizer pipeline — both independently
    # re-project this same env (see _apply_cruise_altitude_to_sams docstring).
    env_to_solve, solve_configs = with_primary_altitude(env_to_solve, req.drone_configs)
    # A drone may only refuel at tankers within reach of its altitude.
    solve_configs = without_unreachable_tankers(env_to_solve, solve_configs)
    cruise_altitude_km = float(env_to_solve.get("cruise_altitude_km", 0.0) or 0.0)
    if cruise_altitude_km > 0:
        env_to_solve = _apply_cruise_altitude_to_sams(env_to_solve, cruise_altitude_km)
        print(
            f"🛫 Applied cruise altitude {cruise_altitude_km} km to SAM radii",
            flush=True,
        )

    result = solve_mission_with_allocation(
        env=env_to_solve,
        drone_configs=solve_configs,
        allocation_strategy=req.allocation_strategy,
        use_sam_aware_distances=req.use_sam_aware_distances,
        post_optimize=False,
        constraints=solver_constraints or None,
    )
    routing_context = _routing_context_from_solver_result(
        env=env_to_solve,
        drone_configs=solve_configs,
        result=result,
    )

    allocations = result.get("allocations", {})
    distance_matrix = routing_context.distance_matrix or {}
    wrapped_polygons = result.get("wrapped_polygons", [])

    pipeline_summary: Optional[Dict[str, Any]] = None
    if req.post_optimize and result.get("routes"):
        pipe = run_pipeline_on_solution(
            solution=result,
            env=routing_context.env,
            drone_configs=routing_context.drone_configs,
            distance_matrix=routing_context.distance_matrix,
            constraints=solver_constraints or None,
        )
        pipeline_summary = pipe.get("pipeline_summary")
        if pipe.get("success"):
            result["routes"] = pipe["routes"]
        elif pipeline_summary:
            print(
                f"⚠️  PIPELINE failed at step={pipeline_summary.get('failed_step')}: "
                f"{pipe.get('error')}",
                flush=True,
            )

    if (
        req.is_checkpoint_replan
        and req.post_optimize
        and req.repair_routes
        and result.get("routes")
    ):
        try:
            candidates = []
            full_routes = result.get("routes", {})
            full_errors = verify_solution_basic(
                routing_context.env,
                full_routes,
                routing_context.drone_configs,
            )
            candidates.append(
                {
                    "name": "full_replan",
                    "success": True,
                    "routes": full_routes,
                    "errors": full_errors,
                    "pipeline_summary": pipeline_summary,
                }
            )

            preserve = _run_checkpoint_repair_candidate(
                repair_routes=req.repair_routes,
                env=routing_context.env,
                drone_configs=routing_context.drone_configs,
                distance_matrix=routing_context.distance_matrix or {},
                run_pipeline=False,
            )
            preserve_routes = preserve.get("routes", {})
            preserve_errors = verify_solution_basic(
                routing_context.env,
                preserve_routes,
                routing_context.drone_configs,
            )
            candidates.append(
                {
                    "name": "preserve_baseline",
                    "success": preserve.get("success"),
                    "routes": preserve_routes,
                    "errors": preserve_errors,
                    "pipeline_summary": preserve.get("pipeline_summary"),
                }
            )

            repair = _run_checkpoint_repair_candidate(
                repair_routes=req.repair_routes,
                env=routing_context.env,
                drone_configs=routing_context.drone_configs,
                distance_matrix=routing_context.distance_matrix or {},
            )
            repair_routes = repair.get("routes", {})
            repair_errors = verify_solution_basic(
                routing_context.env,
                repair_routes,
                routing_context.drone_configs,
            )
            candidates.append(
                {
                    "name": "checkpoint_repair",
                    "success": repair.get("success"),
                    "routes": repair_routes,
                    "errors": repair_errors,
                    "pipeline_summary": repair.get("pipeline_summary"),
                }
            )

            selected = _select_best_checkpoint_candidate(
                candidates, routing_context.env
            )
            if selected:
                result["routes"] = selected["routes"]
                pipeline_summary = selected.get("pipeline_summary") or {}
                pipeline_summary["selected_candidate"] = selected["name"]
                for candidate in candidates:
                    targets, points, fuel = _route_value(
                        candidate.get("routes", {}),
                        routing_context.env,
                    )
                    status = (
                        "valid"
                        if candidate.get("success") and not candidate.get("errors")
                        else "invalid"
                    )
                    print(
                        f"🔧 CHECKPOINT CANDIDATE {candidate['name']}: "
                        f"{status}, points={points}, targets={targets}, fuel={fuel:.2f}"
                        + (
                            f", errors={'; '.join(candidate.get('errors', [])[:2])}"
                            if candidate.get("errors")
                            else ""
                        ),
                        flush=True,
                    )
                print(
                    f"🔧 CHECKPOINT selected candidate: {selected['name']}",
                    flush=True,
                )
            else:
                for candidate in candidates:
                    if candidate.get("errors"):
                        print(
                            f"🔧 CHECKPOINT CANDIDATE {candidate['name']} rejected: "
                            + "; ".join(candidate.get("errors", [])[:3]),
                            flush=True,
                        )
                if pipeline_summary:
                    pipeline_summary["selected_candidate"] = "full_replan"
                if full_errors:
                    print(
                        "🔧 CHECKPOINT no valid alternative; full replan errors were: "
                        + "; ".join(full_errors[:3]),
                        flush=True,
                    )
        except Exception as repair_exc:
            print(f"⚠️ CHECKPOINT REPAIR failed: {repair_exc}", flush=True)

    # Detection radius: fuel from the path actually flown, then use the fuel the
    # shorter path saves (server/solver/detection_radius.py). No-op for drones
    # without a radius.
    result, _dr_log = apply_detection_radius(
        result, env_to_solve, solve_configs, solver_constraints or None
    )
    for _line in _dr_log:
        print(f"🎯 {_line}", flush=True)

    if req.time_sync and req.time_sync.get("enabled"):
        result = apply_time_sync(
            solution=result,
            env=routing_context.env,
            drone_configs=routing_context.drone_configs,
            distance_matrix=routing_context.distance_matrix,
            options=req.time_sync,
        )

    routes: Dict[str, Any] = {}
    sequences: Dict[str, str] = {}
    for drone_id_str, route_data in result.get("routes", {}).items():
        rd = {
            "route": route_data.get("route", []),
            "points": route_data.get("points", 0),
            "distance": route_data.get("distance", 0.0),
            "fuel_budget": route_data.get("fuel_budget", 0.0),
            "trajectory": route_data.get("trajectory", []),
            "waypoint_indices": route_data.get("waypoint_indices", {}),
        }
        if route_data.get("refuel_airport"):
            rd["refuel_airport"] = route_data["refuel_airport"]
            rd["leg1_distance"] = route_data.get("leg1_distance", 0.0)
            rd["leg2_distance"] = route_data.get("leg2_distance", 0.0)
            rd["refuel_idx"] = route_data.get("refuel_idx", -1)
        if route_data.get("leg_distances"):
            rd["leg_distances"] = route_data["leg_distances"]
            rd["refuel_indices"] = route_data.get("refuel_indices", [])
            rd["refuel_stops"] = route_data.get("refuel_stops", [])
        if route_data.get("span_distances"):
            rd["span_distances"] = route_data["span_distances"]
        if route_data.get("error"):
            rd["error"] = route_data["error"]
        _attach_time_sync_route_fields(rd, route_data)
        _attach_anim_frame(
            rd,
            distance_matrix,
            drone_id_str,
            env=routing_context.env,
            drone_config=(routing_context.drone_configs or {}).get(drone_id_str),
        )
        routes[drone_id_str] = rd
        sequences[drone_id_str] = route_data.get("sequence", "")

    known_target_ids = {
        str(t.get("id")) for t in routing_context.env.get("targets", [])
    }
    allocations = {
        did: [w for w in rd.get("route", []) if str(w) in known_target_ids]
        for did, rd in routes.items()
    }

    if run_ledger is not None and run_id:
        total_points = sum(r.get("points", 0) for r in routes.values())
        total_distance = sum(r.get("distance", 0) for r in routes.values())
        run_ledger.log_event(
            run_id,
            "SOLVE_COMPLETED",
            {
                "num_routes": len(routes),
                "total_points": total_points,
                "total_distance": round(total_distance, 2),
                "allocations": allocations,
            },
        )

    errors = verify_solution_basic(
        routing_context.env,
        routes,
        routing_context.drone_configs,
    )
    from server.solver.weather import verify_weather_routes

    errors.extend(verify_weather_routes(env, routes, routing_context.drone_configs))
    warnings = altitude_warnings(env, routing_context.drone_configs, routes) or None
    if errors:
        return SolveResponse(
            success=False,
            routes=routes or {},
            sequences=sequences or {},
            wrapped_polygons=wrapped_polygons,
            allocations=allocations,
            distance_matrix=distance_matrix,
            mission_id=mission_id,
            env_hash=env_hash,
            continuity=continuity,
            errors=errors,
            pipeline_summary=pipeline_summary,
            time_sync=result.get("time_sync"),
            warnings=warnings,
        )

    upsert_mission(
        mission_id,
        env=env,
        drone_configs=routing_context.drone_configs,
        env_hash=env_hash,
        cfg_hash=cfg_hash,
        routes=routes,
        allocation=allocations,
    )
    if req.mission_name:
        set_mission_name(mission_id, req.mission_name)

    return SolveResponse(
        success=True,
        routes=routes,
        sequences=sequences,
        wrapped_polygons=wrapped_polygons,
        allocations=allocations,
        distance_matrix=distance_matrix,
        mission_id=mission_id,
        env_hash=env_hash,
        continuity=continuity,
        errors=None,
        pipeline_summary=pipeline_summary,
        time_sync=result.get("time_sync"),
        warnings=warnings,
    )


def create_solve_router(run_ledger: RunLedgerService) -> APIRouter:
    router = APIRouter()

    @router.post("/api/solve", response_model=SolveResponse)
    def solve(req: SolveRequest):
        """
        Solve mission using the full orienteering solver (Held-Karp optimal).
        This replaces the greedy planner with the exact orienteering solver.
        """
        env = req.env
        drone_configs = req.drone_configs

        # Step 3 guards: mission continuity + env hash (§7 task 0.2: centralized)
        mission_id, continuity, env_hash, cfg_hash, mismatch_reason = (
            get_or_create_mission(req.mission_id, env, drone_configs)
        )
        if mismatch_reason:
            print(
                f"[solve] mission continuity broken ({mismatch_reason}); starting new solve",
                flush=True,
            )

        # Snapshot env used for this solve
        run_ledger.snapshot_solve_environment(env)

        # allocation_strategy = req.allocation_strategy  # TODO: integrate with solve_mission

        # Use the original solver (allocation strategy integration pending)
        result = solve_mission(env, drone_configs)
        routing_context = _routing_context_from_solver_result(
            env=env,
            drone_configs=drone_configs,
            result=result,
        )
        distance_matrix = routing_context.distance_matrix or {}

        if req.time_sync and req.time_sync.get("enabled"):
            result = apply_time_sync(
                solution=result,
                env=routing_context.env,
                drone_configs=routing_context.drone_configs,
                distance_matrix=routing_context.distance_matrix,
                options=req.time_sync,
            )

        routes: Dict[str, Any] = {}
        sequences: Dict[str, str] = {}

        # Convert solver output to API response format
        for drone_id_str, route_data in result.get("routes", {}).items():
            rd = {
                "route": route_data.get("route", []),
                "points": route_data.get("points", 0),
                "distance": route_data.get("distance", 0.0),
                "fuel_budget": route_data.get("fuel_budget", 0.0),
                "trajectory": route_data.get("trajectory", []),
                "waypoint_indices": route_data.get("waypoint_indices", {}),
            }
            if route_data.get("refuel_airport"):
                rd["refuel_airport"] = route_data["refuel_airport"]
                rd["leg1_distance"] = route_data.get("leg1_distance", 0.0)
                rd["leg2_distance"] = route_data.get("leg2_distance", 0.0)
                rd["refuel_idx"] = route_data.get("refuel_idx", -1)
            # Multi-stop refuel: carry the N-leg metadata into the response so the
            # frontend ⛽ stats split + refuel-crossing animation work.
            if route_data.get("leg_distances"):
                rd["leg_distances"] = route_data["leg_distances"]
                rd["refuel_indices"] = route_data.get("refuel_indices", [])
                rd["refuel_stops"] = route_data.get("refuel_stops", [])
            if route_data.get("span_distances"):
                rd["span_distances"] = route_data["span_distances"]
            # Per-drone error message (e.g. runway mismatch) — surface to UI.
            if route_data.get("error"):
                rd["error"] = route_data["error"]
            _attach_time_sync_route_fields(rd, route_data)
            _attach_anim_frame(
                rd,
                distance_matrix,
                drone_id_str,
                env=routing_context.env,
                drone_config=(routing_context.drone_configs or {}).get(drone_id_str),
            )
            routes[drone_id_str] = rd
            sequences[drone_id_str] = route_data.get("sequence", "")

        # Use wrapped_polygons from solver result (already unprojected to lat/lng)
        # The solver_bridge computes these in km, then unprojects to lat/lng before returning
        wrapped_polygons = result.get("wrapped_polygons", [])

        # Store a draft plan for this solve
        trajectories = {d: r.get("trajectory", []) for d, r in routes.items()}
        metrics: Dict[str, Any] = {}
        for d, r in routes.items():
            metrics[d] = {
                "distance": r.get("distance", 0.0),
                "points": r.get("points", 0),
                "fuel_budget": r.get("fuel_budget", 0.0),
                "route": r.get("route", []),
                "sequence": sequences.get(d, ""),
            }
        run_ledger.create_draft_plan(
            trajectories=trajectories,
            metrics=metrics,
            notes="draft from /api/solve",
        )

        # Step 3 guards: verify output matches env/constraints (basic)
        errors = verify_solution_basic(
            routing_context.env,
            routes,
            routing_context.drone_configs,
        )
        if errors:
            # Do not mutate mission store on invalid solve
            return SolveResponse(
                success=False,
                routes=routes or {},
                sequences=sequences or {},
                wrapped_polygons=wrapped_polygons,
                distance_matrix=distance_matrix,
                mission_id=mission_id,
                env_hash=env_hash,
                continuity=continuity,
                errors=errors,
                time_sync=result.get("time_sync"),
            )

        # Persist mission snapshot for continuity (used by chat-v4 and future endpoints)
        upsert_mission(
            mission_id,
            env=env,
            drone_configs=routing_context.drone_configs,
            env_hash=env_hash,
            cfg_hash=cfg_hash,
            routes=routes,
            allocation=None,
        )

        return SolveResponse(
            success=True,
            routes=routes,
            sequences=sequences,
            wrapped_polygons=wrapped_polygons,
            distance_matrix=distance_matrix,
            mission_id=mission_id,
            env_hash=env_hash,
            continuity=continuity,
            errors=None,
            time_sync=result.get("time_sync"),
        )

    @router.post("/api/solve_with_allocation", response_model=SolveResponse)
    def solve_with_allocation(req: SolveWithAllocationRequest):
        """Solve mission using the shared Run Planner execution path."""
        return run_solve_with_allocation(req, run_ledger=run_ledger)

    return router
