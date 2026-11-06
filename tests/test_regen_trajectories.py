"""regenerate_solution_trajectories: server-side SAM-aware trajectory rebuild,
self-resolving a post-cut checkpoint start from the route's old trajectory."""

import math

from server.solver.post_optimizer import regenerate_solution_trajectories

_WP = {
    "A2": (-2.51, -1.33),
    "A4": (4.34, -1.04),
    "A3": (0.71, 3.83),
    "C1-1": (1.26, -3.65),
    "T1": (-0.32, -3.50),
    "T13": (-2.75, 0.17),
}


def _dm():
    labels = list(_WP)
    mat = [
        [math.hypot(_WP[a][0] - _WP[b][0], _WP[a][1] - _WP[b][1]) for b in labels]
        for a in labels
    ]
    return {"labels": labels, "matrix": mat}


def _env():
    return {
        "airports": [
            {"id": a, "x": _WP[a][0], "y": _WP[a][1]} for a in ("A2", "A4", "A3")
        ],
        "targets": [{"id": t, "x": _WP[t][0], "y": _WP[t][1]} for t in ("T1", "T13")],
        "sams": [],
    }


def test_regen_resolves_checkpoint_start_from_old_trajectory():
    route = ["C1-1", "T1", "T13", "A2", "A4", "A3"]
    # C1-1 NOT in env; its position is the old trajectory's first point.
    sol = {
        "routes": {
            "1": {"route": route, "trajectory": [list(_WP["C1-1"])], "distance": 0}
        }
    }
    regenerate_solution_trajectories(sol, _env(), {"1": {}}, _dm())
    r = sol["routes"]["1"]
    assert len(r["trajectory"]) >= 2
    # First trajectory point is the checkpoint position (not [0,0] fallback).
    assert abs(r["trajectory"][0][0] - _WP["C1-1"][0]) < 1e-6
    assert abs(r["trajectory"][0][1] - _WP["C1-1"][1]) < 1e-6
    assert r["distance"] > 0


def test_regen_plain_route_no_checkpoint():
    route = ["A2", "T1", "A3"]
    sol = {"routes": {"1": {"route": route, "trajectory": [], "distance": 0}}}
    regenerate_solution_trajectories(sol, _env(), {"1": {}}, _dm())
    assert len(sol["routes"]["1"]["trajectory"]) >= 2
    assert sol["routes"]["1"]["distance"] > 0
