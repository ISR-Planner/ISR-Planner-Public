"""Integration: multi-stop refuel via virtual drones through the real solver.

Exercises solve_mission_with_allocation end-to-end with a drone that declares
refuel_stops, verifying the expand -> allocate -> Held-Karp -> merge path produces
ONE physical-drone route that passes through the stops, with leg_distances[] and
refuel_indices[].
"""

import pytest

from server.solver.solver_bridge import solve_mission_with_allocation


@pytest.fixture(autouse=True)
def disable_supabase_for_solver_integration_tests(monkeypatch):
    """Keep solver integration tests independent of developer Supabase env vars."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)

    from server.database import supabase_client

    monkeypatch.setattr(supabase_client, "_supabase_client", None)


def _env():
    # A small lat/lng env: 4 airports roughly in a line, targets scattered between.
    # The solver projects to local km and builds the SAM-aware matrix itself.
    return {
        "airports": [
            {"id": "A1", "lat": 32.00, "lng": -90.20, "runway": "L"},
            {"id": "A2", "lat": 32.00, "lng": -90.10, "runway": "L"},
            {"id": "A3", "lat": 32.00, "lng": -90.00, "runway": "L"},
            {"id": "A4", "lat": 32.00, "lng": -89.90, "runway": "L"},
        ],
        "targets": [
            {"id": "T1", "lat": 32.02, "lng": -90.15, "priority": 5, "type": "a"},
            {"id": "T2", "lat": 32.02, "lng": -90.05, "priority": 5, "type": "a"},
            {"id": "T3", "lat": 32.02, "lng": -89.95, "priority": 5, "type": "a"},
        ],
        "sams": [],
    }


def test_two_stop_refuel_produces_one_merged_physical_route():
    env = _env()
    drone_configs = {
        "1": {
            "enabled": True,
            "start_airport": "A1",
            "end_airport": "A4",
            "refuel_stops": ["A2", "A3"],  # two stops -> three legs
            "fuel_budget": 50,
            "runway": "L",
            "target_access": {"a": True, "b": True, "c": True, "d": True, "e": True},
        }
    }

    sol = solve_mission_with_allocation(
        env=env,
        drone_configs=drone_configs,
        allocation_strategy="geographic",
        post_optimize=False,  # isolate the virtual-drone path
    )

    routes = sol.get("routes", {})
    # Result must be keyed by the PHYSICAL drone id, not virtual ids.
    assert "1" in routes
    assert not any("_L" in k for k in routes), f"virtual ids leaked: {list(routes)}"

    r1 = routes["1"]
    route = r1.get("route", [])
    assert route, "no route produced"
    # The merged route must pass through both refuel stops, in order, between
    # the start and end airports.
    assert route[0] == "A1"
    assert route[-1] == "A4"
    assert "A2" in route and "A3" in route
    assert route.index("A2") < route.index("A3")

    # Multi-leg metadata emitted.
    assert r1.get("leg_distances") and len(r1["leg_distances"]) == 3
    assert r1.get("refuel_indices") and len(r1["refuel_indices"]) == 2
    assert r1.get("refuel_stops") == ["A2", "A3"]


def test_no_refuel_stops_unaffected():
    """A drone with no refuel config solves exactly as before (gate off)."""
    env = _env()
    drone_configs = {
        "1": {
            "enabled": True,
            "start_airport": "A1",
            "end_airport": "A1",
            "refuel_airport": "-",
            "refuel_options": [],
            "fuel_budget": 200,
            "runway": "L",
            "target_access": {"a": True, "b": True, "c": True, "d": True, "e": True},
        }
    }
    sol = solve_mission_with_allocation(
        env=env,
        drone_configs=drone_configs,
        allocation_strategy="geographic",
        post_optimize=False,
    )
    routes = sol.get("routes", {})
    assert "1" in routes
    # No virtual-leg metadata for a plain drone.
    assert not routes["1"].get("refuel_indices")
    assert not routes["1"].get("refuel_stops")
    assert not routes["1"].get("refuel_airport")
