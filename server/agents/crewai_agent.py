"""CrewAI multi-agent crew for ISR mission planning.

Two-agent crew: Strategist allocates targets, Route Planner calls the solver.
Uses the same Held-Karp orienteering solver as the reasoning and LangGraph v4 agents.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from crewai import LLM, Agent, Crew, Process, Task
from crewai.tools import tool as crewai_tool

# Import the LangChain tool objects to call their underlying .func
import server.agents.reasoning_agent as _ra

# Import module-level state helpers (plain functions, not LangChain tools)
from server.agents.reasoning_agent import (
    get_solved_routes,
    set_mission_context,
)

# ---------------------------------------------------------------------------
# CrewAI-native tool wrappers
# CrewAI 1.x requires crewai.tools.BaseTool instances; LangChain StructuredTool
# objects are rejected. We wrap each tool's underlying function via .func.
# ---------------------------------------------------------------------------


def _tool_func(tool_obj: Any) -> Any:
    return getattr(tool_obj, "func")


@crewai_tool
def get_targets() -> str:
    """Get all targets with their IDs, priorities, positions, and types.
    Returns a list of targets that can be visited by drones.
    Use this to understand what targets exist and their properties.
    """
    return _tool_func(_ra.get_targets)()


@crewai_tool
def get_airports() -> str:
    """Get all airports with their IDs and positions.
    Airports are where drones start and must return to.
    """
    return _tool_func(_ra.get_airports)()


@crewai_tool
def get_drones() -> str:
    """Get all enabled drones with their fuel budgets and home airports.
    Returns drone configurations including fuel budget, start airport, and target access.
    """
    return _tool_func(_ra.get_drones)()


@crewai_tool
def get_sams() -> str:
    """Get all SAM (Surface-to-Air Missile) zones that drones must avoid.
    Returns SAM positions and ranges. Drones cannot fly through these zones.
    """
    return _tool_func(_ra.get_sams)()


@crewai_tool
def allocate_targets(strategy: str = "geographic") -> str:
    """Allocate targets among drones using the specified strategy.
    Args:
        strategy: One of 'geographic' (default), 'greedy', 'balanced', 'efficient', 'exclusive'.
    Returns the target allocation for each drone.
    """
    return _tool_func(_ra.allocate_targets)(strategy=strategy)


@crewai_tool
def solve_full_mission(strategy: str = "geographic") -> str:
    """Solve the complete mission for all drones using the full pipeline.
    This runs the same Held-Karp orienteering solver as the Run Planner button.
    Handles SAM avoidance, optimal sequencing, and trajectory computation.
    Args:
        strategy: Allocation strategy - 'geographic' (default), 'greedy', 'balanced',
                  'efficient', or 'exclusive'.
    Returns complete solution with routes for all drones.
    """
    return _tool_func(_ra.solve_full_mission)(strategy=strategy)


@crewai_tool
def set_route(drone_id: str, waypoints: list[str]) -> str:
    """Set a drone's route to exactly these waypoints after its start position.
    Args:
        drone_id: Which drone (e.g., '1' or '2')
        waypoints: Ordered target/airport IDs after the start, ending at the
                   landing airport (e.g., ['T1', 'T3', 'A1'])
    Returns the route, fuel cost, and whether it is feasible.
    """
    return _tool_func(_ra.set_route)(drone_id=drone_id, waypoints=waypoints)


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------


def _get_crewai_llm(llm_config: Optional[Dict[str, Any]] = None) -> LLM:
    """Build a CrewAI LLM using LiteLLM model strings.

    CrewAI 1.x uses LiteLLM internally and does not accept LangChain model objects.
    Provider/model resolution mirrors llm_factory.get_llm().
    """
    from server.agents.llm_factory import MODEL_ALIASES, PROVIDERS, get_default_model

    cfg = llm_config or {}
    provider = str(cfg.get("provider") or os.getenv("LLM_PROVIDER", "openai"))
    model_raw = (
        cfg.get("model") or os.getenv("LLM_MODEL") or get_default_model(provider)
    )
    model = str(MODEL_ALIASES.get(str(model_raw), model_raw))

    # LiteLLM provider prefixes (google uses "gemini/" not "google/")
    prefix = {
        "openai": "openai",
        "google": "gemini",
        "groq": "groq",
        "zai": "openai",
    }.get(provider, provider)

    provider_info = PROVIDERS.get(provider)
    api_key = os.getenv(provider_info.env_key) if provider_info else None

    return LLM(model=f"{prefix}/{model}", api_key=api_key, temperature=0)


# ---------------------------------------------------------------------------
# Environment summary helper
# ---------------------------------------------------------------------------


def _build_env_summary(env: Dict[str, Any], drone_configs: Dict[str, Any]) -> str:
    airports = env.get("airports", [])
    targets = env.get("targets", [])
    sams = env.get("sams", [])

    target_lines = ", ".join(
        f"{t.get('id')} (priority={t.get('priority', 0)}, type={t.get('type', '?')})"
        for t in targets
    )
    drone_lines = ", ".join(
        f"{did} fuel={cfg.get('fuel_budget_km', cfg.get('fuel_budget', '?'))}km"
        for did, cfg in drone_configs.items()
        if isinstance(cfg, dict) and cfg.get("enabled", True)
    )
    sam_lines = ", ".join(
        f"{s.get('id')} range={s.get('range_km', '?')}km" for s in sams
    )
    return (
        f"Airports ({len(airports)}): {[a.get('id') for a in airports]}\n"
        f"Targets ({len(targets)}): {target_lines}\n"
        f"SAM zones ({len(sams)}): {sam_lines}\n"
        f"Drones: {drone_lines}"
    )


# ---------------------------------------------------------------------------
# Crew builder
# ---------------------------------------------------------------------------


def _build_crew(
    user_request: str,
    env_summary: str,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Crew:
    llm = _get_crewai_llm(llm_config)

    strategist = Agent(
        role="ISR Mission Strategist",
        goal=(
            "Analyze the mission environment and determine the optimal target allocation "
            "across drones, maximizing total priority points within fuel budgets"
        ),
        backstory=(
            "Senior ISR mission planner with expertise in multi-drone coordination, "
            "target prioritization, and SAM threat assessment."
        ),
        tools=[get_targets, get_airports, get_drones, get_sams, allocate_targets],
        llm=llm,
        verbose=False,
    )

    route_planner = Agent(
        role="ISR Route Planner",
        goal=(
            "Execute the mission strategy by calling the route solver for each drone, "
            "verifying fuel feasibility, and producing the final optimized routes"
        ),
        backstory=(
            "Tactical route specialist who uses the orienteering solver to compute "
            "optimal drone routes, accounting for fuel budgets and SAM exclusion zones."
        ),
        tools=[get_targets, get_drones, solve_full_mission, set_route],
        llm=llm,
        verbose=False,
    )

    strategy_task = Task(
        description=(
            f"User request: {user_request}\n\n"
            f"Mission environment:\n{env_summary}\n\n"
            "Use get_targets, get_drones, and allocate_targets to determine which "
            "targets each drone should cover. Maximize total priority points within "
            "fuel budgets. Check SAM threats with get_sams."
        ),
        expected_output=(
            "Per-drone target allocation: for each drone, which targets it covers "
            "and the reasoning. Include the allocation strategy used."
        ),
        agent=strategist,
    )

    planning_task = Task(
        description=(
            "Using the allocation strategy from the previous task, call solve_full_mission "
            "to generate actual routes for all drones. Verify each drone's fuel usage is "
            "within budget. If a drone exceeds its budget, call set_route with fewer "
            "targets."
        ),
        expected_output=(
            "Completed routes for all drones: per-drone path, fuel used vs budget, "
            "points collected, and whether the solution is feasible."
        ),
        agent=route_planner,
        context=[strategy_task],
    )

    return Crew(
        agents=[strategist, route_planner],
        tasks=[strategy_task, planning_task],
        process=Process.sequential,
        verbose=False,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def handle_crewai_request(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """Run the CrewAI crew on a mission planning request.

    Returns the same shape as handle_reasoning_request:
      {"routes": dict, "response": str, "iterations": int}
    """
    user_request = request_data.get("user_request", "Plan a mission.")
    env = request_data.get("environment", {})
    drone_configs = request_data.get("drone_configs", {})
    distance_matrix = request_data.get("distance_matrix")
    llm_config = request_data.get("llm_config") or {}

    # Bind environment to module-level state so solver tools can access it
    set_mission_context(env, drone_configs, distance_matrix)

    env_summary = _build_env_summary(env, drone_configs)
    crew = _build_crew(user_request, env_summary, llm_config)

    result = crew.kickoff()
    response_text = result.raw if hasattr(result, "raw") else str(result)

    # Retrieve routes written by solve_full_mission / set_route during the run
    routes = get_solved_routes()

    return {
        "routes": routes,
        "response": response_text,
        "iterations": len(crew.tasks),
    }
