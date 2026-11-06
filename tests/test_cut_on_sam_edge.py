"""A cut that lands on (or a hair inside) a SAM edge must still be replannable.

Drones skim the altitude-reduced SAM polygon; a cut on that path picks up
lat/lng <-> km rounding and used to be rejected as "start inside polygon", so
the replan found no path at all (2026-09-23, cut C2-2 on the dashed line).
"""

import contextlib
import io
import math

import pytest

from path_planning_core.boundary_navigation import plan_path
from server.api.models import SolveWithAllocationRequest
from server.api.solve_routes import run_solve_with_allocation

SAM = {"x": 0.0, "y": 0.0, "range": 4.33}


def _point_on_hugging_edge():
    path, _, _ = plan_path((-8.0, 0.5), (8.0, 0.5), [SAM])
    hug = [
        (path[i], path[i + 1])
        for i in range(len(path) - 1)
        if abs(math.hypot(*path[i]) - 4.33) < 0.05
        and abs(math.hypot(*path[i + 1]) - 4.33) < 0.05
    ]
    p1, p2 = hug[len(hug) // 2]
    return ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)


@pytest.mark.parametrize("inward_km", [0.0, 1e-7, 1e-5, 1e-3, 0.04])
def test_start_on_or_a_hair_inside_the_edge_is_planned(inward_km):
    mid = _point_on_hugging_edge()
    k = 1 - inward_km / math.hypot(*mid)
    start = (mid[0] * k, mid[1] * k)
    path, dist, method = plan_path(start, (0.5, 9.0), [SAM])
    assert path and dist < float("inf"), method
    assert path[0] == start  # the path starts at the real cut point


def test_start_genuinely_inside_the_sam_is_still_rejected():
    _, dist, method = plan_path((0.0, 3.0), (0.5, 9.0), [SAM])
    assert dist == float("inf") and "inside" in method


def test_replan_from_a_cut_on_the_altitude_reduced_edge():
    # SAM ground range 5 km at 2.5 km cruise altitude -> 4.33 km slice, as in the
    # reported mission. The cut C2-1 sits on that slice's edge.
    lat0, lng0 = 32.20, -90.10
    # 20 m inside the slice's edge: inside the polygon, within the edge tolerance.
    r_km = math.sqrt(5**2 - 2.5**2) - 0.02
    cut = (lat0, lng0 + r_km / (111.0 * math.cos(math.radians(lat0))))
    env = {
        "airports": [{"id": "A1", "lat": 32.20, "lng": -90.25}],
        "targets": [{"id": "T1", "lat": 32.28, "lng": -90.10, "priority": 5}],
        "sams": [{"center": {"lat": lat0, "lng": lng0}, "range_km": 5}],
        "checkpoints": [{"id": "C2-1", "lat": cut[0], "lng": cut[1]}],
        "cruise_altitude_km": 2.5,
    }
    cfg = {
        "1": {
            "enabled": True,
            "fuel_budget": 150,
            "start_airport": "C2-1",
            "end_airport": "A1",
        }
    }
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        resp = run_solve_with_allocation(
            SolveWithAllocationRequest(
                env=env,
                drone_configs=cfg,
                post_optimize=True,
                is_checkpoint_replan=True,
            )
        )
    assert resp.success, resp.errors
    rd = resp.routes["1"]
    assert rd["route"][0] == "C2-1" and rd["route"][-1] == "A1"
    assert "T1" in rd["route"]
    assert 0 < rd["distance"] < 99999


@pytest.fixture(autouse=True)
def _reset_shared_swap_matrix():
    yield
    from server.solver.optimizers import trajectory_swap

    trajectory_swap._trajectory_optimizer.set_distance_matrix(None)
