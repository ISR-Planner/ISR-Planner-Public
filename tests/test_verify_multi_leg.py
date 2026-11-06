"""verify_solution_basic must validate multi-stop (leg_distances[]) routes per-leg."""

from server.api.mission_state import verify_solution_basic


def _env():
    return {
        "airports": [{"id": "A1"}, {"id": "A2"}, {"id": "A3"}, {"id": "A4"}],
        "targets": [{"id": "T1"}, {"id": "T2"}, {"id": "T3"}],
    }


def test_multi_leg_each_leg_within_tank_passes():
    # 3 legs, each ≤ tank (50). Total (120) exceeds one tank but must NOT fail —
    # each leg refuels to a full tank.
    routes = {
        "1": {
            "route": ["A1", "T1", "A2", "T2", "A3", "T3", "A4"],
            "distance": 120.0,
            "fuel_budget": 50.0,
            "leg_distances": [40.0, 45.0, 35.0],
        }
    }
    cfgs = {"1": {"fuel_budget": 50.0}}
    errors = verify_solution_basic(_env(), routes, cfgs)
    assert errors == [], errors


def test_multi_leg_one_leg_over_tank_fails():
    routes = {
        "1": {
            "route": ["A1", "T1", "A2", "A3"],
            "distance": 90.0,
            "fuel_budget": 50.0,
            "leg_distances": [40.0, 55.0],  # leg 2 over the 50 tank
        }
    }
    cfgs = {"1": {"fuel_budget": 50.0}}
    errors = verify_solution_basic(_env(), routes, cfgs)
    assert any("leg 2" in e for e in errors), errors


def test_multi_leg_first_leg_uses_start_fuel():
    # Dynamic replan: leg 1 flies on reduced start_fuel (20), later legs full tank.
    routes = {
        "1": {
            "route": ["A1", "A2", "T1", "A3"],
            "distance": 60.0,
            "fuel_budget": 50.0,
            "leg_distances": [25.0, 35.0],  # leg 1 (25) > start_fuel (20) → fail
        }
    }
    cfgs = {"1": {"fuel_budget": 50.0, "start_fuel": 20.0}}
    errors = verify_solution_basic(_env(), routes, cfgs)
    assert any("leg 1" in e for e in errors), errors


def test_multi_leg_zero_start_fuel_is_not_full_tank():
    # A cut can happen after the current leg is exhausted. start_fuel=0 is a real
    # value and must not be treated like a missing value/full tank.
    routes = {
        "1": {
            "route": ["A1", "T1", "A2"],
            "distance": 5.0,
            "fuel_budget": 50.0,
            "leg_distances": [5.0],
        }
    }
    cfgs = {"1": {"fuel_budget": 50.0, "start_fuel": 0.0}}
    errors = verify_solution_basic(_env(), routes, cfgs)
    assert any("leg 1 exceeds fuel budget: 5.00 > 0.00" in e for e in errors), errors


def test_single_leg_zero_start_fuel_is_not_full_tank():
    routes = {
        "1": {
            "route": ["A1", "T1", "A2"],
            "distance": 5.0,
            "fuel_budget": 50.0,
        }
    }
    cfgs = {"1": {"fuel_budget": 50.0, "start_fuel": 0.0}}
    errors = verify_solution_basic(_env(), routes, cfgs)
    assert any("exceeds fuel budget: 5.00 > 0.00" in e for e in errors), errors
