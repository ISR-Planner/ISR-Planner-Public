"""Refuel where a loop ends, not where it started
(server/solver/refuel_solver.py _reoptimize_leg_pairs): two back-to-back fuel
legs are re-planned together, exactly, over every allowed refuel point."""

import itertools
import math

from server.solver.refuel_solver import _reoptimize_leg_pairs, _route_fuel_legs

# A1 home at the origin; TK1 a tanker out by the far targets.
PTS = {
    "A1": (0, 0),
    "TK1": (60, 5),
    "T1": (30, 20),
    "T2": (50, 20),
    "T3": (62, -10),
    "T4": (80, 0),
}
LABELS = list(PTS)
MATRIX = [[math.dist(PTS[a], PTS[b]) for b in LABELS] for a in LABELS]
REFUEL = {"A1", "TK1"}


def _legs(route, loiter=None):
    return _route_fuel_legs(route, LABELS, MATRIX, REFUEL, loiter or {})


def _brute_force_best(targets, tank, loiter):
    """Shortest A1 -> some targets -> refuel point -> rest -> A1 (or no refuel)."""
    best = None
    for perm in itertools.permutations(targets):
        for cut in range(len(perm) + 1):
            for r in [None, *sorted(REFUEL)]:
                if r is None and cut != len(perm):
                    continue
                route = ["A1", *perm[:cut], *([r] if r else []), *perm[cut:], "A1"]
                if r and (route[1] == r or route[-2] == r):
                    continue
                travel, _, legs = _legs(route, loiter)
                if max(legs) <= tank + 1e-6 and (best is None or travel < best[0]):
                    best = (travel, route)
    return best


def test_a_home_loop_ends_at_the_tanker_instead():
    # Two loops from home: A1-T1-T2-A1, A1-T3-T4-A1.
    route = ["A1", "T1", "T2", "A1", "T3", "T4", "A1"]
    before, _, _ = _legs(route)
    new = _reoptimize_leg_pairs(
        route,
        LABELS,
        MATRIX,
        refuel_nodes=REFUEL,
        start_budget=150,
        full_tank=150,
        loiter_km={},
    )
    after, refuels, legs = _legs(new)
    assert after < before - 1
    assert max(legs) <= 150
    assert sorted(w for w in new if w.startswith("T") and w != "TK1") == [
        "T1",
        "T2",
        "T3",
        "T4",
    ]


def test_matches_brute_force_for_a_pair_of_legs():
    loiter = {"T1": 10, "T2": 10, "T3": 10, "T4": 10}
    for tank in (130, 150, 200):
        route = ["A1", "T1", "T2", "A1", "T3", "T4", "A1"]
        if max(_legs(route, loiter)[2]) > tank:
            continue  # only feasible starting plans are improved
        new = _reoptimize_leg_pairs(
            route,
            LABELS,
            MATRIX,
            refuel_nodes=REFUEL,
            start_budget=tank,
            full_tank=tank,
            loiter_km=loiter,
        )
        best = _brute_force_best(["T1", "T2", "T3", "T4"], tank, loiter)
        assert abs(_legs(new, loiter)[0] - best[0]) < 1e-6, (tank, new, best)


def test_every_leg_still_fits_its_tank_with_loiter():
    loiter = {"T1": 30, "T2": 30, "T3": 30, "T4": 30}
    route = ["A1", "T1", "T2", "A1", "T3", "T4", "A1"]
    tank = max(_legs(route, loiter)[2])
    new = _reoptimize_leg_pairs(
        route,
        LABELS,
        MATRIX,
        refuel_nodes=REFUEL,
        start_budget=tank,
        full_tank=tank,
        loiter_km=loiter,
    )
    assert max(_legs(new, loiter)[2]) <= tank + 1e-6


def test_one_leg_when_both_fit_in_one_tank():
    route = ["A1", "T1", "A1", "T2", "A1"]
    new = _reoptimize_leg_pairs(
        route,
        LABELS,
        MATRIX,
        refuel_nodes=REFUEL,
        start_budget=500,
        full_tank=500,
        loiter_km={},
    )
    assert new in (["A1", "T1", "T2", "A1"], ["A1", "T2", "T1", "A1"])
