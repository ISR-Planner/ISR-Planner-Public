"""refuel_stops must list the stops the route ACTUALLY refuels at.

Opportunistic refuel (refuel_options) lets the planner choose where to refuel;
the merged route used to copy the drone's configured refuel_stops list instead
(e.g. ['TK1','TK1','TK1','A1'] for a route with one TK1 stop).
"""

import contextlib
import io

import pytest

from server.api.models import SolveWithAllocationRequest
from server.api.solve_routes import run_solve_with_allocation
from tests.test_t5_start_state_regression import _load_fixture


@pytest.fixture(autouse=True)
def _reset_shared_swap_matrix():
    # The Swap optimizer is a module-level singleton that keeps the last distance
    # matrix it was given; a later test that calls it without one would otherwise
    # inherit this mission's matrix.
    yield
    from server.solver.optimizers import trajectory_swap

    trajectory_swap._trajectory_optimizer.set_distance_matrix(None)


def test_refuel_stops_match_refuel_indices():
    env, cfgs = _load_fixture()
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        resp = run_solve_with_allocation(
            SolveWithAllocationRequest(env=env, drone_configs=cfgs, post_optimize=True)
        )
    assert resp.success, resp.errors
    rd = resp.routes["1"]
    route = rd["route"]
    assert rd["refuel_indices"], "this mission needs at least one refuel"
    assert rd["refuel_stops"] == [route[i] for i in rd["refuel_indices"]]
    assert all(leg <= rd["fuel_budget"] + 1e-6 for leg in rd["leg_distances"])
