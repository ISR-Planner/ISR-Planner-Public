"""Allocator node for the v4 ISR agent workflow."""

import sys
from typing import Any, Dict, List, Tuple, cast

from langchain_core.messages import AIMessage

from ...memory.algorithm_registry import AlgorithmExecution, get_algorithm_info
from ...solver.target_allocator import allocate_targets as _allocate_targets_impl
from ...solver.target_allocator import clear_allocator_matrix, set_allocator_matrix
from ..drone_config import resolve_fuel_budget
from .state import MissionState, set_state
from .state import trace_event as _trace_event

# ============================================================================
# ALLOCATOR AGENT - Reasons about target assignments
# ============================================================================

ALLOCATOR_PROMPT = """You are the ALLOCATOR agent in an ISR mission planning system.

ROLE
Allocate a CANDIDATE SET of targets to drones to maximize total achievable priority points
under fuel budgets, while respecting ALL HARD CONSTRAINTS.

HARD CONSTRAINTS (absolute)
- A drone may ONLY be assigned targets from its ELIGIBLE TARGETS list.
- Targets in SAM zones are INELIGIBLE in hard-v1 mode (treat as excluded).
- Respect user forbiddances (e.g., forbidden priorities, forbidden airports).
- Each target may be assigned to at most one drone.

IMPORTANT
- Do NOT assign every accessible target. Downselect candidates to a manageable set.
- The exact orienteering solver performs best up to ~12 targets per drone. Prefer <= 12 candidates per drone.
- If you exclude a target that is eligible, you MUST provide a reason code.

AVAILABLE STRATEGIES (choose one)
- efficient: maximize priority/fuel ratio (auction style)
- greedy: highest priority to nearest capable drone
- balanced: distribute workload evenly
- geographic: minimize detours / corridor fit
- exclusive: prioritize targets only one drone can visit

ALLOCATION APPROACH
1) Start from eligible targets per drone.
2) Build a candidate list per drone (<= 12 preferred) that maximizes points with good fuel efficiency.
3) Assign each candidate target to exactly one best drone.
4) Produce an explicit excluded list with reason codes.

OUTPUT (MUST BE VALID JSON)
Return a single JSON object with keys:
- strategy_used: "efficient"|"greedy"|"balanced"|"geographic"|"exclusive"
- assignments: { "D1": ["T..."], "D2": [...], ... }
- excluded: [
    { "target": "T..", "reason": "IN_SAM_ZONE|TYPE_NOT_ACCESSIBLE|FORBIDDEN_PRIORITY|CANDIDATE_LIMIT|DOMINATED_LOW_VALUE", "notes": "optional" }  # noqa: E501
  ]
- rationale: {
    "D1": "brief reason",
    "D2": "brief reason"
  }
- tradeoffs: "brief summary"

Be concise but explicit.
"""


def _state_dict(state: MissionState, key: str) -> Dict[str, Any]:
    value = state.get(key)
    return value if isinstance(value, dict) else {}


def _state_list(state: MissionState, key: str) -> List[Any]:
    value = state.get(key)
    return value if isinstance(value, list) else []


def _drone_configs_from_state(state: MissionState) -> Dict[str, Dict[str, Any]]:
    return cast(Dict[str, Dict[str, Any]], state.get("drone_configs") or {})


def allocator_node(state: MissionState) -> Dict[str, Any]:
    """Allocator reasons about and performs target allocation."""
    print("\n🎯 [ALLOCATOR] Reasoning about allocation...", file=sys.stderr)
    sys.stderr.flush()

    # CRITICAL: Clear any cached distance matrix from previous solves
    # This ensures we use fresh distances from the current environment
    clear_allocator_matrix()

    set_state(state)

    # Read Coordinator policy
    policy = state.get("policy") or {}
    # Check both keys for backward compatibility (coordinator uses force_allocation)
    force_algo = bool(
        policy.get(
            "force_allocation", policy.get("force_algorithmic_allocation", False)
        )
    )
    policy_strategy = policy.get("allocation_strategy", "geographic")
    use_existing = bool(policy.get("use_existing_allocation", False))
    allocation_mods = policy.get("allocation_modifications", [])
    required_targets = policy.get(
        "required_targets", []
    )  # User-requested targets to include

    if required_targets:
        print(
            f"🎯 [ALLOCATOR] Required targets from user: {required_targets}",
            file=sys.stderr,
        )

    print(
        f"🎯 [ALLOCATOR] Policy: force_algo={force_algo}, strategy={policy_strategy}",
        file=sys.stderr,
    )

    # Priority 1: If use_existing_allocation is True, apply modifications to existing allocation
    if use_existing and allocation_mods:
        print(
            f"🔄 [ALLOCATOR] Applying {len(allocation_mods)} modifications to existing allocation",
            file=sys.stderr,
        )

        # Get existing allocation from state (seeded from existing_solution)
        existing_allocation = state.get("allocation") or {}

        if not existing_allocation:
            print(
                "⚠️ [ALLOCATOR] No existing allocation found, falling back to algorithmic",
                file=sys.stderr,
            )
        else:
            # Deep copy to avoid mutation
            new_allocation = {
                did: list(targets) for did, targets in existing_allocation.items()
            }

            # Apply each modification
            mods_applied = []
            for mod in allocation_mods:
                target_id = mod.get("target")
                to_drone = mod.get("to_drone")

                if not target_id or not to_drone:
                    continue

                # Find and remove target from its current drone
                from_drone = None
                for did, targets in new_allocation.items():
                    if target_id in targets:
                        targets.remove(target_id)
                        from_drone = did
                        break

                # Add to new drone
                if to_drone not in new_allocation:
                    new_allocation[to_drone] = []

                if target_id not in new_allocation[to_drone]:
                    new_allocation[to_drone].append(target_id)

                mods_applied.append(f"{target_id}: D{from_drone or '?'} → D{to_drone}")
                print(
                    f"   ✅ Moved {target_id} from D{from_drone or '?'} to D{to_drone}",
                    file=sys.stderr,
                )

            reasoning = (
                f"[Allocation modification - use_existing_allocation=True]\n"
                f"Applied {len(mods_applied)} modifications:\n"
                + "\n".join(f"  - {m}" for m in mods_applied)
                + "\n"
                f"New allocation: {new_allocation}"
            )

            print(
                f"📋 [ALLOCATOR] Modified allocation: {new_allocation}", file=sys.stderr
            )

            # Track algorithm used
            alloc_execution = AlgorithmExecution(
                algorithm_id="user_modified",
                algorithm_name="User-Modified Allocation",
                agent="Allocator",
                operations_performed=len(mods_applied),
                result_summary=f"Applied {len(mods_applied)} modifications to existing allocation",
            )

            return {
                "messages": [AIMessage(content=f"[ALLOCATOR]\n{reasoning}")],
                "allocation_reasoning": reasoning,
                "allocation": new_allocation,
                "allocation_strategy": "user_modified",
                "excluded_by_allocator": [],
                "algorithms_used": [alloc_execution.to_dict()],
            }

    # ALWAYS use algorithmic allocation - LLM's job is reasoning (in Strategist),
    # not arithmetic. The allocator tool handles the computation.
    print(
        f"🔧 [ALLOCATOR] Using algorithmic allocation with strategy: {policy_strategy}",
        file=sys.stderr,
    )
    env = _state_dict(state, "environment")
    configs = _drone_configs_from_state(state)
    dist_matrix = state.get("distance_matrix")

    # =========================================================================
    # PHASED MISSION: LLM (Strategist) determines Phase 1 targets directly
    # No regex parsing - the LLM understands natural language and outputs target IDs
    # =========================================================================
    phase1_target_ids_from_llm = _state_list(
        state, "phase1_target_ids"
    )  # From Strategist
    phase2_target_ids: List[str] = []
    all_targets = env.get("targets", [])
    all_target_ids = [str(t.get("id", t.get("label"))) for t in all_targets]

    if phase1_target_ids_from_llm:
        # LLM specified which targets are Phase 1 - use directly
        print("🎯 [ALLOCATOR] PHASED MISSION (LLM determined)", file=sys.stderr)
        print(f"   Phase 1 targets: {phase1_target_ids_from_llm}", file=sys.stderr)

        # Phase 2 = all targets not in Phase 1
        phase2_target_ids = [
            tid for tid in all_target_ids if tid not in phase1_target_ids_from_llm
        ]
        print(f"   Phase 2 targets: {phase2_target_ids}", file=sys.stderr)

        # Filter environment to only Phase 1 targets
        phase1_targets = [
            t
            for t in all_targets
            if str(t.get("id", t.get("label"))) in phase1_target_ids_from_llm
        ]
        env = {**env, "targets": phase1_targets}

    # Filter out excluded targets (inside SAM zones) before allocation
    sam_excluded = set(str(t) for t in _state_list(state, "excluded_targets"))
    if sam_excluded:
        env_for_alloc = {
            **env,
            "targets": [
                t
                for t in env.get("targets", [])
                if str(t.get("id", t.get("label", ""))) not in sam_excluded
            ],
        }
        print(
            f"🚫 [ALLOCATOR] Filtered {len(sam_excluded)} SAM-excluded targets: {sorted(sam_excluded)}",
            file=sys.stderr,
        )
    else:
        env_for_alloc = env

    # Build matrix_data for allocator
    matrix_data = None
    if dist_matrix:
        labels = list(dist_matrix.keys())
        matrix = [[dist_matrix[f].get(t, 1000) for t in labels] for f in labels]
        matrix_data = {"labels": labels, "matrix": matrix}
        set_allocator_matrix(matrix_data)

    # Call algorithmic allocation with policy-specified strategy
    allocation = _allocate_targets_impl(
        env_for_alloc, configs, policy_strategy, matrix_data
    )
    strategy_used = policy_strategy
    excluded_targets: List[Dict[str, Any]] = []

    # =========================================================================
    # ENFORCE REQUIRED TARGETS: If user requested specific targets to be included,
    # make sure they appear in the allocation. Add them to the closest drone.
    # =========================================================================
    forced_additions = []
    if required_targets:
        # Find all currently allocated targets
        all_allocated = set()
        for drone_id, targets in allocation.items():
            all_allocated.update(targets)

        # Find required targets that are missing
        missing_required = [t for t in required_targets if t not in all_allocated]

        if missing_required:
            print(
                f"⚠️ [ALLOCATOR] Missing required targets: {missing_required}",
                file=sys.stderr,
            )

            # Get target positions
            targets_data = {
                t.get("id", t.get("label")): t for t in env.get("targets", [])
            }

            # For each missing target, assign to the best drone
            for target_id in missing_required:
                target_data = targets_data.get(target_id)
                if not target_data:
                    print(
                        f"   ⚠️ Target {target_id} not found in environment",
                        file=sys.stderr,
                    )
                    continue

                # Find the drone with most remaining fuel capacity that can reach this target
                best_drone = None
                best_score = -1.0

                for drone_id, drone_cfg in configs.items():
                    if not drone_cfg.get("enabled", True):
                        continue

                    # Check if drone has fuel budget (higher = better)
                    fuel_budget = resolve_fuel_budget(drone_cfg, drone_id=str(drone_id))
                    current_allocated = len(allocation.get(drone_id, []))

                    # Simple heuristic: prefer drones with fewer targets and more fuel
                    score = fuel_budget - (current_allocated * 20)

                    if score > best_score:
                        best_score = score
                        best_drone = drone_id

                if best_drone:
                    if best_drone not in allocation:
                        allocation[best_drone] = []
                    allocation[best_drone].append(target_id)
                    forced_additions.append(f"{target_id} → D{best_drone}")
                    print(
                        f"   ✅ Forced {target_id} into D{best_drone} allocation",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"   ❌ Could not find drone for required target {target_id}",
                        file=sys.stderr,
                    )

    # Count allocated targets (needed for algorithm tracking)
    total_allocated = sum(len(targets) for targets in allocation.values())
    num_drones = len([d for d in allocation if allocation[d]])

    reasoning_parts = [
        "[Algorithmic allocation]",
        f"Strategy: {strategy_used}",
    ]
    if required_targets:
        reasoning_parts.append(f"Required targets (user request): {required_targets}")
    if forced_additions:
        reasoning_parts.append(f"Forced additions: {', '.join(forced_additions)}")
    reasoning_parts.append(f"Final allocation: {allocation}")
    reasoning = "\n".join(reasoning_parts)

    print(
        f"📋 [ALLOCATOR] Algorithmic allocation complete: {allocation}", file=sys.stderr
    )

    # Track algorithm used
    strategy_info = get_algorithm_info(strategy_used)
    strategy_name = strategy_info["name"] if strategy_info else strategy_used.title()

    alloc_execution = AlgorithmExecution(
        algorithm_id=strategy_used,
        algorithm_name=strategy_name,
        agent="Allocator",
        operations_performed=total_allocated,
        result_summary=f"Assigned {total_allocated} targets across {num_drones} drones",
    )

    alloc_summary = {did: len(tids) for did, tids in allocation.items()}
    return {
        "messages": [AIMessage(content=f"[ALLOCATOR]\n{reasoning}")],
        "allocation_reasoning": reasoning,
        "allocation": allocation,
        "allocation_strategy": strategy_used,
        "excluded_by_allocator": excluded_targets,
        "algorithms_used": [alloc_execution.to_dict()],
        "phase2_target_ids": phase2_target_ids,  # Targets deferred for Phase 2
        "node_trace": [
            _trace_event(
                "allocator",
                f"Strategy: {strategy_used}, allocated {total_allocated} targets",
                allocation=alloc_summary,
            )
        ],
    }


def parse_allocation_from_json(
    reasoning: str,
    state: MissionState,
    fallback_strategy: str = "efficient",
) -> Tuple[Dict[str, List[str]], str, List[Dict[str, Any]]]:
    """
    Parse allocation from JSON response.

    Args:
        reasoning: LLM response text
        state: Current mission state
        fallback_strategy: Strategy to use if parsing fails (from Coordinator policy)

    Returns:
        (allocation, strategy_used, excluded_targets)
    """
    import json
    import re

    configs = _drone_configs_from_state(state)
    allocation: Dict[str, List[str]] = {}

    # Initialize all enabled drones
    for did in configs.keys():
        if configs[did].get("enabled", did == "1"):
            allocation[did] = []

    strategy_used = "unknown"
    excluded_targets: List[Dict[str, Any]] = []

    # Try to extract JSON from the response
    try:
        # Remove markdown code fences if present
        json_str = reasoning.strip()
        if json_str.startswith("```"):
            # Remove opening fence
            json_str = re.sub(r"^```(?:json)?\s*\n?", "", json_str)
            # Remove closing fence
            json_str = re.sub(r"\n?```\s*$", "", json_str)

        # Find JSON object in the response
        match = re.search(r"\{[\s\S]*\}", json_str)
        if match:
            json_str = match.group(0)

        data = json.loads(json_str)

        # Extract strategy
        strategy_used = data.get("strategy_used", "unknown")

        # Extract assignments
        assignments = data.get("assignments", {})
        for drone_key, targets in assignments.items():
            # Normalize drone ID (remove "D" prefix if present)
            did = str(drone_key).replace("D", "").strip()
            if did in allocation:
                allocation[did] = [str(t) for t in targets] if targets else []

        # Extract excluded targets
        excluded_targets = data.get("excluded", [])

        print("📋 [ALLOCATOR] Successfully parsed JSON allocation", file=sys.stderr)

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(
            f"⚠️ [ALLOCATOR] JSON parsing failed: {e}, falling back to text parsing",
            file=sys.stderr,
        )
        # Fall back to legacy text parsing
        allocation = parse_allocation_from_reasoning(reasoning, state)
        # Try to extract strategy from text
        for line in reasoning.split("\n"):
            if "strategy_used" in line.lower() or "STRATEGY_USED:" in line.upper():
                for strat in [
                    "efficient",
                    "greedy",
                    "balanced",
                    "geographic",
                    "exclusive",
                ]:
                    if strat in line.lower():
                        strategy_used = strat
                        break

    # Log what we parsed
    print(f"📋 [ALLOCATOR] Parsed allocation: {allocation}", file=sys.stderr)
    total_allocated = sum(len(targets) for targets in allocation.values())
    print(f"📋 [ALLOCATOR] Total targets allocated: {total_allocated}", file=sys.stderr)

    # If parsing failed completely, fall back to algorithmic allocation
    # Use fallback_strategy from Coordinator (not hardcoded "efficient")
    if all(len(v) == 0 for v in allocation.values()):
        print(
            f"⚠️ [ALLOCATOR] Parsing failed, using fallback allocation (strategy={fallback_strategy})",
            file=sys.stderr,
        )
        env_for_alloc = _state_dict(state, "environment")
        dist_matrix = state.get("distance_matrix")

        matrix_data = None
        if dist_matrix:
            labels = list(dist_matrix.keys())
            matrix = [[dist_matrix[f].get(t, 1000) for t in labels] for f in labels]
            matrix_data = {"labels": labels, "matrix": matrix}
            set_allocator_matrix(matrix_data)

        allocation = _allocate_targets_impl(
            env_for_alloc, configs, fallback_strategy, matrix_data
        )
        strategy_used = fallback_strategy
        print(f"📋 [ALLOCATOR] Fallback allocation: {allocation}", file=sys.stderr)

    return allocation, strategy_used, excluded_targets


def parse_allocation_from_reasoning(
    reasoning: str, state: MissionState
) -> Dict[str, List[str]]:
    """Parse allocation from LLM reasoning output (legacy text format)."""
    allocation: Dict[str, List[str]] = {}
    configs = _drone_configs_from_state(state)

    # Initialize all enabled drones
    for did in configs.keys():
        if configs[did].get("enabled", did == "1"):
            allocation[did] = []

    # Look for ALLOCATION_RESULT section
    lines = reasoning.split("\n")
    in_result = False

    for line in lines:
        line = line.strip()
        if "ALLOCATION_RESULT" in line.upper():
            in_result = True
            continue
        if in_result and line.startswith("D"):
            # Parse "D1: T1, T3, T5" format
            parts = line.split(":")
            if len(parts) >= 2:
                did = parts[0].strip().replace("D", "")
                targets_str = parts[1].strip()
                if targets_str and targets_str.lower() not in ["none", "(none)", ""]:
                    targets = [t.strip() for t in targets_str.split(",") if t.strip()]
                    if did in allocation:
                        allocation[did] = targets
        elif in_result and line.startswith("TRADE") or line.startswith("==="):
            in_result = False

    return allocation
