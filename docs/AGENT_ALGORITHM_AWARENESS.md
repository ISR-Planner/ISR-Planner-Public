# Agent Algorithm Awareness

## Overview

This document describes how agents track and report the algorithms they use, and how they respond to explicit algorithm commands from users.

## Requirements

### 1. Algorithm Transparency

When asked "What algorithm did you use?" or similar, agents must be able to explain:

| Agent | Example Response |
|-------|------------------|
| **Allocator** | "I used the 'efficient' allocation strategy (auction-based priority-per-fuel optimization)" |
| **Route Optimizer** | "I used Held-Karp dynamic programming for the orienteering problem, then applied the Unified Optimizer with interleaved Insert+Swap" |
| **Optimizer Agent** | "I ran Insert Missed (added 3 targets), then Swap Closer (5 iterations, 2 swaps), then Crossing Removal (fixed 1 crossing)" |

### 2. Algorithm Control

When commanded to use a specific algorithm, agents must:
1. Recognize the command
2. Use the specified algorithm
3. Confirm what was used in the response

**Example commands:**
- "Use the greedy allocation strategy"
- "Only run swap optimization, no insert"
- "Use geographic allocation"
- "Skip crossing removal"

## Implementation

### Algorithm Registry

Each agent maintains knowledge of its available algorithms:

```python
ALLOCATOR_ALGORITHMS = {
    "efficient": {
        "name": "Efficient (Auction-Based)",
        "description": "Maximizes priority-per-fuel-unit ratio using auction mechanism",
        "aliases": ["auction", "priority-per-fuel", "default"]
    },
    "greedy": {
        "name": "Greedy",
        "description": "Assigns highest-priority targets to nearest capable drone",
        "aliases": ["priority-first", "high-priority"]
    },
    "balanced": {
        "name": "Balanced",
        "description": "Distributes targets evenly by count across drones",
        "aliases": ["even", "equal-distribution"]
    },
    "geographic": {
        "name": "Geographic",
        "description": "Minimizes detours via spatial corridors",
        "aliases": ["spatial", "corridor", "regional"]
    },
    "exclusive": {
        "name": "Exclusive-First",
        "description": "Handles single-drone-only targets first",
        "aliases": ["exclusive-first", "constrained-first"]
    },
    "adaptive": {
        "name": "Adaptive Selection",
        "description": "Automatically selects best strategy based on environment analysis",
        "aliases": ["auto", "smart", "automatic"]
    }
}

OPTIMIZER_ALGORITHMS = {
    "insert_missed": {
        "name": "Insert Missed",
        "description": "Inserts unvisited targets into routes where they fit within fuel budget",
        "aliases": ["insert", "add-missed", "fill"]
    },
    "swap_closer": {
        "name": "Swap Closer",
        "description": "Moves targets to trajectories that pass closer to them",
        "aliases": ["swap", "reassign", "move-closer"]
    },
    "crossing_removal": {
        "name": "Crossing Removal (2-opt)",
        "description": "Removes route self-crossings using 2-opt reversal",
        "aliases": ["no-cross", "2-opt", "uncross"]
    },
    "unified": {
        "name": "Unified Optimizer",
        "description": "Interleaved Insert+Swap with greedy single-operation execution",
        "aliases": ["interleaved", "combined"]
    }
}

SOLVER_ALGORITHMS = {
    "held_karp": {
        "name": "Held-Karp Dynamic Programming",
        "description": "Optimal TSP/orienteering solver for small instances (up to ~15 targets per drone)",
        "aliases": ["dp", "dynamic-programming", "optimal"]
    },
    "greedy_tsp": {
        "name": "Greedy TSP",
        "description": "Fast nearest-neighbor heuristic for larger instances",
        "aliases": ["nearest-neighbor", "nn", "fast"]
    }
}
```

### Algorithm Execution Tracking

Each agent tracks its algorithm usage in a structured format:

```python
@dataclass
class AlgorithmExecution:
    """Record of an algorithm execution."""

    algorithm_id: str           # e.g., "swap_closer"
    algorithm_name: str         # e.g., "Swap Closer"
    agent: str                  # e.g., "Optimizer"

    # Execution details
    iterations: int = 1
    operations_performed: int = 0

    # Results summary
    result_summary: str = ""    # e.g., "5 swaps made, saved 42.3 fuel"

    # Timing (optional)
    duration_ms: float = 0.0

    # User-requested vs auto-selected
    user_requested: bool = False
```

### Agent State Extension

Add algorithm tracking to agent state:

```python
class AgentState(TypedDict):
    # ... existing fields ...

    # Algorithm tracking
    algorithms_used: List[AlgorithmExecution]
    algorithm_overrides: Dict[str, str]  # User-specified algorithm choices
```

### Command Detection

Add algorithm command patterns to the constraint parser:

```python
ALGORITHM_COMMAND_PATTERNS = [
    # Allocation strategy commands
    (r"use\s+(?:the\s+)?(\w+)\s+(?:allocation|strategy)", "allocation_strategy"),
    (r"allocate\s+(?:using|with)\s+(\w+)", "allocation_strategy"),

    # Optimizer commands
    (r"(?:only\s+)?run\s+(\w+)\s+optim", "optimizer_include"),
    (r"skip\s+(\w+)\s+(?:optimization|optimizer)?", "optimizer_exclude"),
    (r"no\s+(\w+)\s+(?:optimization|removal)?", "optimizer_exclude"),

    # Solver commands
    (r"use\s+(?:the\s+)?(\w+)\s+solver", "solver_algorithm"),
]

def parse_algorithm_commands(user_message: str) -> Dict[str, Any]:
    """
    Parse user message for explicit algorithm commands.

    Returns:
        Dict with keys like:
        - allocation_strategy: "greedy" | "balanced" | etc.
        - optimizer_include: ["swap_closer"] (only run these)
        - optimizer_exclude: ["crossing_removal"] (skip these)
        - solver_algorithm: "held_karp" | "greedy_tsp"
    """
    commands = {}
    msg_lower = user_message.lower()

    for pattern, command_type in ALGORITHM_COMMAND_PATTERNS:
        match = re.search(pattern, msg_lower)
        if match:
            value = match.group(1)

            # Resolve aliases to canonical algorithm ID
            canonical = resolve_algorithm_alias(value, command_type)
            if canonical:
                if command_type in ("optimizer_include", "optimizer_exclude"):
                    commands.setdefault(command_type, []).append(canonical)
                else:
                    commands[command_type] = canonical

    return commands


def resolve_algorithm_alias(alias: str, context: str) -> Optional[str]:
    """Resolve an alias to canonical algorithm ID."""
    alias_lower = alias.lower()

    # Select registry based on context
    if context == "allocation_strategy":
        registry = ALLOCATOR_ALGORITHMS
    elif context in ("optimizer_include", "optimizer_exclude"):
        registry = OPTIMIZER_ALGORITHMS
    elif context == "solver_algorithm":
        registry = SOLVER_ALGORITHMS
    else:
        return None

    # Check each algorithm for matching aliases
    for alg_id, alg_info in registry.items():
        if alias_lower == alg_id or alias_lower in alg_info.get("aliases", []):
            return alg_id

    return None
```

### Agent Response Generation

When generating responses, agents include algorithm information:

```python
def generate_algorithm_summary(executions: List[AlgorithmExecution]) -> str:
    """
    Generate a human-readable summary of algorithms used.

    Example output:
    "I used the following algorithms:
    1. **Efficient Allocation** - Assigned 12 targets across 4 drones
    2. **Held-Karp Solver** - Computed optimal routes for each drone
    3. **Unified Optimizer** - 3 iterations: 2 inserts, 1 swap, saved 15.2 fuel
    4. **Crossing Removal** - Fixed 1 crossing in D2's route"
    """
    if not executions:
        return "No algorithms were executed."

    lines = ["I used the following algorithms:"]
    for i, exec in enumerate(executions, 1):
        line = f"{i}. **{exec.algorithm_name}**"
        if exec.result_summary:
            line += f" - {exec.result_summary}"
        lines.append(line)

    return "\n".join(lines)
```

### Responder Agent Enhancement

The Responder agent checks if the user asked about algorithms:

```python
ALGORITHM_QUERY_PATTERNS = [
    r"what\s+algorithm",
    r"which\s+algorithm",
    r"how\s+did\s+you\s+(?:solve|optimize|allocate|plan)",
    r"what\s+(?:method|approach|strategy)\s+did\s+you\s+use",
    r"explain\s+(?:the\s+)?algorithm",
]

def should_include_algorithm_explanation(user_message: str) -> bool:
    """Check if user is asking about algorithms used."""
    msg_lower = user_message.lower()
    return any(re.search(p, msg_lower) for p in ALGORITHM_QUERY_PATTERNS)


def build_response(state: AgentState) -> str:
    """Build response, including algorithm info if requested."""

    # ... existing response building ...

    if should_include_algorithm_explanation(state["user_message"]):
        algo_summary = generate_algorithm_summary(state.get("algorithms_used", []))
        response += f"\n\n## Algorithms Used\n\n{algo_summary}"

    return response
```

## Example Interactions

### Example 1: User Asks About Algorithm

**User**: "What algorithm did you use to optimize the routes?"

**Agent Response**:
```
I used the following algorithms:

1. **Efficient Allocation** - Auction-based assignment of 15 targets to 4 drones,
   maximizing priority-per-fuel ratio

2. **Held-Karp Dynamic Programming** - Computed optimal visiting order for each
   drone's assigned targets (exact solution for instances up to 15 targets)

3. **Unified Optimizer** (3 iterations):
   - Iteration 1: Inserted T7 into D2 (cost: 5.2, priority: 8)
   - Iteration 2: Swapped T12 from D1 to D3 (saved: 8.4 fuel)
   - Iteration 3: Inserted T19 into D1 (cost: 3.1, priority: 6)

4. **Crossing Removal (2-opt)** - Fixed 2 crossings in D2's route
```

### Example 2: User Specifies Algorithm

**User**: "Use the geographic allocation strategy and skip crossing removal"

**Agent Response**:
```
I'll use geographic allocation and skip crossing removal as requested.

**Algorithms Used:**
1. **Geographic Allocation** - Assigned targets based on spatial corridors
2. **Held-Karp Solver** - Computed routes
3. **Unified Optimizer** - 2 iterations, 1 insert, 1 swap

Note: Crossing removal was skipped as requested. D2's route has 1 potential
crossing that could be fixed with 2-opt if desired.
```

### Example 3: User Asks Mid-Conversation

**User**: "Why did T5 end up on D2 instead of D1?"

**Agent Response**:
```
T5 was assigned to D2 during the **Swap Closer** phase of optimization.

Initially, T5 was on D1's route. However:
- T5's removal delta from D1: 12.3 (cost saved by removing)
- T5's insertion cost into D2: 4.1 (cost to add between T8 and T9)
- Net savings: 8.2 fuel

The Unified Optimizer moved T5 to D2 because D2's trajectory passed
significantly closer to T5's position (OSD: 3.2 vs SSD: 11.8).
```

## Files to Modify

| File | Changes |
|------|---------|
| `server/memory/algorithm_registry.py` | NEW: Algorithm definitions and alias resolution |
| `server/memory/constraint_parser.py` | Add algorithm command parsing |
| `server/agents/isr_agent_multi_v4.py` | Add algorithm tracking to state, update all agents |
| `server/solver/post_optimizer.py` | Return algorithm execution details |
| `server/solver/target_allocator.py` | Return algorithm execution details |
| `server/solver/solver_bridge.py` | Return algorithm execution details |

## State Schema Update

```python
class AgentState(TypedDict):
    # Existing fields...
    user_message: str
    environment: Dict
    drone_configs: Dict
    solution: Optional[Dict]

    # NEW: Algorithm tracking
    algorithms_used: List[Dict]  # List of AlgorithmExecution as dicts
    algorithm_overrides: Dict[str, Any]  # User-specified algorithm choices

    # NEW: Per-agent tracking
    allocator_algorithm: Optional[str]
    solver_algorithm: Optional[str]
    optimizer_algorithms: List[str]
```

## Integration with Existing Designs

This design integrates with:

1. **Unified Optimizer** (`UNIFIED_OPTIMIZER.md`):
   - Track each Insert/Swap operation as part of execution log
   - Report iteration count, operations performed, fuel saved

2. **Adaptive Allocation** (`ADAPTIVE_ALLOCATION_STRATEGY.md`):
   - When using adaptive selection, report both:
     - "Adaptive Selection chose 'geographic' based on spatial clustering (0.82)"
     - The actual geographic allocation results

3. **Multi-Phase Missions** (`MULTI_PHASE_MISSION_PLANNING.md`):
   - Track algorithms used in each phase separately
   - Report phase-by-phase breakdown when asked

## Status

**Design**: Complete
**Implementation**: Not started
**Last Updated**: 2026-02-11
