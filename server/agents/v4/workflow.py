"""Workflow assembly for the v4 ISR agent graph."""

import sys

from langgraph.graph import END, StateGraph

from .allocator import allocator_node
from .critic import critic_node
from .mission_planner import mission_planner_node
from .optimizer import create_optimizer_node
from .optimizer_tools import OPTIMIZER_TOOLS
from .responder import responder_node
from .route_optimizer import route_optimizer_node
from .state import MissionState
from .strategist import strategist_node

optimizer_node = create_optimizer_node(OPTIMIZER_TOOLS)


def build_reasoning_workflow():
    """Build the v4 reasoning-based multi-agent workflow with Mission Planner and Optimizer."""
    print(
        "🔧 [v4] Building reasoning workflow with Mission Planner and Optimizer...",
        file=sys.stderr,
    )

    workflow = StateGraph(MissionState)

    # Add nodes
    workflow.add_node("strategist", strategist_node)
    workflow.add_node("mission_planner", mission_planner_node)
    workflow.add_node("allocator", allocator_node)
    workflow.add_node("route_optimizer", route_optimizer_node)
    workflow.add_node("optimizer", optimizer_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("responder", responder_node)

    # Routing function after strategist
    def after_strategist(state: MissionState) -> str:
        request_type = state.get("request_type", "optimize")
        if request_type == "question":
            return "responder"  # Skip solving for questions
        return "mission_planner"  # Route to planner for complex orchestration

    # Routing function after mission planner
    def after_mission_planner(state: MissionState) -> str:
        request_type = state.get("request_type", "optimize")
        if request_type == "question":
            return "responder"
        return "allocator"

    # Set entry point
    workflow.set_entry_point("strategist")

    # Add edges
    workflow.add_conditional_edges(
        "strategist",
        after_strategist,
        {
            "responder": "responder",
            "mission_planner": "mission_planner",
        },
    )

    workflow.add_conditional_edges(
        "mission_planner",
        after_mission_planner,
        {
            "responder": "responder",
            "allocator": "allocator",
        },
    )

    workflow.add_edge("allocator", "route_optimizer")
    workflow.add_edge("route_optimizer", "optimizer")  # Add optimizer after routing
    workflow.add_edge("optimizer", "critic")
    workflow.add_edge("critic", "responder")
    workflow.add_edge("responder", END)

    print(
        "✅ [v4] Workflow built successfully with Mission Planner and Optimizer",
        file=sys.stderr,
    )
    return workflow.compile()
