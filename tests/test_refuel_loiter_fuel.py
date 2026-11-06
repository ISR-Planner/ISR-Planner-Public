"""The exact refuel planner counts loiter as FUEL: minutes x speed (km/min),
the same as every other solver path (optimizers/common.py
compute_route_loiter_fuel). It added raw minutes, so with a real speed it
under-counted loiter and planned legs over the tank (Env tab: a red leg)."""

from server.solver.refuel_solver import _solve_exact_refuel_small

# A line: A1 at 0, T1 at 30, T2 at 35 (km).
XS = {"A1": 0.0, "T1": 30.0, "T2": 35.0}
LABELS = list(XS)
ENV = {
    "matrix_labels": LABELS,
    "distance_matrix": [[abs(XS[a] - XS[b]) for b in LABELS] for a in LABELS],
    "targets": [{"id": "T1", "type": "a"}, {"id": "T2", "type": "a"}],
}


def _solve(speed):
    cfg = {"speed": speed, "loiter_enabled": True, "loiter_times": {"a": 10}}
    return _solve_exact_refuel_small(
        ENV,
        start_id="A1",
        target_ids=["T1", "T2"],
        target_priority={"T1": 5, "T2": 5},
        cfg=cfg,
        refuel_options=["A1"],
        tanker_ids=set(),
        valid_end_airports=["A1"],
        start_budget=100.0,
        full_tank=100.0,
        max_refuels=2,
    )


def _leg_fuel(route, refuels, speed):
    """Each leg's true fuel: travel + 10 min x speed per target."""
    cuts = [0] + list(refuels) + [len(route) - 1]
    legs = []
    for a, b in zip(cuts, cuts[1:]):
        seg = route[a : b + 1]
        travel = sum(abs(XS[p] - XS[q]) for p, q in zip(seg, seg[1:]))
        legs.append(travel + 10 * speed * sum(1 for w in seg[1:] if w.startswith("T")))
    return legs


def test_no_leg_is_over_the_tank_at_a_real_speed():
    # 3 km/min (180 km/h): each target's 10 min orbit burns 30 km.
    # A1-T1-T2-A1 = 70 km travel + 60 km loiter = 130 > 100: it must refuel
    # at A1 between them (60+30 = 90, then 70+30 = 100).
    res = _solve(3.0)
    assert set(res["visited_targets"]) == {"T1", "T2"}
    legs = _leg_fuel(res["route"], res["refuel_indices"], 3.0)
    assert all(leg <= 100 + 1e-6 for leg in legs), (res["route"], legs)
    assert res["leg_distances"] == legs  # reported legs = true fuel


def test_speed_one_is_unchanged():
    # 1 km/min: minutes and km coincide (older files): one 90 km leg fits.
    res = _solve(1.0)
    assert res["refuel_indices"] == [] and res["leg_distances"] == [90.0]
