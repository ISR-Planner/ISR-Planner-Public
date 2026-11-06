import copy
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from server.agents.agent_memory import (
    add_memory,
    clear_memory,
    delete_memory,
    load_memory,
)
from server.agents.isr_agent_multi_v4 import run_multi_agent_v4
from server.database.mission_ledger import (
    create_agent_run,
    create_agent_trace,
    log_optimizer_step,
    update_agent_run,
)
from server.solver.sam_distance_matrix import calculate_sam_aware_matrix
from server.solver.time_sync import apply_time_sync

from .mission_state import (
    compute_cfg_hash,
    compute_env_hash,
    get_existing_solution_if_fresh,
    new_mission_id,
    upsert_mission,
)
from .models import (
    AddMemoryRequest,
    AgentChatRequest,
    AgentChatResponse,
    AgentCompareRequest,
    ReasoningAgentRequest,
    ReasoningAgentResponse,
)

router = APIRouter()


def _safe_agent_error(exc: Exception) -> str:
    """Return a client-safe error without forwarding provider credential text."""
    message = str(exc).lower()
    if "401" in message or "unauthorized" in message or "incorrect api key" in message:
        return (
            "Provider authentication failed. Check the server environment "
            "credential and restart the server."
        )
    return "Agent request failed. Check the server logs for the exception type."


def _extract_time_sync_from_text(text: str) -> Optional[Dict[str, Any]]:
    if not re.search(
        r"\b(?:tot|time[- ]?to[- ]?target|sync(?:ing|ed)?|synch(?:ing|ed)?|synchroniz(?:e|ing|ation))\b",
        text or "",
        flags=re.IGNORECASE,
    ):
        return None

    members: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    pattern = re.compile(
        r"\b(?:drone\s*)?D?\s*([1-9][0-9]*)\s*(?:to|with|at|on|->|→)\s*(T[0-9A-Za-z_-]+)\b",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text or ""):
        did = match.group(1)
        target_id = match.group(2).upper()
        key = (did, target_id)
        if key in seen:
            continue
        seen.add(key)
        members.append({"drone_id": did, "target_id": target_id})

    if len(members) < 2:
        return None
    return {
        "enabled": True,
        "mode": "selected_targets",
        "wait_strategy": "launch_delay",
        "tolerance": 1.0,
        "members": members,
        "source": "prompt",
    }


def _merge_time_sync_options(
    explicit: Optional[Dict[str, Any]],
    prompt_text: str,
) -> Optional[Dict[str, Any]]:
    prompt_sync = _extract_time_sync_from_text(prompt_text)
    if not explicit and not prompt_sync:
        return None

    merged = copy.deepcopy(explicit or {})
    if prompt_sync:
        merged.setdefault("wait_strategy", prompt_sync["wait_strategy"])
        merged.setdefault("tolerance", prompt_sync["tolerance"])
        merged["enabled"] = True
        merged["mode"] = "selected_targets"
        merged["members"] = prompt_sync["members"]
        merged["source"] = prompt_sync["source"]
    return merged


def _distance_matrix_for_sync(
    env: Dict[str, Any],
    existing: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if existing and existing.get("labels") and existing.get("matrix"):
        return existing
    try:
        return calculate_sam_aware_matrix(env, use_supabase_cache=True)
    except Exception as exc:  # pragma: no cover - defensive fallback
        print(f"[agent time_sync] distance matrix unavailable: {exc}", flush=True)
        return existing


def _apply_time_sync_to_agent_result(
    *,
    result: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    distance_matrix: Optional[Dict[str, Any]],
    time_sync: Optional[Dict[str, Any]],
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    routes = result.get("routes")
    if not (time_sync and time_sync.get("enabled") and isinstance(routes, dict)):
        return result, distance_matrix
    if not routes or not all(isinstance(rd, dict) for rd in routes.values()):
        result["time_sync"] = {
            "enabled": True,
            "mode": time_sync.get("mode", "first_target_arrival"),
            "wait_strategy": time_sync.get("wait_strategy", "launch_delay"),
            "status": "skipped",
            "reason": "Agent did not return full route metadata for time sync.",
            "tolerance": time_sync.get("tolerance", 1.0),
            "warnings": ["Time sync requires full route objects."],
        }
        return result, distance_matrix

    # The agent can change speeds or loiter settings while planning. Use the
    # returned configuration, which is also sent to the player, for both clocks.
    effective_configs = result.get("drone_configs") or drone_configs
    matrix = _distance_matrix_for_sync(env, distance_matrix)
    solution = {"routes": copy.deepcopy(routes)}
    apply_time_sync(
        solution=solution,
        env=env,
        drone_configs=effective_configs,
        distance_matrix=matrix,
        options=time_sync,
    )

    # Rebuild synced animation frames using the same helpers as Run Planner.
    from .solve_routes import _attach_anim_frame, _attach_time_sync_route_fields

    synced_routes = {}
    for did, route_data in (solution.get("routes") or {}).items():
        rd = copy.deepcopy(route_data)
        _attach_time_sync_route_fields(rd, route_data)
        _attach_anim_frame(
            rd,
            matrix,
            str(did),
            env=env,
            drone_config=(effective_configs or {}).get(str(did)),
        )
        synced_routes[str(did)] = rd

    result["routes"] = synced_routes
    result["time_sync"] = solution.get("time_sync")
    result["distance_matrix"] = matrix
    if (result.get("time_sync") or {}).get("status") != "skipped":
        result["routes_changed"] = True
    return result, matrix


@router.post("/api/agents/chat", response_model=AgentChatResponse)
async def agent_chat(req: AgentChatRequest):
    print(">>> /api/agents/chat (compat endpoint -> v4) HIT <<<", flush=True)
    """
    ISR Agent endpoint using the configured LLM provider with tool-calling.

    Frontend sends:
      - message: user's natural language instructions
      - env: current environment (airports, targets, sams)
      - sequences: current sequences, if any (for context)
      - drone_configs: config table from the Config tab

    Returns:
      - reply: The agent's response text
      - route: Extracted route waypoints (if a route was planned)
      - points: Total points collected
      - fuel: Total fuel used
    """
    try:
        # Use the current ISR agent with configured LLM provider and tools.
        # Pass drone_configs and sequences so agent knows all constraints
        # Use req.drone_configs if provided, else extract from env
        drone_configs = req.drone_configs or req.env.get("drone_configs")
        result = run_multi_agent_v4(
            user_message=req.message,
            environment=req.env,
            drone_configs=drone_configs or {},
        )

        return AgentChatResponse(
            reply=result.get("response", "(No agent reply)"),
            intent=result.get("intent"),
            actions=result.get("actions"),
            policy=result.get("policy"),
            constraints=result.get("constraints"),
            valid=result.get("valid", True),
            warnings=result.get("warnings"),
            errors=result.get("errors"),
            route=result.get("route"),
            routes=result.get("routes"),  # Multi-drone routes
            trajectories=result.get("trajectories"),  # SAM-avoiding trajectories
            points=result.get("points"),
            fuel=result.get("fuel"),
            allocations=result.get("allocation"),  # Target allocations
            allocation_strategy=result.get("allocation_strategy"),  # Strategy used
        )
    except Exception as e:
        print(f"Agent request failed: {type(e).__name__}", flush=True)
        return AgentChatResponse(
            reply=_safe_agent_error(e),
            route=None,
            routes=None,
            trajectories=None,
            points=None,
            fuel=None,
            allocations=None,
            allocation_strategy=None,
        )


def _mk_event(
    t: str, msg: str, data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Create a single trace event dict."""
    return {"t": t, "msg": msg, "data": data or None}


def _build_trace_events(
    trace: Dict[str, Any],
    allocations: Dict[str, Any],
    allocation_strategy: str,
    routes_for_ui: Dict[str, List[str]],
    trajectories: Dict[str, Any],
    total_points: int,
    total_fuel: float,
    coordinator_trace_events: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Build an ordered list of trace events for the Agents Monitor UI tab.
    This converts the raw trace object into a normalized, renderable timeline.

    If coordinator_trace_events is provided, those are used directly instead of
    extracting from the trace object.
    """
    ev: List[Dict[str, Any]] = []

    # If we have structured coordinator trace events, transform and use them
    if coordinator_trace_events:
        for cte in coordinator_trace_events:
            # Transform Coordinator format {agent, stage, event, details}
            # to AgentTraceEvent format {t, msg, data}
            agent = cte.get("agent", "Coordinator")
            stage = cte.get("stage", "unknown")
            event = cte.get("event", "")
            details = cte.get("details", {})

            # Build t (type) from agent.stage or just stage
            t = f"{agent.lower()}.{stage}" if agent else stage

            # Build msg (human readable) from event and key details
            msg_parts = [event.replace("_", " ").title()]
            if stage == "intent" and "intent" in details:
                msg_parts.append(f"→ {details['intent']}")
            elif stage == "policy" and "allocation_strategy" in details:
                msg_parts.append(f"→ {details['allocation_strategy']} allocation")
            elif stage == "validate":
                if details.get("errors"):
                    msg_parts.append(f"({len(details['errors'])} errors)")
                elif details.get("warnings"):
                    msg_parts.append(f"({len(details['warnings'])} warnings)")
            elif stage == "final":
                msg_parts.append(f"(intent={details.get('intent', '?')})")

            msg = " ".join(msg_parts)
            ev.append(_mk_event(t, msg, details))
    else:
        # Fall back to extracting from trace object (legacy path)
        coord = trace.get("coordinator", {}) if isinstance(trace, dict) else {}
        intent = coord.get("intent") or coord.get("intent_type") or "unknown"
        ev.append(
            _mk_event(
                "coordinator.intent",
                f"Coordinator intent: {intent}",
                {"intent": intent, "coordinator": coord},
            )
        )

        # Policy / flags
        policy = trace.get("policy_rules") or trace.get("policy") or {}
        if isinstance(policy, dict) and policy:
            ev.append(
                _mk_event("coordinator.policy", "Coordinator policy selected", policy)
            )

    # Allocation result
    alloc_count = (
        sum(len(v) for v in allocations.values())
        if isinstance(allocations, dict)
        else 0
    )
    ev.append(
        _mk_event(
            "allocator.result",
            f"Allocation ({allocation_strategy}): {alloc_count} targets assigned",
            {"strategy": allocation_strategy, "allocations": allocations},
        )
    )

    # Solver summary (routes)
    route_summary = {
        did: {"n": len(r), "start": r[0] if r else None, "end": r[-1] if r else None}
        for did, r in (routes_for_ui or {}).items()
    }
    ev.append(
        _mk_event(
            "solver.routes",
            f"Routes produced for {len(route_summary)} drones",
            {"routes_summary": route_summary, "routes": routes_for_ui},
        )
    )

    # Trajectories summary - count of [x,y] coordinate points per drone
    traj_summary = {
        did: (len(traj) if isinstance(traj, list) else 0)
        for did, traj in (trajectories or {}).items()
    }
    total_coords = sum(traj_summary.values())
    ev.append(
        _mk_event(
            "planner.trajectories",
            f"SAM-aware trajectories: {total_coords} total coords",
            {"coords_per_drone": traj_summary},
        )
    )

    # Metrics totals
    ev.append(
        _mk_event(
            "metrics.totals",
            f"Totals: points={total_points}, fuel={total_fuel:.1f}",
            {"points": total_points, "fuel": total_fuel},
        )
    )

    # Any embedded stepwise trace (optional future expansion)
    steps = trace.get("steps") if isinstance(trace, dict) else None
    if isinstance(steps, list) and steps:
        ev.append(
            _mk_event("trace.steps", f"{len(steps)} step records", {"steps": steps})
        )

    return ev


@router.post("/api/agents/chat-v4", response_model=AgentChatResponse)
async def agent_chat_v4(req: AgentChatRequest):
    """
    ISR Agent v4 endpoint using the reasoning-based multi-agent system.

    - If mission_id is omitted: create/solve a new mission and return mission_id.
    - If mission_id is provided: load existing mission state and allow
      question-only interaction (no recompute unless the request asks for it).
    """
    print(">>> /api/agents/chat-v4 (v4 endpoint) HIT <<<", flush=True)
    t0 = time.time()

    try:
        # -------------------------------
        # 1. Resolve env & configs FIRST
        # -------------------------------
        env = req.env
        drone_configs = req.drone_configs or env.get("drone_configs") or {}

        mission_id = req.mission_id
        existing_solution: Optional[Dict[str, Any]] = None

        # Priority 1: Use existing_solution from request (frontend sends current routes)
        # This enables follow-up requests like "move T5 to D1" to work
        if req.existing_solution:
            existing_solution = req.existing_solution
            print(
                f"[v4] Using existing_solution from request: {len(existing_solution.get('routes', {}))} routes",
                flush=True,
            )
        # Priority 2: Load from mission store by mission_id
        elif mission_id:
            mission, stored_solution, stale_reason = get_existing_solution_if_fresh(
                mission_id,
                env,
                req.drone_configs or drone_configs,
            )
            if mission is None:
                print(
                    f"[v4] mission_id={mission_id} not found; treating as new mission",
                    flush=True,
                )
                mission_id = None
            elif stale_reason:
                print(
                    f"[v4] mission_id={mission_id} {stale_reason}; stale solution ignored, mission identity kept",  # noqa: E501
                    flush=True,
                )
                # existing_solution stays None; mission_id unchanged.
            else:
                # Use incoming env if provided, fallback to stored env
                if not req.env:
                    env = mission.get("env", env)
                if not req.drone_configs:
                    drone_configs = mission.get("drone_configs", drone_configs)

                existing_solution = stored_solution
                print(
                    f"[v4] Loaded existing mission {mission_id} from store", flush=True
                )

        # -------------------------------
        # 1b. Multi-segment branch: solve all segments in one call
        # -------------------------------
        if req.is_segmented and req.segments and len(req.segments) > 1:
            print(f"[v4] SEGMENTED MISSION: {len(req.segments)} segments", flush=True)
            from server.agents.isr_agent_multi_v4 import (  # type: ignore[attr-defined]
                run_multi_agent_v4_segmented,
            )

            result = run_multi_agent_v4_segmented(
                user_message=req.message,
                base_env=env,
                segments=req.segments,
                base_drone_configs=drone_configs,
            )
            runtime_ms = int((time.time() - t0) * 1000)
            _ = runtime_ms

            # Build trace_events from coordinator format → AgentTraceEvent format
            raw_trace_events = result.get("trace_events", [])
            trace_events = _build_trace_events(
                trace=result.get("trace", {}),
                allocations=result.get("allocation", {}),
                allocation_strategy=result.get("allocation_strategy", "geographic"),
                routes_for_ui=result.get("routes", {}),
                trajectories=result.get("trajectories", {}),
                total_points=result.get("total_points", 0),
                total_fuel=result.get("total_fuel", 0.0),
                coordinator_trace_events=raw_trace_events,
            )

            return AgentChatResponse(
                reply=result.get("response", "Segmented mission solved."),
                routes=result.get("routes"),
                trajectories=result.get("trajectories"),
                points=result.get("total_points", 0),
                fuel=result.get("total_fuel", 0.0),
                allocations=result.get("allocations"),
                allocation_strategy=result.get("allocation_strategy"),
                route_distances=result.get("route_distances"),
                segment_results=result.get("segment_results"),
                mission_id=mission_id or new_mission_id(),
                trace=result.get("trace"),
                trace_events=trace_events,  # type: ignore[arg-type]
            )

        # -------------------------------
        # 2. Pre-compute SAM-aware distance matrix
        # -------------------------------
        # This ensures cache lookup happens ONCE before v4,
        # and gives us env_hash, routing_model_hash, distance_matrix_id
        sam_matrix: Optional[Dict[str, Any]] = None
        env_hash: Optional[str] = None
        routing_model_hash: Optional[str] = None
        distance_matrix_id: Optional[str] = None

        sams = env.get("sams", [])
        if sams:
            try:
                print("[v4] Pre-computing SAM-aware distance matrix...", flush=True)
                sam_matrix = calculate_sam_aware_matrix(env, use_supabase_cache=True)
                env_hash = sam_matrix.get("env_hash")
                routing_model_hash = sam_matrix.get("routing_model_hash")
                distance_matrix_id = sam_matrix.get("distance_matrix_id")
                cache_hit = sam_matrix.get("cache_hit", False)
                print(
                    f"[v4] Matrix ready: env_hash={env_hash[:8] if env_hash else 'N/A'}..., "
                    f"matrix_id={distance_matrix_id[:8] if distance_matrix_id else 'N/A'}..., "
                    f"cache_hit={cache_hit}",
                    flush=True,
                )
            except Exception as e:
                print(f"[v4] Warning: SAM matrix pre-compute failed: {e}", flush=True)

        # -------------------------------
        # 3. Create agent_run row BEFORE calling v4 (Supabase-first)
        # -------------------------------
        agent_run_id = create_agent_run(
            request_text=req.message,
            agent_version="v4",
            mode="agentic",
        )

        # -------------------------------
        # 4. Call the v4 multi-agent planner (with Mission Planner orchestration)
        # -------------------------------
        result = run_multi_agent_v4(
            user_message=req.message,
            environment=env,
            drone_configs=drone_configs,
            sam_matrix=sam_matrix,  # Pass pre-computed matrix with metadata
            existing_solution=existing_solution,
            llm_config=req.llm_config,  # Pass LLM provider configuration
        )

        if not isinstance(result, dict):
            raise RuntimeError(f"run_multi_agent_v4 returned non-dict: {type(result)}")

        runtime_ms = int((time.time() - t0) * 1000)

        # -------------------------------
        # 4. Extract outputs from result
        # -------------------------------
        raw_routes = result.get("routes") or {}
        trajectories = result.get("trajectories") or {}
        allocations = result.get("allocation") or {}
        allocation_strategy = result.get("allocation_strategy", "unknown")

        # Extract Decision Trace v1 fields (if present)
        trace = result.get("trace", {})
        optimizer_steps = result.get("optimizer_steps", [])
        solver_type = result.get("solver_type")
        solver_runtime_ms = result.get("solver_runtime_ms")
        is_valid = result.get("valid", True)

        total_points = result.get("total_points", 0)
        total_fuel = result.get("total_fuel", 0.0)

        # Build routes for UI + per-drone SAM-aware distances
        routes_for_ui: Dict[str, List[str]] = {}
        route_distances: Dict[str, float] = {}
        for did, route_info in raw_routes.items():
            did_str = str(did)
            if isinstance(route_info, dict):
                routes_for_ui[did_str] = route_info.get("route", []) or []
                route_distances[did_str] = float(route_info.get("distance", 0.0) or 0.0)
            elif isinstance(route_info, list):
                routes_for_ui[did_str] = route_info
            else:
                routes_for_ui[did_str] = []

        # Legacy single route (D1) for older UI helpers
        legacy_route = routes_for_ui.get("1")

        # -------------------------------
        # 5. Update agent_run with results (after v4 completes)
        # -------------------------------
        if agent_run_id and raw_routes:
            # Build summary per-drone
            summary = {}
            for did, route_info in raw_routes.items():
                if isinstance(route_info, dict):
                    summary[str(did)] = {
                        "route": route_info.get("route", []),
                        "distance": route_info.get("distance", 0.0),
                        "points": route_info.get("points", 0),
                    }

            update_agent_run(
                agent_run_id,
                total_points=total_points,
                total_fuel_used=total_fuel,
                runtime_ms=runtime_ms,
                is_valid=is_valid,
                routes=raw_routes,
                summary=summary,
                solver_type=solver_type,
                solver_runtime_ms=solver_runtime_ms,
                # Distance matrix references for reproducibility
                env_hash=env_hash,
                routing_model_hash=routing_model_hash,
                distance_matrix_id=distance_matrix_id,
            )

            # -------------------------------
            # 6. Insert agent_trace (1:1 with run)
            # -------------------------------
            if trace:
                create_agent_trace(agent_run_id, trace)

            # -------------------------------
            # 7. Insert optimizer steps (if any)
            # -------------------------------
            for step in optimizer_steps:
                log_optimizer_step(
                    agent_run_id=agent_run_id,
                    step_index=step.get("step_index", 0),
                    operator=step.get("operator", "unknown"),
                    before_routes=step.get("before_routes", {}),
                    after_routes=step.get("after_routes", {}),
                    delta=step.get("delta", {}),
                    accepted=step.get("accepted", True),
                    notes=step.get("notes"),
                    validation_status=step.get("validation_status", "unknown"),
                    validation_details=step.get("validation_details", {}),
                )

        # -------------------------------
        # 8. Persist/refresh mission state in memory store
        # -------------------------------
        if raw_routes:
            if not mission_id:
                mission_id = new_mission_id()
                print(f"[v4] Creating new mission {mission_id}", flush=True)
            else:
                print(f"[v4] Updating mission {mission_id}", flush=True)

            upsert_mission(
                mission_id,
                env=env,
                drone_configs=drone_configs,
                env_hash=compute_env_hash(env),
                cfg_hash=compute_cfg_hash(drone_configs),
                routes=raw_routes,
                allocation=allocations,
                total_points=total_points,
                total_fuel=total_fuel,
            )

        # -------------------------------
        # 9. Build trace_events for Agents Monitor tab
        # -------------------------------
        trace = trace or {}
        # Get structured coordinator trace_events if available
        coordinator_trace_events = result.get("coordinator_trace_events", [])
        trace_events = _build_trace_events(
            trace=trace,
            allocations=allocations,
            allocation_strategy=allocation_strategy,
            routes_for_ui=routes_for_ui,
            trajectories=trajectories,
            total_points=total_points,
            total_fuel=total_fuel,
            coordinator_trace_events=coordinator_trace_events,
        )

        # -------------------------------
        # 10. Build and return response
        # -------------------------------
        return AgentChatResponse(
            reply=result.get("response", "(No v4 agent reply)"),
            route=legacy_route,
            routes=routes_for_ui,
            trajectories=trajectories,
            points=total_points,
            fuel=total_fuel,
            allocations=allocations,
            allocation_strategy=allocation_strategy,
            route_distances=route_distances,
            mission_id=mission_id,
            trace=trace,
            trace_events=trace_events,  # type: ignore[arg-type]
        )

    except Exception as e:
        safe_error = _safe_agent_error(e)
        print(f"V4 agent request failed: {type(e).__name__}", flush=True)
        return AgentChatResponse(
            reply=safe_error,
            route=None,
            routes=None,
            trajectories=None,
            points=None,
            fuel=None,
            allocations=None,
            allocation_strategy=None,
            mission_id=None,
            trace={"error": safe_error},
            trace_events=[
                {"t": "error", "msg": "Agent error", "data": {"error": safe_error}}  # type: ignore[list-item]
            ],
        )


@router.post("/api/agents/reason", response_model=ReasoningAgentResponse)
async def reasoning_agent_endpoint(req: ReasoningAgentRequest):
    """
    Reasoning Agent endpoint - a single intelligent agent that reasons and uses tools.

    This agent can handle any mission planning request by:
    1. Understanding the user's command
    2. Using tools to explore options (solve routes, compare fuel costs, etc.)
    3. Reasoning step by step to find the optimal solution
    4. Explaining its reasoning when asked
    """
    print(">>> /api/agents/reason (reasoning agent) HIT <<<", flush=True)

    try:
        from server.agents.reasoning_agent import handle_reasoning_request

        mission_id = req.mission_id or new_mission_id()
        drone_configs = req.drone_configs or req.env.get("drone_configs", {})
        time_sync = _merge_time_sync_options(req.time_sync, req.user_request)
        result = handle_reasoning_request(
            {
                "user_request": req.user_request,
                "environment": req.env,
                "drone_configs": drone_configs,
                "distance_matrix": req.distance_matrix,
                "time_sync": time_sync,
                "mission_id": mission_id,
                "is_continue_from_cut": req.is_continue_from_cut,
                "visited_targets": req.visited_targets,
                "repair_routes": req.repair_routes,
                "current_routes": req.current_routes,
                "llm_config": (
                    req.llm_config.model_dump(exclude_none=True)
                    if req.llm_config is not None
                    else None
                ),
            }
        )
        result, distance_matrix = _apply_time_sync_to_agent_result(
            result=result,
            env=req.env,
            drone_configs=drone_configs,
            distance_matrix=result.get("distance_matrix") or req.distance_matrix,
            time_sync=time_sync,
        )

        return ReasoningAgentResponse(
            response=result.get("response", ""),
            reasoning=result.get("reasoning", []),
            iterations=result.get("iterations", 0),
            success=True,
            routes=result.get("routes"),  # Include solved routes for UI rendering
            routes_changed=result.get("routes_changed", True),
            solution_kind=("continuation" if req.is_continue_from_cut else None),
            mission_id=mission_id,
            distance_matrix=distance_matrix,
            time_sync=result.get("time_sync"),
            wrapped_polygons=result.get("wrapped_polygons"),
            drone_configs=result.get("drone_configs"),
        )

    except Exception as e:
        safe_error = _safe_agent_error(e)
        print(f"Reasoning agent request failed: {type(e).__name__}", flush=True)
        return ReasoningAgentResponse(
            response=safe_error,
            reasoning=[],
            iterations=0,
            success=False,
            error=safe_error,
            mission_id=req.mission_id,
        )


@router.post("/api/agents/compare")
async def compare_agents(req: AgentCompareRequest):
    """
    Run all three agents (reasoning + LangGraph v4 + CrewAI) on the same input
    and return side-by-side metrics for comparison.
    """
    print(">>> /api/agents/compare HIT <<<", flush=True)

    from server.agents.agent_metrics import (
        AgentRunMetrics,
        ComparisonResult,
        run_crewai_agent_with_metrics,
        run_gnn_with_metrics,
        run_langgraph_v4_with_metrics,
        run_reasoning_agent_with_metrics,
        run_solver_control_with_metrics,
    )

    env = req.env
    drone_configs = req.drone_configs or env.get("drone_configs", {})

    # Pre-compute SAM-aware distance matrix (shared by all agents)
    sam_matrix = None
    sams = env.get("sams", [])
    if sams:
        try:
            sam_matrix = calculate_sam_aware_matrix(env, use_supabase_cache=True)
        except Exception as e:
            print(f"[compare] SAM matrix failed: {e}", flush=True)

    env_summary = {
        "airports": len(env.get("airports", [])),
        "targets": len(env.get("targets", [])),
        "sams": len(env.get("sams", [])),
        "drones_enabled": sum(
            1
            for c in drone_configs.values()
            if isinstance(c, dict) and c.get("enabled", True)
        ),
    }

    def _log(label: str, m) -> None:
        print(
            f"[compare] {label}: {m.latency_s}s, points={m.total_points}, "
            f"coverage={m.target_coverage}" + (f", ERROR={m.error}" if m.error else ""),
            flush=True,
        )

    def _skipped(agent_type: str) -> AgentRunMetrics:
        """A not-run entrant. Marked with an error so it can never win a category."""
        return AgentRunMetrics(agent_type=agent_type, error="skipped")

    # Run all agents sequentially (they share global state)
    if req.include_llm_agents:
        print("[compare] Running reasoning agent...", flush=True)
        reasoning_metrics = run_reasoning_agent_with_metrics(
            user_request=req.message,
            env=env,
            drone_configs=drone_configs,
            distance_matrix=sam_matrix,
        )
        _log("Reasoning", reasoning_metrics)

        print("[compare] Running LangGraph v4...", flush=True)
        v4_metrics = run_langgraph_v4_with_metrics(
            user_message=req.message,
            env=env,
            drone_configs=drone_configs,
            sam_matrix=sam_matrix,
            llm_config=req.llm_config,
        )
        _log("LangGraph v4", v4_metrics)

        print("[compare] Running CrewAI...", flush=True)
        crewai_metrics = run_crewai_agent_with_metrics(
            user_request=req.message,
            env=env,
            drone_configs=drone_configs,
            distance_matrix=sam_matrix,
            llm_config=req.llm_config,
        )
        _log("CrewAI", crewai_metrics)
    else:
        print("[compare] Skipping LLM agents (include_llm_agents=false)", flush=True)
        reasoning_metrics = _skipped("reasoning")
        v4_metrics = _skipped("langgraph_v4")
        crewai_metrics = _skipped("crewai")

    gnn_metrics = None
    if req.include_gnn:
        print("[compare] Running GNN planner (exact-solver routing)...", flush=True)
        gnn_metrics = run_gnn_with_metrics(
            env=env,
            drone_configs=drone_configs,
            model_path=req.gnn_model_path,
            use_solver=True,
        )
        _log("GNN", gnn_metrics)

    gnn_decoder_metrics = None
    if req.include_gnn_decoder:
        print("[compare] Running GNN planner (learned decoder)...", flush=True)
        gnn_decoder_metrics = run_gnn_with_metrics(
            env=env,
            drone_configs=drone_configs,
            model_path=req.gnn_model_path,
            route_model_path=req.gnn_route_model_path,
            use_solver=False,
        )
        _log("GNN decoder", gnn_decoder_metrics)

    solver_control_metrics = None
    if req.include_solver_control:
        print("[compare] Running deterministic solver control...", flush=True)
        solver_control_metrics = run_solver_control_with_metrics(
            env=env,
            drone_configs=drone_configs,
            allocation_strategy=req.allocation_strategy,
        )
        _log("Solver control", solver_control_metrics)

    comparison = ComparisonResult(
        request=req.message,
        env_summary=env_summary,
        reasoning=reasoning_metrics,
        langgraph_v4=v4_metrics,
        crewai=crewai_metrics,
        gnn=gnn_metrics,
        gnn_decoder=gnn_decoder_metrics,
        solver_control=solver_control_metrics,
    )
    comparison.compute_winners()

    return comparison.to_dict()


@router.get("/api/agent/memory")
async def get_memories():
    """Get all agent memories."""
    memories = load_memory()
    return {"success": True, "memories": memories, "count": len(memories)}


@router.post("/api/agent/memory")
async def add_agent_memory(req: AddMemoryRequest):
    """Add a new memory entry."""
    entry = add_memory(req.content, req.category)
    return {"success": True, "memory": entry}


@router.delete("/api/agent/memory/{memory_id}")
async def delete_agent_memory(memory_id: int):
    """Delete a specific memory by ID."""
    success = delete_memory(memory_id)
    if success:
        return {"success": True, "message": f"Memory {memory_id} deleted"}
    return {"success": False, "message": f"Memory {memory_id} not found"}


@router.delete("/api/agent/memory")
async def clear_agent_memory():
    """Clear all memories."""
    count = clear_memory()
    return {"success": True, "message": f"Cleared {count} memories"}
