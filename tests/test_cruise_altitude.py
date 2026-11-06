import json
import math
from pathlib import Path

import pytest

from server.api.models import SolveWithAllocationRequest
from server.api.solve_routes import run_solve_with_allocation
from server.solver.optimizers.post_insert import set_optimizer_matrix
from server.solver.optimizers.trajectory_swap import set_trajectory_optimizer_matrix
from server.solver.projection import _apply_cruise_altitude_to_sams
from server.solver.solve_mission import solve_mission_with_allocation
from server.solver.solver_bridge import clear_matrix_cache
from server.solver.target_allocator import clear_allocator_matrix

DEMO_ENV_PATH = (
    Path(__file__).resolve().parents[1] / "demo" / "isr_env_DEMO_compact.json"
)


@pytest.fixture(autouse=True)
def _clear_solver_module_caches():
    # solve_mission.py, sam_distance_matrix.py, and target_allocator.py hold
    # module-level distance-matrix caches keyed by an env hash. Running the
    # real solve pipeline repeatedly with different env shapes in this file
    # (altitude 0 vs shrunk vs fully-cleared) can leave those caches in a
    # state that trips up unrelated tests running later in the same pytest
    # session (observed: test_trajectory_swap_debug failing only when run
    # after this file, never in isolation). Not a bug in the altitude
    # feature itself — pre-existing global-state fragility in the cache
    # design — but this file is what surfaces it, so clean up after ourselves.
    #
    # Also: server/solver/optimizers/trajectory_swap.py's trajectory_swap_optimize()
    # only calls set_distance_matrix when its own `distance_matrix` arg is
    # truthy (`if distance_matrix:`), so a caller explicitly passing None to
    # request Euclidean-fallback behavior (as tests/test_trajectory_swap_debug.py
    # does) silently gets whatever matrix a PRIOR call left in the singleton
    # instead. That's a real, separate pre-existing bug — out of scope for
    # this feature — but we still need to reset it here so this file doesn't
    # trip it for tests that run afterward in the same session.
    yield
    clear_matrix_cache()
    clear_allocator_matrix()
    set_optimizer_matrix(None)
    set_trajectory_optimizer_matrix(None)


def test_zero_altitude_is_a_noop():
    env = {"sams": [{"range": 8.0, "range_km": 8.0}]}
    result = _apply_cruise_altitude_to_sams(env, 0.0)
    assert result["sams"][0]["range"] == 8.0
    assert result["sams"][0]["range_km"] == 8.0
    # No-op path returns the same object, not a defensive copy — callers
    # relying on identity when altitude is unset should see that.
    assert result is env


def test_shrinks_range_by_slice_radius():
    env = {"sams": [{"range": 8.0, "range_km": 8.0}]}
    result = _apply_cruise_altitude_to_sams(env, 6.0)
    expected = math.sqrt(8.0**2 - 6.0**2)
    assert result["sams"][0]["range"] == expected
    assert result["sams"][0]["range_km"] == expected
    # Original env must be untouched (shrink returns a copy when altitude > 0).
    assert env["sams"][0]["range"] == 8.0


def test_altitude_at_or_above_range_fully_clears_sam():
    env = {"sams": [{"range": 8.0, "range_km": 8.0}]}
    result = _apply_cruise_altitude_to_sams(env, 8.0)
    assert result["sams"][0]["range"] == 0.0

    result_above = _apply_cruise_altitude_to_sams(env, 20.0)
    assert result_above["sams"][0]["range"] == 0.0


def test_missing_range_key_is_skipped_not_errored():
    env = {"sams": [{"pos": [0, 0]}]}
    result = _apply_cruise_altitude_to_sams(env, 5.0)
    assert "range" not in result["sams"][0]


def test_shrinks_range_km_only_env_pre_projection():
    # At the actual call site (server/api/solve_routes.py), the shrink runs on
    # the RAW lat/lng env, which only has range_km — "range" doesn't exist
    # until _project_env_to_local_km creates it later. Must work here too.
    env = {"sams": [{"center": {"lat": 0, "lng": 0}, "range_km": 8.0}]}
    result = _apply_cruise_altitude_to_sams(env, 6.0)
    expected = math.sqrt(8.0**2 - 6.0**2)
    assert result["sams"][0]["range_km"] == expected
    assert "range" not in result["sams"][0]


def _solve_via_api(env, drone_configs, post_optimize=False):
    req = SolveWithAllocationRequest(
        env=env,
        drone_configs=drone_configs,
        allocation_strategy="geographic",
        post_optimize=post_optimize,
    )
    resp = run_solve_with_allocation(req)
    return resp.routes


def test_solve_mission_with_allocation_shrinks_via_calculate_sam_aware_matrix():
    # solve_mission_with_allocation is the solve every agent path calls
    # directly (never through server/api/solve_routes.py's boundary slice). It
    # must slice SAMs itself — in the matrix AND in the trajectories — so called
    # directly it agrees with the API path: at ground level this env's SAM
    # detour is too long for D1's fuel; above the SAM's range it is cleared and
    # D1 completes. (Before phase B the trajectories here used the full ground
    # range, so D1 failed at every altitude.)
    raw = json.loads(DEMO_ENV_PATH.read_text())
    env = raw["env"]
    drone_configs = raw["drone_configs"]
    sam_range = env["sams"][0]["range_km"]

    baseline = solve_mission_with_allocation(
        env=env, drone_configs=drone_configs, allocation_strategy="geographic"
    )
    assert baseline["routes"]["1"]["distance"] == 0.0
    env_with_alt = {**env, "cruise_altitude_km": sam_range + 1.0}
    with_alt = solve_mission_with_allocation(
        env=env_with_alt, drone_configs=drone_configs, allocation_strategy="geographic"
    )
    assert len(with_alt["routes"]["1"]["route"]) > 1
    assert with_alt["routes"]["1"]["distance"] > 0


def test_api_solve_with_cruise_altitude_matches_prior_behavior_when_zero():
    raw = json.loads(DEMO_ENV_PATH.read_text())
    env = raw["env"]
    drone_configs = raw["drone_configs"]

    baseline = _solve_via_api(env, drone_configs)
    with_zero_alt = _solve_via_api({**env, "cruise_altitude_km": 0.0}, drone_configs)

    assert with_zero_alt["1"]["distance"] == baseline["1"]["distance"]


def test_api_solve_with_cruise_altitude_clears_sam_and_completes_mission():
    raw = json.loads(DEMO_ENV_PATH.read_text())
    env = raw["env"]
    drone_configs = raw["drone_configs"]
    sam_range = env["sams"][0]["range_km"]

    # At ground level (altitude 0) this env's SAM detour is too costly for
    # D1's fuel budget — the drone can't complete the route (matches the
    # altitude_prototype validation script's finding).
    baseline = _solve_via_api(env, drone_configs)
    assert baseline["1"]["route"] == ["A1"]

    # Flying above the SAM's range should fully clear it and let the drone
    # complete the mission on the same fuel budget.
    env_high_alt = {**env, "cruise_altitude_km": sam_range + 1.0}
    with_alt = _solve_via_api(env_high_alt, drone_configs)
    assert len(with_alt["1"]["route"]) > 1


def test_double_shrink_is_prevented_when_boundary_and_matrix_both_apply():
    # REGRESSION (found 2026-09-17, live in the browser): the manual "Run
    # Planner" path shrinks SAM radii TWICE for the same request —
    # server/api/solve_routes.py shrinks once at the API boundary (needed so
    # the post-optimize pipeline's independently-re-projected env sees the
    # shrunk radius — see test_post_optimize_pipeline_preserves_altitude_shrink),
    # then solve_mission_with_allocation calls calculate_sam_aware_matrix,
    # which ALSO shrinks (added for agent solve paths that skip
    # solve_routes.py entirely — see tests/test_sam_matrix_cruise_altitude.py).
    # Applying sqrt(r^2-h^2) twice over-shrinks: e.g. r=8, h=5 gives r'=6.245
    # correctly, but a second pass on 6.245 with the SAME h=5 gives
    # sqrt(6.245^2-5^2)=3.606 — visibly wrong (a SAM that should still be
    # a real, partial obstacle at this altitude reads as much smaller,
    # or a SAM that should be fully cleared can read as still-shrinkable).
    # _apply_cruise_altitude_to_sams must be idempotent per env so this can't
    # happen regardless of how many solve-path call sites apply it.
    env = {"sams": [{"range": 8.0, "range_km": 8.0}]}
    once = _apply_cruise_altitude_to_sams(env, 5.0)
    twice = _apply_cruise_altitude_to_sams(once, 5.0)
    assert twice["sams"][0]["range"] == once["sams"][0]["range"]
    assert twice["sams"][0]["range"] == pytest.approx(math.sqrt(8.0**2 - 5.0**2))


def test_post_optimize_pipeline_preserves_altitude_shrink():
    # REGRESSION: the post-solve optimizer pipeline (Insert/Swap/No-Cross)
    # re-projects the env and rebuilds trajectories independently of the main
    # solve. Before the fix, the shrink lived INSIDE solve_mission_with_allocation
    # on a locally-scoped env copy that never reached the pipeline, so the
    # pipeline regenerated trajectories against the ORIGINAL, unshrunk SAM
    # radius — the route/distance looked altitude-aware but the rendered
    # trajectory still fully detoured around the full-size SAM regardless of
    # altitude. Moving the shrink to the API boundary (server/api/solve_routes.py,
    # applied to env_to_solve before ANYTHING uses it) fixes this because the
    # pipeline's own re-projection now starts from already-shrunk range_km.
    raw = json.loads(DEMO_ENV_PATH.read_text())
    env = raw["env"]
    drone_configs = raw["drone_configs"]
    sam_range = env["sams"][0]["range_km"]

    env_high_alt = {**env, "cruise_altitude_km": sam_range + 1.0}
    routes = _solve_via_api(env_high_alt, drone_configs, post_optimize=True)

    d1 = routes["1"]
    assert len(d1["route"]) > 1
    # A trajectory that still detours around the (now-cleared) SAM would have
    # noticeably more waypoints / distance than a direct-ish route between
    # consecutive targets. The regenerated trajectory must be short — a full
    # detour around the original 8km-radius SAM is the specific failure this
    # test catches.
    assert d1["distance"] < 60.0
