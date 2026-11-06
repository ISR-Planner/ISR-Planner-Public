"""Or-opt: relocate contiguous target runs (len 1..3, fwd/reversed, cross-leg)."""

import math

from server.solver.post_optimizer import or_opt_optimize


def _dm(wp):
    labels = list(wp)
    mat = [
        [math.hypot(wp[a][0] - wp[b][0], wp[a][1] - wp[b][1]) for b in labels]
        for a in labels
    ]
    return {"labels": labels, "matrix": mat}


def _route_len(route, wp):
    return sum(
        math.hypot(
            wp[route[i]][0] - wp[route[i + 1]][0], wp[route[i]][1] - wp[route[i + 1]][1]
        )
        for i in range(len(route) - 1)
    )


def test_or_opt_moves_run_to_cheaper_drone():
    # D1 has a run T2,T3 that sits far from its A1-A1 loop but right next to D2.
    wp = {
        "A1": (0, 0),
        "A2": (10, 0),
        "T1": (0, 1),
        "T2": (10, 1),
        "T3": (10, 2),
        "T4": (10, 3),
    }
    cfg = {
        "1": {
            "enabled": True,
            "start_airport": "A1",
            "end_airport": "A1",
            "fuel_budget": 100,
            "target_access": {"a": True},
        },
        "2": {
            "enabled": True,
            "start_airport": "A2",
            "end_airport": "A2",
            "fuel_budget": 100,
            "target_access": {"a": True},
        },
    }
    sol = {
        "routes": {
            "1": {"route": ["A1", "T1", "T2", "T3", "A1"], "fuel_budget": 100},
            "2": {"route": ["A2", "T4", "A2"], "fuel_budget": 100},
        }
    }
    before = _route_len(sol["routes"]["1"]["route"], wp) + _route_len(
        sol["routes"]["2"]["route"], wp
    )
    res = or_opt_optimize(
        sol,
        {"targets": [{"id": t, "type": "a"} for t in ("T1", "T2", "T3", "T4")]},
        cfg,
        _dm(wp),
    )
    after = _route_len(res["routes"]["1"]["route"], wp) + _route_len(
        res["routes"]["2"]["route"], wp
    )
    assert res["or_opt_moves"], "expected at least one or-opt move"
    assert after < before - 1e-6, f"no improvement: {before} -> {after}"
    # T2,T3 should have left D1 for D2 (they're at x=10 near A2).
    assert (
        "T2" not in res["routes"]["1"]["route"] or "T2" in res["routes"]["2"]["route"]
    )


def test_or_opt_respects_target_access():
    # The run is type 'b' but the cheaper drone D2 only accepts 'a' → no move.
    wp = {"A1": (0, 0), "A2": (10, 0), "T1": (0, 1), "T2": (10, 1)}
    cfg = {
        "1": {
            "enabled": True,
            "start_airport": "A1",
            "end_airport": "A1",
            "fuel_budget": 100,
            "target_access": {"a": True, "b": True},
        },
        "2": {
            "enabled": True,
            "start_airport": "A2",
            "end_airport": "A2",
            "fuel_budget": 100,
            "target_access": {"a": True},
        },
    }  # no 'b'
    sol = {
        "routes": {
            "1": {"route": ["A1", "T1", "T2", "A1"], "fuel_budget": 100},
            "2": {"route": ["A2", "A2"], "fuel_budget": 100},
        }
    }
    res = or_opt_optimize(
        sol,
        {"targets": [{"id": "T1", "type": "a"}, {"id": "T2", "type": "b"}]},
        cfg,
        _dm(wp),
    )
    # T2 (type b) may not move to D2 (a-only). It can still relocate WITHIN D1, but
    # must never appear in D2.
    assert "T2" not in res["routes"]["2"]["route"]


def test_or_opt_no_change_when_optimal():
    wp = {"A1": (0, 0), "T1": (1, 0), "T2": (2, 0)}
    cfg = {
        "1": {
            "enabled": True,
            "start_airport": "A1",
            "end_airport": "A1",
            "fuel_budget": 100,
            "target_access": {"a": True},
        }
    }
    sol = {"routes": {"1": {"route": ["A1", "T1", "T2", "A1"], "fuel_budget": 100}}}
    res = or_opt_optimize(
        sol,
        {"targets": [{"id": "T1", "type": "a"}, {"id": "T2", "type": "a"}]},
        cfg,
        _dm(wp),
    )
    assert res["routes"]["1"]["route"] == ["A1", "T1", "T2", "A1"]
    assert res["or_opt_moves"] == []
