"""Responder node for the v4 ISR agent workflow."""

import sys
from typing import Any, Dict, List, cast

from langchain_core.messages import AIMessage, HumanMessage

from ...memory.algorithm_registry import (
    AlgorithmExecution,
    generate_algorithm_summary,
)
from .context import (
    HARD_CONSTRAINTS,
    build_current_solution_context,
    build_mission_context,
)
from .state import (
    MissionState,
    get_llm_for_state,
    set_state,
    trace_event,
)

RESPONDER_PROMPT = """
You are the ISR Mission Responder for the v4 multi-agent ISR planner.

Your role:
- Answer the user's questions using ONLY the stored mission context and the CURRENT SOLUTION SUMMARY.
- You never recompute routes unless explicitly instructed by the user (e.g., "recompute", "optimize", "rerun", "generate a new plan").  # noqa: E501

When a mission solution exists (routes found):
- ALWAYS cite specific **numbers** from the provided state:
  - fuel usage per drone
  - fuel budget and fuel margin (budget - used)
  - points collected per drone and total points
  - total fuel used across all drones
  - count of visited vs unvisited targets
  - list of unvisited targets, if any
- ALWAYS leverage the numeric metrics in `mission_metrics`.

When answering:
- Be **precise**, **concise**, and **numerical**.
- Prefer clear statements and bullet points over vague text.
- If the user asks a comparative or evaluative question, always compare using numeric values.
- If the mission has unvisited targets, describe them explicitly.
- Never generalize or use fluff language ("likely", "maybe", "probably") when numbers exist.

When NO solution exists yet (routes empty):
- Clearly state that no mission plan exists.
- Suggest generating a plan first.

Follow the user's constraints exactly (e.g., "do not recompute routes").
"""


def _state_dict(state: MissionState, key: str) -> Dict[str, Any]:
    value = state.get(key)
    return value if isinstance(value, dict) else {}


def _routes_from_state(state: MissionState) -> Dict[str, Dict[str, Any]]:
    return cast(Dict[str, Dict[str, Any]], state.get("routes") or {})


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def responder_node(state: MissionState) -> Dict[str, Any]:
    """Responder formulates the final answer."""
    print("\n📝 [RESPONDER] Formulating response...", file=sys.stderr)
    sys.stderr.flush()

    set_state(state)

    request_type = state.get("request_type", "optimize")

    # For questions, use simpler response
    if request_type == "question":
        return handle_question_response(state)

    # For solve/optimize, summarize the solution
    return handle_solution_response(state)


def handle_question_response(state: MissionState) -> Dict[str, Any]:
    """Handle question-type requests."""
    llm = get_llm_for_state(state)

    user_request = state.get("user_request", "")
    mission_context = build_mission_context(state)
    current_solution = build_current_solution_context(state)

    # Check if user is asking about algorithms
    algorithm_context = ""
    asks_about_algorithms = state.get("asks_about_algorithms", False)
    algorithms_used = state.get("algorithms_used", [])

    if asks_about_algorithms and algorithms_used:
        # Convert dicts back to AlgorithmExecution objects if needed
        executions = []
        for alg in algorithms_used:
            if isinstance(alg, dict):
                executions.append(AlgorithmExecution.from_dict(alg))
            else:
                executions.append(alg)

        algorithm_summary = generate_algorithm_summary(executions)
        algorithm_context = f"""

## ALGORITHMS USED

{algorithm_summary}

When answering the user's question about algorithms, use this information to provide a detailed explanation.
"""

    prompt = f"""
{HARD_CONSTRAINTS}

{RESPONDER_PROMPT}

{mission_context}

{current_solution}
{algorithm_context}
USER QUESTION: "{user_request}"

Provide a clear, direct answer to this question.
"""

    messages = [HumanMessage(content=prompt)]
    response = llm.invoke(messages)

    return {
        "messages": [AIMessage(content=response.content)],
        "final_response": response.content,
        "llm_calls": 1,
    }


def handle_solution_response(state: MissionState) -> Dict[str, Any]:
    """Handle solution-type requests."""
    routes = _routes_from_state(state)
    _allocation = state.get("allocation", {})  # noqa: F841
    suggestions = state.get("suggestions", [])
    env = _state_dict(state, "environment")
    allocation_reasoning = state.get("allocation_reasoning", "")

    # Build response
    lines = ["MISSION PLAN COMPUTED", "=" * 40, ""]

    total_points = 0
    total_fuel = 0.0
    all_visited: List[str] = []

    for did in sorted(routes.keys()):
        route_data = routes[did]
        route = _string_list(route_data.get("route", []))
        dist = float(route_data.get("distance", 0) or 0.0)
        points = int(route_data.get("total_points", 0) or 0)
        visited = _string_list(route_data.get("visited_targets", []))

        total_points += points
        total_fuel += dist
        all_visited.extend(visited)

        lines.append(f"D{did}: {' → '.join(route)}")
        lines.append(f"    Fuel: {dist:.0f}, Points: {points}, Targets: {len(visited)}")

    lines.append("")
    lines.append(f"TOTALS: {total_points} points, {total_fuel:.0f} fuel")

    # Check for unvisited targets
    all_targets = [str(t.get("id", t.get("label"))) for t in env.get("targets", [])]
    unvisited = set(all_targets) - set(all_visited)
    if unvisited:
        lines.append(f"UNVISITED: {', '.join(sorted(unvisited))}")

    # Add key reasoning (extract from allocation reasoning)
    if allocation_reasoning and "TRADE_OFFS:" in allocation_reasoning:
        trade_off_start = allocation_reasoning.find("TRADE_OFFS:")
        trade_offs = allocation_reasoning[
            trade_off_start : trade_off_start + 200
        ].split("\n")[1:3]
        if trade_offs:
            lines.append("")
            lines.append("KEY TRADE-OFFS:")
            for t in trade_offs:
                if t.strip():
                    lines.append(f"  {t.strip()}")

    # Add suggestions if any
    if suggestions:
        lines.append("")
        lines.append("SUGGESTIONS:")
        for s in suggestions[:3]:  # Top 3 suggestions
            lines.append(f"  • {s}")

    # Add algorithm summary if user asked about algorithms
    asks_about_algorithms = state.get("asks_about_algorithms", False)
    algorithms_used = state.get("algorithms_used", [])

    if asks_about_algorithms and algorithms_used:
        lines.append("")
        lines.append("ALGORITHMS USED:")
        for i, alg in enumerate(algorithms_used, 1):
            if isinstance(alg, dict):
                name = alg.get("algorithm_name", alg.get("algorithm_id", "Unknown"))
                summary = alg.get("result_summary", "")
            else:
                name = alg.algorithm_name
                summary = alg.result_summary

            line = f"  {i}. {name}"
            if summary:
                line += f" - {summary}"
            lines.append(line)

    response_text = "\n".join(lines)

    return {
        "messages": [AIMessage(content=response_text)],
        "final_response": response_text,
        "node_trace": [
            trace_event("responder", f"Response generated ({len(response_text)} chars)")
        ],
    }
