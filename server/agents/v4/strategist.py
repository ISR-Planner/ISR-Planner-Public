"""Strategist node for the v4 ISR agent workflow."""

import re
import sys
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage

from ..agent_memory import get_memory_for_prompt
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

STRATEGIST_PROMPT = """
You are the v4 ISR Mission Strategist in a multi-agent planning system.

You do NOT generate routes yourself. Your job is to:
- Understand the user's request.
- Decide whether this is:
  - a QUESTION about an existing mission solution,
  - a request to OPTIMIZE or RECOMPUTE the mission,
  - a request to REALLOCATE targets between drones,
  - a request to REROUTE without changing allocation,
  - or a DIAGNOSTIC / EVALUATION request.
- Respect hard constraints in the user text (e.g., "do not recompute routes", "keep allocations fixed").
- Decide which downstream agents/tools should act (Allocator, RouteOptimizer, Critic, Responder).

You have access to:
- MISSION CONTEXT (airports, targets, SAMs, drone configs).
- CURRENT SOLUTION SUMMARY and MISSION METRICS, including:
  - per-drone fuel_used, fuel_budget, fuel_margin,
  - per-drone points and target counts,
  - total_points and total_fuel,
  - list of unvisited_targets.

You MUST use these metrics when reasoning:
- If the user asks "which drone uses the most fuel", "what is the total fuel", or similar:
  - This is a QUESTION. Do NOT trigger re-optimization.
- If the user asks to "generate", "compute", "recompute", "optimize", or "find a better/cheaper/more efficient plan":
  - This is an OPTIMIZATION request.
- If the user asks to "reassign targets", "balance workloads", "move T19 to D4", etc.:
  - This is a REALLOCATION request.
- If the user asks to "adjust routes" or "shorten detours" while keeping allocation fixed:
  - This is a REROUTE request.
- If the user only wants a critique, explanation, or numbers:
  - This is a QUESTION / EVALUATION request.

CRITICAL CONSTRAINT:
- If the user explicitly says **"do not recompute routes"**, you MUST:
  - Treat this as a pure QUESTION/EVALUATION, even if they mention optimization.
  - Set REQUEST_TYPE to `question`.
  - Set MODE to `answer_question`.
  - Ensure downstream behavior does NOT change routes or allocation.

You MUST reply in the following STRICT format:

REQUEST_TYPE: <one of: question | optimize | reallocate | reroute | evaluate | debug>
MODE: <one of: answer_question | optimize_freely | suggest_reallocation | suggest_reroute | analyze_solution>
COMMANDS_DETECTED: <short comma-separated list of interpreted user commands, or "none">
CONSTRAINTS_SUMMARY: <one or two sentences summarizing key constraints from the user text>
SEQUENCING_STRATEGY: <if phased mission or sequencing requested, describe approach; otherwise "none">
PHASE1_TARGETS: <CRITICAL: If user requests a phased mission (e.g., "visit targets with priority > X first", "start with type A targets"), you MUST look at the target list in MISSION CONTEXT above and output the EXACT target IDs that match the criteria. Example: if user says "visit priority > 6 first" and T5,T6,T7,T8,T9,T10,T11,T12,T13,T14,T15 have priority 10, output "T5,T6,T7,T8,T9,T10,T11,T12,T13,T14,T15". If not a phased mission, output "all">  # noqa: E501
NEEDS_ALLOCATION: <true or false>   # true if Allocator should run or re-run
NEEDS_ROUTING: <true or false>      # true if RouteOptimizer should run or re-run
FEASIBILITY: <feasible | infeasible | unknown>
RATIONALE:
- <bullet 1 citing specific metrics or facts from the mission context>
- <bullet 2 citing specific metrics or facts from the mission context>
- <bullet 3, etc.>

Guidelines:
- When REQUEST_TYPE is `question`, MODE must be `answer_question`, and both NEEDS_ALLOCATION and NEEDS_ROUTING must be false.  # noqa: E501
- Always ground your rationale in NUMBERS from the mission metrics when available (fuel_used, total_fuel, margins, unvisited_targets, points).  # noqa: E501
- Never say that "no solution is computed" if routes exist; instead, describe the existing solution using the metrics.
"""


def strategist_node(state: MissionState) -> Dict[str, Any]:
    """Strategist analyzes the request and determines approach."""
    print("\n🧠 [STRATEGIST] Analyzing request...", file=sys.stderr)
    sys.stderr.flush()

    set_state(state)

    # Check if Coordinator flagged this as explain_only (skip to responder)
    policy = state.get("policy") or {}
    if policy.get("explain_only"):
        intent = state.get("intent", "explain")
        print(
            f"🎯 [STRATEGIST] Coordinator set explain_only=True (intent={intent}), routing to responder",
            file=sys.stderr,
        )
        return {
            "messages": [
                AIMessage(
                    content="[STRATEGIST] Explain/debug mode - routing to responder"
                )
            ],
            "strategy_analysis": f"Coordinator classified intent as '{intent}'. Skipping allocation/routing.",
            "request_type": "question",  # Forces routing to responder
        }

    user_request = state.get("user_request", "")
    print(f"📝 [STRATEGIST] User request: '{user_request}'", file=sys.stderr)
    sys.stderr.flush()

    llm = get_llm_for_state(state)

    mission_context = build_mission_context(state)
    current_solution = build_current_solution_context(state)

    # Load agent memory (learned corrections/instructions)
    memory_context = get_memory_for_prompt()
    if memory_context:
        print(
            f"📝 [STRATEGIST] Agent memory loaded into context ({len(memory_context)} chars)",
            file=sys.stderr,
        )

    # RAG Phase 2: similar past missions, computed once upstream in
    # isr_agent_multi_v4.py (advisory only — see
    # server/database/mission_retrieval.py's module docstring for why this
    # is never treated as instructions the agent must follow).
    similar_missions_context = state.get("similar_missions_context") or ""

    # Build prompt with full context - LLM reasons directly from user request
    analysis_prompt = f"""
{HARD_CONSTRAINTS}

{STRATEGIST_PROMPT}

{mission_context}

{current_solution}
{memory_context}
{similar_missions_context}

USER REQUEST: "{user_request}"

Analyze this request and provide your strategic assessment.
"""

    messages = [HumanMessage(content=analysis_prompt)]

    # Attempt LLM call with error handling
    analysis = None
    llm_success = False
    error_message = None

    try:
        print(f"🌐 [STRATEGIST] Calling LLM ({describe_llm(llm)})...", file=sys.stderr)
        sys.stderr.flush()

        response = llm.invoke(messages)

        print("✅ [STRATEGIST] API call successful", file=sys.stderr)
        sys.stderr.flush()

        analysis = response.content
        llm_success = True

        # Log first 200 chars of response for debugging
        preview = analysis[:200] if len(analysis) > 200 else analysis
        print(f"📄 [STRATEGIST] Response preview: {preview}...", file=sys.stderr)
        sys.stderr.flush()

    except Exception as e:
        error_message = str(e)
        print(f"❌ [STRATEGIST] API call FAILED: {error_message}", file=sys.stderr)
        sys.stderr.flush()

        # Fallback analysis if LLM fails
        analysis = f"""API call failed: {error_message}

REQUEST_TYPE: optimize

Defaulting to optimization mode due to API failure.
"""

    print(
        f"📋 [STRATEGIST] Analysis complete (LLM success: {llm_success})",
        file=sys.stderr,
    )
    sys.stderr.flush()

    # Parse request type from analysis
    request_type = "optimize"  # default
    analysis_lower = analysis.lower()
    if "request_type: question" in analysis_lower:
        request_type = "question"
        print("🔍 [STRATEGIST] Detected REQUEST_TYPE: question", file=sys.stderr)
    elif "request_type: command" in analysis_lower:
        request_type = "command"
        print("🔍 [STRATEGIST] Detected REQUEST_TYPE: command", file=sys.stderr)
    else:
        print(
            "⚠️  [STRATEGIST] No REQUEST_TYPE found in response, defaulting to: optimize",
            file=sys.stderr,
        )

    # Parse PHASE1_TARGETS from analysis (LLM determines which targets for Phase 1)
    phase1_target_ids: List[str] = []

    phase1_match = re.search(r"PHASE1_TARGETS:\s*([^\n]+)", analysis, re.IGNORECASE)
    if phase1_match:
        phase1_value = phase1_match.group(1).strip()
        if phase1_value.lower() != "all" and phase1_value.lower() != "none":
            # Parse comma-separated target IDs
            phase1_target_ids = [
                t.strip() for t in re.findall(r"T\d+", phase1_value.upper())
            ]
            if phase1_target_ids:
                print(
                    f"🎯 [STRATEGIST] Phase 1 targets (LLM determined): {phase1_target_ids}",
                    file=sys.stderr,
                )

    sys.stderr.flush()

    return {
        "messages": [AIMessage(content=f"[STRATEGIST]\n{analysis}")],
        "strategy_analysis": analysis,
        "request_type": request_type,
        "phase1_target_ids": phase1_target_ids if phase1_target_ids else None,
        "llm_success": llm_success,
        "llm_error": error_message,
        "node_trace": [
            trace_event(
                "strategist",
                f"Request type: {request_type}",
                llm_success=llm_success,
                phase1=phase1_target_ids,
            )
        ],
        "llm_calls": 1,
    }
