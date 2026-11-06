"""
ISR Multi-Agent Mission Planning System (v4) - REASONING AGENTS

A TRUE reasoning-based multi-agent system where each agent:
1. Receives full context about the mission
2. REASONS about the problem before deciding
3. Explains WHY decisions were made
4. Can answer questions about configurations and trade-offs

Core Principles:
- DEFAULT: Maximize priority points with minimum fuel (optimize)
- COMMANDS: Direct orders from commander override everything
- CONSTRAINTS: Drone configs (accessible targets, fuel) are treated as commands
- REASONING: Explain allocation changes and trade-offs
- SUGGESTIONS: Proactively identify improvements

Architecture:
- STRATEGIST: Analyzes request, determines mode (optimize vs command), identifies constraints
- MISSION_PLANNER: Orchestrates complex missions (segmentation, constraints, multi-phase)
- ALLOCATOR: Reasons about target-drone assignments with explanations
- ROUTE_OPTIMIZER: Computes routes, checks feasibility, reports trade-offs
- CRITIC: Reviews solution quality, suggests improvements
- RESPONDER: Formulates final answer with concise reasoning
"""

import sys

# Import solver components
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from ..solver.sam_distance_matrix import calculate_sam_aware_matrix
from ..solver.trajectory_planner import ISRTrajectoryPlanner

# LLM Factory for provider-agnostic model selection
from .drone_config import resolve_fuel_budget
from .v4.metrics import compute_mission_metrics
from .v4.state import MissionState
from .v4.workflow import build_reasoning_workflow

# Import mission orchestration tools
# from .mission_orchestration_tools import (
#     MissionOrchestrator,
#     get_mission_summary,
#     get_unvisited_targets,
#     solve_full_mission,
#     solve_single_drone,
#     apply_insert_missed,
#     apply_swap_closer,
#     create_segment_at_checkpoint,
#     freeze_segment_at_checkpoint,
#     continue_from_checkpoint,
#     add_fuel_reserve_constraint,
#     add_loiter_constraint,
# )

# Import policy rules from mission_ledger for Learning v1
try:
    from ..database.mission_ledger import get_active_policy_rules
except ImportError:
    get_active_policy_rules = None
    print("⚠️ [v4] mission_ledger not available, policy rules disabled")

from path_planning_core.latlng_utils import plane_dist

# Import constraint parser for sequencing hints
from ..memory.constraint_parser import parse_constraints

# Import Coordinator v4 for deterministic pre-pass
from .coordinator_v4 import CoordinatorDecision, run_coordinator

# Load environment variables
load_dotenv()


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================


def run_multi_agent_v4(
    user_message: str,
    environment: Dict[str, Any],
    drone_configs: Dict[str, Any],
    sam_matrix: Optional[Dict[str, Any]] = None,
    existing_solution: Optional[Dict[str, Any]] = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run the v4 reasoning-based multi-agent system.

    Args:
        user_message: The user's request
        environment: Environment data (airports, targets, sams)
        drone_configs: Drone configurations from the UI
        sam_matrix: Pre-computed SAM-aware distance matrix with metadata
                   (env_hash, routing_model_hash, distance_matrix_id)
        existing_solution: Previous routes/allocation for continuation
        llm_config: LLM configuration dict with provider, model, api_key.
                   If not provided, falls back to env-driven provider config.

    Returns:
        Result dictionary with response, routes, trajectories, etc.
        Includes trace.env with matrix metadata for reproducibility.
    """
    print("### V4: run_multi_agent_v4 (isr_agent_multi_v4.py) CALLED ###", flush=True)
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"[v4] Processing: {user_message[:80]}...", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    sys.stderr.flush()

    # ------------------------------------------------------------------
    # 0) Load active policy rules (Learning v1)
    # ------------------------------------------------------------------
    policy_rules: List[Dict[str, Any]] = []
    if get_active_policy_rules is not None:
        try:
            policy_rules = get_active_policy_rules(mode="agentic")
            if policy_rules:
                print(
                    f"📜 [v4] Loaded {len(policy_rules)} active policy rules",
                    file=sys.stderr,
                )
        except Exception as e:
            print(f"⚠️ [v4] Failed to load policy rules: {e}", file=sys.stderr)

    # ------------------------------------------------------------------
    # 1) Normalize inputs
    # ------------------------------------------------------------------
    # Note: mission-wide cruise altitude (cruise_altitude_km) is handled
    # entirely inside calculate_sam_aware_matrix() (server/solver/
    # sam_distance_matrix.py) — the single choke point every distance-matrix
    # computation in this codebase funnels through, whether called from here,
    # reasoning_agent.py, the manual-solve API, or the /api/agents/compare
    # benchmark harness. Do NOT add a shrink call here too — see that
    # function's comment for why applying it at more than one point risks
    # double-shrinking.
    #
    # Project lat/lng to local-km if needed (env may have lat/lng from frontend)
    from ..solver.solver_bridge import _project_env_to_local_km

    env_projected, ref_lat, ref_lng = _project_env_to_local_km(environment or {})
    env: Dict[str, Any] = env_projected

    # UI-level configs passed in, or from env if not provided
    ui_configs: Dict[str, Any] = drone_configs or env.get("drone_configs") or {}

    # Translate UI configs into what v4 expects:
    # UI:  enabled, fuel_budget, start_airport, end_airport, target_access:{a..e}
    # v4:  enabled, fuelBudget, home_airport, accessible_targets:['A','B',...]
    normalized_configs: Dict[str, Dict[str, Any]] = {}

    # Build set of real airport IDs (exclude synthetic starts like D1_START)
    real_airport_ids: set[str] = set()
    for a in env.get("airports", []):
        if not a.get("is_synthetic", False):
            aid = a.get("id", a.get("label", ""))
            if aid:
                real_airport_ids.add(str(aid))
    default_real_airport = next(iter(sorted(real_airport_ids)), "A1")

    for did, cfg in ui_configs.items():
        if cfg is None:
            continue

        # Make a shallow copy so we don't mutate the original
        nc: Dict[str, Any] = dict(cfg)

        # Ensure ID is a string like "1", "2", ...
        did_str = str(did)

        # Fuel budget: accept "fuelBudget", "fuel_budget", or "fuel_budget_km".
        # resolve_fuel_budget raises if none are present — silent defaults here
        # previously masked the benchmark harness's key-spelling bug for a full
        # 100-run batch.
        fuel_budget = resolve_fuel_budget(cfg, drone_id=did_str)
        # Normalize into both canonical keys so downstream reads (whichever
        # alias they happen to use) all see the same value.
        nc["fuelBudget"] = fuel_budget
        nc["fuel_budget"] = fuel_budget

        # start_id: may be synthetic (D1_START) for checkpoint replanning
        cfg_start = cfg.get("start_airport") or cfg.get("startAirport") or ""
        nc["start_id"] = str(cfg_start) if cfg_start else default_real_airport

        # home_airport: MUST be a real airport (never synthetic)
        # Used for endpoint candidates in best_end mode
        cfg_home = cfg.get("homeAirport") or cfg.get("home_airport") or ""
        if cfg_home and cfg_home in real_airport_ids:
            nc["home_airport"] = str(cfg_home)
        elif cfg_start and cfg_start in real_airport_ids:
            nc["home_airport"] = str(cfg_start)
        else:
            nc["home_airport"] = default_real_airport

        # end_airport: explicit end, "-" for flexible, or None
        cfg_end = cfg.get("end_airport") or cfg.get("endAirport") or ""
        if cfg_end == "-":
            nc["end_airport"] = "-"  # Flexible endpoint
        elif cfg_end:
            nc["end_airport"] = str(cfg_end)
        else:
            nc["end_airport"] = nc["home_airport"]  # Default to home

        # Accessible targets: build list from target_access dict if present
        if "accessible_targets" not in nc and "accessibleTargets" not in nc:
            ta = cfg.get("target_access")
            if isinstance(ta, dict):
                accessible = [str(k).upper() for k, v in ta.items() if v]
                nc["accessible_targets"] = accessible

        normalized_configs[did_str] = nc

    # ------------------------------------------------------------------
    # 2) Build / compute distance matrix (so the HK solver can run)
    # ------------------------------------------------------------------
    # Store matrix metadata for trace (Wiring Point C)
    matrix_env_hash: Optional[str] = None
    matrix_routing_model_hash: Optional[str] = None
    matrix_distance_matrix_id: Optional[str] = None
    matrix_cache_hit: Optional[bool] = None
    matrix_routing_model: Optional[Dict[str, Any]] = None
    excluded_targets: List[str] = []  # Targets inside SAM zones

    dist_matrix: Dict[str, Dict[str, float]]

    if sam_matrix and sam_matrix.get("matrix"):
        # Use pre-computed SAM matrix from main.py (preferred path)
        print("✅ [v4] Using pre-computed SAM matrix from caller", file=sys.stderr)
        labels = sam_matrix.get("labels", [])
        matrix_data = sam_matrix.get("matrix", [])
        dist_matrix = {}
        for i, from_id in enumerate(labels):
            dist_matrix[from_id] = {}
            for j, to_id in enumerate(labels):
                dist_matrix[from_id][to_id] = float(matrix_data[i][j])

        # Extract metadata from sam_matrix
        matrix_env_hash = sam_matrix.get("env_hash")
        matrix_routing_model_hash = sam_matrix.get("routing_model_hash")
        matrix_distance_matrix_id = sam_matrix.get("distance_matrix_id")
        matrix_cache_hit = sam_matrix.get("cache_hit")
        matrix_routing_model = sam_matrix.get("routing_model")
        excluded_targets = sam_matrix.get("excluded_targets", [])

        if excluded_targets:
            print(
                f"🚫 [v4] Excluded targets (inside SAM zones): {excluded_targets}",
                file=sys.stderr,
            )
    else:
        # Fallback: compute matrix locally (for backwards compatibility)
        sams = env.get("sams", [])
        try:
            if sams:
                print(
                    "🎯 [v4] Computing SAM-aware distance matrix (fallback)...",
                    file=sys.stderr,
                )
                dist_data = calculate_sam_aware_matrix(env)
                labels = dist_data.get("labels", [])
                matrix_data = dist_data.get("matrix", [])
                dist_matrix = {}
                for i, from_id in enumerate(labels):
                    dist_matrix[from_id] = {}
                    for j, to_id in enumerate(labels):
                        dist_matrix[from_id][to_id] = float(matrix_data[i][j])

                # Extract metadata from computed matrix
                matrix_env_hash = dist_data.get("env_hash")
                matrix_routing_model_hash = dist_data.get("routing_model_hash")
                matrix_distance_matrix_id = dist_data.get("distance_matrix_id")
                matrix_cache_hit = dist_data.get("cache_hit")
                matrix_routing_model = dist_data.get("routing_model")
                excluded_targets = dist_data.get("excluded_targets", [])

                if excluded_targets:
                    print(
                        f"🚫 [v4] Excluded targets (inside SAM zones): {excluded_targets}",
                        file=sys.stderr,
                    )
            else:
                print("📏 [v4] Computing Euclidean distance matrix...", file=sys.stderr)
                # Simple Euclidean fallback (no SAMs)
                waypoints: Dict[str, Tuple[float, float]] = {}

                for airport in env.get("airports", []):
                    aid = airport.get("id", airport.get("label", "A1"))
                    waypoints[str(aid)] = (
                        float(airport.get("x", 0.0)),
                        float(airport.get("y", 0.0)),
                    )

                for target in env.get("targets", []):
                    tid = target.get("id", target.get("label", "T?"))
                    waypoints[str(tid)] = (
                        float(target.get("x", 0.0)),
                        float(target.get("y", 0.0)),
                    )

                dist_matrix = {}
                for from_id, (x1, y1) in waypoints.items():
                    dist_matrix[from_id] = {}
                    for to_id, (x2, y2) in waypoints.items():
                        if from_id == to_id:
                            dist_matrix[from_id][to_id] = 0.0
                        else:
                            dist = plane_dist((x1, y1), (x2, y2))
                            dist_matrix[from_id][to_id] = float(dist)
        except Exception as e:
            print(f"⚠️ [v4] Failed to compute distance matrix: {e}", file=sys.stderr)
            dist_matrix = {}

    # ------------------------------------------------------------------
    # 3) Run Coordinator pre-pass (deterministic policy/guardrail layer)
    # ------------------------------------------------------------------
    coordinator_decision: CoordinatorDecision = run_coordinator(
        user_message=user_message,
        environment=env,
        drone_configs=normalized_configs,
        sam_matrix=sam_matrix,
        ui_state=None,  # Could be passed from main.py if available
        preferences=None,  # Could be passed from main.py if available
        debug=True,
    )

    print(
        f"🎯 [v4] Coordinator decision: intent={coordinator_decision.intent}, "
        f"confidence={coordinator_decision.confidence:.2f}",
        file=sys.stderr,
    )

    # If Coordinator found validation errors, return early
    if not coordinator_decision.is_valid:
        print(
            f"❌ [v4] Coordinator validation failed: {coordinator_decision.errors}",
            file=sys.stderr,
        )
        return {
            "response": f"Validation error: {'; '.join(coordinator_decision.errors)}",
            "routes": {},
            "total_points": 0,
            "total_fuel": 0.0,
            "trajectories": {},
            "allocation": {},
            "allocation_strategy": "unknown",
            "suggestions": [],
            "strategy_analysis": None,
            "allocation_reasoning": None,
            "route_analysis": None,
            "critic_review": None,
            "mission_metrics": {},
            "solver_type": "unknown",
            "solver_runtime_ms": 0,
            "valid": False,
            "trace": coordinator_decision.trace,
            "optimizer_steps": [],
        }

    # Extract policy decisions and drone contracts
    coordinator_policy = coordinator_decision.policy
    coordinator_guardrails = coordinator_decision.guardrails
    coordinator_drone_contracts = coordinator_decision.drone_contracts
    coordinator_synthetic_starts = coordinator_decision.synthetic_starts
    coordinator_constraints = (
        coordinator_decision.constraints
    )  # includes required_targets

    # ------------------------------------------------------------------
    # 3b) Populate env.synthetic_starts from decision (authoritative data)
    #     Coordinator.synthetic_starts is the single source of truth for
    #     synthetic start coordinates - NOT guardrails (which are for
    #     validation outcomes only)
    # ------------------------------------------------------------------
    if coordinator_synthetic_starts:
        env["synthetic_starts"] = coordinator_synthetic_starts
        print(
            f"📍 [v4] Populated env.synthetic_starts: {list(coordinator_synthetic_starts.keys())}",
            file=sys.stderr,
        )

    # ------------------------------------------------------------------
    # 3c) Parse constraint program from user message (sequencing hints)
    # ------------------------------------------------------------------
    constraint_parse_result = parse_constraints(
        user_message=user_message,
        environment=env,
        drone_configs=normalized_configs,
    )
    sequencing_hints = constraint_parse_result.program.sequencing_hints
    constraint_program = (
        constraint_parse_result.program.to_dict()
        if not constraint_parse_result.program.is_empty() or sequencing_hints
        else None
    )

    if sequencing_hints:
        print(f"🎯 [v4] Parsed sequencing hints: {sequencing_hints}", file=sys.stderr)
    if constraint_parse_result.program.constraints:
        print(
            f"📋 [v4] Parsed {len(constraint_parse_result.program.constraints)} constraint operations",
            file=sys.stderr,
        )

    # ------------------------------------------------------------------
    # 3d) RAG Phase 2: retrieve similar past missions for advisory context.
    # Computed ONCE here (not per-node) since it needs its own embedding API
    # call — same "parse once upstream, thread the formatted result through
    # state" pattern as constraint_program/sequencing_hints above. Fails
    # soft to "" (server/database/mission_retrieval.py never raises), so a
    # missing migration/API key/Supabase config just means no similar-past-
    # mission context this run — never blocks the actual solve.
    # ------------------------------------------------------------------
    try:
        from ..database.mission_retrieval import (
            find_similar_runs,
            format_similar_runs_for_prompt,
        )

        similar_runs = find_similar_runs(user_message)
        similar_missions_context = format_similar_runs_for_prompt(similar_runs)
        if similar_missions_context:
            print(
                f"🔍 [v4] Retrieved {len(similar_runs)} similar past mission(s) "
                "for advisory context",
                file=sys.stderr,
            )
    except (
        Exception
    ) as e:  # pragma: no cover — defensive, retrieval must never break a solve
        print(
            f"⚠️ [v4] Similar-mission retrieval failed (non-fatal): {e}",
            file=sys.stderr,
        )
        similar_missions_context = ""

    # ------------------------------------------------------------------
    # 4) Build initial mission state for the v4 workflow
    # ------------------------------------------------------------------
    initial_state: MissionState = {
        "messages": [HumanMessage(content=user_message)],
        "environment": env,
        "drone_configs": normalized_configs,
        "distance_matrix": dist_matrix,
        "excluded_targets": excluded_targets,  # Targets inside SAM zones - do NOT allocate
        "user_request": user_message,
        "request_type": "optimize",  # default; strategist can refine
        "commands": None,
        # Coordinator v4 injected fields
        "intent": coordinator_decision.intent,
        "policy": {
            **coordinator_policy,
            "required_targets": coordinator_constraints.get("required_targets", []),
        },
        "guardrails": coordinator_guardrails,
        "drone_contracts": coordinator_drone_contracts,  # Per-drone start/home/endpoint contracts
        "allocation_strategy": coordinator_policy.get(
            "allocation_strategy", "efficient"
        ),
        # Constraint program fields
        "constraint_program": constraint_program,
        "sequencing_hints": sequencing_hints,
        # RAG Phase 2: pre-formatted advisory context, "" when no similar
        # past mission was found (or retrieval failed/unconfigured).
        "similar_missions_context": similar_missions_context,
        # Agent reasoning outputs
        "strategy_analysis": None,
        "allocation_reasoning": None,
        "route_analysis": None,
        "critic_review": None,
        "suggestions": None,
        "allocation": None,
        "routes": None,
        "solver_type": None,
        "solver_runtime_ms": None,
        # Algorithm tracking (Algorithm Awareness)
        "algorithms_used": [],  # Will be populated by agents
        "algorithm_overrides": coordinator_policy.get("algorithm_overrides"),
        "asks_about_algorithms": coordinator_policy.get("asks_about_algorithms", False),
        # LLM configuration (provider-agnostic)
        "llm_config": llm_config,
        "policy_rules": policy_rules,  # Learning v1: loaded rules for enforcement
        "final_response": None,
        "error": None,
        "node_trace": [],  # Accumulated by each graph node via operator.add
        "llm_calls": 0,  # Accumulated by each LLM-calling node via operator.add
    }

    # If we are continuing an existing mission (Q&A only), seed the state with
    # previously computed routes/allocation so agents can reason about them.
    if existing_solution:
        prev_routes = existing_solution.get("routes")
        if prev_routes:
            initial_state["routes"] = prev_routes

        prev_allocation = existing_solution.get("allocation")
        if prev_allocation:
            initial_state["allocation"] = prev_allocation

    # ------------------------------------------------------------------
    # 5) Run the multi-agent workflow
    # ------------------------------------------------------------------
    workflow = build_reasoning_workflow()

    try:
        final_state = workflow.invoke(initial_state)
    except Exception as e:
        import traceback

        print(f"❌ [v4] Workflow error: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return {
            "response": f"Error processing request: {str(e)}",
            "routes": {},
            "total_points": 0,
            "total_fuel": 0.0,
            "trajectories": {},
            "allocation": {},
            "allocation_strategy": "unknown",
            "suggestions": [],
            "strategy_analysis": None,
            "allocation_reasoning": None,
            "route_analysis": None,
            "critic_review": None,
            "mission_metrics": {},
            # Decision Trace v1 fields
            "solver_type": "unknown",
            "solver_runtime_ms": 0,
            "valid": False,
            "trace": {},
            "optimizer_steps": [],
        }

    # Extract routes + allocation from agent workflow
    routes = final_state.get("routes") or {}
    allocation = final_state.get("allocation") or {}

    # ---------------------------------------------------------------
    # 5) Compute mission metrics (fuel, margins, points, unvisited)
    # ---------------------------------------------------------------
    mission_metrics = {}
    if routes:
        try:
            mission_metrics = compute_mission_metrics(
                env=env,
                routes=routes,
                allocations=allocation,
                drone_configs=normalized_configs,
            )
        except Exception as e:
            print(f"[v4] Error computing mission metrics: {e}", flush=True)
            mission_metrics = {}

    # Attach metrics for Strategist / Responder
    final_state["mission_metrics"] = mission_metrics
    final_state["total_points"] = mission_metrics.get("total_points")
    final_state["total_fuel"] = mission_metrics.get("total_fuel")

    # ---------------------------------------------------------------
    # 6) Extract routes & compute trajectories (existing logic)
    # ---------------------------------------------------------------

    routes = final_state.get("routes", {}) or {}
    response_text = final_state.get("final_response", "No response generated")

    total_points = 0
    total_fuel = 0.0
    trajectories: Dict[str, List[List[float]]] = {}

    # Build waypoint index
    waypoint_positions: Dict[str, List[float]] = {}
    for airport in env.get("airports", []):
        aid = str(airport.get("id", airport.get("label")))
        waypoint_positions[aid] = [
            float(airport.get("x", 0.0)),
            float(airport.get("y", 0.0)),
        ]

    for target in env.get("targets", []):
        tid = str(target.get("id", target.get("label")))
        waypoint_positions[tid] = [
            float(target.get("x", 0.0)),
            float(target.get("y", 0.0)),
        ]

    # Add synthetic starts from environment (authoritative data, populated in step 3b)
    env_synthetic_starts = env.get("synthetic_starts", {})
    for synth_id, synth_coords in env_synthetic_starts.items():
        if synth_id not in waypoint_positions:
            waypoint_positions[synth_id] = [
                float(synth_coords.get("x", 0.0)),
                float(synth_coords.get("y", 0.0)),
            ]
            print(
                f"📍 [v4] Added synthetic start to waypoints: {synth_id}",
                file=sys.stderr,
            )

    # Also check routes for synthetic starts that may not be in guardrails
    for did, route_data in routes.items():
        if isinstance(route_data, dict):
            start_id = route_data.get("start_id")
            if (
                start_id
                and start_id not in waypoint_positions
                and start_id.endswith("_START")
            ):
                # Try to get coords from sam_matrix waypoints
                if sam_matrix:
                    for wp in sam_matrix.get("waypoints", []):
                        if wp.get("id") == start_id:
                            waypoint_positions[start_id] = [
                                float(wp.get("x", 0.0)),
                                float(wp.get("y", 0.0)),
                            ]
                            print(
                                f"📍 [v4] Added synthetic start from sam_matrix: {start_id}",
                                file=sys.stderr,
                            )
                            break

    sams = env.get("sams", []) or []
    trajectory_planner: Optional[ISRTrajectoryPlanner] = None

    if sams:
        try:
            trajectory_planner = ISRTrajectoryPlanner(sams)
        except Exception as e:
            print(
                f"⚠️ [v4] Failed to initialize ISRTrajectoryPlanner: {e}",
                file=sys.stderr,
            )
            trajectory_planner = None

    # Generate trajectories drone-by-drone
    for did, route_data in routes.items():
        if not isinstance(route_data, dict):
            continue

        points = route_data.get("total_points", 0) or 0
        distance = route_data.get("distance", 0.0) or 0.0

        total_points += points
        total_fuel += distance

        route = route_data.get("route", []) or []
        route_ids = [str(wp_id) for wp_id in route]

        def _straight_line_traj() -> List[List[float]]:
            traj = []
            for wp_id in route_ids:
                pos = waypoint_positions.get(wp_id)
                if pos is not None:
                    traj.append(pos)
            return traj

        if trajectory_planner and waypoint_positions:
            try:
                traj_pts = trajectory_planner.generate_trajectory(
                    route_ids,
                    waypoint_positions,
                    drone_id=str(did),
                )
            except Exception as e:
                print(
                    f"⚠️ [v4] Trajectory planner error for D{did}: {e}", file=sys.stderr
                )
                traj_pts = _straight_line_traj()
        else:
            traj_pts = _straight_line_traj()

        trajectories[str(did)] = traj_pts

    # ---------------------------------------------------------------
    # 7) Build Decision Trace v1 structure
    # ---------------------------------------------------------------
    solver_type = final_state.get("solver_type", "unknown")
    solver_runtime_ms = final_state.get("solver_runtime_ms", 0)

    # Determine if solution is valid (all routes within fuel budget)
    is_valid = True
    for did, route_data in routes.items():
        if isinstance(route_data, dict):
            distance = route_data.get("distance", 0.0)
            cfg = normalized_configs.get(str(did), {})
            fuel_budget = resolve_fuel_budget(cfg, drone_id=str(did))
            if distance > fuel_budget:
                is_valid = False
                break

    # Build the Decision Trace JSONB structure (Wiring Point C)
    trace = {
        # Coordinator v4 pre-pass decisions
        "coordinator": {
            "intent": {
                "name": coordinator_decision.intent,
                "confidence": coordinator_decision.confidence,
                "rules_hit": coordinator_decision.rules_hit,
            },
            "guards": coordinator_guardrails,
            "policy": coordinator_policy,
            "drone_contracts": coordinator_drone_contracts,
            "synthetic_starts": coordinator_synthetic_starts,  # Authoritative coordinate data
        },
        # Environment and matrix metadata for reproducibility
        "env": {
            "env_hash": matrix_env_hash,
            "routing_model_hash": matrix_routing_model_hash,
            "distance_matrix_id": matrix_distance_matrix_id,
            "cache_hit": matrix_cache_hit,
            "sam_mode": (
                matrix_routing_model.get("sam_mode", "hard_v1")
                if matrix_routing_model
                else None
            ),
            "routing_model": matrix_routing_model,
        },
        "eligibility": {},  # Which targets were eligible per drone
        "allocation": {
            "algorithm": final_state.get("allocation_strategy", "unknown"),
            "assignments": final_state.get("allocation", {}),
            "rationale": final_state.get("allocation_reasoning"),
        },
        "solver": {
            "type": solver_type,
            "runtime_ms": solver_runtime_ms,
        },
        # Total llm.invoke() calls across all graph nodes (sum via reducer in MissionState)
        "llm_calls": int(final_state.get("llm_calls", 0) or 0),
        "final_evidence": {},  # Per-drone route details
        "policy_rules_applied": (
            [
                {
                    "id": r.get("id"),
                    "title": r.get("title"),
                    "category": r.get("category"),
                }
                for r in policy_rules
            ]
            if policy_rules
            else []
        ),
    }

    # Populate eligibility and final_evidence from routes
    for did, route_data in routes.items():
        if isinstance(route_data, dict):
            trace["final_evidence"][str(did)] = {
                "waypoints": route_data.get("route", []),
                "fuel": route_data.get("distance", 0.0),
                "points": route_data.get("total_points", 0),
                "visited_targets": route_data.get("visited_targets", []),
            }

    # ---------------------------------------------------------------
    # 8) Return unified V4 result with Decision Trace v1 fields
    # ---------------------------------------------------------------
    # ---------------------------------------------------------------
    # 8a) Coordinator contract fields for the UI (intent/actions/policy)
    # ---------------------------------------------------------------
    actions: List[Dict[str, Any]] = []
    if coordinator_decision.explanation_only:
        actions.append({"type": "EXPLAIN"})
    else:
        actions.append(
            {
                "type": "ALLOCATE",
                "strategy": (
                    coordinator_decision.policy.get("allocation_strategy", "unknown")
                    if isinstance(coordinator_decision.policy, dict)
                    else "unknown"
                ),
            }
        )
        actions.append(
            {
                "type": "SOLVE",
                "solver": solver_type,
                "mode": (
                    coordinator_decision.policy.get("solver_mode", "default")
                    if isinstance(coordinator_decision.policy, dict)
                    else "default"
                ),
            }
        )
        actions.append({"type": "TRAJECTORY", "samAware": True})
        # Post-optimizer actions are conditional; we mark configured intent here
        actions.append({"type": "POST_OPT"})
        actions.append({"type": "VERIFY"})

    # Unproject trajectories from local-km back to lat/lng
    from path_planning_core.latlng_utils import local_xy_to_latlng

    from ..solver.solver_bridge import _unproject_solution_trajectories

    _unproject_solution_trajectories({"routes": routes}, ref_lat, ref_lng)
    # Also unproject the standalone trajectories dict
    for did, traj in trajectories.items():
        if traj and isinstance(traj, list):
            trajectories[did] = [
                (
                    [*local_xy_to_latlng(pt[0], pt[1], ref_lat, ref_lng)]
                    if isinstance(pt, (list, tuple)) and len(pt) >= 2
                    else pt
                )
                for pt in traj
            ]

    return {
        "response": response_text,
        "intent": coordinator_decision.intent,
        "actions": actions,
        "policy": coordinator_decision.policy,
        "constraints": coordinator_decision.constraints,
        "warnings": coordinator_decision.warnings,
        "errors": coordinator_decision.errors,
        "routes": routes,
        "total_points": mission_metrics.get("total_points", total_points),
        "total_fuel": mission_metrics.get("total_fuel", total_fuel),
        "trajectories": trajectories,
        "allocation": final_state.get("allocation", {}),
        "allocation_strategy": final_state.get("allocation_strategy", "unknown"),
        "suggestions": final_state.get("suggestions", []),
        "strategy_analysis": final_state.get("strategy_analysis"),
        "mission_plan": final_state.get("mission_plan"),
        "allocation_reasoning": final_state.get("allocation_reasoning"),
        "route_analysis": final_state.get("route_analysis"),
        "optimizer_analysis": final_state.get("optimizer_analysis"),
        "critic_review": final_state.get("critic_review"),
        "mission_metrics": mission_metrics,
        # Decision Trace v1 fields
        "solver_type": solver_type,
        "solver_runtime_ms": solver_runtime_ms,
        "valid": is_valid,
        "trace": trace,
        "optimizer_steps": [],  # TODO: populate from post_optimizer if used
        # Structured trace_events: coordinator + all graph node events
        "coordinator_trace_events": coordinator_decision.trace_events,
        "trace_events": coordinator_decision.trace_events
        + (final_state.get("node_trace") or []),
    }
