"""Tests confirming calculate_sam_aware_matrix() is the single, correct choke
point for cruise_altitude_km — the fix that makes agent solve paths (v4's
route_optimizer_node via run_multi_agent_v4, reasoning_agent.py's
commit_ordered_plan, and the manual-solve API) all see shrunk SAM radii,
since they all compute their distance matrix through this one function.

Before this fix: only server/api/solve_routes.py (the manual "Run Planner"
button's endpoint) applied the shrink — agent-driven solves ignored
cruise_altitude_km entirely and always treated SAMs as ground-level.
"""

import json
from pathlib import Path

from server.solver.sam_distance_matrix import calculate_sam_aware_matrix

DEMO_ENV_PATH = (
    Path(__file__).resolve().parents[1] / "demo" / "isr_env_DEMO_compact.json"
)


def _demo_env():
    raw = json.loads(DEMO_ENV_PATH.read_text())
    return raw["env"]


def test_zero_altitude_matches_prior_behavior():
    env = _demo_env()
    result_no_alt = calculate_sam_aware_matrix(
        {**env, "cruise_altitude_km": 0.0}, use_supabase_cache=False
    )
    result_omitted = calculate_sam_aware_matrix(env, use_supabase_cache=False)
    # Same env either way (0.0 or absent) must produce the identical matrix.
    assert result_no_alt["matrix"] == result_omitted["matrix"]


def test_altitude_shrinks_sam_and_changes_matrix():
    env = _demo_env()
    sam_range = env["sams"][0]["range_km"]

    result_ground = calculate_sam_aware_matrix(
        {**env, "cruise_altitude_km": 0.0}, use_supabase_cache=False
    )
    result_high_alt = calculate_sam_aware_matrix(
        {**env, "cruise_altitude_km": sam_range + 1.0}, use_supabase_cache=False
    )

    # At an altitude above the SAM's range, the SAM is fully cleared — the
    # distance matrix must differ from the ground-level (full SAM detour) one.
    assert result_ground["matrix"] != result_high_alt["matrix"]


def test_altitude_affects_env_hash_no_cache_collision():
    # Two calls with identical base geometry but different cruise altitudes
    # must NOT share a cache entry — env_hash must differ, or a cached
    # ground-level matrix could get incorrectly reused for a high-altitude
    # solve (or vice versa).
    env = _demo_env()
    sam_range = env["sams"][0]["range_km"]

    result_ground = calculate_sam_aware_matrix(
        {**env, "cruise_altitude_km": 0.0}, use_supabase_cache=False
    )
    result_high_alt = calculate_sam_aware_matrix(
        {**env, "cruise_altitude_km": sam_range + 1.0}, use_supabase_cache=False
    )

    assert result_ground.get("env_hash") != result_high_alt.get("env_hash")


def test_negative_altitude_is_a_noop():
    env = _demo_env()
    result_negative = calculate_sam_aware_matrix(
        {**env, "cruise_altitude_km": -5.0}, use_supabase_cache=False
    )
    result_omitted = calculate_sam_aware_matrix(env, use_supabase_cache=False)
    assert result_negative["matrix"] == result_omitted["matrix"]
