"""Mission planner node for the v4 ISR agent workflow."""

import json
import sys
from typing import Any, Dict

from langchain_core.messages import AIMessage, HumanMessage

from .context import HARD_CONSTRAINTS, build_mission_context
from .state import (
    MissionState,
    describe_llm,
    get_llm_for_state,
    set_state,
    trace_event,
)

MISSION_PLANNER_PROMPT = """You are the MISSION PLANNER agent in an ISR mission planning system.

ROLE
Orchestrate complex mission scenarios that require:
- Multi-segment missions (checkpoint replanning, drone losses, phase transitions)
- Constraint operations (fuel reserves, loiter times, trajectory filtering)
- Special mission patterns (skip targets, prioritize certain types, etc.)

You have access to powerful orchestration tools that can:
1. Create mission segments with frozen trajectories
2. Continue from checkpoints with new drone states
3. Apply fuel reserve constraints
4. Add loiter time at specific targets
5. Inspect and manipulate mission state

WHEN TO ACT
You should orchestrate when the user request involves:
- "After X steps/km, drone Y is lost/lands" → Multi-segment with drone removal
- "Reserve 25% fuel" or "land with X fuel remaining" → Fuel constraint
- "Loiter N steps at target type T" → Loiter constraint
- "Skip every other target" → Trajectory filtering (post-solve)
- "First solve for drones 1-3, then continue with drone 4" → Phased planning
- Multiple checkpoints or mission phases

WHEN NOT TO ACT
For simple single-phase missions, set requires_segmentation=false and let the
normal workflow (Allocator → Router) handle it.

OUTPUT FORMAT (STRICT JSON)
{
  "requires_segmentation": true|false,
  "orchestration_plan": "brief description of multi-phase approach",
  "segments": [
    {
      "name": "Phase 1",
      "description": "Initial patrol with all drones",
      "drone_ids": ["1", "2", "3"],
      "constraints": ["fuel_reserve:25%"],
      "checkpoint_km": 150
    },
    {
      "name": "Phase 2",
      "description": "Continue after drone 2 lands",
      "drone_ids": ["1", "3"],
      "synthetic_starts": {"1": "T5", "3": "T7"},
      "constraints": []
    }
  ],
  "constraint_operations": [
    {"type": "fuel_reserve", "percentage": 25, "applies_to": "all"},
    {"type": "loiter", "target_type": "C", "steps": 20}
  ],
  "skip_to_allocator": true|false
}

If requires_segmentation is false, you can set skip_to_allocator=true to proceed
directly to allocation without additional orchestration.

Be explicit about segment boundaries, drone state changes, and constraint timing.
"""


def mission_planner_node(state: MissionState) -> Dict[str, Any]:
    """Mission Planner orchestrates complex multi-segment missions and constraints."""
    print(
        "\n🗺️  [MISSION_PLANNER] Analyzing orchestration requirements...",
        file=sys.stderr,
    )
    sys.stderr.flush()

    set_state(state)

    user_request = state.get("user_request", "")
    strategy_analysis = state.get("strategy_analysis", "")

    # Check if this is a simple question - skip orchestration
    request_type = state.get("request_type", "optimize")
    if request_type == "question":
        print(
            "🔍 [MISSION_PLANNER] Question mode detected - skipping orchestration",
            file=sys.stderr,
        )
        return {
            "messages": [
                AIMessage(
                    content="[MISSION_PLANNER] Question mode - no orchestration needed"
                )
            ],
            "mission_plan": "No orchestration required for question mode.",
            "requires_segmentation": False,
        }

    llm = get_llm_for_state(state)

    mission_context = build_mission_context(state)

    # Build tools context
    tools_context = """
AVAILABLE ORCHESTRATION TOOLS:

Mission Inspection:
- inspect_mission_state() - Get current mission status
- get_mission_summary() - High-level mission overview
- get_unvisited_targets() - List targets not yet visited

Solving:
- solve_full_mission() - Solve complete mission
- solve_single_drone() - Solve for one drone only

Optimization:
- apply_insert_missed() - Add unvisited targets
- apply_swap_closer() - Reassign targets to closer drones

Segmentation:
- create_segment_at_checkpoint() - Create new segment at distance/step checkpoint
- freeze_segment_at_checkpoint() - Freeze trajectory up to checkpoint
- continue_from_checkpoint() - Resume from checkpoint with new drone state

Constraints:
- add_fuel_reserve_constraint() - Reserve % of fuel
- add_loiter_constraint() - Add loiter time at target types
"""

    planning_prompt = f"""
{HARD_CONSTRAINTS}

{MISSION_PLANNER_PROMPT}

{mission_context}

STRATEGIST ANALYSIS:
{strategy_analysis}

{tools_context}

USER REQUEST: "{user_request}"

Analyze this request and determine if complex orchestration is needed.
Provide your orchestration plan in the required JSON format.
"""

    messages = [HumanMessage(content=planning_prompt)]

    try:
        print(
            f"🌐 [MISSION_PLANNER] Calling LLM ({describe_llm(llm)})...",
            file=sys.stderr,
        )
        response = llm.invoke(messages)
        plan = response.content
        print("✅ [MISSION_PLANNER] API call successful", file=sys.stderr)

        # Try to parse JSON from response
        requires_segmentation = False
        segments = []
        constraint_ops = []

        # Look for JSON in response
        if "{" in plan and "}" in plan:
            try:
                # Extract JSON (may be wrapped in markdown)
                json_start = plan.find("{")
                json_end = plan.rfind("}") + 1
                json_str = plan[json_start:json_end]
                parsed = json.loads(json_str)

                requires_segmentation = parsed.get("requires_segmentation", False)
                segments = parsed.get("segments", [])
                constraint_ops = parsed.get("constraint_operations", [])

                print(
                    f"📊 [MISSION_PLANNER] Parsed plan: segmentation={requires_segmentation}, segments={len(segments)}, constraints={len(constraint_ops)}",  # noqa: E501
                    file=sys.stderr,
                )
            except json.JSONDecodeError as e:
                print(
                    f"⚠️  [MISSION_PLANNER] Failed to parse JSON: {e}", file=sys.stderr
                )

    except Exception as e:
        print(f"❌ [MISSION_PLANNER] API call failed: {e}", file=sys.stderr)
        plan = (
            f"API call failed: {e}\n\nDefaulting to simple mission (no orchestration)."
        )
        requires_segmentation = False
        segments = []
        constraint_ops = []

    return {
        "messages": [AIMessage(content=f"[MISSION_PLANNER]\n{plan}")],
        "mission_plan": plan,
        "requires_segmentation": requires_segmentation,
        "segments": segments,
        "constraint_operations": constraint_ops,
        "node_trace": [
            trace_event(
                "mission_planner",
                f"Segmentation: {requires_segmentation}, {len(segments)} segments, {len(constraint_ops)} constraints",
            )
        ],
        "llm_calls": 1,
    }
