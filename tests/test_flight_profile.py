"""Altitude model phase C: the altitude of every point along a path.

docs/architecture/ALTITUDE_MODEL.md §3, server/solver/flight_profile.py.
"""

import contextlib
import copy
import io
import math

import pytest

from server.solver.animation_frame import build_animation_frame
from server.solver.flight_profile import (
    MAX_TANKER_ALTITUDE_CHANGE_KM,
    attach_flight_profile,
    without_unreachable_tankers,
)

KM_PER_DEG_LAT = 111.195  # haversine on the frame's sphere


def _frame(route, lat_km):
    """A straight north-going path; lat_km = km from the start per waypoint."""
    traj = [[35.0 + km / KM_PER_DEG_LAT, -106.0] for km in lat_km]
    return build_animation_frame(route, traj, {i: i for i in range(len(route))})


def _alt_at(frame, km):
    s, a = frame["cumulative_distances"], frame["altitudes"]
    i = min(range(len(s)), key=lambda k: abs(s[k] - km))
    return a[i]


ENV = {
    "airports": [
        {"id": "A1", "alt_km": 0.0},
        {"id": "A2", "alt_km": 0.5},
        {"id": "TK1", "type": "tanker", "alt_km": 4.5},
    ],
    "targets": [{"id": "T1"}],
    "sams": [],
}
CFG = {"cruise_altitude_km": 3.0, "climb_angle_deg": 12}
G = math.tan(math.radians(12))


def test_take_off_cruise_and_landing_at_the_climb_angle():
    f = _frame(["A1", "T1", "A2"], [0, 50, 100])
    warnings = attach_flight_profile(f, ["A1", "T1", "A2"], ENV, CFG)
    assert warnings == []
    assert len(f["altitudes"]) == len(f["trajectory"])
    assert f["altitudes"][0] == pytest.approx(0.0)  # on A1's ground
    assert f["altitudes"][-1] == pytest.approx(0.5)  # on A2's ground (0.5 km)
    assert _alt_at(f, 50) == pytest.approx(3.0)  # cruise mid-route
    # Climb reaches cruise at 3 / tan(12 deg) ~ 14.1 km, not before.
    assert _alt_at(f, 7.0) == pytest.approx(7.0 * G, abs=0.05)
    assert _alt_at(f, 16.0) == pytest.approx(3.0)
    # Descent to A2's 0.5 km starts (3 - 0.5) / tan(12 deg) ~ 11.8 km out.
    assert _alt_at(f, 100 - 5) == pytest.approx(0.5 + 5 * G, abs=0.05)


def test_tanker_refuel_changes_to_the_tanker_altitude_and_back():
    route = ["A1", "TK1", "A2"]
    f = _frame(route, [0, 50, 100])
    attach_flight_profile(f, route, ENV, CFG, refuel_indices=[1])
    assert _alt_at(f, 50) == pytest.approx(4.5, abs=0.02)  # at the tanker's altitude
    assert _alt_at(f, 30) == pytest.approx(3.0)  # cruise before...
    assert _alt_at(f, 70) == pytest.approx(3.0)  # ...and after
    assert _alt_at(f, 45) == pytest.approx(4.5 - 5 * G, abs=0.05)  # climbing to it


def test_an_edited_route_treats_a_tanker_between_its_ends_as_a_refuel():
    route = ["A1", "TK1", "A2"]
    f = _frame(route, [0, 50, 100])
    attach_flight_profile(f, route, ENV, CFG)  # no refuel_indices given
    assert _alt_at(f, 50) == pytest.approx(4.5, abs=0.02)


def test_a_leg_too_short_to_reach_cruise_peaks_early_and_warns():
    f = _frame(["A1", "A2"], [0, 10])
    warnings = attach_flight_profile(f, ["A1", "A2"], ENV, CFG, drone_id="1")
    assert max(f["altitudes"]) < 3.0
    assert any("only reaches" in w and "D1" in w for w in warnings)


def test_a_cut_start_is_already_airborne():
    route = ["C1-1", "T1", "A2"]
    f = _frame(route, [0, 50, 100])
    attach_flight_profile(
        f, route, {**ENV, "airports": ENV["airports"] + [{"id": "C1-1"}]}, CFG
    )
    assert f["altitudes"][0] == pytest.approx(3.0)


def test_warns_when_a_climb_passes_through_a_sam_at_the_lower_altitude():
    # SAM 10 km range on the ground, 5 km north of A1. Cruising at 12 km it is
    # gone (planned over it), but the climb-out passes through it.
    env = {
        **ENV,
        "sams": [
            {
                "id": "S1",
                "center": {"lat": 35.0 + 5 / KM_PER_DEG_LAT, "lng": -106.0},
                "range_km": 10,
                "alt_km": 0.0,
            }
        ],
    }
    f = _frame(["A1", "T1", "A2"], [0, 50, 100])
    warnings = attach_flight_profile(
        f, ["A1", "T1", "A2"], env, {**CFG, "cruise_altitude_km": 12}
    )
    assert any("S1" in w and "climbing or descending" in w for w in warnings)


def test_tankers_beyond_reach_of_a_drones_altitude_are_not_offered():
    env = {
        "airports": [
            {"id": "A1"},
            {"id": "TK1", "type": "tanker", "alt_km": 4.5},
            {"id": "TK2", "type": "tanker", "alt_km": 9.0},
        ]
    }
    cfg = {
        "1": {
            "cruise_altitude_km": 3.0,
            "refuel_options": ["A1", "TK1", "TK2"],
            "refuel_airport": "TK2",
        },
        "2": {"cruise_altitude_km": 8.0, "refuel_options": ["TK1", "TK2"]},
    }
    out = without_unreachable_tankers(env, cfg)
    assert out["1"]["refuel_options"] == ["A1", "TK1"]  # TK2 is 6 km away
    assert out["1"]["refuel_airport"] == ""
    assert out["2"]["refuel_options"] == ["TK2"]  # TK1 is 3.5 km away
    assert MAX_TANKER_ALTITUDE_CHANGE_KM == 2.0
    assert cfg["1"]["refuel_options"] == ["A1", "TK1", "TK2"]  # input untouched


def test_solve_response_carries_an_altitude_for_every_point():
    from server.api.models import SolveWithAllocationRequest
    from server.api.solve_routes import run_solve_with_allocation

    env = {
        "airports": [
            {"id": "A1", "lat": 35.0, "lng": -106.1, "alt_km": 1.6},
            {"id": "A2", "lat": 35.0, "lng": -105.7, "alt_km": 1.7},
        ],
        "targets": [{"id": "T1", "lat": 35.05, "lng": -105.9, "priority": 5}],
        "sams": [],
    }
    cfg = {
        "1": {
            "enabled": True,
            "fuel_budget": 200,
            "start_airport": "A1",
            "end_airport": "A2",
            "cruise_altitude_km": 4.0,
        }
    }
    with contextlib.redirect_stdout(io.StringIO()):
        routes = run_solve_with_allocation(
            SolveWithAllocationRequest(env=copy.deepcopy(env), drone_configs=cfg)
        ).routes
    af = routes["1"]["anim_frame"]
    assert len(af["altitudes"]) == len(af["trajectory"])
    assert af["altitudes"][0] == pytest.approx(1.6)
    assert af["altitudes"][-1] == pytest.approx(1.7)
    assert max(af["altitudes"]) == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# One frame: flying distance + loiter holds; climbs on ground distance
# ---------------------------------------------------------------------------

from server.solver.animation_frame import (  # noqa: E402
    loiter_holds,
    travel_leg_distances,
)


def test_loiter_is_a_hold_the_drone_stays_put_and_distance_advances():
    route = ["A1", "T1", "A2"]
    traj = [[35.0 + km / KM_PER_DEG_LAT, -106.0] for km in (0, 50, 100)]
    f = build_animation_frame(route, traj, {0: 0, 1: 1, 2: 2}, hold_distances={1: 3.0})
    t, s, wpi = f["trajectory"], f["cumulative_distances"], f["waypoint_indices"]
    k = wpi[1]
    assert t[k + 1] == t[k]  # same place...
    assert s[k + 1] - s[k] == pytest.approx(3.0)  # ...3 km of loiter later
    assert f["total_distance"] == pytest.approx(100.0 + 3.0, abs=0.1)
    assert s[wpi[2]] == pytest.approx(103.0, abs=0.1)  # later points shifted


def test_solver_fuel_legs_lose_their_loiter_so_it_counts_once():
    route = ["A1", "T1", "TK1", "T2", "A2"]
    holds = {1: 2.0, 3: 2.0}
    # per span: loiter sits on the hop INTO the target
    assert travel_leg_distances([12, 10, 12, 10], route, None, holds) == [
        10,
        10,
        10,
        10,
    ]
    # per leg (refuel at index 2)
    assert travel_leg_distances([22, 22], route, [2], holds) == [20, 20]
    assert travel_leg_distances([22, 22], route, [2], {}) == [22, 22]


def test_loiter_holds_follow_the_drones_loiter_times():
    env = {"targets": [{"id": "T1", "type": "a"}, {"id": "T2", "type": "b"}]}
    cfg = {"loiter_enabled": True, "loiter_times": {"a": 2, "b": 0}, "speed": 1}
    assert loiter_holds(["A1", "T1", "T2", "A1"], env, cfg) == {1: 2.0}


def test_a_hold_does_not_change_the_climb():
    # Same path with and without a long loiter hold before the tanker: the
    # climb into the tanker is measured on the ground, so it is identical.
    route = ["A1", "T1", "TK1", "A2"]
    traj = [[35.0 + km / KM_PER_DEG_LAT, -106.0] for km in (0, 40, 50, 100)]
    wpi = {0: 0, 1: 1, 2: 2, 3: 3}
    plain = build_animation_frame(route, traj, wpi)
    held = build_animation_frame(route, traj, wpi, hold_distances={1: 20.0})
    for f in (plain, held):
        attach_flight_profile(f, route, ENV, CFG, refuel_indices=[2])
    tk_plain = plain["altitudes"][plain["waypoint_indices"][2]]
    tk_held = held["altitudes"][held["waypoint_indices"][2]]
    assert tk_plain == pytest.approx(4.5) and tk_held == pytest.approx(4.5)
    # altitude at the loiter target itself: still cruise in both
    assert held["altitudes"][held["waypoint_indices"][1]] == pytest.approx(
        plain["altitudes"][plain["waypoint_indices"][1]]
    )


def test_climb_and_descent_use_their_own_angles():
    # Climb steep (30 deg), descend shallow (6 deg): the climb-out reaches
    # cruise within 3/tan30 = 5.2 km; the descent to land starts 3/tan6 = 28.5 km out.
    route = ["A1", "T1", "A1"]
    f = _frame(route, [0, 50, 100])
    env = {**ENV, "airports": [{"id": "A1", "alt_km": 0.0}]}
    cfg = {"cruise_altitude_km": 3.0, "climb_angle_deg": 30, "descent_angle_deg": 6}
    attach_flight_profile(f, route, env, cfg)
    up, down = math.tan(math.radians(30)), math.tan(math.radians(6))
    assert _alt_at(f, 2.0) == pytest.approx(2.0 * up, abs=0.06)  # steep climb
    assert _alt_at(f, 6.0) == pytest.approx(3.0)  # at cruise already
    assert _alt_at(f, 100 - 20) == pytest.approx(20 * down, abs=0.05)  # shallow descent
    assert _alt_at(f, 100 - 30) == pytest.approx(3.0)


def test_meeting_a_tanker_above_climbs_and_below_descends():
    route = ["A1", "TK1", "A2"]
    cfg = {"cruise_altitude_km": 3.0, "climb_angle_deg": 30, "descent_angle_deg": 6}
    up, down = math.tan(math.radians(30)), math.tan(math.radians(6))
    # Tanker 1 km ABOVE cruise: climb to it (steep), then descend back (shallow).
    high = {
        **ENV,
        "airports": [
            {"id": "A1", "alt_km": 0.0},
            {"id": "A2", "alt_km": 0.0},
            {"id": "TK1", "type": "tanker", "alt_km": 4.0},
        ],
    }
    f = _frame(route, [0, 50, 100])
    attach_flight_profile(f, route, high, cfg, refuel_indices=[1])
    assert _alt_at(f, 49.0) == pytest.approx(4.0 - 1.0 * up, abs=0.06)
    assert _alt_at(f, 55.0) == pytest.approx(4.0 - 5.0 * down, abs=0.06)
    # Tanker 1 km BELOW cruise: descend to it (shallow), then climb back (steep).
    low = {
        **high,
        "airports": high["airports"][:2]
        + [{"id": "TK1", "type": "tanker", "alt_km": 2.0}],
    }
    g = _frame(route, [0, 50, 100])
    attach_flight_profile(g, route, low, cfg, refuel_indices=[1])
    assert _alt_at(g, 45.0) == pytest.approx(2.0 + 5.0 * down, abs=0.06)
    assert _alt_at(g, 51.0) == pytest.approx(2.0 + 1.0 * up, abs=0.06)


def test_a_tanker_above_the_drones_ceiling_is_out_of_reach():
    from server.solver.flight_profile import tanker_usable

    tanker = {"id": "TK1", "type": "tanker", "alt_km": 8.0}
    assert tanker_usable(tanker, {"cruise_altitude_km": 7.0}, {}) is True  # 1 km up
    assert (
        tanker_usable(tanker, {"cruise_altitude_km": 7.0, "ceiling_km": 7.6}, {})
        is False
    )


def test_warns_when_a_drone_cruises_above_its_ceiling():
    from server.solver.altitude import altitude_warnings

    cfg = {
        "1": {"cruise_altitude_km": 12, "ceiling_km": 7.6, "platform_id": "MQ-1B"},
        "2": {"cruise_altitude_km": 7, "ceiling_km": 7.6},
    }
    routes = {"1": {"route": ["A1", "A1"]}, "2": {"route": ["A1", "A1"]}}
    w = altitude_warnings({"airports": [{"id": "A1", "alt_km": 0}]}, cfg, routes)
    assert w == ["D1 cruises at 12 km, above MQ-1B's 7.6 km ceiling"]
