"""Altitude model phase B: each drone plans at its own cruise altitude.

docs/architecture/ALTITUDE_MODEL.md §4.
"""

import math

import pytest

from server.solver.altitude import (
    drone_altitude_km,
    env_for_drone,
    slice_radius_km,
    slice_sams_at_altitude,
)


def _env(alt=0.0, sam_alt=None):
    sam = {"id": "S1", "range_km": 10.0}
    if sam_alt is not None:
        sam["alt_km"] = sam_alt
    return {"cruise_altitude_km": alt, "sams": [sam]}


def test_drone_altitude_defaults_to_mission_altitude():
    env = _env(alt=4.0)
    assert drone_altitude_km({}, env) == 4.0
    assert drone_altitude_km({"cruise_altitude_km": None}, env) == 4.0
    assert drone_altitude_km({"cruise_altitude_km": ""}, env) == 4.0
    assert drone_altitude_km({"cruise_altitude_km": 7.5}, env) == 7.5
    assert drone_altitude_km({}, _env(alt=0.0)) == 0.0


def test_slice_uses_height_above_sam_site():
    # SAM site 2 km up, drone at 8 km: 6 km above the site.
    assert slice_radius_km(10.0, 2.0, 8.0) == pytest.approx(8.0)
    # A drone below the site is sliced the same way (|h - e|).
    assert slice_radius_km(10.0, 2.0, 0.5) == pytest.approx(math.sqrt(100 - 1.5**2))
    # Altitude 0 means "not set": full range, as before altitude existed.
    assert slice_radius_km(10.0, 2.0, 0.0) == 10.0
    # Out of reach.
    assert slice_radius_km(10.0, 2.0, 12.0) == 0.0


def test_reslicing_starts_from_the_ground_range():
    env = _env(sam_alt=0.0)
    at6 = slice_sams_at_altitude(env, 6.0)
    assert at6["sams"][0]["range_km"] == pytest.approx(8.0)
    assert at6["sams"][0]["ground_range_km"] == 10.0
    # Re-slice the sliced env at another altitude: from 10, not from 8.
    at8 = slice_sams_at_altitude(at6, 8.0)
    assert at8["sams"][0]["range_km"] == pytest.approx(6.0)
    # And back to "not set" restores the full range.
    assert slice_sams_at_altitude(at8, 0.0)["sams"][0]["range_km"] == 10.0
    # The input env is never modified.
    assert env["sams"][0]["range_km"] == 10.0


def test_env_for_drone_uses_the_drones_altitude():
    env = _env(alt=6.0)
    assert env_for_drone(env, {})["sams"][0]["range_km"] == pytest.approx(8.0)
    assert env_for_drone(env, {"cruise_altitude_km": 8.0})["sams"][0][
        "range_km"
    ] == pytest.approx(6.0)


# ---------------------------------------------------------------------------
# Planning: each drone around the SAM slice at its own altitude
# ---------------------------------------------------------------------------

import contextlib  # noqa: E402
import copy  # noqa: E402
import io  # noqa: E402

from server.api.models import SolveWithAllocationRequest  # noqa: E402
from server.api.solve_routes import run_solve_with_allocation  # noqa: E402
from server.solver.optimizers.post_insert import set_optimizer_matrix  # noqa: E402
from server.solver.optimizers.trajectory_swap import (  # noqa: E402
    set_trajectory_optimizer_matrix,
)
from server.solver.route_evaluation import evaluate_route  # noqa: E402
from server.solver.solver_bridge import clear_matrix_cache  # noqa: E402
from server.solver.target_allocator import clear_allocator_matrix  # noqa: E402

# A1 west, A2 east, SAM (10 km) between them; T1 sits on the SAM site.
WALL_ENV = {
    "airports": [
        {"id": "A1", "lat": 35.0, "lng": -106.10},
        {"id": "A2", "lat": 35.0, "lng": -105.70},
    ],
    "targets": [
        {"id": "T1", "lat": 35.0, "lng": -105.90, "priority": 5},
        {"id": "T2", "lat": 35.12, "lng": -105.90, "priority": 5},
    ],
    "sams": [{"id": "S1", "center": {"lat": 35.0, "lng": -105.90}, "range_km": 10}],
}


def _cfg(alt=None, start="A1", end="A2"):
    c = {
        "enabled": True,
        "fuel_budget": 200,
        "start_airport": start,
        "end_airport": end,
    }
    if alt is not None:
        c["cruise_altitude_km"] = alt
    return c


@pytest.fixture(autouse=True)
def _clear_solver_caches():
    yield
    clear_matrix_cache()
    clear_allocator_matrix()
    set_optimizer_matrix(None)
    set_trajectory_optimizer_matrix(None)


def _solve(env, cfg, post_optimize=True):
    req = SolveWithAllocationRequest(
        env=copy.deepcopy(env),
        drone_configs=copy.deepcopy(cfg),
        allocation_strategy="geographic",
        post_optimize=post_optimize,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        return run_solve_with_allocation(req).routes


def _plan(routes):
    return {
        d: (rd["route"], round(rd["distance"], 6), rd.get("trajectory"))
        for d, rd in routes.items()
    }


@pytest.mark.parametrize("post_optimize", [False, True])
def test_every_drone_at_the_mission_altitude_plans_exactly_as_before(post_optimize):
    env = {**WALL_ENV, "cruise_altitude_km": 4.0}
    before = _solve(env, {"1": _cfg(), "2": _cfg(start="A2", end="A1")}, post_optimize)
    # Same altitude given per drone: identical plans.
    same = _solve(
        env, {"1": _cfg(4.0), "2": _cfg(4.0, start="A2", end="A1")}, post_optimize
    )
    assert _plan(same) == _plan(before)


def test_a_high_drone_takes_the_target_a_low_drone_cannot_reach():
    # Mission altitude 0: the SAM's full 10 km range. D2 flies 12 km up, above it.
    cfg = {"1": _cfg(), "2": _cfg(12.0)}
    routes = _solve(WALL_ENV, cfg)
    assert "T1" in routes["2"]["route"]
    assert "T1" not in routes["1"]["route"]
    # Its flight path is drawn at its altitude too (straight over the SAM),
    # also after the optimizer re-plans the paths.
    assert len(routes["2"]["trajectory"]) == 3


def test_drones_at_different_altitudes_see_different_sam_slices():
    # Both fly A1 -> A2 straight through the SAM site's longitude.
    env = {**WALL_ENV, "targets": []}
    routes = _solve(env, {"1": _cfg(), "2": _cfg(12.0)}, post_optimize=False)
    straight = routes["2"]["distance"]  # above the SAM: a straight line
    around = routes["1"]["distance"]  # at ground level: around the 10 km circle
    assert around > straight + 1.0
    assert len(routes["2"]["trajectory"]) == 2


def test_route_edit_is_evaluated_at_the_drones_altitude():
    env = {**WALL_ENV, "targets": []}
    low = evaluate_route(["A1", "A2"], env, "1", drone_config=_cfg())
    high = evaluate_route(["A1", "A2"], env, "2", drone_config=_cfg(12.0))
    assert low["success"] and high["success"]
    assert low["distance"] > high["distance"] + 1.0


def test_warns_when_a_drone_cruises_below_the_ground_it_visits():
    from server.solver.altitude import altitude_warnings

    env = {
        "cruise_altitude_km": 3.0,
        "airports": [
            {"id": "A1", "alt_km": 1.6},
            {"id": "TK1", "type": "tanker", "alt_km": 0.5},
        ],
        "targets": [{"id": "T1", "alt_km": 3.4}, {"id": "T2", "alt_km": 2.0}],
    }
    cfg = {"1": {}, "2": {"cruise_altitude_km": 4.0}, "3": {"enabled": False}}
    routes = {
        "1": {"route": ["A1", "T1", "T2", "TK1", "A1"]},
        "2": {"route": ["A1", "T1", "A1"]},
        "3": {"route": ["A1", "T1", "A1"]},
    }
    assert altitude_warnings(env, cfg, routes) == [
        "D1 cruises at 3.00 km, below the ground at T1 (3.40 km)"
    ]
    # No altitude set anywhere: nothing to check.
    assert altitude_warnings({**env, "cruise_altitude_km": 0}, {"1": {}}, routes) == []


def test_explicit_zero_altitude_is_ground_level_not_the_mission_value():
    env = _env(alt=4.0)
    assert drone_altitude_km({"cruise_altitude_km": 0}, env) == 0.0


def test_mission_matrix_is_built_at_the_altitude_most_drones_fly():
    from server.solver.altitude import primary_altitude_km, with_primary_altitude

    env = {"cruise_altitude_km": 0.0, "sams": []}
    cfg = {
        "1": {"cruise_altitude_km": 4.0},
        "2": {"cruise_altitude_km": 4.0},
        "3": {"cruise_altitude_km": 9.0},
        "4": {"cruise_altitude_km": 1.0, "enabled": False},
    }
    assert primary_altitude_km(cfg, env) == 4.0
    new_env, new_cfg = with_primary_altitude(env, cfg)
    assert new_env["cruise_altitude_km"] == 4.0
    assert env["cruise_altitude_km"] == 0.0  # input untouched
    # A drone without its own value keeps the OLD mission altitude.
    legacy = {
        "1": {"cruise_altitude_km": 4.0},
        "2": {"cruise_altitude_km": 4.0},
        "3": {},
    }
    env5 = {"cruise_altitude_km": 5.0}
    e, c = with_primary_altitude(env5, legacy)
    assert e["cruise_altitude_km"] == 4.0
    assert drone_altitude_km(c["3"], e) == 5.0
    # Nothing to change: same objects back.
    same_cfg = {"1": {"cruise_altitude_km": 5.0}}
    assert with_primary_altitude(env5, same_cfg) == (env5, same_cfg)


def test_one_shared_altitude_builds_one_matrix():
    # Every drone at 12 km, no mission altitude: no per-altitude extras.
    env = {**WALL_ENV, "targets": []}
    routes = _solve(env, {"1": _cfg(12.0), "2": _cfg(12.0)}, post_optimize=False)
    assert routes["1"]["distance"] == pytest.approx(routes["2"]["distance"])
    assert len(routes["1"]["trajectory"]) == 2  # straight over the SAM
