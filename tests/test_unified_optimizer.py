#!/usr/bin/env python3
"""
Test script for Unified Optimizer (interleaved Insert + Swap)

Tests the UnifiedOptimizer class which:
1. Evaluates ALL insert and swap candidates at each iteration
2. Executes the single best operation per iteration
3. Runs crossing removal as final cleanup
"""

import math
import os
import sys

# Add path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from server.solver.post_optimizer import UnifiedOptimizer, unified_optimize


def create_simple_env():
    """Create a simple test environment with 4 targets and 2 airports."""
    return {
        "airports": [{"id": "A1", "x": 0, "y": 0}, {"id": "A2", "x": 100, "y": 0}],
        "targets": [
            {"id": "T1", "x": 20, "y": 10, "priority": 8, "type": "a"},
            {"id": "T2", "x": 40, "y": 5, "priority": 6, "type": "a"},
            {"id": "T3", "x": 60, "y": -5, "priority": 9, "type": "b"},
            {"id": "T4", "x": 80, "y": 10, "priority": 5, "type": "a"},
            {"id": "T5", "x": 50, "y": 20, "priority": 7, "type": "a"},  # Unvisited
        ],
        "sams": [],  # No SAMs for simple test
    }


def create_simple_solution():
    """Create a simple initial solution with some unvisited targets."""
    return {
        "routes": {
            "1": {
                "route": ["A1", "T1", "T2", "A1"],
                "distance": 85.0,
                "points": 14,  # 8 + 6
                "frozen_segments": [],
            },
            "2": {
                "route": ["A2", "T3", "T4", "A2"],
                "distance": 65.0,
                "points": 14,  # 9 + 5
                "frozen_segments": [],
            },
        }
    }


def create_simple_drone_configs():
    """Create drone configurations for testing."""
    return {
        "1": {
            "fuel_capacity": 150,
            "enabled": True,
            "target_access": {"a": True, "b": True, "c": True, "d": True, "e": True},
        },
        "2": {
            "fuel_capacity": 150,
            "enabled": True,
            "target_access": {"a": True, "b": True, "c": True, "d": True, "e": True},
        },
    }


def create_simple_distance_matrix(env):
    """Create a simple Euclidean distance matrix."""
    waypoints = []
    positions = {}

    for a in env["airports"]:
        wp_id = str(a["id"])
        waypoints.append(wp_id)
        positions[wp_id] = (float(a["x"]), float(a["y"]))

    for t in env["targets"]:
        wp_id = str(t["id"])
        waypoints.append(wp_id)
        positions[wp_id] = (float(t["x"]), float(t["y"]))

    n = len(waypoints)
    matrix = [[0.0] * n for _ in range(n)]

    for i, wp1 in enumerate(waypoints):
        for j, wp2 in enumerate(waypoints):
            if i != j:
                p1 = positions[wp1]
                p2 = positions[wp2]
                matrix[i][j] = math.hypot(p2[0] - p1[0], p2[1] - p1[1])

    return {"labels": waypoints, "matrix": matrix}


def test_optimizer_initialization():
    """Test that UnifiedOptimizer initializes correctly."""
    print("\n" + "=" * 60)
    print("TEST: Optimizer Initialization")
    print("=" * 60)

    optimizer = UnifiedOptimizer()
    assert optimizer._distance_matrix is None
    assert optimizer._env is None
    assert optimizer._waypoint_positions is None
    print("  Optimizer initialized with None values")

    # Set environment
    env = create_simple_env()
    optimizer.set_environment(env)
    assert optimizer._waypoint_positions is not None
    assert len(optimizer._waypoint_positions) == 7  # 2 airports + 5 targets
    print(f"  Waypoint positions set: {list(optimizer._waypoint_positions.keys())}")

    # Set distance matrix
    matrix = create_simple_distance_matrix(env)
    optimizer.set_distance_matrix(matrix)
    assert optimizer._distance_matrix is not None
    assert len(optimizer._distance_matrix["labels"]) == 7
    print(f"  Distance matrix set with {len(matrix['labels'])} waypoints")

    print("  PASSED")


def test_insertion_cost_calculation():
    """Test insertion cost calculation."""
    print("\n" + "=" * 60)
    print("TEST: Insertion Cost Calculation")
    print("=" * 60)

    env = create_simple_env()
    matrix = create_simple_distance_matrix(env)

    optimizer = UnifiedOptimizer()
    optimizer.set_distance_matrix(matrix)
    optimizer.set_environment(env)

    # Test insertion cost for T5 between T1 and T2
    # T1 at (20, 10), T5 at (50, 20), T2 at (40, 5)
    # d(T1, T5) + d(T5, T2) - d(T1, T2)
    cost = optimizer._calculate_insertion_cost("T1", "T5", "T2")
    print(f"  Insertion cost for T5 between T1 and T2: {cost:.2f}")
    assert cost > 0, "Insertion cost should be positive (adding detour)"

    # Test that cost is calculated correctly
    d_t1_t5 = optimizer._get_distance("T1", "T5")
    d_t5_t2 = optimizer._get_distance("T5", "T2")
    d_t1_t2 = optimizer._get_distance("T1", "T2")
    expected_cost = d_t1_t5 + d_t5_t2 - d_t1_t2
    assert abs(cost - expected_cost) < 0.01, f"Cost mismatch: {cost} vs {expected_cost}"
    print(
        f"  Cost matches formula: d(T1,T5)={d_t1_t5:.2f} + d(T5,T2)={d_t5_t2:.2f} - d(T1,T2)={d_t1_t2:.2f}"
    )

    print("  PASSED")


def test_insert_candidate_evaluation():
    """Test that insert candidates are evaluated correctly."""
    print("\n" + "=" * 60)
    print("TEST: Insert Candidate Evaluation")
    print("=" * 60)

    env = create_simple_env()
    solution = create_simple_solution()
    drone_configs = create_simple_drone_configs()
    matrix = create_simple_distance_matrix(env)

    optimizer = UnifiedOptimizer()
    optimizer.set_distance_matrix(matrix)
    optimizer.set_environment(env)

    candidates = optimizer._evaluate_insert_candidates(solution, env, drone_configs, {})

    print(f"  Found {len(candidates)} insert candidates for T5")

    # T5 should be the only unvisited target
    unvisited_targets = set()
    for c in candidates:
        unvisited_targets.add(c["target"])
    assert "T5" in unvisited_targets, "T5 should be in candidates"
    print(f"  Unvisited targets found: {unvisited_targets}")

    # Check candidate structure
    for c in candidates:
        assert c["type"] == "insert"
        assert "target" in c
        assert "drone" in c
        assert "position" in c
        assert "cost" in c
        assert "priority" in c
        assert "score" in c
        print(
            f"    {c['target']} -> D{c['drone']} pos {c['position']}: cost={c['cost']:.2f}, priority={c['priority']}, score={c['score']:.3f}"
        )

    print("  PASSED")


def test_swap_candidate_evaluation():
    """Test that swap candidates are evaluated correctly."""
    print("\n" + "=" * 60)
    print("TEST: Swap Candidate Evaluation")
    print("=" * 60)

    env = create_simple_env()
    solution = create_simple_solution()
    drone_configs = create_simple_drone_configs()
    matrix = create_simple_distance_matrix(env)

    optimizer = UnifiedOptimizer()
    optimizer.set_distance_matrix(matrix)
    optimizer.set_environment(env)

    candidates = optimizer._evaluate_swap_candidates(solution, env, drone_configs, {})

    print(f"  Found {len(candidates)} swap candidates")

    # Check candidate structure
    for c in candidates[:5]:  # Show first 5
        assert c["type"] == "swap"
        assert "target" in c
        assert "from_drone" in c
        assert "to_drone" in c
        assert "gain" in c
        assert c["gain"] > 0, "Only beneficial swaps should be included"
        print(
            f"    {c['target']}: D{c['from_drone']} -> D{c['to_drone']}, gain={c['gain']:.2f}"
        )

    print("  PASSED")


def test_unified_optimize_inserts_unvisited():
    """Test that unified optimizer inserts unvisited targets."""
    print("\n" + "=" * 60)
    print("TEST: Unified Optimizer Inserts Unvisited Targets")
    print("=" * 60)

    env = create_simple_env()
    solution = create_simple_solution()
    drone_configs = create_simple_drone_configs()
    matrix = create_simple_distance_matrix(env)

    print("  Initial routes:")
    for did, rd in solution["routes"].items():
        print(f"    D{did}: {' -> '.join(rd['route'])}")

    # Run unified optimizer (without crossing removal for simpler test)
    result = unified_optimize(
        solution,
        env,
        drone_configs,
        distance_matrix=matrix,
        run_crossing_removal=False,
        debug=False,
    )

    print("\n  Optimized routes:")
    for did, rd in result["routes"].items():
        print(f"    D{did}: {' -> '.join(rd['route'])}")

    # Check that T5 was inserted
    all_targets_in_routes = set()
    for did, rd in result["routes"].items():
        for wp in rd["route"]:
            if wp.startswith("T"):
                all_targets_in_routes.add(wp)

    assert "T5" in all_targets_in_routes, "T5 should have been inserted"
    print(f"\n  All targets in routes: {all_targets_in_routes}")

    # Check metadata
    if "metadata" in result and "unified_optimizer" in result["metadata"]:
        meta = result["metadata"]["unified_optimizer"]
        print(
            f"  Operations: {meta['total_inserts']} inserts, {meta['total_swaps']} swaps"
        )

    print("  PASSED")


def test_frozen_segments_respected():
    """Test that frozen segments are respected during optimization."""
    print("\n" + "=" * 60)
    print("TEST: Frozen Segments Respected")
    print("=" * 60)

    env = create_simple_env()
    drone_configs = create_simple_drone_configs()
    matrix = create_simple_distance_matrix(env)

    # Create solution with frozen segment
    solution = {
        "routes": {
            "1": {
                "route": ["A1", "T1", "T2", "A1"],
                "distance": 85.0,
                "points": 14,
                "frozen_segments": [0, 1],  # Freeze A1->T1 and T1->T2
            },
            "2": {
                "route": ["A2", "T3", "A2"],
                "distance": 50.0,
                "points": 9,
                "frozen_segments": [],
            },
        }
    }

    print("  Initial routes (D1 has frozen segments [0,1]):")
    for did, rd in solution["routes"].items():
        print(
            f"    D{did}: {' -> '.join(rd['route'])} (frozen: {rd.get('frozen_segments', [])})"
        )

    optimizer = UnifiedOptimizer()
    optimizer.set_distance_matrix(matrix)
    optimizer.set_environment(env)

    # Check insert candidates - should not insert into frozen segments
    candidates = optimizer._evaluate_insert_candidates(solution, env, drone_configs, {})

    d1_insertions = [c for c in candidates if c["drone"] == "1"]
    print(f"\n  Insert candidates for D1: {len(d1_insertions)}")

    # D1 has 3 positions (0,1,2) but 0 and 1 are frozen
    # So only position 2 (between T2 and A1) should be valid
    for c in d1_insertions:
        assert c["position"] not in [1, 2], f"Position {c['position']} should be frozen"
        # Position 1 would be before A1->T1 segment (index 0)
        # Position 2 would be before T1->T2 segment (index 1)
        print(f"    {c['target']} at position {c['position']}")

    print("  PASSED")


def test_hybrid_scoring():
    """Test that hybrid scoring correctly prioritizes operations."""
    print("\n" + "=" * 60)
    print("TEST: Hybrid Scoring Strategy")
    print("=" * 60)

    env = create_simple_env()
    solution = create_simple_solution()
    drone_configs = create_simple_drone_configs()
    matrix = create_simple_distance_matrix(env)

    optimizer = UnifiedOptimizer()
    optimizer.set_distance_matrix(matrix)
    optimizer.set_environment(env)

    insert_candidates = optimizer._evaluate_insert_candidates(
        solution, env, drone_configs, {}
    )

    swap_candidates = optimizer._evaluate_swap_candidates(
        solution, env, drone_configs, {}
    )

    print(f"  Insert candidates: {len(insert_candidates)}")
    print(f"  Swap candidates: {len(swap_candidates)}")

    # Check insert scoring: priority * 10 / max(cost, 1)
    for c in insert_candidates[:3]:
        expected_score = c["priority"] * 10 / max(c["cost"], 1.0)
        assert abs(c["score"] - expected_score) < 0.01, "Insert score mismatch"
        print(
            f"    Insert {c['target']}: priority={c['priority']}, cost={c['cost']:.2f}, score={c['score']:.3f}"
        )

    # Check swap scoring: score = gain
    for c in swap_candidates[:3]:
        assert abs(c["score"] - c["gain"]) < 0.01, "Swap score should equal gain"
        print(f"    Swap {c['target']}: gain={c['gain']:.2f}, score={c['score']:.3f}")

    print("  PASSED")


def test_cross_drone_swap_fuel_check():
    """Test that cross-drone swaps check destination fuel budget."""
    print("\n" + "=" * 60)
    print("TEST: Cross-Drone Swap Fuel Check")
    print("=" * 60)

    env = create_simple_env()
    matrix = create_simple_distance_matrix(env)

    # Create solution where D2 has very little fuel remaining
    solution = {
        "routes": {
            "1": {
                "route": ["A1", "T1", "T2", "A1"],
                "distance": 85.0,
                "points": 14,
                "frozen_segments": [],
            },
            "2": {
                "route": ["A2", "T3", "A2"],
                "distance": 145.0,  # Almost at fuel limit
                "points": 9,
                "frozen_segments": [],
            },
        }
    }

    drone_configs = {
        "1": {
            "fuel_capacity": 150,
            "enabled": True,
            "target_access": {"a": True, "b": True},
        },
        "2": {
            "fuel_capacity": 150,  # Only 5 fuel remaining
            "enabled": True,
            "target_access": {"a": True, "b": True},
        },
    }

    optimizer = UnifiedOptimizer()
    optimizer.set_distance_matrix(matrix)
    optimizer.set_environment(env)

    candidates = optimizer._evaluate_swap_candidates(solution, env, drone_configs, {})

    # Check that no swaps TO D2 are included (not enough fuel)
    swaps_to_d2 = [
        c for c in candidates if c["to_drone"] == "2" and c["from_drone"] != "2"
    ]
    print(f"  Swaps to D2 (with 5 fuel remaining): {len(swaps_to_d2)}")

    for c in swaps_to_d2:
        remaining = 150 - 145  # 5 fuel
        assert c["insertion_cost"] <= remaining, "Swap to D2 should not exceed fuel"
        print(
            f"    {c['target']}: insertion_cost={c['insertion_cost']:.2f} <= remaining={remaining}"
        )

    print("  PASSED")


def run_all_tests():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("UNIFIED OPTIMIZER TEST SUITE")
    print("=" * 60)

    test_optimizer_initialization()
    test_insertion_cost_calculation()
    test_insert_candidate_evaluation()
    test_swap_candidate_evaluation()
    test_unified_optimize_inserts_unvisited()
    test_frozen_segments_respected()
    test_hybrid_scoring()
    test_cross_drone_swap_fuel_check()

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED!")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
