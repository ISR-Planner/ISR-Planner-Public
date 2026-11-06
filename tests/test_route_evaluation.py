"""server/solver/route_evaluation.py — the one route evaluator behind the sequence
bar and the agent's route-edit tools."""

import math

import pytest

from server.solver.route_evaluation import evaluate_route

# Two airports on either side of a SAM; a cut diamond north-west of it.
ENV = {
    "airports": [
        {"id": "A1", "lat": 32.20, "lng": -90.20},
        {"id": "A2", "lat": 32.20, "lng": -90.00},
    ],
    "targets": [{"id": "T1", "lat": 32.30, "lng": -90.10, "priority": 7}],
    "sams": [{"center": {"lat": 32.20, "lng": -90.10}, "range_km": 5}],
    "checkpoints": [{"id": "C2-1", "lat": 32.28, "lng": -90.18}],
    "synthetic_starts": {"D1_START": {"id": "D1_START", "lat": 32.28, "lng": -90.18}},
}


def _km(a, b):
    # small-area approximation, fine for these tests
    dlat = (a[0] - b[0]) * 111.0
    dlng = (a[1] - b[1]) * 111.0 * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlng)


def test_abort_route_from_diamond_with_no_targets():
    r = evaluate_route(["C2-1", "A1"], ENV, "1")
    assert r["success"], r["error"]
    assert r["route"] == ["C2-1", "A1"]
    assert r["points"] == 0
    assert _km(r["trajectory"][0], (32.28, -90.18)) < 0.05  # starts at the diamond
    assert _km(r["trajectory"][-1], (32.20, -90.20)) < 0.05  # ends at A1


def test_synthetic_start_node_is_known():
    r = evaluate_route(["D1_START", "T1", "A2"], ENV, "1")
    assert r["success"], r["error"]
    assert r["points"] == 7


def test_leg_through_sam_is_not_a_straight_line():
    r = evaluate_route(["A1", "A2"], ENV, "1")
    assert r["success"], r["error"]
    straight = _km((32.20, -90.20), (32.20, -90.00))
    assert r["distance"] > straight + 0.5  # went around the SAM


def test_cruise_altitude_shrinks_the_detour():
    ground = evaluate_route(["A1", "A2"], ENV, "1")
    high = evaluate_route(["A1", "A2"], {**ENV, "cruise_altitude_km": 4.0}, "1")
    assert high["success"] and ground["success"]
    assert high["distance"] < ground["distance"]


def test_matrix_value_used_when_both_ends_known():
    matrix = {"labels": ["A1", "T1"], "matrix": [[0, 42.0], [42.0, 0]]}
    r = evaluate_route(["A1", "T1", "A2"], ENV, "1", distance_matrix=matrix)
    assert r["success"], r["error"]
    only_t1_a2 = evaluate_route(["T1", "A2"], ENV, "1")["distance"]
    assert abs(r["distance"] - (42.0 + only_t1_a2)) < 1e-6


def test_unknown_waypoint_is_rejected():
    r = evaluate_route(["C9-1", "A1"], ENV, "1")
    assert not r["success"]
    assert "C9-1" in r["error"]


def test_route_needs_start_and_end():
    assert not evaluate_route(["A1"], ENV, "1")["success"]


def test_apply_sequence_endpoint_accepts_a_route_from_a_synthetic_start():
    """The sequence bar's endpoint uses the same evaluator; a route from a
    D#_START node (a resume from an accepted cut) used to be 'Unknown waypoint'."""
    import fastapi
    from fastapi import testclient

    from server.api.matrix_routes import router

    app = fastapi.FastAPI()
    app.include_router(router)
    client = testclient.TestClient(app)
    resp = client.post(
        "/api/apply_sequence",
        json={
            "drone_id": "1",
            "sequence": "D1_START, A1",
            "env": ENV,
            "fuel_budget": 100,
        },
    )
    body = resp.json()
    assert resp.status_code == 200 and body["success"], body
    assert body["route"] == ["D1_START", "A1"]


# ---- detection radius: edited routes are shaped like planned ones ----

RADIUS_ENV = {
    "airports": [{"id": "A1", "lat": 32.20, "lng": -90.20}],
    "targets": [
        {"id": "T1", "lat": 32.26, "lng": -90.14, "priority": 5, "type": "a"},
        {"id": "T2", "lat": 32.26, "lng": -90.26, "priority": 5, "type": "a"},
        {"id": "T3", "lat": 32.32, "lng": -90.20, "priority": 5, "type": "a"},
    ],
    "sams": [],
}
RADIUS_CFG = {"detection_radius": {"a": 2.0, "b": 0, "c": 0, "d": 0, "e": 0}}
ROUTE = ["A1", "T2", "T3", "T1", "A1"]


def _closest(traj, t):
    return min(_km(p, (t["lat"], t["lng"])) for p in traj)


def test_detection_radius_shortens_edited_route_and_passes_within_radius():
    centres = evaluate_route(ROUTE, RADIUS_ENV, "1")
    radius = evaluate_route(ROUTE, RADIUS_ENV, "1", drone_config=RADIUS_CFG)
    assert radius["success"], radius["error"]
    assert radius["distance"] < centres["distance"] - 5.0  # ~35 → ~27 km
    for t in RADIUS_ENV["targets"]:
        assert _closest(radius["trajectory"], t) <= 2.0 + 0.05


def test_detection_radius_distance_is_the_flown_path_not_the_matrix():
    # A centre-to-centre matrix must not be used once waypoints moved inside radii.
    matrix = {"labels": ["A1", "T1", "T2", "T3"], "matrix": [[0, 99, 99, 99]] * 4}
    r = evaluate_route(
        ROUTE, RADIUS_ENV, "1", distance_matrix=matrix, drone_config=RADIUS_CFG
    )
    assert r["success"] and r["distance"] < 40


def test_loitering_drone_loiters_from_its_detection_radius():
    # A loitering drone flies the same shorter path a radius gives (it loiters
    # from the radius, not over the target's centre) and adds its loiter fuel.
    # The per-hop spans stay travel only; fuel_spans carry the loiter.
    env = {
        "airports": [
            {"id": "A1", "lat": 35.0, "lng": -106.0},
            {"id": "A2", "lat": 35.2, "lng": -106.0},
        ],
        "targets": [{"id": "T1", "lat": 35.1, "lng": -105.89, "type": "a"}],
        "sams": [],
    }
    base = {
        "loiter_times": {"a": 3, "b": 0, "c": 0, "d": 0, "e": 0},
        "speed": 1,
        "detection_radius": {"a": 4, "b": 0, "c": 0, "d": 0, "e": 0},
    }
    flying = evaluate_route(
        ["A2", "T1", "A1"], env, "1", drone_config={**base, "loiter_enabled": False}
    )
    loiter = evaluate_route(
        ["A2", "T1", "A1"], env, "1", drone_config={**base, "loiter_enabled": True}
    )
    assert flying["success"] and loiter["success"]
    assert loiter["trajectory"] == flying["trajectory"]  # same shorter path
    assert loiter["travel_distance"] == pytest.approx(flying["distance"])
    assert loiter["distance"] == pytest.approx(flying["distance"] + 3.0)
    assert loiter["loiter_fuel"] == pytest.approx(3.0)
    assert sum(loiter["span_distances"]) == pytest.approx(flying["distance"])
    assert loiter["fuel_spans"][0] == pytest.approx(loiter["span_distances"][0] + 3.0)


def test_detection_radius_follows_the_path_around_a_sam():
    # A1 west, A2 east, a SAM between them. The drone goes around it; T1 sits
    # 2 km off that detour. With a 4 km radius the drone just flies the detour
    # (T1 costs nothing) — it must not kink toward T1 because the straight
    # A1-A2 chord, which crosses the SAM, happens to pass near it.
    env = {
        "airports": [
            {"id": "A1", "lat": 35.0, "lng": -106.10},
            {"id": "A2", "lat": 35.0, "lng": -105.70},
        ],
        "targets": [{"id": "T1", "lat": 35.11, "lng": -105.90, "type": "a"}],
        "sams": [{"id": "S1", "center": {"lat": 35.0, "lng": -105.90}, "range_km": 10}],
    }

    def cfg(r):
        return {"detection_radius": {"a": r, "b": 0, "c": 0, "d": 0, "e": 0}}

    around = evaluate_route(["A1", "A2"], env, "1", drone_config=cfg(0))
    wide = evaluate_route(["A1", "T1", "A2"], env, "1", drone_config=cfg(4))
    narrow = evaluate_route(["A1", "T1", "A2"], env, "1", drone_config=cfg(1))
    assert around["success"] and wide["success"] and narrow["success"]
    assert wide["distance"] == pytest.approx(around["distance"], abs=0.05)
    assert narrow["distance"] > wide["distance"] + 0.1
