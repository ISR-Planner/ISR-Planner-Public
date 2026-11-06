# Multi-Phase Mission Planning Architecture

## Overview

This document captures the architectural design for handling complex sequencing constraints in the ISR Planner system. The design enables commands like:

- **Per-drone sequencing**: "D1 should start with T1, D2 should visit T5 first, D3 should visit T7 before landing"
- **Global priority segmentation**: "All drones must visit targets with priority > 5 first, then continue"
- **Multi-tier priority phasing**: "Visit P>7 first, then P>3, then complete mission"

## Current System Gaps

### 1. Per-Drone Sequencing Not Supported

**Current state**: The constraint parser only supports global `sequencing_hints`:
```python
sequencing_hints = {
    "start_with": {"priority_gte": 5},  # Applies to ALL drones
    "end_with": {"target_ids": ["T1"]}  # Applies to ALL drones
}
```

**Gap**: No way to specify "D1 starts with T1, D2 starts with T5" (different constraints per drone).

### 2. `segment_by_priority` Parsed But Not Implemented

**Current state** (`constraint_parser.py` lines 207-213):
```python
# Priority threshold segmentation: "priority > 7 first"
priority_threshold = _parse_priority_threshold(user_message)
if priority_threshold and "FIRST" in msg:
    sequencing_hints["segment_by_priority"] = {
        "threshold": priority_threshold,
        "order": "high_first",
    }
```

**Gap**: Route Optimizer (`isr_agent_multi_v4.py`) only implements `start_with`, not `segment_by_priority`.

The difference:
- `start_with`: Ensures the FIRST target meets the criteria
- `segment_by_priority`: Ensures ALL targets above threshold are visited before ANY target below threshold

### 3. Multi-Tier Priority Requires Multiple Phases

Command: "Visit P>7 first, then P>3, then complete"

This requires:
1. Phase 1: Visit all targets with priority > 7
2. Phase 2: Visit all targets with priority > 3 (excluding already visited)
3. Phase 3: Complete remaining targets

Current system cannot represent this.

## Proposed Architecture: Mission Plan Compiler

### Data Structures

Create `/server/memory/mission_plan.py`:

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

@dataclass
class MissionPhase:
    """A single phase of a multi-phase mission."""

    phase_id: int
    description: str

    # Priority-based filtering for this phase
    # Example: "priority > 5" means only targets with priority > 5 are valid
    global_priority_filter: Optional[str] = None

    # Explicit target whitelist (if provided, overrides priority filter)
    target_whitelist: Optional[Set[str]] = None

    # Per-drone sequencing within this phase
    # Example: {"D1": "T1", "D2": "T5"} means D1 must visit T1 first, D2 must visit T5 first
    drone_first_target: Dict[str, str] = field(default_factory=dict)

    # Per-drone last target within this phase
    # Example: {"D3": "T7", "D4": "T22"} means D3 must visit T7 last, D4 must visit T22 last
    drone_last_target: Dict[str, str] = field(default_factory=dict)

    # Per-drone enable/disable for this phase
    # Example: {"D1": True, "D2": False} means only D1 participates in this phase
    drone_enabled: Dict[str, bool] = field(default_factory=dict)

    # Phase behavior
    freeze_after: bool = True  # Lock this phase's solution before moving to next
    use_positions_from_phase: Optional[int] = None  # Start from previous phase's end positions


@dataclass
class MissionPlan:
    """A complete multi-phase mission plan compiled from a user command."""

    phases: List[MissionPhase]
    original_command: str
    reasoning: str  # LLM explanation of how command was interpreted

    def get_phase(self, phase_id: int) -> Optional[MissionPhase]:
        """Get a phase by ID."""
        for phase in self.phases:
            if phase.phase_id == phase_id:
                return phase
        return None
```

### Example: Multi-Tier Priority Command

Command: "Visit targets with priority > 7 first, then priority > 3, then complete the mission"

Compiled `MissionPlan`:
```python
MissionPlan(
    phases=[
        MissionPhase(
            phase_id=1,
            description="Visit high-priority targets (P>7)",
            global_priority_filter="priority > 7",
            freeze_after=True,
        ),
        MissionPhase(
            phase_id=2,
            description="Visit medium-priority targets (P>3)",
            global_priority_filter="priority > 3",
            use_positions_from_phase=1,
            freeze_after=True,
        ),
        MissionPhase(
            phase_id=3,
            description="Complete remaining targets",
            global_priority_filter=None,  # All remaining
            use_positions_from_phase=2,
            freeze_after=True,
        ),
    ],
    original_command="Visit targets with priority > 7 first, then priority > 3, then complete",
    reasoning="User wants 3-tier priority sequencing: P>7, then P>3, then all remaining"
)
```

### Example: Per-Drone Sequencing Command

Command: "D1 should start by visiting T1 first, D2 should visit T5 first, D3 should visit T7 before landing"

Compiled `MissionPlan`:
```python
MissionPlan(
    phases=[
        MissionPhase(
            phase_id=1,
            description="Execute mission with per-drone sequencing constraints",
            drone_first_target={"1": "T1", "2": "T5"},
            drone_last_target={"3": "T7"},
            freeze_after=True,
        ),
    ],
    original_command="D1 should start by visiting T1 first...",
    reasoning="Single-phase mission with per-drone first/last target constraints"
)
```

## Implementation Plan

### 1. Create Data Structures

Create `/server/memory/mission_plan.py` with `MissionPhase` and `MissionPlan` dataclasses.

### 2. Add Mission Planner Agent (or Enhance Strategist)

Options:
- **Option A**: New "Mission Planner" agent in the LangGraph flow
- **Option B**: Enhance Strategist to compile complex commands

The agent should:
1. Detect complex sequencing commands
2. Compile them into `MissionPlan` with appropriate phases
3. Pass the plan to downstream agents

### 3. Update Coordinator V4

Add new intent detection:
```python
def classify_intent(self, user_message: str) -> str:
    # ... existing logic ...

    # Check for complex sequencing patterns
    if self._has_complex_sequencing(user_message):
        return "complex_sequencing"

    # ... rest of classification ...

def _has_complex_sequencing(self, msg: str) -> bool:
    patterns = [
        r"D\d+\s+should\s+(start|visit|end)",  # Per-drone
        r"priority\s*>\s*\d+\s+first.*then",   # Multi-tier priority
        r"all\s+drones\s+must\s+visit.*first", # Global segmentation
    ]
    return any(re.search(p, msg, re.IGNORECASE) for p in patterns)
```

### 4. Update Allocator Agent

Honor `global_priority_filter` from `MissionPhase`:
```python
def allocate_targets(self, targets, drones, phase: MissionPhase):
    # Filter targets based on phase constraints
    valid_targets = targets

    if phase.global_priority_filter:
        # Parse filter like "priority > 5"
        threshold = self._parse_priority_filter(phase.global_priority_filter)
        valid_targets = [t for t in targets if t.priority > threshold]

    if phase.target_whitelist:
        valid_targets = [t for t in valid_targets if t.id in phase.target_whitelist]

    # Exclude already-visited targets from previous phases
    valid_targets = [t for t in valid_targets if t.id not in self.visited_targets]

    # ... proceed with allocation ...
```

### 5. Update Route Optimizer Agent

Honor `drone_first_target` and `drone_last_target`:
```python
def optimize_route(self, route, drone_id, phase: MissionPhase):
    # Check for drone-specific first target
    if drone_id in phase.drone_first_target:
        required_first = phase.drone_first_target[drone_id]
        route = self._ensure_target_first(route, required_first)

    # Check for drone-specific last target
    if drone_id in phase.drone_last_target:
        required_last = phase.drone_last_target[drone_id]
        route = self._ensure_target_last(route, required_last)

    # ... rest of optimization ...
```

### 6. Implement Phase Execution Loop

In solver bridge or orchestration:
```python
def execute_mission_plan(self, plan: MissionPlan):
    visited_targets = set()
    drone_positions = self.get_initial_positions()

    for phase in plan.phases:
        # Set starting positions from previous phase
        if phase.use_positions_from_phase is not None:
            drone_positions = self.get_positions_after_phase(phase.use_positions_from_phase)

        # Execute this phase
        result = self.execute_phase(
            phase,
            drone_positions,
            exclude_targets=visited_targets
        )

        # Track visited targets
        visited_targets.update(result.visited_targets)

        # Freeze phase if required
        if phase.freeze_after:
            self.freeze_phase_solution(phase.phase_id, result)

    return self.combine_phase_results()
```

## Key Design Decisions

### Why Phases Instead of Nested Constraints?

Phases are:
- **Simpler to implement**: Each phase is a standard mission solve with filters
- **Easier to debug**: Can inspect each phase's solution independently
- **Compatible with existing code**: Phases reuse current solving infrastructure
- **Natural for segmented missions**: Each phase can become a frozen segment

### Why Per-Drone Sequencing in Phase?

Per-drone constraints (`drone_first_target`, `drone_last_target`) apply WITHIN a phase:
- Each phase may have different per-drone constraints
- Allows complex patterns like "In phase 1, D1 starts with T1; in phase 2, D1 starts with T10"

### Why Auto-Exclusion of Visited Targets?

When executing phase N:
- Automatically exclude all targets visited in phases 1 to N-1
- Prevents double-visiting targets
- Simplifies constraint specification (no need to explicitly list exclusions)

## Files to Modify

| File | Changes |
|------|---------|
| `server/memory/mission_plan.py` | NEW: `MissionPhase`, `MissionPlan` dataclasses |
| `server/agents/coordinator_v4.py` | Add complex_sequencing intent detection |
| `server/agents/isr_agent_multi_v4.py` | Update Strategist/add Mission Planner, update Allocator, update Route Optimizer |
| `server/solver/solver_bridge.py` | Add phase execution loop |
| `server/memory/constraint_parser.py` | Update to parse per-drone constraints |

## Testing Strategy

1. **Unit tests for `MissionPlan` compilation**:
   - Test parsing various command patterns
   - Verify correct phase generation

2. **Integration tests for phase execution**:
   - Test multi-tier priority commands
   - Test per-drone sequencing
   - Test combination of both

3. **End-to-end tests via agent**:
   - Submit complex commands via `/agent/chat`
   - Verify correct mission execution

## Status

**Design**: Complete
**Implementation**: Not started
**Last Updated**: 2026-02-11
