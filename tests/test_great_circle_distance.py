"""Distances are great-circle km at cruise height, on any size of map.

The solver plane is gnomonic (straight legs are great circles); every length
is measured on the globe and scaled by (R + h) / R for the cruise altitude.
"""

import math

import pytest

from path_planning_core.latlng_utils import (
    EARTH_RADIUS_KM,
    altitude_distance_factor,
    haversine_km,
    latlng_to_local_xy,
    local_xy_to_latlng,
    plane_circle,
    plane_dist,
    projection_centre,
)
from server.solver.projection import _project_env_to_local_km
from server.solver.sam_distance_matrix import calculate_sam_aware_matrix

# Oklahoma to Dubai, with targets past Dubai (isr_env_20261006-0858_N1_2).
WIDE_AIRPORTS = [
    {"id": "A1", "lat": 34.35704160076075, "lng": -94.83123779296875},
    {"id": "A2", "lat": 23.9410755624621, "lng": 55.28594970703125},
]
WIDE_TARGETS = [
    {"id": "T1", "lat": 21.672743540437242, "lng": 62.66876220703125, "priority": 5},
    {"id": "T2", "lat": 16.357038750911784, "lng": 64.77813720703125, "priority": 5},
    {"id": "T5", "lat": 29.587788659909975, "lng": 49.30938720703125, "priority": 5},
]


def _matrix(env):
    data = calculate_sam_aware_matrix(env, use_supabase_cache=False)
    idx = {lbl: i for i, lbl in enumerate(data["labels"])}
    return lambda a, b: data["matrix"][idx[a]][idx[b]]


def test_projection_is_exact_on_a_wide_map():
    pts = [(p["lat"], p["lng"]) for p in WIDE_AIRPORTS + WIDE_TARGETS]
    c = projection_centre(pts)
    xy = [latlng_to_local_xy(*p, *c) for p in pts]
    for (lat, lng), (x, y) in zip(pts, xy):
        back = local_xy_to_latlng(x, y, *c)
        assert back[0] == pytest.approx(lat, abs=1e-9)
        assert back[1] == pytest.approx(lng, abs=1e-9)
    for i in range(len(pts)):
        for j in range(len(pts)):
            assert plane_dist(xy[i], xy[j]) == pytest.approx(
                haversine_km(*pts[i], *pts[j]), abs=1e-6
            )


def test_wide_map_matrix_is_great_circle():
    get = _matrix({"airports": WIDE_AIRPORTS, "targets": WIDE_TARGETS, "sams": []})
    # The flat map used to say 15,355.6 km; the globe says 12,805.5 km.
    assert get("A1", "A2") == pytest.approx(12805.5, abs=0.5)
    assert get("A1", "T5") == pytest.approx(11970, abs=1.0)


def test_matrix_is_measured_at_cruise_height():
    env = {"airports": WIDE_AIRPORTS, "targets": WIDE_TARGETS, "sams": []}
    sea = _matrix(env)("A1", "A2")
    high = _matrix({**env, "cruise_altitude_km": 16.8})("A1", "A2")
    assert high == pytest.approx(sea * (EARTH_RADIUS_KM + 16.8) / EARTH_RADIUS_KM)
    assert altitude_distance_factor(16.8) == pytest.approx(1.002637, abs=1e-6)


def test_small_map_matches_haversine():
    airports = [{"id": "A1", "lat": 35.0, "lng": -106.0}]
    targets = [
        {"id": "T1", "lat": 35.5, "lng": -105.4, "priority": 5},
        {"id": "T2", "lat": 34.7, "lng": -106.6, "priority": 5},
    ]
    get = _matrix({"airports": airports, "targets": targets, "sams": []})
    for a, b in (("A1", "T1"), ("T1", "T2"), ("T2", "A1")):
        pa = next(p for p in airports + targets if p["id"] == a)
        pb = next(p for p in airports + targets if p["id"] == b)
        assert get(a, b) == pytest.approx(
            haversine_km(pa["lat"], pa["lng"], pb["lat"], pb["lng"]), rel=1e-9
        )


def test_far_sam_is_its_true_size():
    """A SAM far from the plane's centre keeps its true size: the inside test
    and the outline it is flown around are both measured on the globe."""
    sam = {"center": {"lat": 25.0, "lng": 55.0}, "range_km": 50}
    env = {
        "airports": WIDE_AIRPORTS,
        "targets": [
            # 40 km north of the SAM: inside its 50 km range.
            {"id": "T1", "lat": 25.0 + 40 / 111.195, "lng": 55.0, "priority": 5},
            # 60 km south: outside.
            {"id": "T2", "lat": 25.0 - 60 / 111.195, "lng": 55.0, "priority": 5},
            # 100 km north: the leg T2 -> T3 runs straight through the SAM.
            {"id": "T3", "lat": 25.0 + 100 / 111.195, "lng": 55.0, "priority": 5},
        ],
        "sams": [sam],
    }
    projected, _, _ = _project_env_to_local_km(env)
    psam = projected["sams"][0]
    assert psam["range"] == 50  # km, unchanged
    centre = (psam["x"], psam["y"])
    assert math.hypot(*centre) > 4000  # far from the plane's centre
    ring = plane_circle(*centre, 50, 72, pad=False)
    assert all(plane_dist(p, centre) == pytest.approx(50) for p in ring)

    data = calculate_sam_aware_matrix(env, use_supabase_cache=False)
    assert "T1" in data["excluded_targets"]
    assert "T2" not in data["excluded_targets"]
    idx = {lbl: i for i, lbl in enumerate(data["labels"])}
    # Straight would be 160 km. Around a 50 km SAM it is longer, and the same
    # as for the same geometry near the plane's centre (no inflation).
    around = data["matrix"][idx["T2"]][idx["T3"]]
    assert around > 160 + 5
    near = calculate_sam_aware_matrix(
        {
            "airports": [{"id": "A1", "lat": 24.0, "lng": 55.3}],
            "targets": env["targets"],
            "sams": [sam],
        },
        use_supabase_cache=False,
    )
    nidx = {lbl: i for i, lbl in enumerate(near["labels"])}
    assert around == pytest.approx(near["matrix"][nidx["T2"]][nidx["T3"]], rel=0.01)


def test_too_wide_map_is_refused():
    with pytest.raises(ValueError, match="too wide"):
        latlng_to_local_xy(-40.0, 170.0, 40.0, -10.0)
    assert math.isfinite(plane_dist((0, 0), (1e6, 0)))
