from server.solver.animation_frame import build_animation_frame
from server.solver.time_sync import apply_time_sync


def _matrix():
    return {
        "labels": ["A1", "A2", "T1", "T2"],
        "matrix": [
            [0, 0, 10, 0],
            [0, 0, 0, 20],
            [10, 0, 0, 0],
            [0, 20, 0, 0],
        ],
    }


def test_time_sync_defaults_to_launch_delay_without_extra_fuel():
    solution = {
        "routes": {
            "1": {
                "route": ["A1", "T1", "A1"],
                "distance": 20.0,
                "fuel_budget": 100.0,
            },
            "2": {
                "route": ["A2", "T2", "A2"],
                "distance": 40.0,
                "fuel_budget": 100.0,
            },
        }
    }
    env = {"targets": [{"id": "T1", "type": "a"}, {"id": "T2", "type": "a"}]}
    drone_configs = {
        "1": {"enabled": True, "speed": 2.0, "fuel_budget": 100.0},
        "2": {"enabled": True, "speed": 1.0, "fuel_budget": 100.0},
    }

    result = apply_time_sync(
        solution=solution,
        env=env,
        drone_configs=drone_configs,
        distance_matrix=_matrix(),
        options={"enabled": True, "tolerance": 0.1},
    )

    assert result["time_sync"]["status"] == "synchronized"
    assert result["time_sync"]["wait_strategy"] == "launch_delay"
    assert result["time_sync"]["sync_time"] == 20.0
    assert result["routes"]["1"]["arrival_times"]["T1"] == 19.9
    assert result["routes"]["2"]["arrival_times"]["T2"] == 20.0
    assert result["routes"]["1"]["launch_delay"] == 14.9
    assert result["routes"]["1"]["wait_times"] == {}
    assert result["routes"]["1"]["sync_wait_fuel"] == 0.0
    assert result["routes"]["1"]["distance"] == 20.0
    assert result["routes"]["2"]["wait_times"] == {}


def test_time_sync_selected_targets_syncs_explicit_route_waypoints():
    solution = {
        "routes": {
            "1": {
                "route": ["A1", "T1", "T2", "A1"],
                "distance": 40.0,
                "fuel_budget": 100.0,
            },
            "2": {
                "route": ["A2", "T2", "T1", "A2"],
                "distance": 50.0,
                "fuel_budget": 100.0,
            },
        }
    }
    env = {"targets": [{"id": "T1", "type": "a"}, {"id": "T2", "type": "a"}]}
    drone_configs = {
        "1": {"enabled": True, "speed": 2.0, "fuel_budget": 100.0},
        "2": {"enabled": True, "speed": 1.0, "fuel_budget": 100.0},
    }
    distance_matrix = {
        "labels": ["A1", "A2", "T1", "T2"],
        "matrix": [
            [0, 0, 10, 20],
            [0, 0, 20, 5],
            [10, 20, 0, 10],
            [20, 5, 10, 0],
        ],
    }

    result = apply_time_sync(
        solution=solution,
        env=env,
        drone_configs=drone_configs,
        distance_matrix=distance_matrix,
        options={
            "enabled": True,
            "mode": "selected_targets",
            "tolerance": 0.1,
            "members": [
                {"drone_id": "1", "target_id": "T2"},
                {"drone_id": "2", "target_id": "T1"},
            ],
        },
    )

    assert result["time_sync"]["status"] == "synchronized"
    assert result["time_sync"]["mode"] == "selected_targets"
    assert result["time_sync"]["sync_time"] == 15.0
    assert result["routes"]["1"]["arrival_times"]["T2"] == 14.9
    assert result["routes"]["2"]["arrival_times"]["T1"] == 15.0
    assert result["routes"]["1"]["launch_delay"] == 4.9


def test_time_sync_selected_targets_skips_invalid_selection():
    solution = {
        "routes": {
            "1": {
                "route": ["A1", "T1", "A1"],
                "distance": 20.0,
                "fuel_budget": 100.0,
            },
            "2": {
                "route": ["A2", "T2", "A2"],
                "distance": 40.0,
                "fuel_budget": 100.0,
            },
        }
    }
    env = {"targets": [{"id": "T1", "type": "a"}, {"id": "T2", "type": "a"}]}
    drone_configs = {
        "1": {"enabled": True, "speed": 2.0, "fuel_budget": 100.0},
        "2": {"enabled": True, "speed": 1.0, "fuel_budget": 100.0},
    }

    result = apply_time_sync(
        solution=solution,
        env=env,
        drone_configs=drone_configs,
        distance_matrix=_matrix(),
        options={
            "enabled": True,
            "mode": "selected_targets",
            "members": [
                {"drone_id": "1", "target_id": "T2"},
                {"drone_id": "2", "target_id": "T2"},
            ],
        },
    )

    assert result["time_sync"]["status"] == "skipped"
    assert (
        result["time_sync"]["reason"]
        == "Need at least two valid selected drone-target pairs."
    )
    assert result["time_sync"]["warnings"] == [
        "D1 route does not include selected sync target T2."
    ]


def test_time_sync_hover_wait_marks_fuel_budget_infeasible_after_wait():
    solution = {
        "routes": {
            "1": {
                "route": ["A1", "T1", "A1"],
                "distance": 20.0,
                "fuel_budget": 25.0,
            },
            "2": {
                "route": ["A2", "T2", "A2"],
                "distance": 40.0,
                "fuel_budget": 100.0,
            },
        }
    }
    env = {"targets": [{"id": "T1", "type": "a"}, {"id": "T2", "type": "a"}]}
    drone_configs = {
        "1": {"enabled": True, "speed": 2.0, "fuel_budget": 25.0},
        "2": {"enabled": True, "speed": 1.0, "fuel_budget": 100.0},
    }

    result = apply_time_sync(
        solution=solution,
        env=env,
        drone_configs=drone_configs,
        distance_matrix=_matrix(),
        options={"enabled": True, "wait_strategy": "hover_wait", "tolerance": 0.1},
    )

    assert result["time_sync"]["status"] == "infeasible"
    assert result["time_sync"]["wait_strategy"] == "hover_wait"
    assert result["routes"]["1"]["distance"] == 49.8
    assert result["routes"]["1"]["wait_times"] == {"T1": 14.9}
    assert result["time_sync"]["warnings"] == [
        "D1 exceeds fuel budget after sync wait (49.80 > 25.00)."
    ]


def test_time_sync_checks_refuel_routes_per_leg_not_total_distance():
    solution = {
        "routes": {
            "1": {
                "route": ["A1", "T1", "TK1", "A1"],
                "distance": 50.0,
                "fuel_budget": 30.0,
                "leg_distances": [20.0, 30.0],
                "refuel_indices": [2],
            },
            "2": {
                "route": ["A1", "T2", "A1"],
                "distance": 30.0,
                "fuel_budget": 30.0,
            },
        }
    }
    env = {"targets": [{"id": "T1", "type": "a"}, {"id": "T2", "type": "a"}]}
    drone_configs = {
        "1": {"enabled": True, "speed": 1.0, "fuel_budget": 30.0},
        "2": {"enabled": True, "speed": 1.0, "fuel_budget": 30.0},
    }
    distance_matrix = {
        "labels": ["A1", "TK1", "T1", "T2"],
        "matrix": [
            [0, 30, 20, 25],
            [30, 0, 0, 0],
            [20, 0, 0, 0],
            [25, 0, 0, 0],
        ],
    }

    result = apply_time_sync(
        solution=solution,
        env=env,
        drone_configs=drone_configs,
        distance_matrix=distance_matrix,
        options={"enabled": True, "wait_strategy": "hover_wait", "tolerance": 0.1},
    )

    assert result["time_sync"]["status"] == "synchronized"
    assert result["time_sync"]["wait_strategy"] == "hover_wait"
    assert result["routes"]["1"]["sync_wait_fuel"] == 4.9
    assert result["routes"]["1"]["distance"] == 54.9
    assert result["time_sync"]["warnings"] == []


def test_time_sync_uses_explicit_selected_targets_only():
    solution = {
        "routes": {
            "1": {
                "route": ["A1", "T1", "T2", "A1"],
                "distance": 30.0,
                "fuel_budget": 100.0,
            },
            "2": {
                "route": ["A2", "T3", "T4", "A2"],
                "distance": 40.0,
                "fuel_budget": 100.0,
            },
            "3": {
                "route": ["A1", "T1", "A1"],
                "distance": 20.0,
                "fuel_budget": 100.0,
            },
        }
    }
    env = {
        "targets": [
            {"id": "T1", "type": "a"},
            {"id": "T2", "type": "a"},
            {"id": "T3", "type": "a"},
            {"id": "T4", "type": "a"},
        ]
    }
    drone_configs = {
        "1": {"enabled": True, "speed": 1.0, "fuel_budget": 100.0},
        "2": {"enabled": True, "speed": 1.0, "fuel_budget": 100.0},
        "3": {"enabled": True, "speed": 1.0, "fuel_budget": 100.0},
    }
    distance_matrix = {
        "labels": ["A1", "A2", "T1", "T2", "T3", "T4"],
        "matrix": [
            [0, 0, 5, 0, 0, 0],
            [0, 0, 0, 0, 10, 0],
            [5, 0, 0, 15, 0, 0],
            [0, 0, 15, 0, 0, 0],
            [0, 10, 0, 0, 0, 20],
            [0, 0, 0, 0, 20, 0],
        ],
    }

    result = apply_time_sync(
        solution=solution,
        env=env,
        drone_configs=drone_configs,
        distance_matrix=distance_matrix,
        options={
            "enabled": True,
            "mode": "selected_targets",
            "wait_strategy": "launch_delay",
            "tolerance": 0.1,
            "members": [
                {"drone_id": "1", "target_id": "T2"},
                {"drone_id": "2", "target_id": "T4"},
            ],
        },
    )

    assert result["time_sync"]["status"] == "synchronized"
    assert result["time_sync"]["mode"] == "selected_targets"
    assert result["time_sync"]["sync_time"] == 30.0
    assert result["routes"]["1"]["sync_target"] == "T2"
    assert result["routes"]["1"]["launch_delay"] == 9.9
    assert result["routes"]["1"]["arrival_times"]["T2"] == 29.9
    assert result["routes"]["2"]["sync_target"] == "T4"
    assert result["routes"]["2"]["arrival_times"]["T4"] == 30.0
    assert "sync_target" not in result["routes"]["3"]


def test_selected_target_sync_skips_unmatched_and_malformed_members():
    solution = {
        "routes": {
            "1": {
                "route": ["A1", "T1", "A1"],
                "distance": 20.0,
                "fuel_budget": 100.0,
            },
            "2": {
                "route": ["A2", "T2", "A2"],
                "distance": 40.0,
                "fuel_budget": 100.0,
            },
        }
    }

    result = apply_time_sync(
        solution=solution,
        env={"targets": [{"id": "T1"}, {"id": "T2"}]},
        drone_configs={
            "1": {"enabled": True, "speed": 1.0},
            "2": {"enabled": True, "speed": 1.0},
        },
        distance_matrix=_matrix(),
        options={
            "enabled": True,
            "mode": "selected_targets",
            "members": [
                {"drone_id": "1", "target_id": "T1"},
                {"drone_id": "2", "target_id": "T9"},
                {"drone_id": "2"},
                None,
            ],
        },
    )

    assert result["time_sync"]["status"] == "skipped"
    assert result["time_sync"]["synchronized_waypoints"] == [
        {"drone_id": "1", "waypoint": "T1", "natural_arrival": 10.0}
    ]
    assert result["time_sync"]["warnings"] == [
        "D2 selected sync target T9 is not a known target.",
        "Ignored selected sync member with missing drone or target.",
        "Ignored malformed selected sync member.",
    ]


def test_time_sync_tolerance_reduces_or_eliminates_wait():
    def run(tolerance):
        return apply_time_sync(
            solution={
                "routes": {
                    "1": {
                        "route": ["A1", "T1", "A1"],
                        "distance": 20.0,
                        "fuel_budget": 100.0,
                    },
                    "2": {
                        "route": ["A2", "T2", "A2"],
                        "distance": 40.0,
                        "fuel_budget": 100.0,
                    },
                }
            },
            env={"targets": [{"id": "T1"}, {"id": "T2"}]},
            drone_configs={
                "1": {"enabled": True, "speed": 2.0},
                "2": {"enabled": True, "speed": 1.0},
            },
            distance_matrix=_matrix(),
            options={"enabled": True, "tolerance": tolerance},
        )

    exact = run(0)
    windowed = run(5)
    already_close = run(20)

    assert exact["time_sync"]["tolerance"] == 0.0
    assert exact["routes"]["1"]["launch_delay"] == 15.0
    assert exact["routes"]["1"]["arrival_times"]["T1"] == 20.0
    assert windowed["routes"]["1"]["launch_delay"] == 10.0
    assert windowed["routes"]["1"]["arrival_times"]["T1"] == 15.0
    assert already_close["routes"]["1"]["launch_delay"] == 0.0
    assert already_close["routes"]["1"]["arrival_times"]["T1"] == 5.0


def test_animation_frame_includes_sync_wait_before_target_arrival():
    fast_frame = build_animation_frame(
        ["A1", "T1", "A1"],
        [[0.0, 0.0], [0.0, 0.1], [0.0, 0.0]],
        {0: 0, 1: 1, 2: 2},
        leg_distances=[10.0, 10.0],
        wait_distances={"T1": 30.0},
        densify_points=3,
    )
    slow_frame = build_animation_frame(
        ["A2", "T2", "A2"],
        [[1.0, 0.0], [1.0, 0.2], [1.0, 0.0]],
        {0: 0, 1: 1, 2: 2},
        leg_distances=[20.0, 20.0],
        densify_points=3,
    )

    fast_target_idx = fast_frame["waypoint_indices"][1]
    slow_target_idx = slow_frame["waypoint_indices"][1]

    assert fast_frame["cumulative_distances"][fast_target_idx] == 40.0
    assert fast_frame["total_distance"] == 50.0
    assert (
        fast_frame["cumulative_distances"][fast_target_idx] / 2.0
        == slow_frame["cumulative_distances"][slow_target_idx] / 1.0
        == 20.0
    )
