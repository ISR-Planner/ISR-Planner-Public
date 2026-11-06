# Adaptive Allocation Strategy Selection

## Overview

This document captures the design for enabling the Allocator agent to intelligently select the best allocation algorithm based on target layout and mission characteristics, rather than relying on static defaults or manual selection.

## Current System

### Allocation Strategy Flow

```
API Request (allocation_strategy param, default="efficient")
    ↓
main.py (SolveWithAllocationRequest)
    ↓
Coordinator V4 (injects into policy)
    ↓
Allocator Agent (reads from policy)
    ↓
target_allocator.py (executes strategy)
```

### Available Strategies

| Strategy | Optimization Objective |
|----------|----------------------|
| `greedy` | Assign highest-priority targets to nearest capable drone |
| `balanced` | Distribute targets evenly by count across drones |
| `efficient` | Maximize priority-per-fuel-unit ratio (auction-based) |
| `geographic` | Minimize detours via spatial corridors |
| `exclusive` | Handle single-drone-only targets first |

**Important**: All strategies respect the fuel budget as a HARD CONSTRAINT. The difference is the OPTIMIZATION OBJECTIVE, not fuel sensitivity.

### Current Gap

There is no intelligent selection mechanism. The system either:
1. Uses the API-provided `allocation_strategy` parameter
2. Defaults to "efficient" if not specified

The Allocator agent does not analyze the environment to determine which strategy would work best.

## Proposed Solution: Hybrid Approach

A three-phase approach combining heuristics, parallel evaluation, and eventual ML-based selection.

### Phase 1: Heuristic Selection + Parallel Evaluation

**Heuristic Rules** - Quick initial selection based on environment characteristics:

```python
def select_strategy_heuristic(env, drone_configs):
    """Select allocation strategy based on environment heuristics."""

    targets = env.get("targets", [])
    drones = drone_configs

    # Calculate environment features
    exclusivity_ratio = _calculate_exclusivity_ratio(targets, drones)
    priority_variance = _calculate_priority_variance(targets)
    spatial_clustering = _calculate_clustering_coefficient(targets)
    drone_count = len(drones)
    target_count = len(targets)

    # Heuristic rules
    if exclusivity_ratio > 0.3:
        # Many targets can only be reached by one drone
        return "exclusive"

    if spatial_clustering > 0.7:
        # Targets are tightly clustered in geographic regions
        return "geographic"

    if priority_variance > 2.0 and target_count > drone_count * 3:
        # High priority spread with many targets - prioritize high-value
        return "greedy"

    if target_count < drone_count * 2:
        # Few targets relative to drones - balance workload
        return "balanced"

    # Default: maximize efficiency
    return "efficient"
```

**Parallel Candidate Evaluation** - For uncertain cases, run top 2-3 strategies:

```python
def select_strategy_with_evaluation(env, drone_configs, matrix_data):
    """Run multiple strategies and select best result."""

    # Get heuristic suggestion
    primary = select_strategy_heuristic(env, drone_configs)

    # Define candidates to evaluate
    candidates = [primary]
    if primary != "efficient":
        candidates.append("efficient")  # Always try efficient as backup

    # Run all candidates
    results = {}
    for strategy in candidates:
        allocation = _allocate_targets_impl(env, drone_configs, strategy, matrix_data)
        score = _evaluate_allocation_quality(allocation, env, drone_configs)
        results[strategy] = (allocation, score)

    # Select best
    best_strategy = max(results.keys(), key=lambda s: results[s][1])
    return best_strategy, results[best_strategy][0]


def _evaluate_allocation_quality(allocation, env, drone_configs):
    """Score an allocation result."""
    total_priority = sum(
        sum(t["priority"] for t in drone_targets)
        for drone_targets in allocation.values()
    )

    workload_balance = _calculate_workload_std(allocation)
    coverage_ratio = len(set(
        t["id"] for targets in allocation.values() for t in targets
    )) / len(env.get("targets", []))

    # Weighted score
    return (
        total_priority * 0.5 +
        coverage_ratio * 100 * 0.3 +
        (1 / (1 + workload_balance)) * 50 * 0.2
    )
```

### Phase 2: Logging Infrastructure

Add logging to collect training data for future ML model:

```python
@dataclass
class AllocationDecision:
    """Record of an allocation decision for ML training."""

    # Environment features
    target_count: int
    drone_count: int
    priority_variance: float
    priority_skew: float
    spatial_clustering: float
    exclusivity_ratio: float
    sam_coverage_pct: float
    avg_fuel_budget: float

    # Decision made
    strategy_used: str

    # Outcome metrics
    total_priority_captured: float
    coverage_ratio: float
    workload_std: float
    total_distance: float

    # Optional: user feedback
    user_accepted: bool = True


def log_allocation_decision(env, drone_configs, strategy, result):
    """Log allocation decision for ML training data collection."""

    decision = AllocationDecision(
        target_count=len(env.get("targets", [])),
        drone_count=len(drone_configs),
        priority_variance=_calculate_priority_variance(env.get("targets", [])),
        priority_skew=_calculate_priority_skew(env.get("targets", [])),
        spatial_clustering=_calculate_clustering_coefficient(env.get("targets", [])),
        exclusivity_ratio=_calculate_exclusivity_ratio(env.get("targets", []), drone_configs),
        sam_coverage_pct=_calculate_sam_coverage(env),
        avg_fuel_budget=np.mean([d.get("fuel_capacity", 0) for d in drone_configs]),
        strategy_used=strategy,
        total_priority_captured=_sum_priority(result),
        coverage_ratio=_calculate_coverage(result, env),
        workload_std=_calculate_workload_std(result),
        total_distance=_calculate_total_distance(result),
        user_accepted=True  # Updated later if user rejects
    )

    # Append to training log
    _append_to_training_log(decision)
```

### Phase 3: ML-Based Selection

Train a classifier on collected data:

**Feature Vector**:
```python
features = [
    target_count,
    drone_count,
    target_count / drone_count,  # targets per drone
    priority_variance,
    priority_skew,
    spatial_clustering,
    exclusivity_ratio,
    sam_coverage_pct,
    avg_fuel_budget,
    min_fuel_budget,
    max_fuel_budget,
]
```

**Model Options**:
1. **Random Forest** - Good baseline, interpretable
2. **XGBoost** - Better performance, handles imbalanced data
3. **Small Neural Network** - If dataset is large enough

**Training Pipeline**:
```python
from sklearn.ensemble import RandomForestClassifier
import joblib

def train_strategy_selector(training_data_path: str, output_model_path: str):
    """Train strategy selection model."""

    data = load_training_data(training_data_path)

    # Filter for accepted solutions only
    data = data[data["user_accepted"] == True]

    # For each environment, find best-performing strategy
    best_strategies = data.groupby("env_hash").apply(
        lambda g: g.loc[g["total_priority_captured"].idxmax(), "strategy_used"]
    )

    X = data[FEATURE_COLUMNS].values
    y = best_strategies.values

    model = RandomForestClassifier(n_estimators=100, max_depth=10)
    model.fit(X, y)

    joblib.dump(model, output_model_path)
    return model
```

**Integration**:
```python
class MLStrategySelector:
    """ML-based strategy selection."""

    def __init__(self, model_path: str = None):
        self.model = None
        if model_path and os.path.exists(model_path):
            self.model = joblib.load(model_path)

    def select(self, env, drone_configs) -> str:
        """Select strategy using ML model."""

        if self.model is None:
            # Fall back to heuristics
            return select_strategy_heuristic(env, drone_configs)

        features = self._extract_features(env, drone_configs)
        strategy = self.model.predict([features])[0]

        return strategy
```

## Implementation Plan

### Files to Modify

| File | Changes |
|------|---------|
| `server/solver/target_allocator.py` | Add heuristic selection, parallel evaluation, logging |
| `server/agents/isr_agent_multi_v4.py` | Update Allocator to use adaptive selection |
| `server/solver/strategy_selector.py` | NEW: ML model wrapper and feature extraction |
| `server/memory/allocation_log.py` | NEW: Training data collection and storage |

### Phase 1 Implementation Steps

1. Add feature extraction functions to `target_allocator.py`:
   - `_calculate_exclusivity_ratio()`
   - `_calculate_priority_variance()`
   - `_calculate_clustering_coefficient()`
   - `_calculate_sam_coverage()`

2. Add `select_strategy_heuristic()` function

3. Add `select_strategy_with_evaluation()` for parallel candidate testing

4. Add `_evaluate_allocation_quality()` scoring function

5. Update Allocator agent to use adaptive selection when no explicit strategy provided

### Phase 2 Implementation Steps

1. Create `AllocationDecision` dataclass

2. Implement logging to JSON/Supabase

3. Add user feedback capture (accept/reject)

### Phase 3 Implementation Steps

1. Collect sufficient training data (500+ decisions recommended)

2. Train and evaluate classifier

3. Deploy model and integrate into Allocator

## Feature Definitions

| Feature | Description | Calculation |
|---------|-------------|-------------|
| `target_count` | Number of targets | `len(targets)` |
| `drone_count` | Number of drones | `len(drone_configs)` |
| `priority_variance` | Spread of priority values | `np.var([t["priority"] for t in targets])` |
| `priority_skew` | Asymmetry of priority distribution | `scipy.stats.skew(priorities)` |
| `spatial_clustering` | How clustered targets are (0-1) | DBSCAN cluster coefficient |
| `exclusivity_ratio` | Fraction of targets reachable by only 1 drone | Count exclusive / total |
| `sam_coverage_pct` | Area covered by SAM zones | SAM area / bounding box area |
| `avg_fuel_budget` | Average drone fuel capacity | `np.mean(fuel_capacities)` |

## Success Criteria

1. **Phase 1**: Heuristic selection performs at least as well as "efficient" default in 90% of cases

2. **Phase 2**: Logging captures 500+ allocation decisions within first month of deployment

3. **Phase 3**: ML model achieves >85% accuracy on held-out test set and improves total priority captured by 5%+ over Phase 1 heuristics

## Status

**Design**: Complete
**Implementation**: Not started
**Last Updated**: 2026-02-11
