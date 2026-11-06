"""Agent performance metrics collection and comparison.

Wraps agent runs to capture latency, token usage, solution quality,
and other metrics for side-by-side comparison of different agent
architectures (single reasoning agent vs multi-agent LangGraph v4).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from server.agents.drone_config import resolve_fuel_budget


def _budget_or_zero(cfg: Mapping[str, Any]) -> float:
    """Read a drone's fuel budget, returning 0.0 when no budget alias is present.

    The compare tab tolerates partially-configured drones (e.g. a drone the user
    disabled mid-mission), so we silence the loud failure that resolve_fuel_budget
    raises and treat "no budget" as "not counted".
    """
    try:
        return resolve_fuel_budget(cfg)
    except ValueError:
        return 0.0


@dataclass
class AgentRunMetrics:
    agent_type: str  # "reasoning" or "langgraph_v4"
    latency_s: float = 0.0
    llm_calls: int = 0
    iterations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    # Solution quality
    total_points: int = 0
    total_fuel_km: float = 0.0
    total_budget_km: float = 0.0
    fuel_efficiency: float = 0.0  # fuel_used / budget (lower is better)
    targets_visited: int = 0
    targets_total: int = 0
    target_coverage: float = 0.0  # visited / total
    valid_solution: bool = False

    # Per-drone breakdown
    drone_metrics: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Route data (for rendering in UI)
    routes: Optional[Dict[str, Any]] = None
    trajectories: Optional[Dict[str, Any]] = None
    allocations: Optional[Dict[str, Any]] = None
    allocation_strategy: Optional[str] = None

    # Agent response text
    response_text: str = ""

    # Trace events (node-level execution trace for UI)
    trace_events: List[Dict[str, Any]] = field(default_factory=list)

    # Errors
    error: Optional[str] = None

    def to_dict(self, include_routes: bool = True) -> Dict[str, Any]:
        d = asdict(self)
        if not include_routes:
            d.pop("routes", None)
            d.pop("trajectories", None)
        return d


def extract_solution_metrics(
    routes: Dict[str, Any],
    drone_configs: Dict[str, Any],
    targets: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Extract quality metrics from a solved route set."""
    total_points = 0
    total_fuel = 0.0
    targets_visited = set()
    drone_metrics = {}
    all_within_budget = True
    any_drone_checked = False

    # Anchor the denominator on the full fleet config so fuel_efficiency stays
    # comparable across agents that emit different subsets of drones in `routes`.
    total_budget = sum(_budget_or_zero(cfg) for cfg in drone_configs.values())

    # Build priority lookup from targets
    priority_map = {str(t.get("id", "")): int(t.get("priority", 0)) for t in targets}

    for did, route_data in routes.items():
        did_str = str(did)
        if isinstance(route_data, dict):
            route = route_data.get("route", [])
            distance = float(route_data.get("distance", 0.0) or 0.0)
            refuel_ap = route_data.get("refuel_airport") or ""
            leg1 = float(route_data.get("leg1_distance", 0.0) or 0.0)
            leg2 = float(route_data.get("leg2_distance", 0.0) or 0.0)
        elif isinstance(route_data, list):
            route = route_data
            distance = 0.0
            refuel_ap = ""
            leg1 = leg2 = 0.0
        else:
            continue

        cfg = drone_configs.get(did_str, {})
        budget = _budget_or_zero(cfg)

        visited = [wp for wp in route if str(wp).startswith("T")]
        targets_visited.update(visited)
        points = sum(priority_map.get(wp, 0) for wp in visited)
        total_points += points
        total_fuel += distance

        # A drone is "within budget" if it has a non-empty route and didn't
        # overflow its tank. Empty routes don't count as violations but also
        # don't count as planned — tracked via any_drone_checked. Refueling
        # drones validate per-leg (the tank is refilled at the stop), not
        # against the trip total which would always exceed a single tank.
        drone_within_budget = True
        if budget > 0 and len(visited) > 0:
            any_drone_checked = True
            if refuel_ap:
                if leg1 > budget or leg2 > budget:
                    drone_within_budget = False
                    all_within_budget = False
            elif distance > budget:
                drone_within_budget = False
                all_within_budget = False

        drone_metrics[did_str] = {
            "route": route,
            "distance_km": round(distance, 2),
            "points": points,
            "budget_km": round(budget, 2),
            "fuel_pct": round(distance / budget * 100, 1) if budget > 0 else 0,
            "targets_visited": visited,
            "within_budget": drone_within_budget,
        }

    target_ids = {str(t.get("id", "")) for t in targets}
    total_targets = len(target_ids)

    # Valid iff at least one drone actually planned a route AND no drone exceeded its budget.
    # A routes dict where every drone has an empty route is NOT a valid solution.
    valid = any_drone_checked and all_within_budget

    return {
        "total_points": total_points,
        "total_fuel_km": round(total_fuel, 2),
        "total_budget_km": round(total_budget, 2),
        "fuel_efficiency": (
            round(total_fuel / total_budget, 3) if total_budget > 0 else 0
        ),
        "targets_visited": len(targets_visited),
        "targets_total": total_targets,
        "target_coverage": (
            round(len(targets_visited) / total_targets, 3) if total_targets > 0 else 0
        ),
        "drone_metrics": drone_metrics,
        "valid_solution": valid,
    }


def run_reasoning_agent_with_metrics(
    user_request: str,
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    distance_matrix: Optional[Dict[str, Any]] = None,
) -> AgentRunMetrics:
    """Run the single reasoning agent and capture metrics."""
    from server.agents.reasoning_agent import handle_reasoning_request

    metrics = AgentRunMetrics(agent_type="reasoning")
    t0 = time.time()

    try:
        result = handle_reasoning_request(
            {
                "user_request": user_request,
                "environment": env,
                "drone_configs": drone_configs,
                "distance_matrix": distance_matrix,
            }
        )

        metrics.latency_s = round(time.time() - t0, 2)
        metrics.iterations = result.get("iterations", 0)
        metrics.llm_calls = result.get("iterations", 0)
        metrics.response_text = result.get("response", "")

        routes = result.get("routes") or {}
        if routes:
            metrics.routes = routes

            # Build trajectories dict from routes
            trajectories = {}
            for did, rd in routes.items():
                if isinstance(rd, dict) and rd.get("trajectory"):
                    trajectories[str(did)] = rd["trajectory"]
            metrics.trajectories = trajectories

            # Extract solution quality
            targets = env.get("targets", [])
            sol_metrics = extract_solution_metrics(routes, drone_configs, targets)
            metrics.total_points = sol_metrics["total_points"]
            metrics.total_fuel_km = sol_metrics["total_fuel_km"]
            metrics.total_budget_km = sol_metrics["total_budget_km"]
            metrics.fuel_efficiency = sol_metrics["fuel_efficiency"]
            metrics.targets_visited = sol_metrics["targets_visited"]
            metrics.targets_total = sol_metrics["targets_total"]
            metrics.target_coverage = sol_metrics["target_coverage"]
            metrics.drone_metrics = sol_metrics["drone_metrics"]
            metrics.valid_solution = sol_metrics["valid_solution"]

    except Exception as e:
        metrics.latency_s = round(time.time() - t0, 2)
        metrics.error = str(e)

    return metrics


def run_langgraph_v4_with_metrics(
    user_message: str,
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    sam_matrix: Optional[Dict[str, Any]] = None,
    existing_solution: Optional[Dict[str, Any]] = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> AgentRunMetrics:
    """Run the multi-agent LangGraph v4 system and capture metrics."""
    from server.agents.isr_agent_multi_v4 import run_multi_agent_v4

    metrics = AgentRunMetrics(agent_type="langgraph_v4")
    t0 = time.time()

    try:
        result = run_multi_agent_v4(
            user_message=user_message,
            environment=env,
            drone_configs=drone_configs,
            sam_matrix=sam_matrix,
            existing_solution=existing_solution,
            llm_config=llm_config,
        )

        metrics.latency_s = round(time.time() - t0, 2)
        metrics.response_text = result.get("reply", result.get("response", ""))
        metrics.allocation_strategy = result.get("allocation_strategy", "unknown")

        # Count LLM calls and iterations from trace_events.
        # Node events have t="node.<name>"; coordinator events use "agent"/"stage" keys.
        # These nodes each make exactly one LLM call per run.
        _LLM_NODES = frozenset(
            {"strategist", "mission_planner", "optimizer", "critic", "responder"}
        )
        trace_events = result.get("trace_events") or []
        metrics.trace_events = trace_events
        node_events = [e for e in trace_events if e.get("t", "").startswith("node.")]
        metrics.iterations = len(node_events)
        metrics.llm_calls = sum(
            1 for e in node_events if e.get("t", "")[5:] in _LLM_NODES
        )

        routes = result.get("routes") or {}
        trajectories = result.get("trajectories") or {}
        allocations = result.get("allocation") or {}

        if routes:
            metrics.routes = routes
            metrics.trajectories = trajectories
            metrics.allocations = allocations

            targets = env.get("targets", [])
            sol_metrics = extract_solution_metrics(routes, drone_configs, targets)
            metrics.total_points = sol_metrics["total_points"]
            metrics.total_fuel_km = sol_metrics["total_fuel_km"]
            metrics.total_budget_km = sol_metrics["total_budget_km"]
            metrics.fuel_efficiency = sol_metrics["fuel_efficiency"]
            metrics.targets_visited = sol_metrics["targets_visited"]
            metrics.targets_total = sol_metrics["targets_total"]
            metrics.target_coverage = sol_metrics["target_coverage"]
            metrics.drone_metrics = sol_metrics["drone_metrics"]
            # Always require fuel-vs-budget feasibility. If v4 ALSO asserts
            # validity via its own per-node check, require both to be true —
            # but never trust v4's "valid" flag when fuel exceeds budget.
            metrics.valid_solution = sol_metrics["valid_solution"]
            if "valid" in result and not bool(result.get("valid")):
                metrics.valid_solution = False

    except Exception as e:
        metrics.latency_s = round(time.time() - t0, 2)
        metrics.error = str(e)

    return metrics


def run_crewai_agent_with_metrics(
    user_request: str,
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    distance_matrix: Optional[Dict[str, Any]] = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> AgentRunMetrics:
    """Run the CrewAI crew and capture metrics."""
    from server.agents.crewai_agent import handle_crewai_request

    metrics = AgentRunMetrics(agent_type="crewai")
    t0 = time.time()

    try:
        result = handle_crewai_request(
            {
                "user_request": user_request,
                "environment": env,
                "drone_configs": drone_configs,
                "distance_matrix": distance_matrix,
                "llm_config": llm_config,
            }
        )

        metrics.latency_s = round(time.time() - t0, 2)
        metrics.iterations = result.get("iterations", 0)
        metrics.llm_calls = result.get("iterations", 0)
        metrics.response_text = result.get("response", "")

        routes = result.get("routes") or {}
        if routes:
            metrics.routes = routes
            trajectories = {}
            for did, rd in routes.items():
                if isinstance(rd, dict) and rd.get("trajectory"):
                    trajectories[str(did)] = rd["trajectory"]
            metrics.trajectories = trajectories

            targets = env.get("targets", [])
            sol_metrics = extract_solution_metrics(routes, drone_configs, targets)
            metrics.total_points = sol_metrics["total_points"]
            metrics.total_fuel_km = sol_metrics["total_fuel_km"]
            metrics.total_budget_km = sol_metrics["total_budget_km"]
            metrics.fuel_efficiency = sol_metrics["fuel_efficiency"]
            metrics.targets_visited = sol_metrics["targets_visited"]
            metrics.targets_total = sol_metrics["targets_total"]
            metrics.target_coverage = sol_metrics["target_coverage"]
            metrics.drone_metrics = sol_metrics["drone_metrics"]
            metrics.valid_solution = sol_metrics["valid_solution"]

    except Exception as e:
        metrics.latency_s = round(time.time() - t0, 2)
        metrics.error = str(e)

    return metrics


def _apply_solution_metrics(
    metrics: AgentRunMetrics,
    solution: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
) -> Dict[str, Any]:
    """Fold a solver solution's routes into an AgentRunMetrics in place."""
    routes = (solution or {}).get("routes") or {}
    if not routes:
        return routes

    metrics.routes = routes
    metrics.trajectories = {
        str(did): rd["trajectory"]
        for did, rd in routes.items()
        if isinstance(rd, dict) and rd.get("trajectory")
    }

    sol_metrics = extract_solution_metrics(
        routes, drone_configs, env.get("targets", [])
    )
    metrics.total_points = sol_metrics["total_points"]
    metrics.total_fuel_km = sol_metrics["total_fuel_km"]
    metrics.total_budget_km = sol_metrics["total_budget_km"]
    metrics.fuel_efficiency = sol_metrics["fuel_efficiency"]
    metrics.targets_visited = sol_metrics["targets_visited"]
    metrics.targets_total = sol_metrics["targets_total"]
    metrics.target_coverage = sol_metrics["target_coverage"]
    metrics.drone_metrics = sol_metrics["drone_metrics"]
    metrics.valid_solution = sol_metrics["valid_solution"]
    return routes


def run_gnn_with_metrics(
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    model_path: Optional[str] = None,
    route_model_path: Optional[str] = None,
    use_solver: bool = True,
) -> AgentRunMetrics:
    """Run the GNN planner and capture comparison metrics.

    `use_solver=True` uses the GNN purely as an allocator and hands routing to
    the exact Held-Karp solver. `use_solver=False` decodes routes from the GNN
    itself, which is the configuration that can scale past the exact solver.
    """
    from server.gnn import run_gnn_planner

    metrics = AgentRunMetrics(agent_type="gnn" if use_solver else "gnn_decoder")
    t0 = time.time()

    try:
        result = run_gnn_planner(
            env=env,
            drone_configs=drone_configs,
            model_path=model_path,
            route_model_path=route_model_path,
            use_solver=use_solver,
        )
        metrics.latency_s = result.latency_s
        metrics.llm_calls = 0
        metrics.iterations = 1
        metrics.allocation_strategy = result.model_name
        metrics.response_text = (
            f"{result.model_name}: assigned {len(result.assignments)} targets "
            f"from graph features; routing via "
            f"{'exact solver' if use_solver else 'learned edge decoder'}."
        )
        metrics.trace_events = [
            {
                "t": "gnn.graph_built",
                "msg": (
                    f"{result.graph.get('n_nodes', 0)} nodes, "
                    f"{result.graph.get('n_edges', 0)} edges, "
                    f"{result.graph.get('n_drones', 0)} drones"
                ),
                "data": {
                    "node_feature_names": result.graph.get("node_feature_names", []),
                    "edge_feature_names": result.graph.get("edge_feature_names", []),
                    "drone_feature_names": result.graph.get("drone_feature_names", []),
                },
            },
            {
                "t": "gnn.assignments",
                "msg": f"{len(result.assignments)} target assignments",
                "data": {"assignments": result.assignments},
            },
        ]

        routes = _apply_solution_metrics(
            metrics, result.solution or {}, env, drone_configs
        )
        if routes:
            metrics.allocations = {
                did: [
                    tid
                    for tid, assigned_did in result.assignments.items()
                    if str(assigned_did) == str(did)
                ]
                for did in drone_configs.keys()
            }
        if result.error:
            metrics.error = result.error

    except Exception as e:
        metrics.latency_s = round(time.time() - t0, 2)
        metrics.error = str(e)

    return metrics


def run_solver_control_with_metrics(
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    allocation_strategy: str = "geographic",
    post_optimize: bool = True,
) -> AgentRunMetrics:
    """Run the deterministic allocator + exact-solver pipeline as a control.

    This is the no-LLM control for the scaling sweep: at hundreds of drones the
    LLM agents are neither affordable nor reliable, so the meaningful baseline
    is the shipped solver pipeline itself. Any timeout or blow-up is recorded on
    `error` rather than raised, since the scale at which the exact solver stops
    returning *is* one of the results we are measuring.
    """
    from server.solver.solver_bridge import solve_mission_with_allocation

    metrics = AgentRunMetrics(agent_type="solver_control")
    metrics.allocation_strategy = allocation_strategy
    t0 = time.time()

    try:
        solution = solve_mission_with_allocation(
            env,
            drone_configs,
            allocation_strategy=allocation_strategy,
            post_optimize=post_optimize,
        )
        metrics.latency_s = round(time.time() - t0, 2)
        metrics.llm_calls = 0
        metrics.iterations = 1
        metrics.response_text = f"deterministic {allocation_strategy} allocation + exact orienteering solver"
        _apply_solution_metrics(metrics, solution or {}, env, drone_configs)
        allocations = (solution or {}).get("allocations")
        if allocations:
            metrics.allocations = allocations

    except Exception as e:
        metrics.latency_s = round(time.time() - t0, 2)
        metrics.error = str(e)

    return metrics


@dataclass
class ComparisonResult:
    request: str
    env_summary: Dict[str, Any]
    reasoning: AgentRunMetrics
    langgraph_v4: AgentRunMetrics
    crewai: AgentRunMetrics
    gnn: Optional[AgentRunMetrics] = None
    gnn_decoder: Optional[AgentRunMetrics] = None
    solver_control: Optional[AgentRunMetrics] = None
    winner: Dict[str, str] = field(default_factory=dict)

    def _entrants(self) -> Dict[str, AgentRunMetrics]:
        entrants = {
            "reasoning": self.reasoning,
            "langgraph_v4": self.langgraph_v4,
            "crewai": self.crewai,
        }
        for name in ("gnn", "gnn_decoder", "solver_control"):
            run = getattr(self, name)
            if run is not None:
                entrants[name] = run
        return entrants

    def compute_winners(self):
        # Only runs that actually produced a solution may win a category. A run
        # that errored or timed out records latency_s ~ 0 and total_points 0, so
        # scoring it would let a *failed* planner win "fastest" and drag the
        # lower-is-better categories — exactly backwards for a scaling sweep
        # where the exact solver is expected to fall over at high drone counts.
        agents = {
            name: run
            for name, run in self._entrants().items()
            if not run.error and run.routes
        }

        if not agents:
            self.winner = {
                key: "none"
                for key in (
                    "latency",
                    "total_points",
                    "total_fuel_km",
                    "fuel_efficiency",
                    "target_coverage",
                    "llm_calls",
                )
            }
            return

        def pick(metric, higher_is_better=True):
            vals = {name: getattr(a, metric, 0) or 0 for name, a in agents.items()}
            best = max(vals.values()) if higher_is_better else min(vals.values())
            leaders = [name for name, v in vals.items() if v == best]
            return "tie" if len(leaders) > 1 else leaders[0]

        self.winner = {
            "latency": pick("latency_s", higher_is_better=False),
            "total_points": pick("total_points", higher_is_better=True),
            "total_fuel_km": pick("total_fuel_km", higher_is_better=False),
            "fuel_efficiency": pick("fuel_efficiency", higher_is_better=False),
            "target_coverage": pick("target_coverage", higher_is_better=True),
            "llm_calls": pick("llm_calls", higher_is_better=False),
        }

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "request": self.request,
            "env_summary": self.env_summary,
            "reasoning": self.reasoning.to_dict(),
            "langgraph_v4": self.langgraph_v4.to_dict(),
            "crewai": self.crewai.to_dict(),
            "winner": self.winner,
        }
        for name in ("gnn", "gnn_decoder", "solver_control"):
            run = getattr(self, name)
            payload[name] = run.to_dict() if run is not None else None
        return payload
