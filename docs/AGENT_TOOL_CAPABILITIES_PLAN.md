# Agent Tool Capabilities — Implementation Plan

> **Purpose**: Add on-demand agent tools so users can ask the agent to optimize solutions, add constraints like "visit T3 before T5", and modify routes through natural language commands.

> **Date**: 2026-06-08

---

## Overview

### Current State
- Agent has 3 optimizer tools: `insert_unvisited_tool`, `swap_closer_tool`, `remove_crossings_tool`
- Tools are auto-invoked by LLM during Optimizer node, not on user command
- No support for ordering constraints ("visit T3 before T5")
- No unified "optimize" command
- Agent can answer questions about solutions but cannot modify them on demand

### Target State
- User can say "optimize this solution" and agent runs full pipeline
- User can say "visit T3 before T5" and agent re-solves with constraint
- User can say "exclude T7 from this solution" and agent removes it
- User can say "assign T5 to D2 instead" and agent reallocates
- Agent understands these intents and invokes appropriate tools

### Key Design Decisions
1. **Tool-based architecture** — Each capability is a bound tool the LLM can invoke
2. **Intent recognition** — Strategist detects modification intents and routes appropriately
3. **Constraint accumulation** — Constraints persist in state for re-solving
4. **Post-process ordering** — Ordering constraints enforced after solving (simpler than modifying Held-Karp)

---

## Part 1: Intent Recognition

### 1.1 New Intent Categories

**Location**: `server/agents/coordinator_v4.py`

Add new intents to the coordinator's classification:

```python
INTENT_CATEGORIES = {
    # Existing
    "plan": "User wants to create a new mission plan",
    "replan": "User wants to modify existing plan",
    "explain": "User wants explanation of solution",
    "what_if": "User asks hypothetical question",
    "debug": "User reports a problem",

    # New modification intents
    "optimize": "User wants to run optimizers on current solution",
    "add_constraint": "User wants to add ordering/assignment constraints",
    "exclude_target": "User wants to remove targets from solution",
    "reassign_target": "User wants to move target to different drone",
    "modify_route": "User wants to change route order or structure",
}
```

### 1.2 Intent Detection Patterns

```python
INTENT_PATTERNS = {
    "optimize": [
        r"optimize",
        r"improve",
        r"run optimizer",
        r"make.*better",
        r"reduce fuel",
        r"insert missed",
        r"swap",
        r"remove crossing",
    ],
    "add_constraint": [
        r"visit\s+(\w+)\s+before\s+(\w+)",
        r"(\w+)\s+first.*then\s+(\w+)",
        r"go to\s+(\w+)\s+before",
        r"(\w+)\s+must come before\s+(\w+)",
        r"start with\s+(\w+)",
        r"end with\s+(\w+)",
    ],
    "exclude_target": [
        r"skip\s+(\w+)",
        r"don't visit\s+(\w+)",
        r"remove\s+(\w+)",
        r"exclude\s+(\w+)",
        r"drop\s+(\w+)",
    ],
    "reassign_target": [
        r"assign\s+(\w+)\s+to\s+(D\d+)",
        r"move\s+(\w+)\s+to\s+(D\d+)",
        r"(\w+)\s+should be.*?(D\d+)",
        r"give\s+(\w+)\s+to\s+(D\d+)",
    ],
}
```

### 1.3 Constraint Extraction

**New function in coordinator_v4.py**:

```python
def extract_constraints(user_message: str) -> Dict[str, Any]:
    """
    Extract actionable constraints from user message.

    Returns:
        {
            "ordering": [("T3", "T5"), ("T1", "T2")],  # T3 before T5, T1 before T2
            "exclusions": ["T7", "T8"],                 # Don't visit these
            "assignments": {"T5": "2", "T6": "1"},      # Force target to drone
            "start_with": {"1": "T1"},                  # Drone 1 starts with T1
            "end_with": {"2": "T9"},                    # Drone 2 ends with T9
        }
    """
    constraints = {
        "ordering": [],
        "exclusions": [],
        "assignments": {},
        "start_with": {},
        "end_with": {},
    }

    # Extract ordering: "visit T3 before T5"
    ordering_pattern = r"visit\s+(T\d+)\s+before\s+(T\d+)"
    for match in re.finditer(ordering_pattern, user_message, re.IGNORECASE):
        constraints["ordering"].append((match.group(1), match.group(2)))

    # Extract exclusions: "skip T7"
    exclusion_pattern = r"(?:skip|exclude|remove|don't visit)\s+(T\d+)"
    for match in re.finditer(exclusion_pattern, user_message, re.IGNORECASE):
        constraints["exclusions"].append(match.group(1))

    # Extract assignments: "assign T5 to D2"
    assignment_pattern = r"(?:assign|move|give)\s+(T\d+)\s+to\s+D(\d+)"
    for match in re.finditer(assignment_pattern, user_message, re.IGNORECASE):
        constraints["assignments"][match.group(1)] = match.group(2)

    # Extract start_with: "D1 should start with T1"
    start_pattern = r"D(\d+)\s+(?:should\s+)?start\s+with\s+(T\d+)"
    for match in re.finditer(start_pattern, user_message, re.IGNORECASE):
        constraints["start_with"][match.group(1)] = match.group(2)

    return constraints
```

---

## Part 2: New Agent Tools

### 2.1 Tool Registry Update

**Location**: `server/agents/isr_agent_multi_v4.py`

```python
# Existing tools
OPTIMIZER_TOOLS = [
    insert_unvisited_tool,
    swap_closer_tool,
    remove_crossings_tool,
]

# New modification tools
MODIFICATION_TOOLS = [
    run_optimizer_pipeline_tool,
    add_ordering_constraint_tool,
    exclude_targets_tool,
    reassign_target_tool,
    re_solve_with_constraints_tool,
]

# Combined for agent binding
ALL_AGENT_TOOLS = OPTIMIZER_TOOLS + MODIFICATION_TOOLS
```

### 2.2 Tool: Run Optimizer Pipeline

```python
@tool
def run_optimizer_pipeline_tool() -> str:
    """
    Run the full optimizer pipeline (INSERT → SWAP → NO-CROSS) on the current solution.
    Use this when user asks to "optimize", "improve", or "make the solution better".
    Returns summary of improvements made.
    """
    state = get_state()

    if not state.get("routes"):
        return "Error: No solution to optimize. Run solve first."

    # Import pipeline function
    from server.solver.solver_bridge import apply_optimizer_pipeline

    # Run pipeline
    result = apply_optimizer_pipeline(
        routes=state["routes"],
        env=state["environment"],
        drone_configs=state["drone_configs"],
        distance_matrix=state.get("distance_matrix"),
    )

    if not result.get("success"):
        return f"Optimization failed: {result.get('error', 'Unknown error')}"

    # Update state with optimized routes
    set_state("routes", result["routes"])

    # Build summary
    summary = result.get("pipeline_summary", {})
    insertions = summary.get("insertions", [])
    swaps = summary.get("swaps_made", [])
    crossings_fixed = summary.get("crossings_fixed", 0)
    passes = summary.get("passes", 1)

    improvements = []
    if insertions:
        improvements.append(f"Inserted {len(insertions)} targets: {', '.join(insertions)}")
    if swaps:
        improvements.append(f"Made {len(swaps)} swaps")
    if crossings_fixed:
        improvements.append(f"Fixed {crossings_fixed} crossings")

    if improvements:
        return f"Optimization complete ({passes} passes). " + "; ".join(improvements)
    else:
        return "Optimization complete. No improvements possible — solution is already optimal."
```

### 2.3 Tool: Add Ordering Constraint

```python
@tool
def add_ordering_constraint_tool(before_target: str, after_target: str) -> str:
    """
    Add constraint that before_target must be visited before after_target.
    Example: add_ordering_constraint("T3", "T5") means T3 must be visited before T5.
    This will re-solve to enforce the ordering.

    Args:
        before_target: Target ID that must be visited first (e.g., "T3")
        after_target: Target ID that must be visited second (e.g., "T5")
    """
    state = get_state()

    # Validate targets exist
    targets = state.get("environment", {}).get("targets", [])
    target_ids = [t.get("id") or t.get("name") for t in targets]

    if before_target not in target_ids:
        return f"Error: Target {before_target} not found in environment."
    if after_target not in target_ids:
        return f"Error: Target {after_target} not found in environment."

    # Add to constraints in state
    constraints = state.get("constraints", {})
    ordering = constraints.get("ordering", [])
    ordering.append((before_target, after_target))
    constraints["ordering"] = ordering
    set_state("constraints", constraints)

    # Re-solve with constraint
    result = re_solve_with_ordering(state, ordering)

    if result.get("success"):
        set_state("routes", result["routes"])
        set_state("allocation", result["allocation"])
        return f"Constraint applied: {before_target} will now be visited before {after_target}."
    else:
        return f"Could not apply constraint: {result.get('error', 'Unknown error')}"
```

### 2.4 Tool: Exclude Targets

```python
@tool
def exclude_targets_tool(target_ids: str) -> str:
    """
    Exclude specified targets from the solution. They will not be visited.

    Args:
        target_ids: Comma-separated list of target IDs to exclude (e.g., "T3,T5,T7")
    """
    state = get_state()

    # Parse target IDs
    targets_to_exclude = [t.strip() for t in target_ids.split(",")]

    # Validate targets exist
    targets = state.get("environment", {}).get("targets", [])
    target_id_set = {t.get("id") or t.get("name") for t in targets}

    invalid = [t for t in targets_to_exclude if t not in target_id_set]
    if invalid:
        return f"Error: Targets not found: {', '.join(invalid)}"

    # Add to excluded_targets in state
    excluded = state.get("excluded_targets", [])
    excluded.extend(targets_to_exclude)
    excluded = list(set(excluded))  # Dedupe
    set_state("excluded_targets", excluded)

    # Re-solve excluding these targets
    result = re_solve_excluding(state, excluded)

    if result.get("success"):
        set_state("routes", result["routes"])
        set_state("allocation", result["allocation"])
        return f"Excluded {len(targets_to_exclude)} targets. Solution re-computed."
    else:
        return f"Could not exclude targets: {result.get('error', 'Unknown error')}"
```

### 2.5 Tool: Reassign Target

```python
@tool
def reassign_target_tool(target_id: str, drone_id: str) -> str:
    """
    Force a target to be assigned to a specific drone.

    Args:
        target_id: Target to reassign (e.g., "T5")
        drone_id: Drone to assign it to (e.g., "2" for D2)
    """
    state = get_state()

    # Validate target exists
    targets = state.get("environment", {}).get("targets", [])
    target_ids = [t.get("id") or t.get("name") for t in targets]
    if target_id not in target_ids:
        return f"Error: Target {target_id} not found."

    # Validate drone exists
    drone_configs = state.get("drone_configs", {})
    if drone_id not in drone_configs:
        return f"Error: Drone D{drone_id} not found."

    # Check drone can access this target type
    target = next(t for t in targets if (t.get("id") or t.get("name")) == target_id)
    target_type = target.get("type", "a")
    drone_access = drone_configs[drone_id].get("target_access", {})
    if not drone_access.get(target_type, True):
        return f"Error: Drone D{drone_id} cannot access target type '{target_type}'."

    # Add forced assignment
    constraints = state.get("constraints", {})
    assignments = constraints.get("assignments", {})
    assignments[target_id] = drone_id
    constraints["assignments"] = assignments
    set_state("constraints", constraints)

    # Re-solve with forced assignment
    result = re_solve_with_assignments(state, assignments)

    if result.get("success"):
        set_state("routes", result["routes"])
        set_state("allocation", result["allocation"])
        return f"Reassigned {target_id} to D{drone_id}. Solution re-computed."
    else:
        return f"Could not reassign: {result.get('error', 'Unknown error')}"
```

---

## Part 3: Constraint Enforcement in Solver

### 3.1 Ordering Constraint Enforcement

**Approach**: Post-process the route to enforce ordering.

**Location**: New function in `server/solver/solver_bridge.py`

```python
def enforce_ordering_constraints(
    routes: Dict[str, Dict],
    ordering_constraints: List[Tuple[str, str]]
) -> Dict[str, Dict]:
    """
    Enforce ordering constraints on solved routes.

    For each (before, after) constraint:
    1. Find which drone(s) have both targets
    2. If same drone: swap positions if needed
    3. If different drones: move 'after' target to 'before' drone, place it after

    Args:
        routes: Current solution routes
        ordering_constraints: List of (before_target, after_target) tuples

    Returns:
        Modified routes with constraints enforced
    """
    routes = copy.deepcopy(routes)

    for before_target, after_target in ordering_constraints:
        # Find which drones have these targets
        before_drone = None
        after_drone = None

        for drone_id, route_data in routes.items():
            route = route_data.get("route", [])
            if before_target in route:
                before_drone = drone_id
            if after_target in route:
                after_drone = drone_id

        if before_drone is None or after_drone is None:
            # One or both targets not in solution — skip
            continue

        if before_drone == after_drone:
            # Same drone — just reorder within route
            route = routes[before_drone]["route"]
            before_idx = route.index(before_target)
            after_idx = route.index(after_target)

            if before_idx > after_idx:
                # Wrong order — swap
                route.remove(after_target)
                route.insert(before_idx, after_target)
                routes[before_drone]["route"] = route
        else:
            # Different drones — move after_target to before_drone
            # Remove from after_drone
            routes[after_drone]["route"].remove(after_target)

            # Insert into before_drone right after before_target
            route = routes[before_drone]["route"]
            before_idx = route.index(before_target)
            route.insert(before_idx + 1, after_target)
            routes[before_drone]["route"] = route

    # Recalculate distances and validate fuel
    routes = recalculate_route_distances(routes)

    return routes
```

### 3.2 Re-solve with Constraints

**Location**: New function in `server/solver/solver_bridge.py`

```python
def solve_with_constraints(
    env: Dict,
    drone_configs: Dict,
    constraints: Dict,
    distance_matrix: Dict = None,
) -> Dict:
    """
    Solve mission with user-specified constraints.

    Args:
        env: Environment (airports, targets, SAMs)
        drone_configs: Drone configurations
        constraints: {
            "ordering": [(before, after), ...],
            "exclusions": [target_id, ...],
            "assignments": {target_id: drone_id, ...},
        }
        distance_matrix: Optional pre-computed matrix

    Returns:
        Solution with constraints enforced
    """
    # Apply exclusions
    excluded = set(constraints.get("exclusions", []))
    filtered_env = filter_excluded_targets(env, excluded)

    # Apply forced assignments to allocator
    forced_assignments = constraints.get("assignments", {})

    # Solve with allocation
    result = solve_mission_with_allocation(
        env=filtered_env,
        drone_configs=drone_configs,
        strategy="geographic",
        forced_assignments=forced_assignments,
        distance_matrix=distance_matrix,
    )

    if not result.get("routes"):
        return {"success": False, "error": "Solving failed"}

    # Enforce ordering constraints (post-process)
    ordering = constraints.get("ordering", [])
    if ordering:
        result["routes"] = enforce_ordering_constraints(result["routes"], ordering)

    # Validate fuel constraints still met
    validation = validate_solution_fuel(result["routes"], drone_configs)
    if not validation["valid"]:
        return {
            "success": False,
            "error": f"Constraints violated fuel budget: {validation['violations']}"
        }

    return {
        "success": True,
        "routes": result["routes"],
        "allocation": result.get("allocation", {}),
    }
```

### 3.3 Forced Assignment in Allocator

**Location**: Modify `server/solver/target_allocator.py`

```python
def allocate_targets(
    targets: List[Dict],
    drones: List[Dict],
    distance_matrix: Dict,
    strategy: str = "geographic",
    forced_assignments: Dict[str, str] = None,  # NEW PARAMETER
) -> Dict[str, List[str]]:
    """
    Allocate targets to drones.

    Args:
        targets: List of targets
        drones: List of drone configs
        distance_matrix: SAM-aware distances
        strategy: Allocation strategy
        forced_assignments: {target_id: drone_id} - must respect these
    """
    forced_assignments = forced_assignments or {}

    # Pre-assign forced targets
    allocation = {d["id"]: [] for d in drones}
    remaining_targets = []

    for target in targets:
        tid = target.get("id") or target.get("name")
        if tid in forced_assignments:
            drone_id = forced_assignments[tid]
            allocation[drone_id].append(tid)
        else:
            remaining_targets.append(target)

    # Allocate remaining using strategy
    remaining_allocation = _allocate_by_strategy(
        remaining_targets, drones, distance_matrix, strategy
    )

    # Merge
    for drone_id, targets in remaining_allocation.items():
        allocation[drone_id].extend(targets)

    return allocation
```

---

## Part 4: Agent Workflow Changes

### 4.1 Strategist Node Updates

**Location**: `server/agents/isr_agent_multi_v4.py` — strategist_node

Add modification intent handling:

```python
def strategist_node(state: MissionState) -> Dict[str, Any]:
    """Classify intent and extract constraints."""

    user_message = state.get("user_request", "")

    # Detect intent
    intent = classify_intent(user_message)

    # Extract constraints if modification intent
    constraints = {}
    if intent in ["add_constraint", "exclude_target", "reassign_target", "modify_route"]:
        constraints = extract_constraints(user_message)

    # Build policy
    policy = {
        "intent": intent,
        "constraints": constraints,
        "run_optimizer": intent == "optimize",
        "needs_resolve": bool(constraints),
    }

    return {
        "intent": intent,
        "policy": policy,
        "constraints": constraints,
    }
```

### 4.2 New Modifier Node

Add a new node specifically for handling modification requests:

```python
def modifier_node(state: MissionState) -> Dict[str, Any]:
    """
    Handle modification requests: optimize, add constraints, exclude targets, etc.
    """
    policy = state.get("policy", {})
    constraints = state.get("constraints", {})

    results = []

    # Run optimizer if requested
    if policy.get("run_optimizer"):
        opt_result = run_optimizer_pipeline_tool.invoke({})
        results.append(opt_result)

    # Apply ordering constraints
    if constraints.get("ordering"):
        for before, after in constraints["ordering"]:
            result = add_ordering_constraint_tool.invoke({
                "before_target": before,
                "after_target": after
            })
            results.append(result)

    # Apply exclusions
    if constraints.get("exclusions"):
        result = exclude_targets_tool.invoke({
            "target_ids": ",".join(constraints["exclusions"])
        })
        results.append(result)

    # Apply reassignments
    if constraints.get("assignments"):
        for target, drone in constraints["assignments"].items():
            result = reassign_target_tool.invoke({
                "target_id": target,
                "drone_id": drone
            })
            results.append(result)

    return {
        "modification_results": results,
        "routes": get_state().get("routes"),
        "allocation": get_state().get("allocation"),
    }
```

### 4.3 Updated Workflow Graph

```python
def build_workflow():
    workflow = StateGraph(MissionState)

    # Nodes
    workflow.add_node("strategist", strategist_node)
    workflow.add_node("allocator", allocator_node)
    workflow.add_node("route_optimizer", route_optimizer_node)
    workflow.add_node("modifier", modifier_node)  # NEW
    workflow.add_node("critic", critic_node)
    workflow.add_node("responder", responder_node)

    # Edges
    workflow.add_edge(START, "strategist")

    # Conditional routing based on intent
    workflow.add_conditional_edges(
        "strategist",
        route_by_intent,
        {
            "plan": "allocator",           # New mission → allocate + solve
            "replan": "allocator",         # Replan → allocate + solve
            "optimize": "modifier",        # Optimize → run optimizer
            "add_constraint": "modifier",  # Constraint → modify + resolve
            "exclude_target": "modifier",  # Exclude → modify + resolve
            "reassign_target": "modifier", # Reassign → modify + resolve
            "explain": "responder",        # Question → answer directly
            "what_if": "responder",        # Question → answer directly
        }
    )

    workflow.add_edge("allocator", "route_optimizer")
    workflow.add_edge("route_optimizer", "critic")
    workflow.add_edge("modifier", "critic")  # After modification, critique
    workflow.add_edge("critic", "responder")
    workflow.add_edge("responder", END)

    return workflow.compile()
```

---

## Part 5: State Management

### 5.1 Extended State Schema

```python
class MissionState(TypedDict):
    # Existing fields
    messages: Annotated[list, add_messages]
    environment: Optional[Dict[str, Any]]
    drone_configs: Optional[Dict[str, Any]]
    distance_matrix: Optional[Dict[str, Any]]
    routes: Optional[Dict[str, Any]]
    allocation: Optional[Dict[str, Any]]
    mission_metrics: Optional[Dict[str, Any]]

    # New fields for constraints
    constraints: Optional[Dict[str, Any]]  # {ordering, exclusions, assignments}
    excluded_targets: Optional[List[str]]  # Targets to exclude
    modification_results: Optional[List[str]]  # Results from modifier tools

    # Workflow control
    intent: Optional[str]
    policy: Optional[Dict[str, Any]]
```

### 5.2 Constraint Persistence

Constraints accumulate during conversation:

```python
def accumulate_constraints(state: MissionState, new_constraints: Dict) -> Dict:
    """
    Merge new constraints with existing ones.
    User can add multiple constraints across messages.
    """
    existing = state.get("constraints", {})

    # Merge ordering
    existing_ordering = existing.get("ordering", [])
    new_ordering = new_constraints.get("ordering", [])
    merged_ordering = existing_ordering + [o for o in new_ordering if o not in existing_ordering]

    # Merge exclusions
    existing_exclusions = set(existing.get("exclusions", []))
    new_exclusions = set(new_constraints.get("exclusions", []))
    merged_exclusions = list(existing_exclusions | new_exclusions)

    # Merge assignments (new overwrites old for same target)
    existing_assignments = existing.get("assignments", {})
    new_assignments = new_constraints.get("assignments", {})
    merged_assignments = {**existing_assignments, **new_assignments}

    return {
        "ordering": merged_ordering,
        "exclusions": merged_exclusions,
        "assignments": merged_assignments,
    }
```

### 5.3 Clear Constraints Command

```python
@tool
def clear_constraints_tool() -> str:
    """
    Clear all constraints and re-solve without them.
    Use when user says "start fresh", "remove all constraints", etc.
    """
    state = get_state()
    set_state("constraints", {})
    set_state("excluded_targets", [])

    # Re-solve without constraints
    result = solve_mission_with_allocation(
        env=state["environment"],
        drone_configs=state["drone_configs"],
        strategy="geographic",
    )

    if result.get("routes"):
        set_state("routes", result["routes"])
        set_state("allocation", result["allocation"])
        return "All constraints cleared. Solution re-computed from scratch."
    else:
        return "Constraints cleared but re-solve failed."
```

---

## Part 6: Example Conversations

### Example 1: Optimize Solution

```
User: "Solve this mission"
Agent: [Runs allocator → solver → critic → responder]
       "Mission solved. 8 targets visited, 2 missed (T7, T9 - fuel constraints)."

User: "Optimize it"
Agent: [Strategist detects "optimize" intent]
       [Routes to modifier node]
       [Runs run_optimizer_pipeline_tool]
       "Optimization complete. Inserted T7 into D2's route. 9 targets now visited."

User: "Can you also remove crossings?"
Agent: [Already done by optimizer, but can run NO-CROSS explicitly]
       "No crossings found — routes are already clean."
```

### Example 2: Ordering Constraint

```
User: "Solve this mission"
Agent: "Mission solved. D1 visits T1, T3, T5 in that order."

User: "I need D1 to visit T5 before T3"
Agent: [Strategist detects "add_constraint" intent]
       [Extracts: ordering=[("T5", "T3")]]
       [Modifier runs add_ordering_constraint_tool("T5", "T3")]
       "Constraint applied. D1 now visits T1, T5, T3. Fuel usage increased by 12km."

User: "Also, T1 should be visited before T5"
Agent: [Extracts: ordering=[("T1", "T5")]]
       [Merges with existing: ordering=[("T5", "T3"), ("T1", "T5")]]
       "Constraint applied. D1 now visits T1, T5, T3 (in that exact order)."
```

### Example 3: Exclude and Reassign

```
User: "Solve this mission"
Agent: "Mission solved. D1: T1,T2,T3. D2: T4,T5."

User: "Skip T2 and assign T4 to D1"
Agent: [Extracts: exclusions=["T2"], assignments={"T4": "1"}]
       [Modifier runs exclude_targets_tool + reassign_target_tool]
       "Done. T2 excluded. T4 moved to D1. New routes: D1: T1,T4,T3. D2: T5."

User: "Start fresh"
Agent: [Runs clear_constraints_tool]
       "All constraints cleared. Original solution restored."
```

---

## Part 7: Testing Plan

### 7.1 Unit Tests

```python
# tests/test_agent_tools.py

def test_run_optimizer_pipeline_tool():
    """Tool runs full pipeline and returns improvements."""

def test_add_ordering_constraint_same_drone():
    """Constraint reorders targets within same drone."""

def test_add_ordering_constraint_different_drones():
    """Constraint moves target to other drone."""

def test_exclude_targets():
    """Excluded targets removed from solution."""

def test_reassign_target():
    """Target moved to specified drone."""

def test_clear_constraints():
    """All constraints cleared and solution reset."""

def test_constraint_accumulation():
    """Multiple constraints accumulate correctly."""

def test_constraint_fuel_validation():
    """Constraints that violate fuel budget are rejected."""
```

### 7.2 Integration Tests

```python
def test_optimize_intent_detection():
    """'Optimize this' routes to modifier node."""

def test_ordering_intent_extraction():
    """'Visit T3 before T5' extracts correct constraint."""

def test_full_conversation_flow():
    """Multi-turn conversation with constraints."""
```

### 7.3 Manual Testing Checklist

- [ ] Say "solve this mission" — get solution
- [ ] Say "optimize it" — pipeline runs
- [ ] Say "visit T3 before T5" — constraint applied
- [ ] Say "skip T7" — target excluded
- [ ] Say "assign T5 to D2" — target reassigned
- [ ] Say "start fresh" — constraints cleared
- [ ] Verify fuel constraints respected after modifications
- [ ] Verify ordering enforced correctly

---

## Part 8: Implementation Order

### Phase 1: Intent Recognition (Day 1)
1. Add new intent categories to coordinator_v4.py
2. Implement intent detection patterns
3. Implement extract_constraints() function
4. Update strategist node to route by intent

### Phase 2: Core Tools (Day 2)
1. Implement run_optimizer_pipeline_tool
2. Implement add_ordering_constraint_tool
3. Implement exclude_targets_tool
4. Implement reassign_target_tool
5. Implement clear_constraints_tool

### Phase 3: Solver Integration (Day 2-3)
1. Implement enforce_ordering_constraints() in solver_bridge
2. Implement solve_with_constraints()
3. Add forced_assignments parameter to allocator
4. Add fuel validation after constraint application

### Phase 4: Workflow Changes (Day 3)
1. Add modifier_node
2. Update workflow graph with conditional routing
3. Extend MissionState with constraint fields
4. Implement constraint accumulation

### Phase 5: Testing (Day 4)
1. Write unit tests
2. Write integration tests
3. Manual testing
4. Fix bugs

---

## Appendix A: File Changes Summary

| File | Changes |
|------|---------|
| `server/agents/coordinator_v4.py` | Intent patterns, extract_constraints() |
| `server/agents/isr_agent_multi_v4.py` | New tools, modifier_node, workflow updates |
| `server/solver/solver_bridge.py` | enforce_ordering_constraints(), solve_with_constraints() |
| `server/solver/target_allocator.py` | forced_assignments parameter |
| `tests/test_agent_tools.py` | New test file |

---

## Appendix B: Tool Binding Summary

```python
# All tools available to agent
ALL_AGENT_TOOLS = [
    # Existing optimizers (auto-invoked in optimizer node)
    insert_unvisited_tool,
    swap_closer_tool,
    remove_crossings_tool,

    # New modification tools (invoked on user command)
    run_optimizer_pipeline_tool,
    add_ordering_constraint_tool,
    exclude_targets_tool,
    reassign_target_tool,
    clear_constraints_tool,
]

# Bind to LLM
llm_with_tools = llm.bind_tools(ALL_AGENT_TOOLS, tool_choice="auto")
```

---

## Appendix C: Constraint Data Flow

```
User: "Visit T3 before T5"
         ↓
Strategist: extract_constraints()
         → constraints = {"ordering": [("T3", "T5")]}
         ↓
Modifier Node: add_ordering_constraint_tool("T3", "T5")
         ↓
enforce_ordering_constraints(routes, [("T3", "T5")])
         ↓
Routes modified: T3 now before T5 in route
         ↓
validate_solution_fuel() — check fuel still valid
         ↓
State updated: routes, allocation
         ↓
Responder: "Constraint applied. T3 now visited before T5."
```

---

**End of Implementation Plan**
