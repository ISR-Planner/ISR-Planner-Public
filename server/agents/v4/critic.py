"""Critic node for the v4 ISR agent workflow."""

import sys
from typing import Any, Dict

from langchain_core.messages import AIMessage, HumanMessage

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

CRITIC_PROMPT = """You are the CRITIC agent in an ISR mission planning system.

Your job is to review the solution and identify:
1. Any issues or violations (commands not followed, constraints violated)
2. Potential improvements (targets that could be reassigned for better efficiency)
3. Suggestions for the commander

Be CONCISE but THOROUGH. Focus on actionable insights.

OUTPUT FORMAT:
REVIEW:
[Overall assessment - is this a good solution?]

ISSUES:
[List any problems found, or "None"]

SUGGESTIONS:
[List improvement suggestions, e.g., "Allowing D2 to access T5 would add 15 points with only 20 extra fuel"]

FINAL_VERDICT: [APPROVED|NEEDS_REVISION|REJECTED]
"""


def critic_node(state: MissionState) -> Dict[str, Any]:
    """Critic reviews the solution and suggests improvements."""
    print("\n🔍 [CRITIC] Reviewing solution...", file=sys.stderr)
    sys.stderr.flush()

    set_state(state)

    llm = get_llm_for_state(state)

    mission_context = build_mission_context(state)
    allocation_reasoning = state.get("allocation_reasoning", "")
    route_analysis = state.get("route_analysis", "")
    current_solution = build_current_solution_context(state)

    critic_prompt = f"""
{HARD_CONSTRAINTS}

{CRITIC_PROMPT}

{mission_context}

ALLOCATION REASONING:
{allocation_reasoning}

ROUTE ANALYSIS:
{route_analysis}

{current_solution}

Review this solution and provide your assessment.
Flag any violations of HARD CONSTRAINTS (e.g., drone assigned targets it cannot visit).
"""

    messages = [HumanMessage(content=critic_prompt)]
    response = llm.invoke(messages)

    review = response.content
    print("📋 [CRITIC] Review complete", file=sys.stderr)

    # Extract suggestions
    suggestions = []
    lines = review.split("\n")
    in_suggestions = False
    for line in lines:
        if "SUGGESTIONS:" in line.upper():
            in_suggestions = True
            continue
        if in_suggestions and line.strip().startswith("-"):
            suggestions.append(line.strip()[1:].strip())
        elif in_suggestions and (
            "FINAL_VERDICT" in line.upper() or line.startswith("===")
        ):
            in_suggestions = False

    return {
        "messages": [AIMessage(content=f"[CRITIC]\n{review}")],
        "critic_review": review,
        "suggestions": suggestions,
        "node_trace": [
            trace_event(
                "critic",
                f"Review complete, {len(suggestions)} suggestions",
                suggestions=suggestions,
            )
        ],
        "llm_calls": 1,
    }
