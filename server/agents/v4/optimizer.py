"""Optimizer node for the v4 ISR agent workflow."""

import sys
from typing import Any, Callable, Dict, List

from langchain_core.messages import AIMessage, HumanMessage

from ...memory.algorithm_registry import AlgorithmExecution, get_algorithm_info
from .context import (
    HARD_CONSTRAINTS,
    build_current_solution_context,
    build_mission_context,
)
from .state import (
    MissionState,
    describe_llm,
    get_llm_for_state,
    set_state,
    trace_event,
)

OPTIMIZER_PROMPT = """You are the OPTIMIZER agent in an ISR mission planning system.

ROLE
Analyze the current solution and apply post-optimization techniques to improve efficiency.

You have THREE optimization tools available:
1. **insert_unvisited_tool()** - Insert missed/unvisited targets into routes where fuel allows
2. **swap_closer_tool()** - Reassign targets to drones whose trajectories pass closer
3. **remove_crossings_tool()** - Apply 2-opt to eliminate self-crossing trajectories

WHEN TO USE EACH TOOL:
- **Insert Unvisited**: When unvisited_targets list is not empty and drones have spare fuel capacity
- **Swap Closer**: When targets might be better served by a different drone (fuel efficiency)
- **Remove Crossings**: When routes have obvious detours or crossing paths

CRITICAL RULES:
1. Each tool checks if the optimization improves efficiency before applying
2. Tools will REJECT changes that don't improve the solution
3. You can call multiple tools in sequence (insert, then swap, then crossing removal)
4. Always explain WHY you're calling each tool
5. Frozen segments (from checkpoint replanning) will NOT be modified by optimizers

DECISION PROCESS:
1. Review current solution metrics (fuel, points, unvisited targets)
2. Identify optimization opportunities
3. Call appropriate tools in logical order
4. Report final optimization results

Be strategic - don't call tools that won't help (e.g., don't try to insert targets if all are visited).
"""


def create_optimizer_node(
    optimizer_tools: List[Any],
) -> Callable[[MissionState], Dict[str, Any]]:
    """Create an optimizer node bound to the current module's tool callbacks."""

    def optimizer_node(state: MissionState) -> Dict[str, Any]:
        """Optimizer applies post-optimization tools to improve solution."""
        print(
            "\n⚙️  [OPTIMIZER] Analyzing optimization opportunities...", file=sys.stderr
        )
        sys.stderr.flush()

        set_state(state)

        # Check if routes exist
        routes = state.get("routes", {})
        if not routes:
            print("⚠️  [OPTIMIZER] No routes to optimize - skipping", file=sys.stderr)
            return {
                "messages": [
                    AIMessage(content="[OPTIMIZER] No routes to optimize - skipping.")
                ],
                "optimizer_analysis": "No routes available for optimization.",
            }

        llm = get_llm_for_state(state)

        # Bind optimizer tools
        llm_with_tools = llm.bind_tools(optimizer_tools, tool_choice="auto")

        mission_context = build_mission_context(state)
        current_solution = build_current_solution_context(state)
        route_analysis = state.get("route_analysis", "")

        optimizer_prompt = f"""
{HARD_CONSTRAINTS}

{OPTIMIZER_PROMPT}

{mission_context}

ROUTE OPTIMIZER ANALYSIS:
{route_analysis}

{current_solution}

Analyze this solution and decide which optimization tools to apply.
"""

        messages = [HumanMessage(content=optimizer_prompt)]

        # Track which optimizer algorithms were used
        optimizer_algorithms_used = []

        try:
            print(
                f"🌐 [OPTIMIZER] Calling LLM with tools ({describe_llm(llm)})...",
                file=sys.stderr,
            )
            response = llm_with_tools.invoke(messages)
            print("✅ [OPTIMIZER] API call successful", file=sys.stderr)

            # Check if tools were called
            if hasattr(response, "tool_calls") and response.tool_calls:
                print(
                    f"🔧 [OPTIMIZER] {len(response.tool_calls)} tool(s) called",
                    file=sys.stderr,
                )

                # Execute each tool call
                tool_results = []
                for tool_call in response.tool_calls:
                    tool_name = tool_call.get("name", "unknown")
                    print(f"   → Executing {tool_name}...", file=sys.stderr)

                    # Find and execute the tool
                    for tool_item in optimizer_tools:
                        if tool_item.name == tool_name:
                            try:
                                result = tool_item.invoke({})
                                tool_results.append(f"\n{tool_name} result:\n{result}")
                                print(f"   ✓ {tool_name} completed", file=sys.stderr)

                                # Map tool name to algorithm ID
                                tool_to_algorithm = {
                                    "insert_missed_targets": "insert_missed",
                                    "swap_closer_targets": "swap_closer",
                                    "remove_crossings": "crossing_removal",
                                    "unified_optimize": "unified",
                                }
                                alg_id = tool_to_algorithm.get(tool_name, tool_name)
                                alg_info = get_algorithm_info(alg_id)

                                optimizer_algorithms_used.append(
                                    AlgorithmExecution(
                                        algorithm_id=alg_id,
                                        algorithm_name=(
                                            alg_info["name"] if alg_info else tool_name
                                        ),
                                        agent="Optimizer",
                                        result_summary=f"Executed via {tool_name}",
                                    ).to_dict()
                                )

                            except Exception as e:
                                error_msg = f"Error in {tool_name}: {e}"
                                tool_results.append(error_msg)
                                print(f"   ✗ {tool_name} failed: {e}", file=sys.stderr)
                            break

                analysis = response.content + "\n\n" + "\n".join(tool_results)
            else:
                print(
                    "ℹ️  [OPTIMIZER] No tools called - keeping solution as is",
                    file=sys.stderr,
                )
                analysis = response.content

        except Exception as e:
            print(f"❌ [OPTIMIZER] API call failed: {e}", file=sys.stderr)
            analysis = (
                f"Optimizer error: {e}\n\nSolution preserved without optimization."
            )

        # Get existing algorithms_used from state and append optimizer algorithms
        existing_algorithms = state.get("algorithms_used") or []
        updated_algorithms = existing_algorithms + optimizer_algorithms_used

        return {
            "messages": [AIMessage(content=f"[OPTIMIZER]\n{analysis}")],
            "optimizer_analysis": analysis,
            "algorithms_used": updated_algorithms,
            "node_trace": [
                trace_event(
                    "optimizer",
                    f"Post-optimization: {len(optimizer_algorithms_used)} operations",
                    operations=[
                        a.get("algorithm_name", "?") for a in optimizer_algorithms_used
                    ],
                )
            ],
            "llm_calls": 1,
        }

    return optimizer_node
