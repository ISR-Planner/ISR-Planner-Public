"""A drone that landed (mission over, no refuel allowed there) is sent to the
planner parked: start = end = its airport, start_fuel = 0. The planner must give
it no targets and keep planning the other drones normally."""

import contextlib
import io

from server.api.models import SolveWithAllocationRequest
from server.api.solve_routes import run_solve_with_allocation

ENV = {
    "airports": [
        {"id": "A1", "lat": 32.20, "lng": -90.20},
        {"id": "A2", "lat": 32.20, "lng": -90.00},
    ],
    "targets": [
        {"id": "T1", "lat": 32.24, "lng": -90.16, "priority": 5},
        {"id": "T2", "lat": 32.25, "lng": -90.05, "priority": 5},
    ],
    "sams": [],
}


def test_parked_drone_gets_no_targets():
    configs = {
        "1": {
            "enabled": True,
            "fuel_budget": 150,
            "start_airport": "A1",
            "end_airport": "A1",
            "start_fuel": 0,
        },
        "2": {
            "enabled": True,
            "fuel_budget": 150,
            "start_airport": "A2",
            "end_airport": "A2",
        },
    }
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        resp = run_solve_with_allocation(
            SolveWithAllocationRequest(
                env=ENV, drone_configs=configs, post_optimize=True
            )
        )
    assert resp.success, resp.errors
    d1 = resp.routes["1"]["route"]
    assert all(not str(w).startswith("T") for w in d1), d1
    assert float(resp.routes["1"].get("distance", 0) or 0) == 0
    assert {"T1", "T2"} <= set(resp.routes["2"]["route"])
