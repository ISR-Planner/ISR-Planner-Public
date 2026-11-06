# Unified Optimizer Design

## Overview

This document describes the unified optimizer that interleaves Insert and Swap operations to achieve better results than fixed-order execution.

## Problem Statement

The previous fixed-order approach (Insert → Swap → Crossing Removal) doesn't always produce optimal results because:

1. A swap might free up fuel capacity, enabling an insert that wasn't previously possible
2. An insert might create a better swap opportunity
3. The optimization landscape changes after each operation

**Observation**: Interleaving operations (e.g., Swap → Insert → Swap) can yield better results than either fixed order.

## Design Principles

### 1. Direct Cost Comparison (No OSD/SSD)

All operations use the same cost formula:

```
insertion_cost = d(A, X) + d(X, B) - d(A, B)
```

Where:
- `A` = previous waypoint in segment
- `B` = next waypoint in segment
- `X` = target being inserted
- `d()` = SAM-aware distance from distance matrix

For **Swap** operations:
- `removal_delta` = insertion_cost of target in its current position (what we save by removing)
- `insertion_cost` = cost to insert at destination
- `gain` = `removal_delta - insertion_cost`

A swap is beneficial when `gain > 0`.

### 2. Unified Candidate Evaluation

Both Insert and Swap candidates are evaluated in the same pass:

| Operation | Score Formula | Description |
|-----------|---------------|-------------|
| Insert | `priority / insertion_cost` | Maximize priority per fuel unit |
| Swap | `gain` (= removal_delta - insertion_cost) | Maximize fuel savings |

### 3. Greedy Single-Operation Execution

At each iteration:
1. Evaluate ALL possible Insert candidates
2. Evaluate ALL possible Swap candidates
3. Pick the single best operation (highest score)
4. Execute it
5. Repeat until no beneficial operations remain

### 4. Crossing Removal Last

Crossing Removal runs only after Insert/Swap convergence:
- Both Insert and Swap can introduce crossings
- 2-opt reversal is a cleanup operation
- Running it mid-optimization would be wasteful

## Algorithm

```python
def unified_optimize(solution, env, drone_configs, distance_matrix, max_iterations=100):
    """
    Unified optimizer with interleaved Insert and Swap.
    """
    optimizer = UnifiedOptimizer(distance_matrix)

    for iteration in range(max_iterations):
        # Collect all candidates
        insert_candidates = optimizer.evaluate_insert_candidates(
            solution, env, drone_configs
        )
        swap_candidates = optimizer.evaluate_swap_candidates(
            solution, env, drone_configs
        )

        all_candidates = insert_candidates + swap_candidates

        if not all_candidates:
            break  # Converged

        # Pick best operation
        best = max(all_candidates, key=lambda c: c["score"])

        # Execute
        if best["type"] == "insert":
            optimizer.execute_insert(solution, best)
        else:
            optimizer.execute_swap(solution, best)

        # Update solution state (routes, fuel, trajectories)
        optimizer.update_metrics(solution, env)

    # Final cleanup: remove crossings
    solution = crossing_removal_optimize(solution, env, drone_configs, distance_matrix)

    return solution
```

## Candidate Evaluation

### Insert Candidates

```python
def evaluate_insert_candidates(solution, env, drone_configs):
    """
    Find all viable insert operations for unvisited targets.
    """
    candidates = []
    unvisited = find_unvisited_targets(solution, env)

    for target in unvisited:
        for drone_id, cfg in drone_configs.items():
            # Check constraints
            if not target_allowed_for_drone(target, drone_id, cfg):
                continue

            route_data = solution["routes"].get(drone_id, {})
            route = route_data.get("route", [])
            frozen_segments = set(route_data.get("frozen_segments", []))
            remaining_fuel = calculate_remaining_fuel(route_data, cfg)

            # Find best insertion position
            best_cost = float('inf')
            best_position = None

            for i in range(1, len(route)):  # Insert between waypoints
                if (i - 1) in frozen_segments:
                    continue  # Frozen segment

                prev_wp = route[i - 1]
                next_wp = route[i]

                cost = (
                    get_distance(prev_wp, target["id"]) +
                    get_distance(target["id"], next_wp) -
                    get_distance(prev_wp, next_wp)
                )

                if cost < best_cost and cost <= remaining_fuel:
                    best_cost = cost
                    best_position = i

            if best_position is not None:
                candidates.append({
                    "type": "insert",
                    "target": target["id"],
                    "drone": drone_id,
                    "position": best_position,
                    "cost": best_cost,
                    "priority": target["priority"],
                    "score": target["priority"] / max(best_cost, 0.1)  # Avoid div by zero
                })

    return candidates
```

### Swap Candidates

```python
def evaluate_swap_candidates(solution, env, drone_configs):
    """
    Find all viable swap operations for visited targets.
    """
    candidates = []

    for drone_id, route_data in solution["routes"].items():
        route = route_data.get("route", [])
        frozen_segments = set(route_data.get("frozen_segments", []))

        for i, wp_id in enumerate(route):
            if not wp_id.startswith("T"):
                continue  # Skip airports

            # Check if target is in frozen segment
            if i > 0 and (i - 1) in frozen_segments:
                continue
            if i < len(route) - 1 and i in frozen_segments:
                continue

            # Calculate removal delta
            if i <= 0 or i >= len(route) - 1:
                continue  # Can't remove first/last waypoint

            prev_wp = route[i - 1]
            next_wp = route[i + 1]

            removal_delta = (
                get_distance(prev_wp, wp_id) +
                get_distance(wp_id, next_wp) -
                get_distance(prev_wp, next_wp)
            )

            if removal_delta <= 0:
                continue  # No benefit to moving this target

            target = get_target_by_id(wp_id, env)

            # Find best destination across all drones
            for other_drone, other_route_data in solution["routes"].items():
                other_route = other_route_data.get("route", [])
                other_frozen = set(other_route_data.get("frozen_segments", []))

                # Check capability
                if other_drone != drone_id:
                    other_cfg = drone_configs.get(other_drone, {})
                    if not target_allowed_for_drone(target, other_drone, other_cfg):
                        continue

                for j in range(1, len(other_route)):
                    # Skip frozen segments
                    if (j - 1) in other_frozen:
                        continue

                    # Skip adjacent segments (same drone)
                    if other_drone == drone_id:
                        if j == i or j == i + 1:
                            continue

                    seg_start = other_route[j - 1]
                    seg_end = other_route[j]

                    insertion_cost = (
                        get_distance(seg_start, wp_id) +
                        get_distance(wp_id, seg_end) -
                        get_distance(seg_start, seg_end)
                    )

                    # Check fuel for receiving drone
                    if other_drone != drone_id:
                        other_remaining = calculate_remaining_fuel(
                            other_route_data,
                            drone_configs.get(other_drone, {})
                        )
                        if insertion_cost > other_remaining:
                            continue

                    gain = removal_delta - insertion_cost

                    if gain > 0:
                        candidates.append({
                            "type": "swap",
                            "target": wp_id,
                            "from_drone": drone_id,
                            "to_drone": other_drone,
                            "from_idx": i,
                            "to_position": j,
                            "removal_delta": removal_delta,
                            "insertion_cost": insertion_cost,
                            "gain": gain,
                            "score": gain  # Direct fuel savings
                        })

    return candidates
```

## Scoring Strategy

The unified optimizer needs a way to compare Insert and Swap candidates fairly.

### Option 1: Priority-Per-Fuel (Coverage Focus)

```python
def score(candidate):
    if candidate["type"] == "insert":
        return candidate["priority"] / candidate["cost"]
    else:  # swap
        return candidate["gain"] / 10  # Normalize to similar scale
```

### Option 2: Fuel Savings (Efficiency Focus)

```python
def score(candidate):
    if candidate["type"] == "insert":
        # Negative cost = negative score (we spend fuel)
        # But we gain priority, so weight it
        return candidate["priority"] - candidate["cost"] * 0.1
    else:  # swap
        return candidate["gain"]
```

### Option 3: Hybrid (Recommended)

```python
def score(candidate):
    if candidate["type"] == "insert":
        # Insert adds coverage - prioritize high-priority targets
        return candidate["priority"] * 10 / max(candidate["cost"], 1)
    else:  # swap
        # Swap saves fuel - enables more inserts later
        return candidate["gain"]
```

## Constraints Respected

All operations respect:

| Constraint | How Enforced |
|------------|--------------|
| Frozen Segments | Skip frozen segment indices during candidate evaluation |
| Fuel Budget | Check `insertion_cost <= remaining_fuel` before adding candidate |
| Target Type Access | Call `target_allowed_for_drone()` for cross-drone operations |
| Priority Constraints | Include in `target_allowed_for_drone()` check |
| Drone Enabled | Check `cfg.get("enabled")` before considering drone |

## Implementation Plan

### Files to Modify

| File | Changes |
|------|---------|
| `server/solver/post_optimizer.py` | Add `UnifiedOptimizer` class |
| `server/solver/post_optimizer.py` | Add `unified_optimize()` entry point |
| `server/agents/isr_agent_multi_v4.py` | Update Route Optimizer to use unified optimizer |
| `server/main.py` | Add `/unified_optimize` endpoint (optional) |

### Class Structure

```python
class UnifiedOptimizer:
    """Interleaved Insert + Swap optimizer."""

    def __init__(self):
        self._distance_matrix = None
        self._env = None
        self._waypoint_positions = None

    def set_distance_matrix(self, matrix_data):
        self._distance_matrix = matrix_data

    def set_environment(self, env):
        self._env = env
        # Build waypoint positions...

    def optimize(self, solution, env, drone_configs, max_iterations=100):
        """Main entry point."""
        pass

    def _evaluate_insert_candidates(self, solution, drone_configs):
        """Find all viable insert operations."""
        pass

    def _evaluate_swap_candidates(self, solution, drone_configs):
        """Find all viable swap operations."""
        pass

    def _execute_insert(self, solution, candidate):
        """Apply an insert operation."""
        pass

    def _execute_swap(self, solution, candidate):
        """Apply a swap operation."""
        pass

    def _calculate_insertion_cost(self, prev_wp, target, next_wp):
        """Calculate cost to insert target between two waypoints."""
        pass

    def _get_distance(self, from_id, to_id):
        """Get SAM-aware distance from matrix."""
        pass
```

## Convergence

The optimizer converges when:
1. No Insert candidates exist (all targets visited or unreachable)
2. No beneficial Swap candidates exist (all `gain <= 0`)

Cycle detection (optional):
- Track solution signatures
- If same signature seen twice, return best solution encountered

## Example Execution

```
Initial:
  D1: A1→T1→T3→A1 (fuel: 80/100)
  D2: A2→T2→A2    (fuel: 90/100)
  Unvisited: T4, T5

Iteration 1:
  Insert candidates: T4→D1 (cost=15, priority=8, score=0.53)
                     T4→D2 (cost=12, priority=8, score=0.67) ← BEST
                     T5→D1 (cost=20, priority=6, score=0.30)
  Swap candidates:   T2 D2→D1 (gain=5, score=5)

  Execute: Insert T4 into D2

Iteration 2:
  Insert candidates: T5→D1 (cost=20, priority=6, score=0.30)
                     T5→D2 (cost=25, priority=6, score=0.24)
  Swap candidates:   T2 D2→D1 (gain=5, score=5) ← BEST
                     T4 D2→D1 (gain=3, score=3)

  Execute: Swap T2 from D2 to D1

Iteration 3:
  Insert candidates: T5→D2 (cost=18, priority=6, score=0.33) ← BEST
  Swap candidates:   None beneficial

  Execute: Insert T5 into D2

Iteration 4:
  Insert candidates: None
  Swap candidates:   None beneficial

  CONVERGED

Final:
  D1: A1→T1→T2→T3→A1
  D2: A2→T4→T5→A2
  All targets covered!
```

## Status

**Design**: Complete
**Implementation**: Not started
**Last Updated**: 2026-02-11
