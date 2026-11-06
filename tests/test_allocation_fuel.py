"""Every allocation strategy gives a target to its best drone WITH FUEL LEFT for
it (server/solver/target_allocator.py _FuelEstimate), and only when no drone
has room falls back to its best drone regardless."""

import pytest

from server.solver.target_allocator import (
    REFUEL_TANK_SHARE,
    AllocationStrategy,
    TargetAllocator,
    allocation_fuel,
)

ALL = [s for s in AllocationStrategy]


def _line_matrix(xs):
    """Points on a line: distance = |x_a - x_b|."""
    labels = list(xs)
    return {
        "labels": labels,
        "matrix": [[abs(xs[a] - xs[b]) for b in labels] for a in labels],
    }


# A1 at 0, A2 at 100; four targets near A1. D1 flies from A1 with range 45
# (room for T1+T2 only: out-and-back to 20 = 40 km); D2 from A2 with lots of fuel.
XS = {"A1": 0, "A2": 100, "T1": 10, "T2": 20, "T3": 30, "T4": 35}
AIRPORTS = [{"id": "A1"}, {"id": "A2"}]
TARGETS = [{"id": t, "type": "a", "priority": 5} for t in ("T1", "T2", "T3", "T4")]


def _allocate(strategy, drones, xs=XS, targets=TARGETS):
    allocator = TargetAllocator()
    allocator.set_distance_matrix(_line_matrix(xs))
    return allocator.allocate(targets, drones, AIRPORTS, strategy)


@pytest.mark.parametrize("strategy", ALL)
def test_no_drone_is_given_more_than_its_fuel(strategy):
    drones = {
        "1": {"start_airport": "A1", "fuel_budget": 45},
        "2": {"start_airport": "A2", "fuel_budget": 1000},
    }
    result = _allocate(strategy, drones)
    assert sorted(result["1"] + result["2"]) == ["T1", "T2", "T3", "T4"]  # all placed
    far = max((XS[t] for t in result["1"]), default=0)
    assert 2 * far <= 45, f"D1 got {result['1']}: out-and-back {2 * far} km > 45"
    assert result["2"], "the drone with fuel took the overflow"


@pytest.mark.parametrize("strategy", ALL)
def test_without_fuel_budgets_nothing_changes(strategy):
    # No fuel_budget = no limit: same as before the fuel check.
    drones = {"1": {"start_airport": "A1"}, "2": {"start_airport": "A2"}}
    result = _allocate(strategy, drones)
    if strategy != AllocationStrategy.BALANCED:  # balanced splits by count
        assert result["2"] == []


@pytest.mark.parametrize("strategy", ALL)
def test_a_target_no_drone_has_room_for_is_still_placed(strategy):
    # T9 at 500: out of both drones' reach. It still goes somewhere; the solver
    # drops it if it really doesn't fit.
    xs = dict(XS, T9=500)
    targets = TARGETS + [{"id": "T9", "type": "a", "priority": 5}]
    drones = {
        "1": {"start_airport": "A1", "fuel_budget": 45},
        "2": {"start_airport": "A2", "fuel_budget": 300},
    }
    result = _allocate(strategy, drones, xs, targets)
    assert "T9" in result["1"] + result["2"]


def test_loiter_counts_against_fuel():
    # Room for T1 alone by distance (20 km), but 10 min x 1 km/min loiter = 10 km
    # each: T1+T2 would be 40 + 20 = 60 > 45.
    drones = {
        "1": {
            "start_airport": "A1",
            "fuel_budget": 45,
            "speed": 1,
            "loiter_enabled": True,
            "loiter_times": {"a": 10},
        },
        "2": {"start_airport": "A2", "fuel_budget": 1000},
    }
    result = _allocate(AllocationStrategy.GEOGRAPHIC, drones)
    assert result["1"] == ["T1"]


def test_allocation_fuel_rules():
    assert allocation_fuel({}) is None
    assert allocation_fuel({"fuel_budget": 100}) == 100
    assert allocation_fuel({"fuel_budget": 100, "start_fuel": 40}) == 40  # replan
    one = {"fuel_budget": 100, "refuel_options": ["TK1"], "max_refuels": 1}
    assert allocation_fuel(one) == pytest.approx(100 + REFUEL_TANK_SHARE * 100)
    assert allocation_fuel(dict(one, refuels_used=1)) == 100  # none left
    assert allocation_fuel(dict(one, opportunistic_refuel=False)) == 100


def test_open_landing_airport_is_not_charged_a_return():
    # end "-": the solver picks where to land, so no trip home is counted:
    # T4 at 35 fits a range of 40 one-way (out-and-back would be 70).
    drones = {
        "1": {"start_airport": "A1", "end_airport": "-", "fuel_budget": 40},
        "2": {"start_airport": "A2", "fuel_budget": 1000},
    }
    result = _allocate(AllocationStrategy.GEOGRAPHIC, drones)
    assert set(result["1"]) == {"T1", "T2", "T3", "T4"}
