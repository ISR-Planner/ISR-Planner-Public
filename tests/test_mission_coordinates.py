"""Geographic solver inputs must not silently wrap malformed mission coordinates."""

import copy
import math

import pytest

from server.solver.projection import (
    _project_env_to_local_km,
    _project_solution_trajectories,
)


def environment(lat=35, lng=-100):
    point = {"lat": lat, "lng": lng}
    return {
        "airports": [{"id": "A1", **point}],
        "targets": [{"id": "T1", **point}],
        "sams": [{"id": "S1", "center": dict(point), "range_km": 5}],
        "checkpoints": [{"id": "C1", **point}],
        "synthetic_starts": {"D1_START": {"id": "D1_START", **point}},
    }


def mission_point(env, kind):
    if kind == "Synthetic start D1_START":
        return env["synthetic_starts"]["D1_START"]
    if kind == "SAM S1 centre":
        return env["sams"][0]["center"]
    collection = {
        "Airport A1": "airports",
        "Target T1": "targets",
        "Checkpoint C1": "checkpoints",
    }[kind]
    return env[collection][0]


@pytest.mark.parametrize(
    "kind",
    [
        "Airport A1",
        "Target T1",
        "SAM S1 centre",
        "Checkpoint C1",
        "Synthetic start D1_START",
    ],
)
@pytest.mark.parametrize(
    "field,value,axis,limit",
    [
        ("lat", 90.01, "latitude", 90),
        ("lat", -90.01, "latitude", 90),
        ("lat", float("nan"), "latitude", 90),
        ("lat", None, "latitude", 90),
        ("lng", 181, "longitude", 180),
        ("lng", -181, "longitude", 180),
        ("lng", 620, "longitude", 180),
        ("lng", float("inf"), "longitude", 180),
        ("lng", "invalid", "longitude", 180),
        ("lng", True, "longitude", 180),
    ],
)
def test_invalid_mission_coordinates_are_rejected_with_object_and_range(
    kind, field, value, axis, limit
):
    env = environment()
    mission_point(env, kind)[field] = value
    with pytest.raises(ValueError) as error:
        _project_env_to_local_km(env)
    assert kind in str(error.value)
    assert f"invalid {axis}" in str(error.value)
    assert f"between {-limit} and {limit} degrees" in str(error.value)


@pytest.mark.parametrize("field", ["lat", "lng"])
def test_incomplete_geographic_pair_does_not_fall_back_to_xy(field):
    env = environment()
    target = env["targets"][0]
    del target[field]
    target.update(x=1, y=2)
    with pytest.raises(ValueError, match="Target T1: invalid"):
        _project_env_to_local_km(env)


@pytest.mark.parametrize("lat,lng", [(90, 180), (-90, -180), (0, 180), (0, -180)])
def test_valid_geographic_boundaries_project_without_mutating_source(lat, lng):
    original = environment(lat, lng)
    before = copy.deepcopy(original)
    projected, _, _ = _project_env_to_local_km(original)
    assert original == before
    for kind in [
        "Airport A1",
        "Target T1",
        "Checkpoint C1",
        "Synthetic start D1_START",
    ]:
        point = mission_point(projected, kind)
        assert math.isfinite(point["x"]) and math.isfinite(point["y"])
    assert all(math.isfinite(value) for value in projected["sams"][0]["pos"])


def test_legacy_xy_only_mission_remains_supported():
    env = environment()
    for kind in [
        "Airport A1",
        "Target T1",
        "SAM S1 centre",
        "Checkpoint C1",
        "Synthetic start D1_START",
    ]:
        point = mission_point(env, kind)
        point.pop("lat")
        point.pop("lng")
        point.update(x=1000, y=-500)
    projected, _, _ = _project_env_to_local_km(env)
    for kind in [
        "Airport A1",
        "Target T1",
        "SAM S1 centre",
        "Checkpoint C1",
        "Synthetic start D1_START",
    ]:
        point = mission_point(projected, kind)
        assert (point["x"], point["y"]) == (1000, -500)


def test_existing_numeric_string_coordinates_remain_supported():
    projected, _, _ = _project_env_to_local_km(environment("35", "-100"))
    assert math.isfinite(projected["targets"][0]["x"])


def test_weather_polygons_keep_their_unwrapped_antimeridian_coordinates():
    env = environment(0, 180)
    env["sams"] = [
        {
            "id": "WX-antimeridian",
            "center": {"lat": 0, "lng": 181},
            "weather_ring": [[179, -1], [181, -1], [181, 1], [179, 1], [179, -1]],
            "range_km": 0,
        }
    ]
    projected, _, _ = _project_env_to_local_km(env)
    assert len(projected["sams"][0]["avoidance_polygon"]) >= 4


@pytest.mark.parametrize("point", [[35, 620], [91, -100], [35, float("inf")]])
def test_optimizer_trajectory_rejects_invalid_geographic_points(point):
    solution = {"routes": {"1": {"trajectory": [point]}}}
    with pytest.raises(ValueError, match="Drone 1 trajectory point 0: invalid"):
        _project_solution_trajectories(solution, 35, -100)
    assert solution["routes"]["1"]["trajectory"] == [point]


def test_optimizer_trajectory_accepts_valid_antimeridian_boundary():
    solution = {"routes": {"1": {"trajectory": [[0, 180], [0, -180]]}}}
    _project_solution_trajectories(solution, 0, 180)
    assert solution["routes"]["1"]["trajectory"][0] == [0, 0]
    assert solution["routes"]["1"]["trajectory"][1] == pytest.approx([0, 0], abs=1e-9)
