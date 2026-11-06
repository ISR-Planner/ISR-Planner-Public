"""A D{did}_START resume node must be sent as lat/lng so the server projects it.

The browser once sent [lat, lng] in x/y; projection only converts nodes that have
lat/lng, so those numbers were used as km and the node landed tens of km away.
"""

from server.solver.projection import _project_env_to_local_km

A1 = {"lat": 32.20, "lng": -90.10}


def _env(start_node):
    return {
        "airports": [{"id": "A1", **A1}],
        "targets": [{"id": "T1", "lat": 32.25, "lng": -90.15}],
        "sams": [],
        "synthetic_starts": {"D1_START": start_node},
    }


def test_latlng_synthetic_start_is_projected_to_the_same_place():
    env, _, _ = _project_env_to_local_km(_env({"id": "D1_START", **A1}))
    a1 = env["airports"][0]
    node = env["synthetic_starts"]["D1_START"]
    assert abs(node["x"] - a1["x"]) < 1e-6
    assert abs(node["y"] - a1["y"]) < 1e-6
