from path_planning_core.boundary_navigation import _segment_enters_polygon
from path_planning_core.latlng_utils import local_xy_to_latlng
from server.solver.projection import _project_env_to_local_km
from server.solver.sam_distance_matrix import SAMDistanceMatrixCalculator
from server.solver.trajectory_planner import ISRTrajectoryPlanner
from server.solver.weather import verify_weather_routes


def environment():
    return {
        "airports": [
            {"id": "A1", "lat": 32, "lng": -98},
            {"id": "A2", "lat": 32, "lng": -96},
        ],
        "targets": [],
        "sams": [
            {
                "id": "WX-test",
                "center": {"lat": 31.8, "lng": -97.2},
                "range": 0,
                "weather_ring": [
                    [-97.2, 31.8],
                    [-96.8, 31.8],
                    [-96.8, 32.2],
                    [-97.2, 32.2],
                    [-97.2, 31.8],
                ],
            }
        ],
    }


def test_weather_changes_distance_and_trajectory():
    env, lat, lng = _project_env_to_local_km(environment())
    a, b = env["airports"]
    start, end = (a["x"], a["y"]), (b["x"], b["y"])
    direct, direct_dist = ISRTrajectoryPlanner([]).plan_path(start, end)
    path, dist = ISRTrajectoryPlanner(env["sams"]).plan_path(start, end)
    assert len(path) > 2
    assert dist > direct_dist
    polygon = env["sams"][0]["avoidance_polygon"]
    assert not any(
        _segment_enters_polygon(p, q, polygon) for p, q in zip(path, path[1:])
    )
    matrix = SAMDistanceMatrixCalculator().calculate_matrix(
        env["airports"], [], env["sams"]
    )
    assert matrix["matrix"][0][1] > direct_dist
    configs = {"1": {"enabled": True}}
    unsafe = [list(local_xy_to_latlng(*p, lat, lng)) for p in direct]
    safe = [list(local_xy_to_latlng(*p, lat, lng)) for p in path]
    assert verify_weather_routes(environment(), {"1": {"trajectory": unsafe}}, configs)
    assert not verify_weather_routes(
        environment(), {"1": {"trajectory": safe}}, configs
    )


def test_weather_blocks_airport_inside_polygon():
    env, _, _ = _project_env_to_local_km(environment())
    a = env["airports"][0]
    polygon = env["sams"][0]["avoidance_polygon"]
    centre = tuple(sum(p[i] for p in polygon) / len(polygon) for i in (0, 1))
    path, dist = ISRTrajectoryPlanner(env["sams"]).plan_path((a["x"], a["y"]), centre)
    assert path == []
    assert dist == float("inf")


def test_verification_catches_short_crossing_and_allows_boundary():
    from server.solver.weather import segment_enters

    polygon = [(0, 0), (0.001, 0), (0.001, 1), (0, 1)]
    assert segment_enters((-100, 0.5), (100, 0.5), polygon)
    assert not segment_enters((-100, 0), (100, 0), polygon)


def test_distant_weather_does_not_move_or_break_flight_projection():
    env = environment()
    baseline, lat, lng = _project_env_to_local_km(env)
    env["sams"].append(
        {
            "id": "WX-distant",
            "range": 0,
            "center": {"lat": -10, "lng": 58},
            "weather_ring": [[58, -10], [59, -10], [59, -9], [58, -9], [58, -10]],
        }
    )
    projected, new_lat, new_lng = _project_env_to_local_km(env)
    assert (new_lat, new_lng) == (lat, lng)
    assert len(projected["sams"]) == len(baseline["sams"])


def test_boundary_weather_is_clipped_instead_of_dropped():
    from server.solver.weather_geometry import project_weather_ring

    ring = [[75, -1], [89, -1], [89, 1], [75, 1], [75, -1]]
    assert project_weather_ring(ring, 0, 0)
