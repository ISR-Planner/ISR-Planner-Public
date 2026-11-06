# Agent Experiential Learning — Implementation Plan

> **Purpose**: Enable the agent to learn from experience which allocation strategies and solver configurations work best for different types of environments.

> **Date**: 2026-06-08

---

## Overview

### Current State
- Agent memory stores text corrections/preferences
- User must explicitly tell agent "use geographic for clustered targets"
- No automatic learning from outcomes
- Strategy selection is rule-based or random

### Target State
- Agent characterizes each environment (clustered, distributed, etc.)
- Agent tracks outcomes for each strategy used
- Agent learns correlations: "geographic works best for clustered targets"
- Agent automatically selects best strategy based on learned patterns
- Agent can explain its strategy choices based on experience

### Example Conversation

```
User: "Solve this mission"
Agent: "I notice targets are clustered around A1. Based on 12 similar missions,
        'geographic' allocation achieves 15% higher coverage than 'greedy'.
        Using geographic strategy."
        [Solves mission]
        "Done. 9/10 targets visited. Recording this outcome for future learning."
```

---

## Part 1: Environment Characterization

### 1.1 Feature Extraction

**Location**: New file `server/agents/environment_analyzer.py`

```python
from typing import Dict, List, Any
import numpy as np
from collections import defaultdict

def characterize_environment(env: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract features that describe the environment's structure.

    Returns:
        {
            "target_distribution": "clustered" | "distributed" | "mixed",
            "cluster_center": "airport" | "center" | "peripheral",
            "airport_coverage": "single" | "dual" | "multi",
            "sam_density": "none" | "sparse" | "dense",
            "target_count": int,
            "airport_count": int,
            "avg_target_priority": float,
            "target_type_diversity": float,  # 0-1, how many different types
            "features_vector": [float, ...],  # For similarity matching
        }
    """
    targets = env.get("targets", [])
    airports = env.get("airports", [])
    sams = env.get("sams", [])

    features = {}

    # Basic counts
    features["target_count"] = len(targets)
    features["airport_count"] = len(airports)
    features["sam_count"] = len(sams)

    # Target distribution analysis
    features["target_distribution"] = analyze_target_distribution(targets, airports)
    features["cluster_center"] = find_cluster_center(targets, airports)

    # Airport coverage
    features["airport_coverage"] = classify_airport_coverage(airports)

    # SAM density
    features["sam_density"] = classify_sam_density(sams, targets)

    # Priority distribution
    priorities = [t.get("priority", 5) for t in targets]
    features["avg_target_priority"] = np.mean(priorities) if priorities else 5.0
    features["priority_variance"] = np.var(priorities) if priorities else 0.0

    # Target type diversity
    types = set(t.get("type", "a") for t in targets)
    features["target_type_diversity"] = len(types) / 5.0  # Assume 5 max types

    # Numeric feature vector for similarity matching
    features["features_vector"] = build_feature_vector(features)

    return features


def analyze_target_distribution(targets: List[Dict], airports: List[Dict]) -> str:
    """
    Classify target distribution as clustered, distributed, or mixed.

    Uses coefficient of variation of pairwise distances.
    """
    if len(targets) < 3:
        return "distributed"

    positions = np.array([[t.get("lat", t.get("x", 0)),
                           t.get("lng", t.get("y", 0))] for t in targets])

    # Compute pairwise distances
    from scipy.spatial.distance import pdist
    distances = pdist(positions)

    if len(distances) == 0:
        return "distributed"

    # Coefficient of variation
    cv = np.std(distances) / np.mean(distances) if np.mean(distances) > 0 else 0

    # High CV = clustered (some close, some far)
    # Low CV = evenly distributed
    if cv > 0.8:
        return "clustered"
    elif cv < 0.4:
        return "distributed"
    else:
        return "mixed"


def find_cluster_center(targets: List[Dict], airports: List[Dict]) -> str:
    """
    Determine where targets are clustered relative to airports.
    """
    if len(targets) < 2 or len(airports) == 0:
        return "center"

    target_positions = np.array([[t.get("lat", t.get("x", 0)),
                                  t.get("lng", t.get("y", 0))] for t in targets])
    target_centroid = np.mean(target_positions, axis=0)

    airport_positions = np.array([[a.get("lat", a.get("x", 0)),
                                   a.get("lng", a.get("y", 0))] for a in airports])

    # Distance from target centroid to nearest airport
    distances_to_airports = np.linalg.norm(airport_positions - target_centroid, axis=1)
    min_dist_to_airport = np.min(distances_to_airports)

    # Spread of targets
    target_spread = np.max(pdist(target_positions)) if len(targets) > 1 else 1.0

    # If centroid is close to an airport relative to spread
    if min_dist_to_airport < target_spread * 0.3:
        return "airport"
    elif min_dist_to_airport > target_spread * 0.7:
        return "peripheral"
    else:
        return "center"


def classify_airport_coverage(airports: List[Dict]) -> str:
    """Classify based on number and spread of airports."""
    if len(airports) <= 1:
        return "single"
    elif len(airports) == 2:
        return "dual"
    else:
        return "multi"


def classify_sam_density(sams: List[Dict], targets: List[Dict]) -> str:
    """Classify SAM density relative to target area."""
    if len(sams) == 0:
        return "none"
    elif len(sams) <= 2:
        return "sparse"
    else:
        return "dense"


def build_feature_vector(features: Dict) -> List[float]:
    """
    Build numeric vector for similarity matching.

    Normalized to [0, 1] range where possible.
    """
    distribution_map = {"clustered": 1.0, "mixed": 0.5, "distributed": 0.0}
    center_map = {"airport": 1.0, "center": 0.5, "peripheral": 0.0}
    coverage_map = {"single": 0.0, "dual": 0.5, "multi": 1.0}
    density_map = {"none": 0.0, "sparse": 0.5, "dense": 1.0}

    return [
        distribution_map.get(features.get("target_distribution", "mixed"), 0.5),
        center_map.get(features.get("cluster_center", "center"), 0.5),
        coverage_map.get(features.get("airport_coverage", "single"), 0.0),
        density_map.get(features.get("sam_density", "none"), 0.0),
        min(features.get("target_count", 10) / 20.0, 1.0),  # Normalize to 20 max
        features.get("avg_target_priority", 5.0) / 10.0,
        features.get("target_type_diversity", 0.5),
    ]
```

### 1.2 Environment Similarity

```python
def compute_environment_similarity(features1: List[float], features2: List[float]) -> float:
    """
    Compute similarity between two environments (0 to 1).
    Uses cosine similarity.
    """
    v1 = np.array(features1)
    v2 = np.array(features2)

    dot = np.dot(v1, v2)
    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot / (norm1 * norm2)


def find_similar_experiences(
    current_features: List[float],
    experience_db: List[Dict],
    threshold: float = 0.85,
    top_k: int = 10
) -> List[Dict]:
    """
    Find past experiences with similar environments.

    Args:
        current_features: Feature vector of current environment
        experience_db: List of past experiences
        threshold: Minimum similarity to consider
        top_k: Maximum number of matches to return

    Returns:
        List of similar experiences, sorted by similarity
    """
    matches = []

    for exp in experience_db:
        exp_features = exp.get("env_features", [])
        if not exp_features:
            continue

        similarity = compute_environment_similarity(current_features, exp_features)

        if similarity >= threshold:
            matches.append({
                **exp,
                "similarity": similarity
            })

    # Sort by similarity descending
    matches.sort(key=lambda x: x["similarity"], reverse=True)

    return matches[:top_k]
```

---

## Part 2: Experience Storage

### 2.1 Experience Data Model

**Supabase table: `agent_experiences`**

```sql
CREATE TABLE agent_experiences (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMP DEFAULT NOW(),

    -- Environment features
    env_features JSONB NOT NULL,
    target_distribution VARCHAR(20),
    cluster_center VARCHAR(20),
    airport_coverage VARCHAR(20),
    sam_density VARCHAR(20),
    target_count INTEGER,

    -- Strategy used
    allocation_strategy VARCHAR(50) NOT NULL,
    solver_mode VARCHAR(50),
    post_optimize BOOLEAN DEFAULT TRUE,

    -- Outcome metrics
    targets_visited INTEGER,
    targets_total INTEGER,
    coverage_ratio FLOAT,
    total_fuel_used FLOAT,
    total_fuel_budget FLOAT,
    fuel_efficiency FLOAT,
    total_points INTEGER,
    max_possible_points INTEGER,
    point_ratio FLOAT,
    outcome_score FLOAT,  -- Composite score

    -- Context
    mission_id VARCHAR(100),
    user_feedback VARCHAR(20),  -- "good", "bad", NULL
    notes TEXT
);

CREATE INDEX idx_env_features ON agent_experiences USING GIN (env_features);
CREATE INDEX idx_allocation_strategy ON agent_experiences (allocation_strategy);
CREATE INDEX idx_outcome_score ON agent_experiences (outcome_score);
```

### 2.2 Experience Storage Functions

**Location**: Extend `server/agents/agent_memory.py` or new `server/agents/experience_store.py`

```python
from typing import Dict, List, Any, Optional
from datetime import datetime
import json

def store_experience(
    env_features: Dict[str, Any],
    strategy: str,
    outcome: Dict[str, Any],
    mission_id: str = None,
) -> Dict[str, Any]:
    """
    Store an experience (environment + strategy + outcome).

    Args:
        env_features: Output from characterize_environment()
        strategy: Allocation strategy used (e.g., "geographic", "greedy")
        outcome: {
            "targets_visited": int,
            "targets_total": int,
            "total_fuel_used": float,
            "total_fuel_budget": float,
            "total_points": int,
            "max_possible_points": int,
        }
        mission_id: Optional mission identifier

    Returns:
        The stored experience record
    """
    # Compute derived metrics
    coverage_ratio = outcome["targets_visited"] / max(outcome["targets_total"], 1)
    fuel_efficiency = 1.0 - (outcome["total_fuel_used"] / max(outcome["total_fuel_budget"], 1))
    point_ratio = outcome["total_points"] / max(outcome["max_possible_points"], 1)

    # Composite outcome score (weighted combination)
    outcome_score = (
        0.5 * coverage_ratio +    # Coverage is most important
        0.3 * point_ratio +       # Points matter
        0.2 * fuel_efficiency     # Efficiency is nice to have
    )

    experience = {
        "env_features": env_features.get("features_vector", []),
        "target_distribution": env_features.get("target_distribution"),
        "cluster_center": env_features.get("cluster_center"),
        "airport_coverage": env_features.get("airport_coverage"),
        "sam_density": env_features.get("sam_density"),
        "target_count": env_features.get("target_count"),

        "allocation_strategy": strategy,
        "solver_mode": "standard",
        "post_optimize": True,

        "targets_visited": outcome["targets_visited"],
        "targets_total": outcome["targets_total"],
        "coverage_ratio": coverage_ratio,
        "total_fuel_used": outcome["total_fuel_used"],
        "total_fuel_budget": outcome["total_fuel_budget"],
        "fuel_efficiency": fuel_efficiency,
        "total_points": outcome["total_points"],
        "max_possible_points": outcome["max_possible_points"],
        "point_ratio": point_ratio,
        "outcome_score": outcome_score,

        "mission_id": mission_id,
        "created_at": datetime.now().isoformat(),
    }

    # Store in Supabase or JSON fallback
    if _use_supabase():
        return _store_experience_supabase(experience)
    else:
        return _store_experience_json(experience)


def _store_experience_supabase(experience: Dict) -> Dict:
    """Store experience in Supabase."""
    client = _get_supabase()
    response = client.table("agent_experiences").insert(experience).execute()
    return response.data[0] if response.data else experience


def _store_experience_json(experience: Dict) -> Dict:
    """Store experience in local JSON file."""
    file_path = Path(__file__).parent / "agent_experiences.json"

    experiences = []
    if file_path.exists():
        experiences = json.loads(file_path.read_text())

    experience["id"] = len(experiences) + 1
    experiences.append(experience)

    file_path.write_text(json.dumps(experiences, indent=2))
    return experience


def load_experiences() -> List[Dict]:
    """Load all experiences."""
    if _use_supabase():
        client = _get_supabase()
        response = client.table("agent_experiences").select("*").execute()
        return response.data
    else:
        file_path = Path(__file__).parent / "agent_experiences.json"
        if file_path.exists():
            return json.loads(file_path.read_text())
        return []
```

---

## Part 3: Strategy Recommendation

### 3.1 Recommend Strategy Based on Experience

```python
def recommend_strategy(
    env: Dict[str, Any],
    available_strategies: List[str] = None
) -> Dict[str, Any]:
    """
    Recommend the best allocation strategy based on past experiences.

    Args:
        env: Current environment
        available_strategies: List of strategies to consider
            Default: ["geographic", "greedy", "balanced", "efficient", "exclusive"]

    Returns:
        {
            "recommended_strategy": str,
            "confidence": float (0-1),
            "reasoning": str,
            "similar_experiences": int,
            "strategy_scores": {strategy: avg_score, ...}
        }
    """
    if available_strategies is None:
        available_strategies = ["geographic", "greedy", "balanced", "efficient", "exclusive"]

    # Characterize current environment
    features = characterize_environment(env)
    feature_vector = features.get("features_vector", [])

    # Load past experiences
    experiences = load_experiences()

    if not experiences:
        # No experience yet — use rule-based default
        return {
            "recommended_strategy": get_default_strategy(features),
            "confidence": 0.3,
            "reasoning": "No past experience. Using rule-based default.",
            "similar_experiences": 0,
            "strategy_scores": {},
        }

    # Find similar environments
    similar = find_similar_experiences(feature_vector, experiences, threshold=0.8)

    if not similar:
        # No similar environments — use rule-based default
        return {
            "recommended_strategy": get_default_strategy(features),
            "confidence": 0.4,
            "reasoning": "No similar environments in experience database. Using rule-based default.",
            "similar_experiences": 0,
            "strategy_scores": {},
        }

    # Aggregate scores by strategy
    strategy_scores = defaultdict(list)
    for exp in similar:
        strategy = exp.get("allocation_strategy")
        score = exp.get("outcome_score", 0)
        strategy_scores[strategy].append(score)

    # Compute average score per strategy
    avg_scores = {}
    for strategy, scores in strategy_scores.items():
        if strategy in available_strategies:
            avg_scores[strategy] = sum(scores) / len(scores)

    if not avg_scores:
        return {
            "recommended_strategy": get_default_strategy(features),
            "confidence": 0.4,
            "reasoning": "Similar environments found but strategies not in available list.",
            "similar_experiences": len(similar),
            "strategy_scores": {},
        }

    # Pick best strategy
    best_strategy = max(avg_scores, key=avg_scores.get)
    best_score = avg_scores[best_strategy]

    # Confidence based on number of similar experiences and score consistency
    confidence = min(0.5 + (len(similar) * 0.05), 0.95)

    # Build reasoning
    distribution = features.get("target_distribution", "unknown")
    reasoning = (
        f"Based on {len(similar)} similar missions with {distribution} target distribution, "
        f"'{best_strategy}' achieved average score {best_score:.2f} "
        f"(vs {', '.join(f'{s}: {v:.2f}' for s, v in avg_scores.items() if s != best_strategy)})."
    )

    return {
        "recommended_strategy": best_strategy,
        "confidence": confidence,
        "reasoning": reasoning,
        "similar_experiences": len(similar),
        "strategy_scores": avg_scores,
    }


def get_default_strategy(features: Dict) -> str:
    """
    Rule-based default when no experience available.
    """
    distribution = features.get("target_distribution", "mixed")
    coverage = features.get("airport_coverage", "single")

    # Heuristics
    if distribution == "clustered":
        return "geographic"  # Cluster-based allocation
    elif distribution == "distributed" and coverage == "multi":
        return "balanced"    # Spread across drones
    elif features.get("target_count", 0) > 15:
        return "efficient"   # Prioritize high-value targets
    else:
        return "greedy"      # Simple greedy for small missions
```

### 3.2 Integration with Coordinator

**Location**: Modify `server/agents/coordinator_v4.py`

```python
def build_policy(user_request: str, env: Dict, drone_configs: Dict) -> Dict[str, Any]:
    """
    Build execution policy including strategy recommendation.
    """
    # Get strategy recommendation from experience
    recommendation = recommend_strategy(env)

    policy = {
        "allocation_strategy": recommendation["recommended_strategy"],
        "strategy_confidence": recommendation["confidence"],
        "strategy_reasoning": recommendation["reasoning"],
        "learned_from_experience": recommendation["similar_experiences"] > 0,
        # ... other policy fields
    }

    return policy
```

---

## Part 4: Automatic Learning After Solve

### 4.1 Post-Solve Learning Hook

**Location**: `server/agents/isr_agent_multi_v4.py` — end of solve workflow

```python
def record_experience_after_solve(
    state: MissionState,
    strategy_used: str,
    mission_id: str = None
) -> None:
    """
    Automatically record experience after a successful solve.
    Called at the end of the solve workflow.
    """
    env = state.get("environment", {})
    routes = state.get("routes", {})
    allocation = state.get("allocation", {})

    # Characterize environment
    env_features = characterize_environment(env)

    # Compute outcome metrics
    outcome = compute_outcome_metrics(env, routes, allocation)

    # Store experience
    store_experience(
        env_features=env_features,
        strategy=strategy_used,
        outcome=outcome,
        mission_id=mission_id,
    )


def compute_outcome_metrics(
    env: Dict,
    routes: Dict,
    allocation: Dict
) -> Dict[str, Any]:
    """
    Compute outcome metrics from a solved mission.
    """
    targets = env.get("targets", [])
    target_ids = {t.get("id") or t.get("name") for t in targets}

    # Count visited targets
    visited = set()
    total_fuel_used = 0.0
    total_fuel_budget = 0.0
    total_points = 0

    for drone_id, route_data in routes.items():
        route = route_data.get("route", [])
        visited.update(t for t in route if t in target_ids)
        total_fuel_used += route_data.get("distance", 0)
        total_fuel_budget += route_data.get("fuel_budget", 0)

    # Compute points
    target_map = {t.get("id") or t.get("name"): t for t in targets}
    for tid in visited:
        target = target_map.get(tid, {})
        total_points += target.get("priority", 1)

    max_points = sum(t.get("priority", 1) for t in targets)

    return {
        "targets_visited": len(visited),
        "targets_total": len(targets),
        "total_fuel_used": total_fuel_used,
        "total_fuel_budget": total_fuel_budget,
        "total_points": total_points,
        "max_possible_points": max_points,
    }
```

### 4.2 User Feedback Integration

Allow user to rate solutions to improve learning:

```python
@tool
def rate_solution_tool(rating: str) -> str:
    """
    Rate the current solution to help the agent learn.

    Args:
        rating: "good" or "bad"
    """
    state = get_state()
    mission_id = state.get("mission_id")

    if not mission_id:
        return "No active mission to rate."

    # Update experience with user feedback
    update_experience_feedback(mission_id, rating)

    if rating == "good":
        return "Thanks! I'll remember this strategy works well for this type of environment."
    else:
        return "Got it. I'll try different strategies for similar environments in the future."


def update_experience_feedback(mission_id: str, feedback: str) -> None:
    """Update stored experience with user feedback."""
    if _use_supabase():
        client = _get_supabase()
        client.table("agent_experiences").update({
            "user_feedback": feedback
        }).eq("mission_id", mission_id).execute()
    else:
        # JSON fallback - find and update
        experiences = load_experiences()
        for exp in experiences:
            if exp.get("mission_id") == mission_id:
                exp["user_feedback"] = feedback
                break
        save_experiences(experiences)
```

---

## Part 5: Agent Prompts for Learning

### 5.1 Strategy Explanation Prompt

When the agent recommends a strategy, it should explain:

```python
STRATEGY_RECOMMENDATION_PROMPT = """
Based on my analysis of this environment and past experience:

ENVIRONMENT CHARACTERISTICS:
- Target distribution: {target_distribution}
- Targets clustered: {cluster_center}
- Airport coverage: {airport_coverage}
- SAM density: {sam_density}
- Target count: {target_count}

STRATEGY RECOMMENDATION:
I recommend using '{recommended_strategy}' allocation.

REASONING:
{reasoning}

Confidence: {confidence:.0%}
Based on: {similar_experiences} similar past missions
"""
```

### 5.2 Learning Summary Prompt

Periodically summarize what the agent has learned:

```python
def generate_learning_summary() -> str:
    """
    Generate a summary of what the agent has learned.
    """
    experiences = load_experiences()

    if not experiences:
        return "I haven't learned from any missions yet."

    # Aggregate by environment type and strategy
    analysis = defaultdict(lambda: defaultdict(list))

    for exp in experiences:
        env_type = exp.get("target_distribution", "unknown")
        strategy = exp.get("allocation_strategy", "unknown")
        score = exp.get("outcome_score", 0)
        analysis[env_type][strategy].append(score)

    lines = [f"## Learning Summary ({len(experiences)} missions)\n"]

    for env_type, strategies in analysis.items():
        lines.append(f"\n### {env_type.title()} Target Distribution")

        # Rank strategies
        ranked = []
        for strategy, scores in strategies.items():
            avg = sum(scores) / len(scores)
            ranked.append((strategy, avg, len(scores)))

        ranked.sort(key=lambda x: x[1], reverse=True)

        for strategy, avg, count in ranked:
            lines.append(f"- {strategy}: avg score {avg:.2f} ({count} missions)")

    return "\n".join(lines)
```

---

## Part 6: Example Conversations

### Example 1: Strategy Recommendation

```
User: "Solve this mission"

Agent: "Analyzing environment...

        ENVIRONMENT:
        - 12 targets clustered around airport A1
        - 2 airports (A1, A2)
        - No SAM zones

        STRATEGY RECOMMENDATION:
        Based on 8 similar missions, I recommend 'geographic' allocation.
        - geographic: avg 0.87 score (6 missions)
        - greedy: avg 0.72 score (2 missions)

        Using geographic strategy..."

        [Solves mission]

        "Done! 11/12 targets visited, 45 points.
         Experience recorded for future learning."
```

### Example 2: Learning Over Time

```
User: "What have you learned about allocation strategies?"

Agent: "## Learning Summary (47 missions)

        ### Clustered Target Distribution
        - geographic: avg 0.89 (23 missions) ⭐ Best
        - balanced: avg 0.76 (8 missions)
        - greedy: avg 0.71 (5 missions)

        ### Distributed Target Distribution
        - balanced: avg 0.84 (6 missions) ⭐ Best
        - efficient: avg 0.79 (3 missions)
        - geographic: avg 0.68 (2 missions)

        Key insight: Geographic works best when targets cluster around
        airports. Balanced works better when targets are spread out."
```

### Example 3: User Feedback

```
User: "That solution was terrible"

Agent: [Runs rate_solution_tool("bad")]
       "Got it. I'll try different strategies for similar environments
        in the future. Would you like me to re-solve with a different
        approach? I could try 'balanced' instead of 'geographic'."
```

---

## Part 7: Implementation Order

### Phase 1: Environment Characterization (Day 1)
1. Create `environment_analyzer.py`
2. Implement `characterize_environment()`
3. Implement `analyze_target_distribution()`
4. Implement `build_feature_vector()`
5. Test characterization on sample environments

### Phase 2: Experience Storage (Day 1-2)
1. Create Supabase table `agent_experiences`
2. Implement `store_experience()`
3. Implement `load_experiences()`
4. Implement JSON fallback
5. Test storage and retrieval

### Phase 3: Strategy Recommendation (Day 2)
1. Implement `find_similar_experiences()`
2. Implement `recommend_strategy()`
3. Implement `get_default_strategy()`
4. Integrate with coordinator
5. Test recommendations

### Phase 4: Automatic Learning (Day 3)
1. Implement `record_experience_after_solve()`
2. Implement `compute_outcome_metrics()`
3. Add hook to solve workflow
4. Implement `rate_solution_tool()`
5. Test end-to-end learning

### Phase 5: Prompts & UI (Day 3-4)
1. Add strategy explanation prompts
2. Implement `generate_learning_summary()`
3. Add "What have you learned?" response
4. Test conversations
5. Polish and document

---

## Appendix A: File Changes Summary

| File | Changes |
|------|---------|
| `server/agents/environment_analyzer.py` | NEW - environment characterization |
| `server/agents/experience_store.py` | NEW - experience storage |
| `server/agents/agent_memory.py` | Extend with experience functions |
| `server/agents/coordinator_v4.py` | Integrate strategy recommendation |
| `server/agents/isr_agent_multi_v4.py` | Add learning hook, rate tool |
| `server/database/supabase_migrations/` | Add agent_experiences table |

---

## Appendix B: Feature Vector Specification

| Index | Feature | Range | Description |
|-------|---------|-------|-------------|
| 0 | Distribution | 0-1 | 0=distributed, 0.5=mixed, 1=clustered |
| 1 | Cluster center | 0-1 | 0=peripheral, 0.5=center, 1=airport |
| 2 | Airport coverage | 0-1 | 0=single, 0.5=dual, 1=multi |
| 3 | SAM density | 0-1 | 0=none, 0.5=sparse, 1=dense |
| 4 | Target count | 0-1 | Normalized: count/20 (capped at 1) |
| 5 | Avg priority | 0-1 | avg_priority/10 |
| 6 | Type diversity | 0-1 | unique_types/5 |

Similarity computed as cosine similarity between vectors.

---

## Appendix C: Outcome Score Formula

```
outcome_score = 0.5 × coverage_ratio + 0.3 × point_ratio + 0.2 × fuel_efficiency

where:
  coverage_ratio = targets_visited / targets_total
  point_ratio = points_achieved / max_possible_points
  fuel_efficiency = 1 - (fuel_used / fuel_budget)
```

Weights can be adjusted based on mission priorities.

---

**End of Implementation Plan**
