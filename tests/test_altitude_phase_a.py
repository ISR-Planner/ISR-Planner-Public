"""Altitude model Phase A (docs/architecture/ALTITUDE_MODEL.md): every item may
carry alt_km, and plans are unchanged by it."""

import contextlib
import copy
import io

import pytest

from server.api.models import SolveWithAllocationRequest
from server.api.solve_routes import run_solve_with_allocation
from server.solver.route_evaluation import evaluate_route

ENV = {
    "airports": [
        {"id": "A1", "lat": 32.20, "lng": -90.20},
        {"id": "A2", "lat": 32.36, "lng": -90.02},
        {"id": "TK1", "lat": 32.30, "lng": -90.10, "type": "tanker"},
    ],
    "targets": [
        {"id": "T1", "lat": 32.26, "lng": -90.14, "priority": 5},
        {"id": "T2", "lat": 32.24, "lng": -90.05, "priority": 8},
        {"id": "T3", "lat": 32.33, "lng": -90.18, "priority": 3},
    ],
    "sams": [{"center": {"lat": 32.28, "lng": -90.12}, "range_km": 3}],
}
CFG = {
    "1": {
        "enabled": True,
        "fuel_budget": 80,
        "start_airport": "A1",
        "end_airport": "A1",
    },
    "2": {
        "enabled": True,
        "fuel_budget": 80,
        "start_airport": "A2",
        "end_airport": "A2",
    },
}


def _with_altitudes(env):
    e = copy.deepcopy(env)
    for a in e["airports"]:
        a["alt_km"] = 2.0 if a.get("type") == "tanker" else 1.6  # Denver-high airports
    for t in e["targets"]:
        t["alt_km"] = 1.7
    for s in e["sams"]:
        s["alt_km"] = 1.65
    return e


@pytest.fixture(autouse=True)
def _reset_shared_swap_matrix():
    yield
    from server.solver.optimizers import trajectory_swap

    trajectory_swap._trajectory_optimizer.set_distance_matrix(None)


def _solve(env):
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        resp = run_solve_with_allocation(
            SolveWithAllocationRequest(
                env=env, drone_configs=copy.deepcopy(CFG), post_optimize=True
            )
        )
    assert resp.success, resp.errors
    return {d: (rd["route"], round(rd["distance"], 6)) for d, rd in resp.routes.items()}


def test_plans_are_identical_with_altitudes_on_every_item():
    assert _solve(_with_altitudes(ENV)) == _solve(ENV)


def test_route_evaluation_ignores_altitude_fields():
    route = ["A1", "T3", "T2", "A1"]  # T1 is inside the SAM
    plain = evaluate_route(route, ENV, "1")
    high = evaluate_route(route, _with_altitudes(ENV), "1")
    assert plain["success"] and high["success"]
    assert high["distance"] == pytest.approx(plain["distance"])
    assert high["route"] == plain["route"]
