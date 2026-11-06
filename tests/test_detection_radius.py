"""Detection radius: fuel from the flown path, and use of the fuel it saves
(server/solver/detection_radius.py, wired into run_solve_with_allocation)."""

import contextlib
import io

import pytest

from server.api.models import SolveWithAllocationRequest
from server.api.solve_routes import run_solve_with_allocation

ENV = {
    "airports": [{"id": "A1", "lat": 32.20, "lng": -90.20}],
    "targets": [
        {"id": "T1", "lat": 32.26, "lng": -90.14, "priority": 5, "type": "a"},
        {"id": "T2", "lat": 32.26, "lng": -90.26, "priority": 5, "type": "a"},
        {"id": "T3", "lat": 32.32, "lng": -90.20, "priority": 5, "type": "a"},
    ],
    "sams": [],
}


@pytest.fixture(autouse=True)
def _reset_shared_swap_matrix():
    yield
    from server.solver.optimizers import trajectory_swap

    trajectory_swap._trajectory_optimizer.set_distance_matrix(None)


def _solve(tank, radius, constraints=None, target_access=None):
    cfg = {
        "1": {
            "enabled": True,
            "fuel_budget": tank,
            "start_airport": "A1",
            "end_airport": "A1",
            "detection_radius": {"a": radius, "b": 0, "c": 0, "d": 0, "e": 0},
        }
    }
    if target_access:
        cfg["1"]["target_access"] = target_access
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        resp = run_solve_with_allocation(
            SolveWithAllocationRequest(
                env=ENV, drone_configs=cfg, post_optimize=True, constraints=constraints
            )
        )
    assert resp.success, resp.errors
    return resp.routes["1"]


def test_fuel_and_animation_follow_the_flown_path():
    centres = _solve(150, 0.0)
    flown = _solve(150, 2.0)
    assert flown["route"] == centres["route"]
    assert flown["distance"] < centres["distance"] - 5.0  # ~35 → ~27 km
    assert flown["anim_frame"]["total_distance"] == pytest.approx(
        flown["distance"], abs=0.05
    )
    assert sum(flown["span_distances"]) == pytest.approx(flown["distance"], abs=1e-6)


def test_no_radius_is_unchanged():
    rd = _solve(150, 0.0)
    assert "span_distances" not in rd
    assert rd["anim_frame"]["total_distance"] == pytest.approx(rd["distance"], abs=0.05)


def test_saved_fuel_adds_a_target_that_centre_distances_could_not_afford():
    without = _solve(30, 0.0)
    with_radius = _solve(30, 2.0)
    assert len([w for w in without["route"] if w.startswith("T")]) == 2
    assert {"T1", "T2", "T3"} <= set(with_radius["route"])
    assert with_radius["distance"] <= 30 + 1e-6


def test_saved_fuel_never_breaks_the_tank():
    rd = _solve(20, 2.0)
    assert rd["distance"] <= 20 + 1e-6


def test_constrained_drone_gets_no_extra_targets():
    # An insert could break the ordering rule, so the radius may only change the
    # distance of a constrained drone's route, never which targets it visits.
    rules = {"1": {"first": "T2"}}
    without = _solve(30, 0.0, constraints=rules)
    with_radius = _solve(30, 2.0, constraints=rules)
    assert with_radius["route"] == without["route"]
    assert with_radius["route"][1] == "T2"
    assert with_radius["distance"] < without["distance"]


def test_target_type_the_drone_cannot_visit_is_not_added():
    rd = _solve(30, 2.0, target_access={"a": True, "b": True})
    # all targets are type "a" and allowed; now forbid "a" for inserts via a type-b-only drone
    rd_b = _solve(30, 2.0, target_access={"a": False, "b": True})
    assert not any(w.startswith("T") for w in rd_b["route"])
    assert any(w.startswith("T") for w in rd["route"])


def test_route_edit_endpoint_returns_matching_animation_frame():
    import fastapi
    from fastapi import testclient

    from server.api.matrix_routes import router

    app = fastapi.FastAPI()
    app.include_router(router)
    body = (
        testclient.TestClient(app)
        .post(
            "/api/apply_sequence",
            json={
                "drone_id": "1",
                "sequence": "A1,T2,T3,T1,A1",
                "env": ENV,
                "fuel_budget": 150,
                "drone_config": {"detection_radius": {"a": 2.0}},
            },
        )
        .json()
    )
    assert body["success"], body
    assert body["distance"] < 30
    assert body["anim_frame"]["total_distance"] == pytest.approx(
        body["distance"], abs=0.05
    )


def test_agent_ordered_plan_reports_flown_fuel_too():
    from server.agents import reasoning_agent

    def commit(radius):
        cfg = {
            "1": {
                "enabled": True,
                "fuel_budget": 150,
                "start_airport": "A1",
                "end_airport": "A1",
                "detection_radius": {"a": radius, "b": 0, "c": 0, "d": 0, "e": 0},
            }
        }
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            reasoning_agent.set_mission_context(ENV, cfg)
            reasoning_agent.commit_ordered_plan.invoke(
                {"drone_id": "1", "constraints": {"first": "T2"}}
            )
        return reasoning_agent.get_solved_routes()["1"]

    centres = commit(0.0)
    flown = commit(2.0)
    assert flown["route"][1] == "T2"  # ordering rule kept
    assert flown["distance"] < centres["distance"] - 5.0
    assert sum(flown["span_distances"]) == pytest.approx(flown["distance"], abs=1e-6)
