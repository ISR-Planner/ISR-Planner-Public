"""Time-on-target must use the same travel and loiter clock as playback."""

import copy

import pytest

from server.api.solve_routes import _attach_anim_frame
from server.solver.time_sync import apply_time_sync


def _matrix(edges):
    labels = sorted({node for a, b, _ in edges for node in (a, b)})
    index = {node: i for i, node in enumerate(labels)}
    values = [[0.0] * len(labels) for _ in labels]
    for a, b, distance in edges:
        values[index[a]][index[b]] = distance
        values[index[b]][index[a]] = distance
    return {"labels": labels, "matrix": values}


def _route(waypoints, distance, *, lat=35.0, **extra):
    # Equally spaced north-going spans make refuel-leg subdivision unambiguous.
    return {
        "route": waypoints,
        "distance": distance,
        "fuel_budget": 200.0,
        "trajectory": [[lat + i * 0.1, -100.0] for i in range(len(waypoints))],
        "waypoint_indices": {i: i for i in range(len(waypoints))},
        **extra,
    }


def _fixture():
    env = {
        "airports": [{"id": wid} for wid in ("A1", "A2", "A3", "A4", "R1")],
        "targets": [
            {"id": "T1", "type": "a"},
            {"id": "T2", "type": "b"},
            {"id": "T3", "type": "c"},
        ],
        "sams": [],
    }
    configs = {
        "1": {
            "enabled": True,
            "speed": 1.0,
            "fuel_budget": 200.0,
            "loiter_enabled": True,
            "loiter_times": {"a": 7.0, "b": 11.0},
        },
        "2": {"enabled": True, "speed": 1.0, "fuel_budget": 200.0},
    }
    solution = {
        "routes": {
            "1": _route(["A1", "T1", "T2", "A3"], 58.0),
            "2": _route(["A2", "T3", "A4"], 60.0, lat=36.0),
        }
    }
    matrix = _matrix(
        [
            ("A1", "T1", 10.0),
            ("T1", "T2", 10.0),
            ("T2", "A3", 20.0),
            ("A2", "T3", 30.0),
            ("T3", "A4", 30.0),
        ]
    )
    return env, configs, solution, matrix


def _synchronize(env, configs, solution, matrix, strategy, tolerance, members=None):
    result = apply_time_sync(
        solution=solution,
        env=env,
        drone_configs=configs,
        distance_matrix=matrix,
        options={
            "enabled": True,
            "mode": "selected_targets",
            "wait_strategy": strategy,
            "tolerance": tolerance,
            "members": members
            or [
                {"drone_id": "1", "target_id": "T2"},
                {"drone_id": "2", "target_id": "T3"},
            ],
        },
    )
    assert result["time_sync"]["status"] == "synchronized"
    for did, route in result["routes"].items():
        _attach_anim_frame(route, matrix, did, env, configs[did])
    return result


def _assert_playback_clock(route, speed, holds):
    frame = route["anim_frame"]
    delay = route.get("launch_delay", 0.0)
    cumulative = frame["cumulative_distances"]
    for row in route["waypoint_times"]:
        indices = frame["waypoint_indices"]
        index = indices.get(row["index"], indices.get(str(row["index"])))
        arrival = delay + cumulative[index] / speed
        hold = holds.get(row["waypoint"], 0.0)
        assert row["arrival"] == pytest.approx(arrival, abs=0.00051)
        assert row["departure"] == pytest.approx(arrival + hold, abs=0.00051)
        assert row["service_time"] == pytest.approx(hold, abs=0.00051)
        if hold:
            assert frame["trajectory"][index + 1] == frame["trajectory"][index]
            assert (cumulative[index + 1] - cumulative[index]) / speed == pytest.approx(
                hold
            )
    assert route["mission_time"] == pytest.approx(
        delay + frame["total_distance"] / speed, abs=0.00051
    )


@pytest.mark.parametrize("strategy", ["launch_delay", "hover_wait"])
@pytest.mark.parametrize("tolerance", [0.0, 0.5])
def test_selected_target_counts_prior_loiter_but_own_loiter_follows_arrival(
    strategy, tolerance
):
    env, configs, solution, matrix = _fixture()
    result = _synchronize(env, configs, solution, matrix, strategy, tolerance)
    first = result["routes"]["1"]
    member = result["time_sync"]["synchronized_waypoints"][0]
    assert member["natural_arrival"] == 27.0  # 10 + 7 minutes loiter + 10
    assert member["wait_time"] == 3.0 - tolerance
    assert first["arrival_times"]["T2"] == 30.0 - tolerance
    assert first["departure_times"]["T2"] == 41.0 - tolerance
    assert first["sync_wait_fuel"] == (
        3.0 - tolerance if strategy == "hover_wait" else 0
    )
    _assert_playback_clock(first, 1.0, {"T1": 7.0, "T2": 11.0})
    _assert_playback_clock(result["routes"]["2"], 1.0, {})


@pytest.mark.parametrize("strategy", ["launch_delay", "hover_wait"])
@pytest.mark.parametrize("tolerance", [0.0, 0.5])
def test_actual_flown_spans_replace_centre_matrix_before_adding_loiter(
    strategy, tolerance
):
    env, configs, solution, matrix = _fixture()
    # Detection-radius evaluation supplies the shortened path's travel spans.
    solution["routes"]["1"]["span_distances"] = [5.0, 5.0, 10.0]
    solution["routes"]["1"]["distance"] = 38.0
    result = _synchronize(env, configs, solution, matrix, strategy, tolerance)
    first = result["routes"]["1"]
    member = result["time_sync"]["synchronized_waypoints"][0]
    assert member["natural_arrival"] == 17.0  # 5 + 7 minutes loiter + 5
    assert member["wait_time"] == 13.0 - tolerance
    assert first["arrival_times"]["T2"] == 30.0 - tolerance
    assert first["departure_times"]["T2"] == 41.0 - tolerance
    _assert_playback_clock(first, 1.0, {"T1": 7.0, "T2": 11.0})
    _assert_playback_clock(result["routes"]["2"], 1.0, {})


@pytest.mark.parametrize("strategy", ["launch_delay", "hover_wait"])
def test_altitude_specific_matrix_and_loiter_share_playback_clock(strategy):
    env, configs, solution, matrix = _fixture()
    shorter = _matrix(
        [
            ("A1", "T1", 4.0),
            ("T1", "T2", 6.0),
            ("T2", "A3", 10.0),
            ("A2", "T3", 30.0),
            ("T3", "A4", 30.0),
        ]
    )
    matrix["by_altitude"] = {"0.0": copy.deepcopy(matrix), "10.0": shorter}
    matrix["drone_altitudes"] = {"1": "10.0", "2": "0.0"}
    configs["1"]["cruise_altitude_km"] = 10.0
    solution["routes"]["1"]["distance"] = 38.0
    result = _synchronize(env, configs, solution, matrix, strategy, 0.0)
    member = result["time_sync"]["synchronized_waypoints"][0]
    assert member["natural_arrival"] == 17.0  # 4 + 7 minutes loiter + 6
    assert member["wait_time"] == 13.0
    _assert_playback_clock(result["routes"]["1"], 1.0, {"T1": 7.0, "T2": 11.0})
    _assert_playback_clock(result["routes"]["2"], 1.0, {})


@pytest.mark.parametrize("strategy", ["launch_delay", "hover_wait"])
@pytest.mark.parametrize("tolerance", [0.0, 0.5])
def test_refuel_leg_fuel_counts_each_loiter_once_at_nonunit_speed(strategy, tolerance):
    env, configs, solution, matrix = _fixture()
    configs["1"]["speed"] = 2.0
    # Each leg travels 20 km. The fuel values already include 7*2 and 11*2 km
    # of loiter, so playback must subtract these before inserting stationary holds.
    solution["routes"]["1"] = _route(
        ["A1", "T1", "R1", "T2", "A3"],
        76.0,
        leg_distances=[34.0, 42.0],
        refuel_indices=[2],
        refuel_stops=["R1"],
    )
    # Intentionally stale matrix: stored flown leg distances are authoritative.
    matrix = _matrix(
        [
            ("A1", "T1", 12.0),
            ("T1", "R1", 12.0),
            ("R1", "T2", 12.0),
            ("T2", "A3", 12.0),
            ("A2", "T3", 30.0),
            ("T3", "A4", 30.0),
        ]
    )
    result = _synchronize(env, configs, solution, matrix, strategy, tolerance)
    first = result["routes"]["1"]
    member = result["time_sync"]["synchronized_waypoints"][0]
    assert member["natural_arrival"] == 22.0  # (10 + 14 + 10 + 10) / 2
    assert member["wait_time"] == pytest.approx(8.0 - tolerance)
    assert first["arrival_times"]["T2"] == 30.0 - tolerance
    assert first["departure_times"]["T2"] == 41.0 - tolerance
    assert first["anim_frame"]["leg_breaks"] == pytest.approx([34.0])
    _assert_playback_clock(first, 2.0, {"T1": 7.0, "T2": 11.0})
    _assert_playback_clock(result["routes"]["2"], 1.0, {})


def _frame_arrival(route, speed, target):
    route_index = route["route"].index(target)
    indices = route["anim_frame"]["waypoint_indices"]
    index = indices.get(route_index, indices.get(str(route_index)))
    return (
        route.get("launch_delay", 0.0)
        + route["anim_frame"]["cumulative_distances"][index] / speed
    )


@pytest.mark.parametrize("strategy", ["launch_delay", "hover_wait"])
def test_zero_tolerance_synchronizes_actual_fractional_arrivals(strategy):
    env, configs, solution, matrix = _fixture()
    configs["1"].update(speed=0.3333, loiter_times={"a": 0.234567, "b": 0.345678})
    configs["2"]["speed"] = 0.717171
    solution["routes"]["1"]["span_distances"] = [0.1234567, 0.7654321, 0.9876543]
    solution["routes"]["2"]["span_distances"] = [3.1415926, 2.7182818]
    result = _synchronize(env, configs, solution, matrix, strategy, 0.0)
    first, second = result["routes"]["1"], result["routes"]["2"]
    # Matching rounded labels is insufficient: both aircraft must really reach
    # their selected anchors at the same playback instant.
    assert _frame_arrival(first, configs["1"]["speed"], "T2") == pytest.approx(
        _frame_arrival(second, configs["2"]["speed"], "T3"), abs=1e-9
    )
    _assert_playback_clock(first, 0.3333, {"T1": 0.234567, "T2": 0.345678})
    _assert_playback_clock(second, 0.717171, {})


def test_reapplying_hover_sync_does_not_charge_the_wait_twice():
    env, configs, solution, matrix = _fixture()
    solution["routes"]["1"].update(span_distances=[5.0, 5.0, 10.0], distance=38.0)
    result = _synchronize(env, configs, solution, matrix, "hover_wait", 0.0)
    assert result["routes"]["1"]["distance"] == 51.0  # 38 + 13 minutes wait
    original = copy.deepcopy(result)
    result = _synchronize(env, configs, result, matrix, "hover_wait", 0.0)
    for did, route in result["routes"].items():
        assert route["distance"] == original["routes"][did]["distance"]
        assert route["anim_frame"] == original["routes"][did]["anim_frame"]
        assert route["waypoint_times"] == original["routes"][did]["waypoint_times"]


def test_changing_selected_target_replaces_the_old_wait():
    env, configs, solution, matrix = _fixture()
    solution["routes"]["1"].update(span_distances=[5.0, 5.0, 10.0], distance=38.0)
    result = _synchronize(env, configs, solution, matrix, "hover_wait", 0.0)
    result = _synchronize(
        env,
        configs,
        result,
        matrix,
        "hover_wait",
        0.0,
        members=[
            {"drone_id": "1", "target_id": "T1"},
            {"drone_id": "2", "target_id": "T3"},
        ],
    )
    first = result["routes"]["1"]
    assert first["sync_target"] == "T1"
    assert first["wait_times"] == {"T1": 25.0}
    assert first["distance"] == 63.0  # base38 + new25, without old13
    assert first["arrival_times"]["T1"] == 30.0
    assert first["arrival_times"]["T2"] == 42.0  # 30 + 7 loiter + 5 travel
    _assert_playback_clock(first, 1.0, {"T1": 7.0, "T2": 11.0})


def test_removing_drone_from_selection_removes_its_previous_wait():
    env, configs, solution, matrix = _fixture()
    solution["routes"]["1"].update(span_distances=[5.0, 5.0, 10.0], distance=38.0)
    result = _synchronize(env, configs, solution, matrix, "hover_wait", 0.0)
    env["targets"].append({"id": "T4", "type": "c"})
    configs["3"] = {"enabled": True, "speed": 1.0, "fuel_budget": 200.0}
    result["routes"]["3"] = _route(
        ["A3", "T4", "A4"], 50.0, span_distances=[40.0, 10.0]
    )
    result = _synchronize(
        env,
        configs,
        result,
        matrix,
        "hover_wait",
        0.0,
        members=[
            {"drone_id": "2", "target_id": "T3"},
            {"drone_id": "3", "target_id": "T4"},
        ],
    )
    first = result["routes"]["1"]
    assert not first.get("sync_target")
    assert not first.get("sync_wait_fuel")
    assert not first.get("launch_delay")
    assert first["wait_times"] == {}
    assert first["distance"] == 38.0
    assert first["arrival_times"]["T2"] == 17.0
    _assert_playback_clock(first, 1.0, {"T1": 7.0, "T2": 11.0})


@pytest.mark.parametrize("strategy", ["launch_delay", "hover_wait"])
@pytest.mark.parametrize("agent_changes_config", [False, True])
def test_agent_sync_helper_uses_the_same_final_route_clock(
    strategy, agent_changes_config
):
    from server.api.agent_routes import _apply_time_sync_to_agent_result

    env, configs, solution, matrix = _fixture()
    solution["routes"]["1"].update(span_distances=[5.0, 5.0, 10.0], distance=38.0)
    effective_configs = copy.deepcopy(configs)
    if agent_changes_config:
        effective_configs["1"].update(speed=2.0, loiter_times={"a": 3.0, "b": 9.0})
        effective_configs["2"]["speed"] = 0.5
        solution["drone_configs"] = effective_configs
        solution["routes"]["1"]["distance"] = 44.0  # 20 travel + 12*2 loiter
    expected = _synchronize(
        env, effective_configs, copy.deepcopy(solution), matrix, strategy, 0.5
    )
    actual, actual_matrix = _apply_time_sync_to_agent_result(
        result=solution,
        env=env,
        drone_configs=configs,
        distance_matrix=matrix,
        time_sync={
            "enabled": True,
            "mode": "selected_targets",
            "wait_strategy": strategy,
            "tolerance": 0.5,
            "members": [
                {"drone_id": "1", "target_id": "T2"},
                {"drone_id": "2", "target_id": "T3"},
            ],
        },
    )
    assert actual_matrix == matrix
    assert actual["time_sync"] == expected["time_sync"]
    for did, route in actual["routes"].items():
        assert route["anim_frame"] == expected["routes"][did]["anim_frame"]
        assert route["waypoint_times"] == expected["routes"][did]["waypoint_times"]
    _assert_playback_clock(
        actual["routes"]["1"],
        effective_configs["1"]["speed"],
        {"T1": 3.0, "T2": 9.0} if agent_changes_config else {"T1": 7.0, "T2": 11.0},
    )
    _assert_playback_clock(actual["routes"]["2"], effective_configs["2"]["speed"], {})


def test_manual_sequence_api_and_planner_use_same_flown_spans_and_loiter(monkeypatch):
    from server.api import matrix_routes
    from server.api.models import ApplySequenceRequest

    env = {
        "airports": [
            {"id": "A1", "lat": 35.0, "lng": -100.0},
            {"id": "A3", "lat": 35.0, "lng": -99.6},
        ],
        "targets": [
            {"id": "T1", "lat": 35.1, "lng": -99.9, "type": "a", "priority": 5},
            {"id": "T2", "lat": 35.1, "lng": -99.7, "type": "b", "priority": 5},
        ],
        "sams": [],
    }
    config = {
        "speed": 2.0,
        "loiter_enabled": True,
        "loiter_times": {"a": 7.0, "b": 11.0},
        "detection_radius": {"a": 2.0, "b": 2.0},
    }
    monkeypatch.setattr(matrix_routes, "get_current_matrix", lambda: None)
    response = matrix_routes.api_apply_sequence(
        ApplySequenceRequest(
            drone_id="1",
            sequence="A1,T1,T2,A3",
            env=env,
            drone_config=config,
            fuel_budget=200.0,
        )
    )
    assert response.success, response.error
    route = response.model_dump()
    manual_frame = copy.deepcopy(route.pop("anim_frame"))
    _attach_anim_frame(route, None, "1", env, config)
    assert route["anim_frame"] == manual_frame
    assert manual_frame["total_distance"] == pytest.approx(
        sum(route["span_distances"]) + (7.0 + 11.0) * 2.0
    )


@pytest.mark.parametrize("strategy", ["launch_delay", "hover_wait"])
def test_manual_solve_response_preserves_flown_spans_and_loiter_timing(
    monkeypatch, strategy
):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.api import solve_routes

    env, configs, solution, matrix = _fixture()
    solution["routes"]["1"].update(span_distances=[5.0, 5.0, 10.0], distance=38.0)
    solution["distance_matrix"] = matrix
    expected = _synchronize(
        env, configs, copy.deepcopy(solution), matrix, strategy, 0.5
    )
    # Exercise the real request/response adapter, retaining real timing and frame
    # builders. Route optimization and persistence are outside this regression.
    monkeypatch.setattr(
        solve_routes, "solve_mission", lambda *args: copy.deepcopy(solution)
    )
    monkeypatch.setattr(
        solve_routes,
        "get_or_create_mission",
        lambda *args: ("test-tot", False, "env-hash", "config-hash", None),
    )
    monkeypatch.setattr(solve_routes, "upsert_mission", lambda *args, **kwargs: None)
    ledger = SimpleNamespace(
        snapshot_solve_environment=lambda *args: None,
        create_draft_plan=lambda **kwargs: None,
    )
    app = FastAPI()
    app.include_router(solve_routes.create_solve_router(ledger))
    response = TestClient(app).post(
        "/api/solve",
        json={
            "env": env,
            "drone_configs": configs,
            "time_sync": {
                "enabled": True,
                "mode": "selected_targets",
                "wait_strategy": strategy,
                "tolerance": 0.5,
                "members": [
                    {"drone_id": "1", "target_id": "T2"},
                    {"drone_id": "2", "target_id": "T3"},
                ],
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"], body.get("errors")
    assert body["time_sync"] == expected["time_sync"]
    first = body["routes"]["1"]
    assert first["span_distances"] == [5.0, 5.0, 10.0]
    assert first["anim_frame"]["total_distance"] == pytest.approx(
        38.0 + (12.5 if strategy == "hover_wait" else 0.0)
    )
    _assert_playback_clock(first, 1.0, {"T1": 7.0, "T2": 11.0})
    _assert_playback_clock(body["routes"]["2"], 1.0, {})
